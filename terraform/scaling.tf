# ===========================================================================
# SCALING RULES
# ===========================================================================

# ---------------------------------------------------------------------------
# REACTIVE scaling: CloudWatch watches the average CPU of the group.
#   CPU > 60% for 1 minute   -> alarm fires -> add 2 servers
#   CPU < 25% for 5 minutes  -> remove 1 server
# We use explicit alarms (step scaling) rather than target tracking so the
# exact moment the alarm fired is recorded and can be measured.
# ---------------------------------------------------------------------------
resource "aws_autoscaling_policy" "scale_out" {
  count                     = local.s.reactive ? 1 : 0
  name                      = "${var.project}-scale-out"
  autoscaling_group_name    = aws_autoscaling_group.app.name
  policy_type               = "StepScaling"
  adjustment_type           = "ChangeInCapacity"
  metric_aggregation_type   = "Average"
  estimated_instance_warmup = 60

  step_adjustment {
    scaling_adjustment          = var.scale_out_step
    metric_interval_lower_bound = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  count               = local.s.reactive ? 1 : 0
  alarm_name          = "${var.project}-cpu-high"
  alarm_description   = "Average web-server CPU above ${var.scale_out_cpu_percent}% - add servers"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app.name }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.scale_out_cpu_percent
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_autoscaling_policy.scale_out[0].arn]
}

resource "aws_autoscaling_policy" "scale_in" {
  count                   = local.s.reactive ? 1 : 0
  name                    = "${var.project}-scale-in"
  autoscaling_group_name  = aws_autoscaling_group.app.name
  policy_type             = "StepScaling"
  adjustment_type         = "ChangeInCapacity"
  metric_aggregation_type = "Average"

  step_adjustment {
    scaling_adjustment          = -1
    metric_interval_upper_bound = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_low" {
  count               = local.s.reactive ? 1 : 0
  alarm_name          = "${var.project}-cpu-low"
  alarm_description   = "Average web-server CPU below 25% for 5 minutes - remove a server"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app.name }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  threshold           = 25
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_autoscaling_policy.scale_in[0].arn]
}

# ---------------------------------------------------------------------------
# SCHEDULED scaling: the result-release time is known in advance, so ask for
# extra servers BEFORE students arrive, then go back to normal afterwards.
#
# The "release time" is release_in_minutes after you run `terraform apply`.
# `terraform output` tells you exactly when to start the load test.
# ---------------------------------------------------------------------------
locals {
  release_time = timeadd(plantimestamp(), "${var.release_in_minutes}m")
  prewarm_time = timeadd(local.release_time, "-${var.prewarm_lead_minutes}m")
  restore_time = timeadd(local.release_time, "${var.prewarm_hold_minutes}m")
}

resource "aws_autoscaling_schedule" "prewarm" {
  count                  = local.s.scheduled ? 1 : 0
  scheduled_action_name  = "${var.project}-prewarm"
  autoscaling_group_name = aws_autoscaling_group.app.name
  start_time             = local.prewarm_time
  min_size               = var.prewarm_servers
  max_size               = local.s.max
  desired_capacity       = var.prewarm_servers
}

resource "aws_autoscaling_schedule" "restore" {
  count                  = local.s.scheduled ? 1 : 0
  scheduled_action_name  = "${var.project}-restore"
  autoscaling_group_name = aws_autoscaling_group.app.name
  start_time             = local.restore_time
  min_size               = local.s.min
  max_size               = local.s.max
  desired_capacity       = local.s.min
}
