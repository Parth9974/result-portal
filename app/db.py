"""
Database layer.

We use SQLAlchemy so the SAME code works with:
  * SQLite  - a single file on your laptop (zero setup, for local testing)
  * MySQL   - Amazon RDS in the cloud

Which one is used depends on the DATABASE_URL environment variable.
"""
import os

from sqlalchemy import (
    BigInteger, Column, Integer, MetaData, String, Table, create_engine,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///local.db")

# A small connection pool per web-server process. Opening a brand-new
# database connection for every request is slow, so we keep a few open
# and reuse them. pool_pre_ping re-checks a connection before using it.
_engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("mysql"):
    _engine_kwargs.update(pool_size=5, max_overflow=5, pool_recycle=280, pool_timeout=10)

engine = create_engine(DATABASE_URL, **_engine_kwargs)
metadata = MetaData()

students = Table(
    "students", metadata,
    Column("roll_no", BigInteger, primary_key=True, autoincrement=False),
    Column("name", String(80), nullable=False),
    Column("branch", String(60), nullable=False),
    Column("semester", Integer, nullable=False),
    Column("password_hash", String(200), nullable=False),
)

results = Table(
    "results", metadata,
    Column("roll_no", BigInteger, primary_key=True, autoincrement=False),
    Column("code", String(10), primary_key=True),
    Column("subject", String(80), nullable=False),
    Column("credits", Integer, nullable=False),
    Column("marks", Integer, nullable=False),
    Column("grade", String(3), nullable=False),
)

# Single-row-per-key table that holds whether results are published.
settings = Table(
    "settings", metadata,
    Column("name", String(40), primary_key=True),
    Column("value", String(80), nullable=False),
)

# Grade scale used by the portal (fictional, 10-point scale).
GRADE_POINTS = {"A+": 10, "A": 9, "B+": 8, "B": 7, "C+": 6, "C": 5, "D": 4, "F": 0}


def grade_for(marks: int) -> str:
    for cutoff, grade in ((90, "A+"), (80, "A"), (70, "B+"), (60, "B"),
                          (50, "C+"), (45, "C"), (40, "D")):
        if marks >= cutoff:
            return grade
    return "F"
