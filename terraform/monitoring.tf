# ===========================================================================
# CLOUDWATCH DASHBOARD: one screen showing the whole experiment live.
# Open it from the `dashboard_url` output during a load test (great for the
# viva demo).
# ===========================================================================

locals {
  alb = aws_lb.app.arn_suffix
  tg  = aws_lb_target_group.app.arn_suffix
  asg = aws_autoscaling_group.app.name
  db  = aws_db_instance.main.identifier

  widget = {
    period = 60
    region = var.region
    view   = "timeSeries"
  }
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project}-experiment"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = merge(local.widget, {
          title       = "Response time seen at the load balancer (p95, seconds)"
          metrics     = [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", local.alb, { stat = "p95", label = "p95 latency" }]]
          annotations = { horizontal = [{ label = "SLO 0.5 s", value = 0.5, color = "#d62728" }] }
        })
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = merge(local.widget, {
          title = "Requests and errors per minute"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", local.alb, { stat = "Sum", label = "Requests" }],
            [".", "HTTPCode_ELB_5XX_Count", ".", ".", { stat = "Sum", label = "5xx from load balancer", yAxis = "right" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum", label = "5xx from servers", yAxis = "right" }],
          ]
        })
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = merge(local.widget, {
          title = "Number of web servers"
          metrics = [
            ["AWS/AutoScaling", "GroupDesiredCapacity", "AutoScalingGroupName", local.asg, { stat = "Maximum", label = "Requested" }],
            [".", "GroupInServiceInstances", ".", ".", { stat = "Maximum", label = "In service" }],
            ["AWS/ApplicationELB", "HealthyHostCount", "TargetGroup", local.tg, "LoadBalancer", local.alb, { stat = "Maximum", label = "Healthy behind load balancer" }],
          ]
        })
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = merge(local.widget, {
          title       = "Web server CPU (average across the group, %)"
          metrics     = [["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", local.asg, { stat = "Average", label = "CPU %" }]]
          annotations = { horizontal = [{ label = "Scale-out threshold", value = var.scale_out_cpu_percent }] }
          yAxis       = { left = { min = 0, max = 100 } }
        })
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = merge(local.widget, {
          title   = "Database CPU (%) - the bottleneck check"
          metrics = [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", local.db, { stat = "Average", label = "RDS CPU %" }]]
          yAxis   = { left = { min = 0, max = 100 } }
        })
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = merge(local.widget, {
          title   = "Open database connections"
          metrics = [["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", local.db, { stat = "Maximum", label = "Connections" }]]
        })
      },
    ]
  })
}

# Optional monthly budget email (personal AWS accounts only).
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}
