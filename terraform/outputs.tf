# ===========================================================================
# Printed after `terraform apply` (and any time with `terraform output`).
# The measurement scripts in ../analysis also read these values.
# ===========================================================================

output "portal_url" {
  description = "Open this in your browser: the result portal"
  value       = "http://${aws_lb.app.dns_name}"
}

output "admin_url" {
  description = "Publish / hide results here"
  value       = "http://${aws_lb.app.dns_name}/admin"
}

output "dashboard_url" {
  description = "Live CloudWatch dashboard for the experiment"
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "locust_url" {
  description = "Locust control page (only if load generators were created)"
  value       = local.loadgen_enabled ? "http://${aws_instance.locust_master[0].public_ip}:8089" : ""
}

output "strategy" {
  value = var.strategy
}

output "release_time_utc" {
  description = "Scheduled strategies: start the load test at this time"
  value       = local.s.scheduled ? local.release_time : "not a scheduled strategy - start the load test whenever you like"
}

output "release_time_ist" {
  description = "The same time in Indian Standard Time"
  value       = local.s.scheduled ? formatdate("DD MMM YYYY, HH:mm 'IST'", timeadd(local.release_time, "5h30m")) : ""
}

# --- values used by analysis/monitor.py and analysis/fetch_cloudwatch.py ---
output "region" {
  value = var.region
}

output "asg_name" {
  value = aws_autoscaling_group.app.name
}

output "target_group_arn" {
  value = aws_lb_target_group.app.arn
}

output "alb_arn_suffix" {
  value = aws_lb.app.arn_suffix
}

output "target_group_arn_suffix" {
  value = aws_lb_target_group.app.arn_suffix
}

output "db_identifier" {
  value = aws_db_instance.main.identifier
}

output "cpu_alarm_name" {
  value = local.s.reactive ? aws_cloudwatch_metric_alarm.cpu_high[0].alarm_name : ""
}

output "instance_type" {
  value = var.instance_type
}

output "min_servers" {
  value = local.s.min
}

output "max_servers" {
  value = local.s.max
}

output "prewarm_servers" {
  value = var.prewarm_servers
}

output "prewarm_lead_minutes" {
  value = var.prewarm_lead_minutes
}

output "prewarm_hold_minutes" {
  value = var.prewarm_hold_minutes
}
