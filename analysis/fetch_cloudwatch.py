"""
Downloads the CloudWatch numbers for one recorded run.

Run it about 5 minutes after monitor.py finishes (CloudWatch lags a little):

    python fetch_cloudwatch.py --name reactive-run1

Saves into results/<name>/:
  cloudwatch.csv - one row per minute: web-server CPU, database CPU,
                   database connections, load-balancer p95 latency,
                   request count and 5xx error counts
  alarms.csv     - every time the CPU alarm changed state (reactive runs).
                   The moment it went to ALARM is "alarm evaluation done".
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone

import boto3

from common import load_meta, run_dir


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    meta = load_meta(args.name)
    tf = meta["terraform"]
    start = parse(meta["started_utc"]) - timedelta(minutes=5)
    end = parse(meta["ended_utc"]) + timedelta(minutes=5)
    cw = boto3.client("cloudwatch", region_name=tf["region"])

    def q(qid, ns, metric, dims, stat):
        return {
            "Id": qid,
            "MetricStat": {
                "Metric": {"Namespace": ns, "MetricName": metric,
                           "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
                "Period": 60, "Stat": stat,
            },
            "ReturnData": True,
        }

    alb = {"LoadBalancer": tf["alb_arn_suffix"]}
    queries = [
        q("web_cpu", "AWS/EC2", "CPUUtilization", {"AutoScalingGroupName": tf["asg_name"]}, "Average"),
        q("db_cpu", "AWS/RDS", "CPUUtilization", {"DBInstanceIdentifier": tf["db_identifier"]}, "Average"),
        q("db_connections", "AWS/RDS", "DatabaseConnections", {"DBInstanceIdentifier": tf["db_identifier"]}, "Maximum"),
        q("alb_p95_s", "AWS/ApplicationELB", "TargetResponseTime", alb, "p95"),
        q("requests", "AWS/ApplicationELB", "RequestCount", alb, "Sum"),
        q("elb_5xx", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", alb, "Sum"),
        q("target_5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", alb, "Sum"),
        q("in_service", "AWS/AutoScaling", "GroupInServiceInstances", {"AutoScalingGroupName": tf["asg_name"]}, "Maximum"),
    ]

    series: dict[str, dict] = {}
    token = None
    while True:
        kwargs = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
        if token:
            kwargs["NextToken"] = token
        resp = cw.get_metric_data(**kwargs)
        for r in resp["MetricDataResults"]:
            for ts, val in zip(r["Timestamps"], r["Values"]):
                series.setdefault(ts.strftime("%Y-%m-%dT%H:%M:%SZ"), {})[r["Id"]] = round(val, 4)
        token = resp.get("NextToken")
        if not token:
            break

    folder = run_dir(args.name)
    cols = [qq["Id"] for qq in queries]
    with open(folder / "cloudwatch.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", *cols])
        for ts in sorted(series):
            w.writerow([ts, *[series[ts].get(c, "") for c in cols]])
    print(f"cloudwatch.csv: {len(series)} minutes of data")

    alarm = tf.get("cpu_alarm_name")
    with open(folder / "alarms.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "alarm", "summary"])
        if alarm:
            hist = cw.describe_alarm_history(AlarmName=alarm, HistoryItemType="StateUpdate",
                                             StartDate=start, EndDate=end, MaxRecords=100)
            for h in sorted(hist["AlarmHistoryItems"], key=lambda h: h["Timestamp"]):
                w.writerow([h["Timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"), h["AlarmName"], h["HistorySummary"]])
            print(f"alarms.csv: {len(hist['AlarmHistoryItems'])} state changes")
        else:
            print("alarms.csv: no CPU alarm in this strategy")


if __name__ == "__main__":
    main()
