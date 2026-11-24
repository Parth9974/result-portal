# ===========================================================================
# LOAD GENERATORS (Locust)
# One laptop cannot pretend to be 5,000 students, so we rent a few extra
# machines to do it. They live OUTSIDE the Auto Scaling Group, so they never
# count as "web servers". Created only when loadgen_workers > 0.
#
#   master  -> the control page you open in your browser (port 8089)
#   workers -> do the actual work of simulating students
# ===========================================================================

locals {
  loadgen_enabled = var.loadgen_workers > 0
  loadgen_common = {
    repo_url    = var.app_repo_url
    repo_branch = var.app_repo_branch
    target_url  = "http://${aws_lb.app.dns_name}"
    peak_users  = var.peak_users
  }
}

resource "aws_instance" "locust_master" {
  count                  = local.loadgen_enabled ? 1 : 0
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.loadgen_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.loadgen.id]

  user_data = templatefile("${path.module}/templates/loadgen_user_data.sh.tftpl", merge(local.loadgen_common, {
    role      = "master"
    master_ip = ""
  }))
  user_data_replace_on_change = true

  lifecycle {
    ignore_changes = [ami] # don't rebuild if Amazon publishes a newer image mid-project
  }

  tags = { Name = "${var.project}-locust-master" }
}

resource "aws_instance" "locust_worker" {
  count                  = local.loadgen_enabled ? var.loadgen_workers : 0
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.loadgen_instance_type
  subnet_id              = aws_subnet.public[count.index % 2].id
  vpc_security_group_ids = [aws_security_group.loadgen.id]

  user_data = templatefile("${path.module}/templates/loadgen_user_data.sh.tftpl", merge(local.loadgen_common, {
    role      = "worker"
    master_ip = aws_instance.locust_master[0].private_ip
  }))
  user_data_replace_on_change = true

  lifecycle {
    ignore_changes = [ami] # don't rebuild if Amazon publishes a newer image mid-project
  }

  tags = { Name = "${var.project}-locust-worker-${count.index + 1}" }
}
