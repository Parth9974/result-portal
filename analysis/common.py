"""Shared helpers for the measurement scripts."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(os.environ.get("PORTAL_RESULTS_DIR", ROOT / "results"))
TERRAFORM_DIR = ROOT / "terraform"

# The order (and colour) of each strategy is fixed everywhere, so a strategy
# keeps the same colour in every chart.
STRATEGIES = ["fixed_minimal", "reactive", "scheduled", "over_provisioned", "scheduled_reactive"]
LABELS = {
    "fixed_minimal": "Fixed minimal (2)",
    "reactive": "Reactive auto-scaling",
    "scheduled": "Scheduled scaling",
    "over_provisioned": "Over-provisioned (10)",
    "scheduled_reactive": "Scheduled + reactive",
}


def terraform_outputs() -> dict:
    """Read `terraform output -json` and flatten it to {name: value}."""
    exe = shutil.which("terraform") or shutil.which("tofu")
    if not exe:
        sys.exit("Couldn't find terraform on your PATH. Install it, or run this from a terminal where `terraform -version` works.")
    proc = subprocess.run([exe, "output", "-json"], cwd=TERRAFORM_DIR, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip() == "{}":
        sys.exit("Terraform has no outputs yet. Run `terraform apply` in the terraform folder first.\n" + proc.stderr)
    return {k: v["value"] for k, v in json.loads(proc.stdout).items()}


def run_dir(name: str) -> Path:
    path = RESULTS_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_meta(name: str) -> dict:
    path = RESULTS_DIR / name / "meta.json"
    if not path.exists():
        sys.exit(f"No results/{name}/meta.json. Run monitor.py --name {name} first.")
    return json.loads(path.read_text())


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}
