# ===========================================================================
# FIREWALLS (security groups)
#   Internet  --80-->  Load balancer  --8000-->  Web servers  --3306-->  Database
# Each layer accepts traffic ONLY from the layer in front of it.
# ===========================================================================

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "Load balancer: HTTP from anywhere"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from the internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name        = "${var.project}-app"
  description = "Web servers: only the load balancer may connect"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "App port from the load balancer only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Servers may download code/packages and reach the database"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db"
  description = "Database: only web servers may connect"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from web servers only"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_security_group" "loadgen" {
  name        = "${var.project}-loadgen"
  description = "Locust machines"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Locust web page, from your laptop only"
    from_port   = 8089
    to_port     = 8089
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  ingress {
    description = "Workers talk to the master"
    from_port   = 5557
    to_port     = 5557
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
