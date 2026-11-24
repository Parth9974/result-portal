"""
The examination result portal (the website itself).

Pages
-----
  /               login page
  /login  (POST)  check roll number + password
  /result         the logged-in student's marksheet
  /logout         sign out
  /admin          admin page: publish / unpublish results
  /health         tiny page the Load Balancer checks every few seconds

Why the app is "stateless"
--------------------------
After login we store the roll number inside Flask's *session cookie*.
That cookie lives in the student's browser and is signed with SECRET_KEY,
so it cannot be forged. Every server is given the SAME SECRET_KEY, so ANY
server can read ANY student's cookie. Nothing about the student is kept in
a server's memory, which means the Auto Scaling Group can add or remove
servers at any moment without logging anybody out.
"""
import os
import socket
import time
import urllib.request
from functools import wraps

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for,
)
from sqlalchemy import func, select
from werkzeug.security import check_password_hash

from db import GRADE_POINTS, engine, results, settings, students

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "local-dev-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "0"))   # 0 = caching off


# ---------------------------------------------------------------------------
# Which server am I?  (shown in the page footer - great for demos, because
# you can refresh and watch the Load Balancer send you to different servers)
# ---------------------------------------------------------------------------
def _ec2_metadata(path: str, token: str) -> str:
    req = urllib.request.Request(
        f"http://169.254.169.254/latest/meta-data/{path}",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urllib.request.urlopen(req, timeout=0.5) as resp:
        return resp.read().decode()


def _discover_server() -> dict:
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
        )
        with urllib.request.urlopen(token_req, timeout=0.5) as resp:
            token = resp.read().decode()
        return {
            "id": _ec2_metadata("instance-id", token),
            "zone": _ec2_metadata("placement/availability-zone", token),
        }
    except Exception:  # noqa: BLE001 - not running on EC2 (e.g. your laptop)
        return {"id": socket.gethostname(), "zone": "local machine"}


SERVER = _discover_server()


@app.context_processor
def inject_server():
    return {"server": SERVER}


# ---------------------------------------------------------------------------
# Optional in-memory cache (the "fix the database bottleneck" upgrade).
# Published results never change, so a server can remember them for a while
# instead of asking the database on every single refresh.
# ---------------------------------------------------------------------------
_cache: dict = {}


def cached(key, loader):
    if CACHE_TTL <= 0:
        return loader()
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    value = loader()
    _cache[key] = (now + CACHE_TTL, value)
    return value


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_conn():
    if "conn" not in g:
        g.conn = engine.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def get_setting(name: str, default: str = "") -> str:
    row = get_conn().execute(select(settings.c.value).where(settings.c.name == name)).first()
    return row[0] if row else default


def results_published() -> bool:
    return cached("published", lambda: get_setting("published", "0") == "1")


def load_marksheet(roll: int):
    def loader():
        conn = get_conn()
        student = conn.execute(select(students).where(students.c.roll_no == roll)).mappings().first()
        rows = conn.execute(
            select(results).where(results.c.roll_no == roll).order_by(results.c.code)
        ).mappings().all()
        return (dict(student) if student else None), [dict(r) for r in rows]
    return cached(("marksheet", roll), loader)


def sgpa(rows) -> float:
    credits = sum(r["credits"] for r in rows)
    if not credits:
        return 0.0
    points = sum(GRADE_POINTS[r["grade"]] * r["credits"] for r in rows)
    return round(points / credits, 2)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "roll" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    # Deliberately does NOT touch the database: if the database is slow under
    # load we do not want the Load Balancer to mark EVERY server as dead.
    return {"status": "ok", "server": SERVER["id"]}, 200


@app.route("/", methods=["GET"])
def login():
    if "roll" in session:
        return redirect(url_for("result"))
    return render_template("login.html", published=_safe_published())


@app.post("/login")
def login_submit():
    roll_text = request.form.get("roll_no", "").strip()
    password = request.form.get("password", "")
    if not roll_text.isdigit():
        flash("Enter your roll number using digits only, for example 1024030001.", "error")
        return redirect(url_for("login"))

    row = get_conn().execute(
        select(students.c.password_hash).where(students.c.roll_no == int(roll_text))
    ).first()
    if not row or not check_password_hash(row[0], password):
        flash("That roll number and password don't match. Check both and try again.", "error")
        return redirect(url_for("login"))

    session.clear()
    session["roll"] = int(roll_text)
    return redirect(url_for("result"))


@app.get("/result")
@login_required
def result():
    student, rows = load_marksheet(session["roll"])
    if student is None:
        session.clear()
        return redirect(url_for("login"))
    if not results_published():
        return render_template("waiting.html", student=student)

    total_marks = sum(r["marks"] for r in rows)
    return render_template(
        "result.html",
        student=student,
        rows=rows,
        sgpa=sgpa(rows),
        total_marks=total_marks,
        max_marks=100 * len(rows),
        total_credits=sum(r["credits"] for r in rows),
        passed=all(r["grade"] != "F" for r in rows),
        term=get_setting("term", ""),
    )


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("That admin password is wrong.", "error")
    return render_template("admin_login.html")


@app.get("/admin")
@admin_required
def admin():
    conn = get_conn()
    stats = {
        "students": conn.execute(select(func.count()).select_from(students)).scalar(),
        "records": conn.execute(select(func.count()).select_from(results)).scalar(),
        "published": get_setting("published", "0") == "1",
        "published_at": get_setting("published_at", ""),
        "term": get_setting("term", ""),
    }
    return render_template("admin.html", stats=stats)


@app.post("/admin/publish")
@admin_required
def admin_publish():
    publish = request.form.get("action") == "publish"
    conn = get_conn()
    conn.execute(settings.update().where(settings.c.name == "published").values(value="1" if publish else "0"))
    stamp = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime()) if publish else ""
    updated = conn.execute(settings.update().where(settings.c.name == "published_at").values(value=stamp))
    if updated.rowcount == 0:
        conn.execute(settings.insert().values(name="published_at", value=stamp))
    conn.commit()
    _cache.clear()
    flash("Results published. Students can see their marks now." if publish
          else "Results hidden. Students will see the waiting page.", "ok")
    return redirect(url_for("admin"))


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404,
                           title="This page doesn't exist",
                           message="Check the address, or go back to the sign-in page."), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           title="The server couldn't finish your request",
                           message="Wait a few seconds and try once. Refreshing repeatedly makes the queue longer."), 500


def _safe_published():
    try:
        return results_published()
    except Exception:  # noqa: BLE001 - database not ready yet
        return False


if __name__ == "__main__":
    # Local development only. On AWS the app is started by gunicorn.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
