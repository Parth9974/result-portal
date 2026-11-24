# ===========================================================================
# WEB SERVERS, LOAD BALANCER and AUTO SCALING GROUP
# ===========================================================================

# Translate the chosen strategy into concrete numbers.
locals {
  strategy_settings = {
    fixed_minimal      = { min = var.min_servers, max = var.min_servers, reactive = false, scheduled = false }
    reactive           = { min = var.min_servers, max = var.max_servers, reactive = true, scheduled = false }
    scheduled          = { min = var.min_servers, max = var.max_servers, reactive = false, scheduled = true }
    over_provisioned   = { min = var.max_servers, max = var.max_servers, reactive = false, scheduled = false }
    scheduled_reactive = { min = var.min_servers, max = var.max_servers, reactive = true, scheduled = true }
  }
  s = local.strategy_settings[var.strategy]
}

# Latest Amazon Linux 2023 image, published by Amazon.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023*-x86_64"]
  }
}

# ---------------------------------------------------------------------------
# Launch template = the "recipe card" for every new web server.
# ---------------------------------------------------------------------------
resource "aws_launch_template" "app" {
  name_prefix   = "${var.project}-app-"
  image_id      = data.aws_ami.al2023.id
  instance_type = var.instance_type

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.app.id]
  }

  # 1-minute CloudWatch metrics instead of 5-minute ones. Without this the
  # CPU alarm would react five times slower.
  monitoring {
    enabled = true
  }

  # Burstable (t3) instances: "unlimited" stops AWS from silently throttling
  # the CPU when credits run out, which would distort the measurements.
  credit_specification {
    cpu_credits = "unlimited"
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 (the app reads its own instance id)
  }

  user_data = base64encode(templatefile("${path.module}/templates/app_user_data.sh.tftpl", {
    repo_url       = var.app_repo_url
    repo_branch    = var.app_repo_branch
    db_host        = aws_db_instance.main.address
    db_password    = random_password.db.result
    secret_key     = random_password.flask_secret.result
    admin_password = var.admin_password
    cache_seconds  = var.app_cache_seconds
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-web" }
  }

  # Keep the same base image for the whole project, so a new Amazon Linux
  # release can't change the servers halfway through your experiments.
  lifecycle {
    ignore_changes = [image_id]
  }
}

# ---------------------------------------------------------------------------
# Application Load Balancer: the single public address students visit.
# It spreads requests across healthy servers and checks /health every 10 s.
# ---------------------------------------------------------------------------
resource "aws_lb" "app" {
  name               = "${var.project}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  idle_timeout       = 30
}

resource "aws_lb_target_group" "app" {
  name                 = "${var.project}-tg"
  port                 = 8000
  protocol             = "HTTP"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# ---------------------------------------------------------------------------
# Auto Scaling Group: keeps between min and max servers running, in both
# availability zones, and replaces any server the load balancer marks dead.
# ---------------------------------------------------------------------------
resource "aws_autoscaling_group" "app" {
  name                = "${var.project}-asg"
  vpc_zone_identifier = aws_subnet.public[*].id
  target_group_arns   = [aws_lb_target_group.app.arn]

  min_size         = local.s.min
  max_size         = local.s.max
  desired_capacity = local.s.min

  health_check_type         = "ELB"
  health_check_grace_period = 300
  default_instance_warmup   = 60

  launch_template {
    id      = aws_launch_template.app.id
    version = aws_launch_template.app.latest_version
  }

  # Record group-size metrics every minute (used in the graphs).
  metrics_granularity = "1Minute"
  enabled_metrics = [
    "GroupDesiredCapacity", "GroupInServiceInstances", "GroupPendingInstances",
    "GroupTotalInstances", "WarmPoolWarmedCapacity",
  ]

  # Optional upgrade: a pool of already-booted, stopped servers.
  dynamic "warm_pool" {
    for_each = var.enable_warm_pool ? [1] : []
    content {
      pool_state                  = "Stopped"
      min_size                    = 0
      max_group_prepared_capacity = local.s.max
    }
  }

  # If the recipe changes (e.g. caching turned on), replace servers gradually.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-web"
    propagate_at_launch = true
  }

  # The database must exist before servers try to seed it.
  depends_on = [aws_db_instance.main, aws_lb_listener.http]
}
