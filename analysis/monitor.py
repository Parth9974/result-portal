"""
Records an experiment, every 5 seconds, while the load test runs.

Start this BEFORE you press "Start" on the Locust page, and leave it running.
It stops on its own a minute after the Locust test finishes (or press Ctrl+C).

    python monitor.py --name reactive-run1

What it records (into results/<name>/):
  timeline.csv   - users, requests/s, failures/s, p50 & p95 latency,
                   servers requested / in service / healthy, every 5 s
  instances.csv  - for every web server: when it was launched, when the Auto
                   Scaling Group marked it InService, and when the load
                   balancer first reported it healthy. These are the
                   timestamps used to break scale-out time into its parts.
  activities.csv - the Auto Scaling Group's own activity history
  meta.json      - strategy, start/end time and resource names
"""
import argparse
import csv
import json
import signal
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from common import run_dir, terraform_outputs

POLL_SECONDS = 5
TIMELINE_FIELDS = [
    "time_utc", "elapsed_s", "locust_state", "users", "rps", "fail_per_s",
    "p50_ms", "p95_ms", "total_requests", "total_failures", "overall_p95_ms",
    "desired", "in_service", "pending", "healthy_targets",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def read_locust(url: str) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/stats/requests", timeout=4) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        return {"locust_state": f"unreachable ({type(exc).__name__})"}
    total = data["stats"][-1] if data.get("stats") else {}
    current = data.get("current_response_time_percentiles") or {}
    return {
        "locust_state": data.get("state", ""),
        "users": data.get("user_count", 0),
        "rps": round(total.get("current_rps", 0.0), 2),
        "fail_per_s": round(total.get("current_fail_per_sec", 0.0), 2),
        "p50_ms": current.get("response_time_percentile_0.5") or "",
        "p95_ms": current.get("response_time_percentile_0.95") or "",
        "total_requests": total.get("num_requests", 0),
        "total_failures": total.get("num_failures", 0),
        "overall_p95_ms": total.get("response_time_percentile_0.95", ""),
    }


class AwsWatcher:
    def __init__(self, out: dict):
        import boto3  # imported here so local-only runs don't need it
        self.asg_name = out["asg_name"]
        self.tg_arn = out["target_group_arn"]
        self.asg = boto3.client("autoscaling", region_name=out["region"])
        self.elb = boto3.client("elbv2", region_name=out["region"])
        self.ec2 = boto3.client("ec2", region_name=out["region"])
        self.instances: dict[str, dict] = {}

    def poll(self, t) -> dict:
        group = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[self.asg_name])["AutoScalingGroups"][0]
        health = self.elb.describe_target_health(TargetGroupArn=self.tg_arn)["TargetHealthDescriptions"]
        healthy_ids = {h["Target"]["Id"] for h in health if h["TargetHealth"]["State"] == "healthy"}

        new_ids = []
        for inst in group["Instances"]:
            iid = inst["InstanceId"]
            rec = self.instances.setdefault(iid, {
                "instance_id": iid, "zone": inst["AvailabilityZone"], "first_seen": t,
                "launch_time": None, "in_service": None, "healthy": None, "left": None,
            })
            if rec["launch_time"] is None:
                new_ids.append(iid)
            if inst["LifecycleState"] == "InService" and rec["in_service"] is None:
                rec["in_service"] = t
            if iid in healthy_ids and rec["healthy"] is None:
                rec["healthy"] = t
        current = {i["InstanceId"] for i in group["Instances"]}
        for iid, rec in self.instances.items():
            if iid not in current and rec["left"] is None:
                rec["left"] = t

        if new_ids:
            for res in self.ec2.describe_instances(InstanceIds=new_ids)["Reservations"]:
                for inst in res["Instances"]:
                    self.instances[inst["InstanceId"]]["launch_time"] = inst["LaunchTime"]

        states = [i["LifecycleState"] for i in group["Instances"]]
        return {
            "desired": group["DesiredCapacity"],
            "in_service": states.count("InService"),
            "pending": sum(s.startswith("Pending") for s in states),
            "healthy_targets": len(healthy_ids),
        }

    def save(self, folder, started):
        with open(folder / "instances.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["instance_id", "zone", "first_seen", "launch_time", "in_service", "healthy", "left"])
            for r in self.instances.values():
                w.writerow([r["instance_id"], r["zone"], iso(r["first_seen"]), iso(r["launch_time"]),
                            iso(r["in_service"]), iso(r["healthy"]), iso(r["left"])])
        acts = self.asg.describe_scaling_activities(AutoScalingGroupName=self.asg_name, MaxRecords=100)["Activities"]
        with open(folder / "activities.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["start_time", "end_time", "status", "description", "cause"])
            for a in sorted(acts, key=lambda a: a["StartTime"]):
                if a["StartTime"] >= started - timedelta(minutes=30):
                    w.writerow([iso(a["StartTime"]), iso(a.get("EndTime")), a["StatusCode"], a["Description"], a["Cause"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="a folder name for this run, e.g. reactive-run1")
    ap.add_argument("--locust-url", help="defaults to the locust_url Terraform output")
    ap.add_argument("--strategy", help="defaults to the strategy Terraform output")
    ap.add_argument("--no-aws", action="store_true", help="only record Locust (for local testing)")
    ap.add_argument("--max-minutes", type=float, default=60)
    args = ap.parse_args()

    out = {} if args.no_aws else terraform_outputs()
    locust_url = args.locust_url or out.get("locust_url")
    if not locust_url:
        raise SystemExit("No Locust URL. Create load generators (loadgen_workers > 0) or pass --locust-url.")
    strategy = args.strategy or out.get("strategy", "unknown")
    aws = None if args.no_aws else AwsWatcher(out)

    folder = run_dir(args.name)
    started = now_utc()
    meta = {"name": args.name, "strategy": strategy, "started_utc": iso(started),
            "locust_url": locust_url, "terraform": out}
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

    print(f"Recording '{args.name}' ({strategy}) into {folder}. Press Ctrl+C to stop.")
    print(f"{'elapsed':>8} {'users':>6} {'req/s':>7} {'p95 ms':>7} {'fail/s':>7} {'servers':>8} {'healthy':>8}")
    seen_running, idle_since = False, None

    with open(folder / "timeline.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TIMELINE_FIELDS)
        writer.writeheader()
        while not stop["flag"]:
            t = now_utc()
            row = {"time_utc": iso(t), "elapsed_s": int((t - started).total_seconds())}
            row.update(read_locust(locust_url))
            if aws:
                try:
                    row.update(aws.poll(t))
                except Exception as exc:  # noqa: BLE001 - keep recording even if one poll fails
                    print(f"  (AWS poll failed: {exc})")
            writer.writerow(row)
            f.flush()
            print(f"{row['elapsed_s']:>7}s {row.get('users', ''):>6} {row.get('rps', ''):>7} "
                  f"{row.get('p95_ms', ''):>7} {row.get('fail_per_s', ''):>7} "
                  f"{row.get('in_service', '-'):>8} {row.get('healthy_targets', '-'):>8}")

            state = row.get("locust_state", "")
            if state in ("running", "spawning"):
                seen_running, idle_since = True, None
            elif seen_running:
                idle_since = idle_since or t
                if (t - idle_since).total_seconds() > 60:
                    print("Locust test finished. Stopping.")
                    break
            if (t - started).total_seconds() > args.max_minutes * 60:
                print("Reached --max-minutes. Stopping.")
                break
            time.sleep(POLL_SECONDS)

    ended = now_utc()
    meta["ended_utc"] = iso(ended)
    if aws:
        aws.save(folder, started)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"Saved in {folder}.")
    if aws:
        print(f"Wait ~5 minutes for CloudWatch to catch up, then run:\n"
              f"    python fetch_cloudwatch.py --name {args.name}")


if __name__ == "__main__":
    main()
