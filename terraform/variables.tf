# ===========================================================================
# Settings you can change. Put your own values in terraform.tfvars
# (copy terraform.tfvars.example). Anything not set there uses the default.
# ===========================================================================

# ---------------- Basics ----------------
variable "region" {
  description = "AWS region. AWS Academy Learner Lab only allows us-east-1 / us-west-2. A personal account can use ap-south-1 (Mumbai)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "result-portal"
}

variable "app_repo_url" {
  description = "PUBLIC GitHub URL of YOUR copy of this project. New servers download the website code from here."
  type        = string
}

variable "app_repo_branch" {
  description = "Git branch to deploy."
  type        = string
  default     = "main"
}

variable "admin_password" {
  description = "Password for the /admin page. Letters and digits only."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9]{8,}$", var.admin_password))
    error_message = "Use at least 8 letters/digits (no symbols)."
  }
}

# ---------------- The experiment ----------------
variable "strategy" {
  description = <<-EOT
    Which capacity strategy to test:
      fixed_minimal      - always 2 servers (today's practice)
      reactive           - 2..10 servers, CloudWatch CPU alarm adds servers
      scheduled          - servers are added BEFORE the known release time
      over_provisioned   - always 10 servers
      scheduled_reactive - scheduled + reactive as a safety net (best practice)
  EOT
  type        = string
  default     = "fixed_minimal"

  validation {
    condition     = contains(["fixed_minimal", "reactive", "scheduled", "over_provisioned", "scheduled_reactive"], var.strategy)
    error_message = "strategy must be fixed_minimal, reactive, scheduled, over_provisioned or scheduled_reactive."
  }
}

variable "min_servers" {
  description = "Servers kept running at all times (the baseline)."
  type        = number
  default     = 2
}

variable "max_servers" {
  description = "Upper limit on servers. Learner Lab accounts may cap total instances; lower this if launches fail."
  type        = number
  default     = 10
}

variable "prewarm_servers" {
  description = "How many servers scheduled scaling brings up before the release."
  type        = number
  default     = 8
}

variable "release_in_minutes" {
  description = "Scheduled strategies only: the pretend result-release time is this many minutes after you run terraform apply. Start the load test at that time."
  type        = number
  default     = 20
}

variable "prewarm_lead_minutes" {
  description = "Scheduled strategies only: how many minutes BEFORE the release the extra servers are requested."
  type        = number
  default     = 10
}

variable "prewarm_hold_minutes" {
  description = "Scheduled strategies only: how long after the release to keep the extra servers."
  type        = number
  default     = 40
}

variable "scale_out_cpu_percent" {
  description = "Reactive strategies: add servers when the average CPU is above this."
  type        = number
  default     = 60
}

variable "scale_out_step" {
  description = "Reactive strategies: how many servers to add each time the alarm fires."
  type        = number
  default     = 2
}

# ---------------- "Best version" upgrades ----------------
variable "enable_warm_pool" {
  description = "Keep pre-booted, stopped servers ready so scale-out skips most of the boot time."
  type        = bool
  default     = false
}

variable "app_cache_seconds" {
  description = "Seconds each server caches a published marksheet in memory (0 = off). Turning this on relieves the database bottleneck."
  type        = number
  default     = 0
}

# ---------------- Machine sizes ----------------
variable "instance_type" {
  description = "Size of each web server. t3.micro is cheap and makes the CPU alarm easy to trigger."
  type        = string
  default     = "t3.micro"
}

variable "db_instance_class" {
  description = "Size of the MySQL database."
  type        = string
  default     = "db.t3.micro"
}

# ---------------- Load generators (Locust) ----------------
variable "loadgen_workers" {
  description = "Number of Locust worker machines. 0 = don't create any load generators."
  type        = number
  default     = 0
}

variable "loadgen_instance_type" {
  description = "Size of each Locust machine."
  type        = string
  default     = "t3.medium"
}

variable "peak_users" {
  description = "Peak number of simulated students in the burst."
  type        = number
  default     = 5000
}

variable "my_ip_cidr" {
  description = "Your laptop's public IP followed by /32 (search 'what is my ip'). Only this IP can open the Locust control page."
  type        = string
  default     = "0.0.0.0/0"
}

# ---------------- Cost safety ----------------
variable "budget_email" {
  description = "If set, creates a monthly AWS Budget that emails you at 80% of budget_usd. Leave empty in Learner Lab."
  type        = string
  default     = ""
}

variable "budget_usd" {
  description = "Monthly budget in US dollars for the alert above."
  type        = number
  default     = 10
}
