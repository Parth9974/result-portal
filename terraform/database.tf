# ===========================================================================
# DATABASE (Amazon RDS for MySQL)
# One shared database that every web server reads from. Kept in the private
# subnets: it has no public address at all.
#
# This is deliberately a SINGLE, small instance: part of the experiment is
# showing that it becomes the bottleneck when the web tier scales out.
# ===========================================================================

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "random_password" "flask_secret" {
  length  = 48
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project}-db"
  engine         = "mysql"
  engine_version = "8.0"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "results"
  username = "admin"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  # Speed and cost settings for a short-lived test environment:
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = { Name = "${var.project}-db" }
}
