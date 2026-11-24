# How the project works, in plain words

This guide explains every part of the project so you can describe it
confidently in your report and viva. No prior web or cloud knowledge assumed.

---

## 1. The problem in one minute

A result website is almost empty all semester. Then results are announced
and about 5,000 students open it within the same few minutes. The site slows
down, students keep pressing refresh, and that makes it slower still.

Colleges usually pick one of two bad options:

- **A few servers all the time.** Cheap, but the site falls over on result day.
- **Many servers all the time.** Never falls over, but you pay for machines that sit idle more than 99% of the semester.

The cloud lets you rent servers by the hour and add or remove them
automatically. **This project measures whether that actually rescues result
day, and which way of adding servers works best.**

---

## 2. The building blocks

```
                 Students (5,000 simulated by Locust)
                               |
                               v   http://<load balancer address>
                 +---------------------------+
                 | Application Load Balancer |  spreads students across servers,
                 +---------------------------+  checks each one at /health every 10 s
                     |          |          |
           +---------+----------+----------+------- Auto Scaling Group (2 to 10)
           |  web server   web server   web server  ...   in 2 availability zones
           +---------+----------+----------+-------
                               |
                               v
                     +--------------------+
                     |  RDS MySQL database|  one shared copy of all marks
                     +--------------------+

   CloudWatch watches CPU on every server  -->  alarm  -->  tells the Auto Scaling Group to add servers
```

| Part | What it is | Everyday comparison |
|---|---|---|
| **EC2 instance** | One rented virtual computer running the website | One counter at the canteen |
| **Auto Scaling Group** | Keeps between a minimum and maximum number of servers running, replaces broken ones, adds or removes servers when told to | The manager who opens or closes counters |
| **Application Load Balancer** | The one public address. Sends each request to a healthy server | The person at the door pointing you to the shortest queue |
| **Health check** | The load balancer requests `/health` on each server every 10 seconds; 2 good answers = healthy, 3 bad = removed | Checking each counter is actually staffed |
| **RDS (MySQL)** | A database that Amazon runs and maintains for you | The single register every counter has to look things up in |
| **CloudWatch** | Collects measurements (CPU, response time, errors) and raises alarms | CCTV plus an alarm bell |
| **VPC and subnets** | Your private network inside AWS. Public subnets can reach the internet; private ones can't | A fenced campus with an inner, locked building |
| **Security groups** | Firewalls on each layer | Guards who only let in people from the previous room |
| **Terraform** | Code that builds all of the above in one command and deletes it in one command | A blueprint that builds and demolishes the building |
| **Locust** | A tool that pretends to be thousands of students | 5,000 volunteers pressing refresh |

---

## 3. What happens when a student checks their result

1. The browser sends **"sign me in as 1024030001"** to the load balancer.
2. The load balancer picks a healthy server and forwards the request.
3. That server checks the password against the database and replies with a
   **signed cookie** that says "this browser belongs to 1024030001".
4. The browser asks for `/result`, carrying the cookie. The load balancer may
   send this to a **different** server, and that's fine: every server shares
   the same secret key, so any of them can read the cookie.
5. The server fetches the marks from the database and sends back the marksheet.

The footer of every page shows which server answered. Refresh a few times and
it changes: that's the load balancer at work.

---

## 4. Design decisions worth explaining

**The app is stateless.** No server remembers who is logged in; that
information lives in the student's signed cookie. So servers can be added
or removed at any moment without logging anyone out. Without this,
auto-scaling would break sessions.

**The health check does not touch the database.** If `/health` queried the
database and the database got slow under load, the load balancer would mark
*every* server unhealthy at once and take the whole site down. A shallow
check only asks "is this server's web process alive?"

**Step scaling with an explicit CloudWatch alarm.** AWS also offers *target
tracking*, which hides the alarm inside. We use explicit alarms so the exact
moment the alarm fired is recorded, which lets us measure the three parts of
scale-out time separately.

**Detailed (1-minute) monitoring.** By default EC2 reports CPU every 5
minutes, which would make the reactive strategy look five times slower than
necessary. We give it the fairest possible chance.

**Unlimited CPU credits.** Small `t3` servers normally slow down when they use
up their CPU "credits". That would quietly distort results, so it's turned off.

**Servers sit in public subnets.** To download code they need internet
access. The usual alternative (private subnets plus a NAT Gateway) costs
money every hour. The firewall still only accepts traffic from the load
balancer, so they are not exposed.

**Fake data only.** 5,000 students with random names and marks, created by
`seed.py`. The same random "seed" is used every time, so every experiment
starts from identical data.

**Load comes from outside.** Locust runs on separate machines that are not
part of the Auto Scaling Group. Otherwise the test machines would compete
with the servers being measured.

---

## 5. The five strategies

Every strategy faces the identical 20-minute burst.

| Strategy | Servers | Idea |
|---|---|---|
| `fixed_minimal` | always 2 | Today's practice: cheap, sized for a normal day |
| `reactive` | 2 → up to 10 | When average CPU passes 60% for a minute, add 2 servers |
| `scheduled` | 2, then 8 from 10 min before release | The release time is known, so prepare in advance |
| `over_provisioned` | always 10 | Sized for the peak, all semester |
| `scheduled_reactive` | scheduled + reactive | Plan for the expected peak, keep the alarm as a safety net |

### Why reactive scaling is expected to be too slow

When the burst hits, a new server needs three things to happen, one after another:

1. **Alarm evaluation.** CloudWatch collects a full minute of CPU data, then
   the alarm decides. About 1–2 minutes.
2. **Instance launch.** AWS starts a virtual machine. Under a minute.
3. **App start-up and health checks.** The new machine installs Python and the
   app, starts it, and must pass 2 health checks. About 1–3 minutes.

Total: roughly **3–6 minutes**. All 5,000 students arrive within **2
minutes**. So the help arrives after the damage is done. Your
`scaleout_breakdown.png` chart shows this directly.

### Why the database matters

Every server reads from the same single database. Adding web servers spreads
the work of *building pages*, but every page still needs the database. Watch
`database_cpu.png`: as servers are added, database CPU climbs. At some point
more web servers stop helping. **Elasticity at one tier doesn't make the whole
system elastic.**

The cache upgrade (`app_cache_seconds`) is the fix: published marks never
change, so each server keeps a copy in memory for a minute and the database
is asked far less often.

---

## 6. What is measured

| Measure | Meaning | Where it comes from |
|---|---|---|
| **p95 response time** | 95% of requests were faster than this. More honest than the average, because it captures the slow experiences | Locust |
| **Error rate** | Share of requests that failed (timeouts, 502/503/504) | Locust |
| **SLO** | Pass = p95 under 500 ms **and** errors under 1%. Declared before running anything, so results can't be cherry-picked | Your proposal |
| **Healthy servers over time** | How many servers the load balancer was actually using | AWS API, every 5 s |
| **Scale-out time, in three parts** | Alarm evaluation, instance launch, app start-up + health checks | CloudWatch alarm history + timestamps of each new server |
| **Database CPU and connections** | Evidence for the bottleneck | CloudWatch (RDS) |
| **Semester cost** | Server-hours × price, scaled to a 20-week semester with 4 result releases | `analyze.py` |

---

## Reading your results

After `python analyze.py`, open `results/report/`:

- **`latency_over_time.png`**: one line per strategy, with the red 500 ms SLO
  line. Expect fixed-minimal to shoot up and stay up, reactive to spike and
  only recover minutes later, and scheduled/over-provisioned to stay low.
- **`errors_over_time.png`**: the same story told through failures.
- **`servers_over_time.png`**: top panel is students online; bottom is
  healthy servers. The gap between the burst (top) and reactive servers
  arriving (bottom) is the core finding.
- **`scaleout_breakdown.png`**: the three delays stacked, next to a red line
  showing when all students had already arrived.
- **`database_cpu.png`**: the bottleneck.
- **`semester_cost.png`**: cost per strategy, marked "meets SLO" or "misses SLO".
- **`summary.md`**: every number in a table you can paste into the report.

Write your conclusion from **your measured numbers**, not from the
expectations above. If a result surprises you (for example, reactive scaling
passes because your burst was gentler than expected), that is still a valid
finding; explain why it happened.

---

## 7. Viva questions and short answers

**What is cloud computing?**
Renting computing resources (servers, storage, databases) over the internet
and paying only for what you use, instead of buying hardware.

**What is elasticity?**
The ability to add or remove capacity automatically as demand changes.

**What does an Auto Scaling Group do?**
Keeps the number of servers between a minimum and a maximum, replaces any
server that fails its health check, and changes the count when a scaling
policy or schedule tells it to.

**Why do you need a load balancer?**
To give students one address, spread requests across servers, and stop
sending traffic to a server that fails its health check.

**Why two availability zones?**
They are physically separate data centres. If one has a problem, servers in
the other keep the site running.

**Why must the app be stateless?**
So any server can handle any request. Then servers can be added or removed
without logging users out.

**Why is reactive scaling slow?**
It only acts after load has already risen, and then needs alarm evaluation,
a machine launch, and app start-up plus health checks, which together take
minutes. The burst peaks in about two.

**Why does scheduled scaling work here?**
Result release times are announced in advance, so capacity can be ready
before demand arrives.

**Isn't over-provisioning simplest?**
It works, but you pay for 10 servers for the whole semester to survive a few
hours of peak. Compare the costs in `semester_cost.png`.

**What is p95 and why not the average?**
95% of requests were faster than the p95 value. An average hides the slow
experiences that make students refresh repeatedly.

**What is the bottleneck, and how would you fix it?**
The single database. Fixes: cache published results (implemented as an
upgrade), add read replicas, or pre-generate result pages.

**Why Terraform instead of clicking in the console?**
The setup is rebuilt many times; Terraform makes it identical every time,
which keeps the experiments fair. It also deletes everything cleanly, which
controls cost.

**Why Locust?**
It simulates thousands of realistic users with a repeatable traffic shape,
which you can't do with real people.

**How did you keep costs and risk down?**
Billing alert / Learner Lab credits, `terraform destroy` after every session,
small instance sizes, no NAT Gateway, and synthetic data only.

**Is any real student data used?**
No. Everything is generated by `seed.py`, and no test touches college systems.

**What would you do with more time?**
Bake the app into a custom machine image (AMI) to cut start-up time,
predictive scaling, HTTPS with a domain, read replicas, and testing a larger
peak.
