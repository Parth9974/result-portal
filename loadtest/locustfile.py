"""
Locust load test: thousands of fake students checking their result at once.

Each simulated student:
  1. opens the sign-in page
  2. signs in with a random roll number (password = roll number)
  3. views their result, then keeps refreshing it (the "refresh storm")

Load shapes (pick one on the Locust page, thanks to --class-picker):
  ResultDayBurst - the real experiment. Near-zero traffic, then PEAK_USERS
                   students arrive within 2 minutes, stay 15 minutes, leave.
  SmokeTest      - 50 users for 3 minutes. Run this first to check things work.

Run locally against your laptop:
    locust -f locustfile.py --host http://127.0.0.1:5000
then open http://localhost:8089
"""
import os
import random

from locust import HttpUser, LoadTestShape, between, task

FIRST_ROLL = 1024030001
STUDENTS = int(os.environ.get("STUDENT_COUNT", "5000"))
PEAK_USERS = int(os.environ.get("PEAK_USERS", "5000"))


class Student(HttpUser):
    # Real students wait 1-3 s between actions (reading, impatiently refreshing).
    wait_time = between(1, 3)

    def on_start(self):
        self.client.get("/", name="sign-in page")
        self.client.get("/static/style.css", name="stylesheet")
        roll = str(FIRST_ROLL + random.randrange(STUDENTS))
        with self.client.post(
            "/login",
            data={"roll_no": roll, "password": roll},
            name="sign in",
            allow_redirects=True,
            catch_response=True,
        ) as resp:
            # A successful sign-in ends on /result; a failed one bounces to "/".
            if resp.status_code == 200 and resp.url.rstrip("/").endswith("/result"):
                resp.success()
            else:
                resp.failure(f"sign-in failed (HTTP {resp.status_code})")

    @task(8)
    def view_result(self):
        self.client.get("/result", name="view result")

    @task(1)
    def refresh_storm(self):
        # An impatient student hammering refresh three times in a row.
        for _ in range(3):
            self.client.get("/result", name="view result (refresh)")


class ResultDayBurst(LoadTestShape):
    """
    Time (min)   0-1    1-3            3-18       18-20       20
    Users        2%     ramp to 100%   100%       back to 2%  stop
    """
    def tick(self):
        t = self.get_run_time()
        quiet = max(10, PEAK_USERS // 50)
        if t < 60:
            return quiet, quiet
        if t < 180:                                    # the burst: 2-minute ramp
            return PEAK_USERS, max(1, PEAK_USERS // 100)
        if t < 1080:                                   # everybody is on the site
            return PEAK_USERS, max(1, PEAK_USERS // 100)
        if t < 1200:                                   # students leave
            return quiet, max(1, PEAK_USERS // 50)
        return None


class SmokeTest(LoadTestShape):
    def tick(self):
        return (50, 10) if self.get_run_time() < 180 else None
