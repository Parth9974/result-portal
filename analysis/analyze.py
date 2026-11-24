"""
Turns every recorded run in results/ into graphs and a summary table.

    python analyze.py

Reads results/<run>/{meta.json, timeline.csv, instances.csv, cloudwatch.csv, alarms.csv}
and writes into results/report/:
  latency_over_time.png    p95 response time, every strategy, with the 500 ms SLO
  errors_over_time.png     % of requests failing
  servers_over_time.png    students online (top) and healthy servers (bottom)
  database_cpu.png         database CPU: the system-level bottleneck
  scaleout_breakdown.png   alarm + launch + start-up time vs. burst length
  semester_cost.png        estimated semester cost, marked pass / fail
  summary.md / summary.csv the numbers behind all of the above

Cost model (edit with the flags below):
  semester cost = (baseline servers x semester hours
                   + extra server-hours per result release x releases) x price
The load balancer and database cost the same in every strategy, so they are
left out of the comparison.
"""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from common import LABELS, RESULTS_DIR, STRATEGIES  # noqa: E402

SLO_MS = 500
SLO_ERROR_PCT = 1.0

# Fixed colour per strategy (validated categorical palette, light surface).
COLORS = {
    "fixed_minimal": "#2a78d6",
    "reactive": "#eb6834",
    "scheduled": "#1baf7a",
    "over_provisioned": "#c98500",
    "scheduled_reactive": "#e87ba4",
}
INK, INK_2, GRID, SURFACE = "#1d2b53", "#55607e", "#e4e7ee", "#fcfcfb"
PART_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]

# Approximate on-demand Linux prices (USD/hour). Check the AWS EC2 pricing
# page for your region and pass --price to override.
PRICES = {"t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416, "t3.large": 0.0832}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10.5, "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "axes.titlelocation": "left", "legend.frameon": False, "axes.axisbelow": True,
})


def ts(s: str):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


# ---------------------------------------------------------------------------
# Loading one run
# ---------------------------------------------------------------------------
class Run:
    def __init__(self, folder: Path):
        self.folder = folder
        self.name = folder.name
        self.meta = json.loads((folder / "meta.json").read_text())
        self.strategy = self.meta.get("strategy", "unknown")
        self.tf = self.meta.get("terraform", {})

        tl = pd.read_csv(folder / "timeline.csv")
        tl["t"] = pd.to_datetime(tl["time_utc"], utc=True)
        for col in ["users", "rps", "fail_per_s", "p50_ms", "p95_ms", "total_requests",
                    "total_failures", "overall_p95_ms", "in_service", "healthy_targets", "desired"]:
            if col in tl:
                tl[col] = pd.to_numeric(tl[col], errors="coerce")
        self.tl = tl
        self.peak = tl["users"].max() if "users" in tl else 0

        # Burst start: the first moment the number of students passes 10% of
        # the peak (the quiet first minute runs at ~2%).
        over = tl[tl["users"] >= 0.10 * self.peak]
        self.t0 = over["t"].iloc[0] if len(over) else tl["t"].iloc[0]
        reached = tl[tl["users"] >= 0.95 * self.peak]
        self.burst_seconds = ((reached["t"].iloc[0] - self.t0).total_seconds()
                              if len(reached) else float("nan"))
        tl["minutes"] = (tl["t"] - self.t0).dt.total_seconds() / 60
        tl["error_pct"] = (tl["fail_per_s"] / tl["rps"].where(tl["rps"] > 0) * 100).fillna(0)

        self.instances = self._read(folder / "instances.csv")
        self.cloudwatch = self._read(folder / "cloudwatch.csv")
        self.alarms = self._read(folder / "alarms.csv")
        if self.cloudwatch is not None:
            self.cloudwatch["t"] = pd.to_datetime(self.cloudwatch["time_utc"], utc=True)
            self.cloudwatch["minutes"] = (self.cloudwatch["t"] - self.t0).dt.total_seconds() / 60

    @staticmethod
    def _read(path: Path):
        if not path.exists():
            return None
        df = pd.read_csv(path)
        return df if len(df) else None

    # ---- headline numbers ----
    def totals(self):
        last = self.tl.dropna(subset=["total_requests"]).iloc[-1]
        req, fail = last["total_requests"], last["total_failures"]
        return {
            "requests": int(req),
            "error_pct": round(100 * fail / req, 2) if req else 0.0,
            "p95_ms": float(last.get("overall_p95_ms", float("nan"))),
        }

    def slo_breach_minutes(self):
        """Minutes during which the live p95 or error rate broke the SLO."""
        tl = self.tl[self.tl["minutes"] >= 0]
        bad = (tl["p95_ms"] > SLO_MS) | (tl["error_pct"] > SLO_ERROR_PCT)
        step = tl["t"].diff().dt.total_seconds().median() or 5
        return round(bad.sum() * step / 60, 1)

    def server_hours_above(self, level: float) -> float:
        col = "in_service" if "in_service" in self.tl and self.tl["in_service"].notna().any() else None
        if col is None:
            return 0.0
        tl = self.tl.dropna(subset=[col])
        dt = tl["t"].diff().dt.total_seconds().fillna(0) / 3600
        return float(((tl[col] - level).clip(lower=0) * dt).sum())

    def scaleout_parts(self):
        """Break the first scale-out after the burst into its three delays (seconds)."""
        if self.instances is None:
            return None
        inst = self.instances.copy()
        for c in ["launch_time", "in_service", "healthy"]:
            inst[c] = pd.to_datetime(inst[c], utc=True, errors="coerce")
        new = inst[inst["launch_time"] > self.t0].sort_values("launch_time")
        if new.empty:
            return None
        first_launch = new["launch_time"].iloc[0]
        batch = new[new["launch_time"] <= first_launch + pd.Timedelta(seconds=90)]

        alarm_t = None
        if self.alarms is not None:
            fired = self.alarms[self.alarms["summary"].str.contains("to ALARM", na=False)].copy()
            fired["t"] = pd.to_datetime(fired["time_utc"], utc=True)
            fired = fired[fired["t"] >= self.t0 - pd.Timedelta(seconds=30)]
            if len(fired):
                alarm_t = fired["t"].iloc[0]
        if alarm_t is None or alarm_t > first_launch:
            alarm_t = first_launch      # no alarm record: count everything up to launch as "alarm"

        in_service = batch["in_service"].median()
        healthy = batch["healthy"].median()
        return {
            "alarm_s": max(0.0, (alarm_t - self.t0).total_seconds()),
            "launch_s": max(0.0, (in_service - alarm_t).total_seconds()) if pd.notna(in_service) else float("nan"),
            "startup_s": max(0.0, (healthy - in_service).total_seconds()) if pd.notna(healthy) and pd.notna(in_service) else float("nan"),
        }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def by_strategy(runs):
    order = {s: i for i, s in enumerate(STRATEGIES)}
    return sorted(runs, key=lambda r: (order.get(r.strategy, 99), r.name))


def line_chart(runs, ycol, title, ylabel, path, slo=None, log=False):
    fig, ax = plt.subplots(figsize=(10, 5.2))
    seen = set()
    for r in by_strategy(runs):
        tl = r.tl[(r.tl["minutes"] >= -1.5)].dropna(subset=[ycol])
        first = r.strategy not in seen
        seen.add(r.strategy)
        ax.plot(tl["minutes"], tl[ycol].clip(lower=1 if log else None), color=COLORS.get(r.strategy, INK_2),
                lw=2, alpha=1 if first else 0.45, label=LABELS.get(r.strategy, r.strategy) if first else None)
    if slo is not None:
        ax.axhline(slo, color="#c1272d", lw=1.2, ls=(0, (5, 3)))
        ax.text(ax.get_xlim()[1], slo, f"  SLO {slo:g}", va="center", color="#c1272d", fontsize=9.5, clip_on=False)
    ax.axvline(0, color=INK_2, lw=0.8)
    ax.text(0.1, ax.get_ylim()[1], "burst starts", color=INK_2, fontsize=9, va="top")
    if log:
        ax.set_yscale("log")
    ax.set_title(title)
    ax.set_xlabel("Minutes since the burst started")
    ax.set_ylabel(ylabel)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncols=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def servers_chart(runs, path):
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 6.6), sharex=True, height_ratios=[1, 1.6])
    seen = set()
    for r in by_strategy(runs):
        tl = r.tl[r.tl["minutes"] >= -1.5]
        first = r.strategy not in seen
        seen.add(r.strategy)
        if first and len(seen) == 1:
            top.fill_between(tl["minutes"], tl["users"], color="#b9c2d6", lw=0)
            top.plot(tl["minutes"], tl["users"], color=INK_2, lw=1.5)
        if "healthy_targets" in tl and tl["healthy_targets"].notna().any():
            bottom.step(tl["minutes"], tl["healthy_targets"], where="post", color=COLORS.get(r.strategy, INK_2),
                        lw=2, alpha=1 if first else 0.45, label=LABELS.get(r.strategy, r.strategy) if first else None)
    top.set_title("Students online, and the healthy servers serving them")
    top.set_ylabel("Students online")
    bottom.set_ylabel("Healthy servers")
    bottom.set_ylim(bottom=0)
    bottom.set_xlabel("Minutes since the burst started")
    for ax in (top, bottom):
        ax.axvline(0, color=INK_2, lw=0.8)
    bottom.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncols=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def db_chart(runs, path):
    with_cw = [r for r in by_strategy(runs) if r.cloudwatch is not None and "db_cpu" in r.cloudwatch]
    if not with_cw:
        return False
    fig, ax = plt.subplots(figsize=(10, 5))
    seen = set()
    for r in with_cw:
        cw = r.cloudwatch[r.cloudwatch["minutes"] >= -2]
        first = r.strategy not in seen
        seen.add(r.strategy)
        ax.plot(cw["minutes"], cw["db_cpu"], color=COLORS.get(r.strategy, INK_2), lw=2, marker="o", ms=4,
                alpha=1 if first else 0.45, label=LABELS.get(r.strategy, r.strategy) if first else None)
    ax.set_ylim(0, 100)
    ax.axvline(0, color=INK_2, lw=0.8)
    ax.set_title("Database CPU during the burst: more web servers push the database harder")
    ax.set_xlabel("Minutes since the burst started")
    ax.set_ylabel("RDS CPU (%)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncols=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return True


def scaleout_chart(rows, path):
    rows = [r for r in rows if r["parts"]]
    if not rows:
        return False
    fig, ax = plt.subplots(figsize=(10, 1.6 + 0.75 * len(rows)))
    names = [r["run"] for r in rows]
    parts = [("alarm_s", "Alarm evaluation"), ("launch_s", "Instance launch"),
             ("startup_s", "App start-up + health checks")]
    left = [0.0] * len(rows)
    for (key, label), color in zip(parts, PART_COLORS):
        vals = [(r["parts"][key] if pd.notna(r["parts"][key]) else 0) / 60 for r in rows]
        ax.barh(names, vals, left=left, color=color, edgecolor=SURFACE, linewidth=2, height=0.55, label=label)
        left = [a + b for a, b in zip(left, vals)]
    for y, total in enumerate(left):
        ax.text(total + 0.05, y, f"{total:.1f} min", va="center", color=INK, fontsize=10)
    burst = [r["burst_s"] / 60 for r in rows if pd.notna(r["burst_s"])]
    if burst:
        b = sum(burst) / len(burst)
        ax.axvline(b, color="#c1272d", lw=1.5, ls=(0, (5, 3)))
        ax.text(b, -0.5, f" all students arrived by {b:.1f} min", color="#c1272d", fontsize=9, va="bottom")
    ax.set_title("How long reactive scaling takes to deliver a new server")
    ax.set_xlabel("Minutes after the burst started")
    ax.grid(axis="y", visible=False)
    ax.set_ylim(len(rows) - 0.5, -0.8)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28 if len(rows) < 3 else -0.15), ncols=3, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return True


def cost_chart(best, path):
    fig, ax = plt.subplots(figsize=(10, 4.8))
    names = [LABELS.get(b["strategy"], b["strategy"]) for b in best]
    costs = [b["semester_cost_usd"] for b in best]
    ax.bar(names, costs, color=[COLORS.get(b["strategy"], INK_2) for b in best], width=0.55)
    for i, b in enumerate(best):
        verdict = "meets SLO" if b["slo_met"] else "misses SLO"
        ax.text(i, costs[i], f"${costs[i]:,.0f}\n{verdict}", ha="center", va="bottom", color=INK, fontsize=10)
    ax.set_ylim(0, max(costs) * 1.25 if costs else 1)
    ax.set_ylabel("Estimated web-server cost per semester (USD)")
    ax.set_title("What each strategy costs over a semester, and whether it held up")
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", type=float, help="USD per server-hour (default: looked up from instance type)")
    ap.add_argument("--semester-weeks", type=float, default=20)
    ap.add_argument("--releases", type=int, default=4, help="result releases (bursts) per semester")
    args = ap.parse_args()

    runs = []
    for folder in sorted(RESULTS_DIR.glob("*")):
        if (folder / "meta.json").exists() and (folder / "timeline.csv").exists():
            try:
                runs.append(Run(folder))
            except Exception as exc:  # noqa: BLE001
                print(f"Skipping {folder.name}: {exc}")
    if not runs:
        raise SystemExit("No runs found in results/. Record one with monitor.py first.")

    out = RESULTS_DIR / "report"
    out.mkdir(exist_ok=True)
    semester_hours = args.semester_weeks * 7 * 24

    rows = []
    for r in by_strategy(runs):
        tf = r.tf
        price = args.price or PRICES.get(tf.get("instance_type", "t3.micro"), 0.0104)
        baseline = float(tf.get("min_servers", 2))
        if r.strategy.startswith("scheduled"):
            pre = float(tf.get("prewarm_servers", 8))
            hold_h = (float(tf.get("prewarm_lead_minutes", 10)) + float(tf.get("prewarm_hold_minutes", 40))) / 60
            extra = (pre - baseline) * hold_h + r.server_hours_above(pre)
        else:
            extra = r.server_hours_above(baseline)
        tot = r.totals()
        slo_met = tot["p95_ms"] < SLO_MS and tot["error_pct"] < SLO_ERROR_PCT
        rows.append({
            "run": r.name, "strategy": r.strategy,
            "peak_users": int(r.peak), "requests": tot["requests"],
            "p95_ms": tot["p95_ms"], "error_pct": tot["error_pct"],
            "slo_breach_min": r.slo_breach_minutes(), "slo_met": slo_met,
            "max_servers_seen": int(r.tl["healthy_targets"].max()) if r.tl["healthy_targets"].notna().any() else None,
            "extra_server_hours_per_release": round(extra, 2),
            "semester_cost_usd": round((baseline * semester_hours + extra * args.releases) * price, 2),
            "burst_s": r.burst_seconds, "parts": r.scaleout_parts() if "reactive" in r.strategy else None,
        })

    line_chart(runs, "p95_ms", "95th-percentile response time during the burst",
               "p95 response time (ms, log scale)", out / "latency_over_time.png", slo=SLO_MS, log=True)
    line_chart(runs, "error_pct", "Share of requests that failed", "Failed requests (%)",
               out / "errors_over_time.png", slo=SLO_ERROR_PCT)
    servers_chart(runs, out / "servers_over_time.png")
    has_db = db_chart(runs, out / "database_cpu.png")
    has_scale = scaleout_chart(rows, out / "scaleout_breakdown.png")

    # One bar per strategy: average its repeated runs.
    best = []
    for s in STRATEGIES + sorted({r["strategy"] for r in rows} - set(STRATEGIES)):
        mine = [r for r in rows if r["strategy"] == s]
        if mine:
            best.append({"strategy": s,
                         "semester_cost_usd": sum(m["semester_cost_usd"] for m in mine) / len(mine),
                         "slo_met": all(m["slo_met"] for m in mine)})
    cost_chart(best, out / "semester_cost.png")

    # ---- tables ----
    cols = ["run", "strategy", "peak_users", "requests", "p95_ms", "error_pct", "slo_breach_min",
            "slo_met", "max_servers_seen", "extra_server_hours_per_release", "semester_cost_usd"]
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols + ["alarm_s", "launch_s", "startup_s", "burst_s"])
        w.writeheader()
        for r in rows:
            parts = r["parts"] or {}
            w.writerow({**{c: r[c] for c in cols}, **parts, "burst_s": r["burst_s"]})

    md = ["# Experiment summary", "",
          f"SLO: p95 under {SLO_MS} ms and fewer than {SLO_ERROR_PCT:g}% failed requests. "
          f"Semester = {args.semester_weeks:g} weeks with {args.releases} result releases.", "",
          "| Run | Strategy | Peak users | p95 (ms) | Errors | Minutes breaking SLO | SLO met | Most servers | Semester cost |",
          "|---|---|---:|---:|---:|---:|:---:|---:|---:|"]
    for r in rows:
        md.append(f"| {r['run']} | {LABELS.get(r['strategy'], r['strategy'])} | {r['peak_users']:,} | "
                  f"{r['p95_ms']:,.0f} | {r['error_pct']:.2f}% | {r['slo_breach_min']} | "
                  f"{'yes' if r['slo_met'] else 'no'} | {r['max_servers_seen'] or '-'} | ${r['semester_cost_usd']:,.2f} |")
    scale_rows = [r for r in rows if r["parts"]]
    if scale_rows:
        md += ["", "## Scale-out time (reactive runs)", "",
               "| Run | Alarm evaluation | Instance launch | App start-up + health checks | Total | Burst length |",
               "|---|---:|---:|---:|---:|---:|"]
        for r in scale_rows:
            p = r["parts"]
            total = sum(v for v in p.values() if pd.notna(v))
            md.append(f"| {r['run']} | {p['alarm_s']:.0f} s | {p['launch_s']:.0f} s | {p['startup_s']:.0f} s | "
                      f"{total:.0f} s | {r['burst_s']:.0f} s |")
    md += ["", "## Charts", "", "![Latency](latency_over_time.png)", "", "![Errors](errors_over_time.png)", "",
           "![Servers](servers_over_time.png)", ""]
    if has_db:
        md += ["![Database CPU](database_cpu.png)", ""]
    if has_scale:
        md += ["![Scale-out breakdown](scaleout_breakdown.png)", ""]
    md += ["![Semester cost](semester_cost.png)", ""]
    (out / "summary.md").write_text("\n".join(md))

    print("\n".join(md[:len(rows) + 6]))
    print(f"\nCharts and tables saved in {out}")


if __name__ == "__main__":
    main()
