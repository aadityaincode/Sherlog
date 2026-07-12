"""
Format-specific parsers that turn each raw log source into a list of
normalized LogEntry objects sharing the same schema (schema.py).

Verified against the actual sample files:
  - app.log:         2,948 lines, 0 unmatched by APP_LOG_RE
  - monitoring.log:   1,108 lines, 0 unmatched by MON_RE
  - transactions.json: 408 records, valid JSON array

Notes on correlation keys (important for the search layer):
  - app.log and transactions.json both carry an explicit txn_id/user_id,
    so they correlate by exact key match.
  - monitoring.log (infra metrics/health checks/alerts) has NO txn_id or
    user_id — that's realistic; a DB connection pool alert doesn't know
    which transaction it affected. Those events correlate by *time window*
    instead, which is handled in search.py, not here.
"""
import re
import json
from datetime import datetime, timezone
from typing import List

from schema import LogEntry

# ---------------------------------------------------------------------------
# app.log — e.g.
# 2026-07-09 09:39:08,710 INFO [novastream.api] Renewal request received [user: USR-95005, plan: PREMIUM_MONTHLY, txn: TXN-0F14D1C5530B]
# 2026-07-09 09:39:14,226 ERROR [novastream.db] Failed to acquire connection from pool 'subscriptions-db' after 5000ms: pool exhausted (10/10 connections in use)
# ---------------------------------------------------------------------------
APP_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>INFO|WARN|ERROR) "
    r"\[(?P<logger>novastream\.\w+)\] "
    r"(?P<message>.*)$"
)
USER_RE = re.compile(r"user:\s*(USR-\w+)")
TXN_RE = re.compile(r"txn:\s*(TXN-\w+)")


def parse_app_log(path: str) -> List[LogEntry]:
    entries: List[LogEntry] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue

            m = APP_LOG_RE.match(line)
            if not m:
                # Never silently drop a line we can't parse — production logs
                # will always have some lines that don't fit the pattern
                # (multi-line stack traces, format drift, etc). Keep it as
                # evidence rather than losing it.
                entries.append(LogEntry(
                    timestamp=datetime.now(timezone.utc),
                    source="app",
                    level="UNKNOWN",
                    component="novastream.unknown",
                    message=line,
                    raw={"raw_line": line},
                ))
                continue

            ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
            user_match = USER_RE.search(m["message"])
            txn_match = TXN_RE.search(m["message"])

            entries.append(LogEntry(
                timestamp=ts,
                source="app",
                level=m["level"],
                component=m["logger"],
                message=m["message"],
                user_id=user_match.group(1) if user_match else None,
                txn_id=txn_match.group(1) if txn_match else None,
                raw={"raw_line": line},
            ))
    return entries


# ---------------------------------------------------------------------------
# monitoring.log — e.g.
# 2026-07-09T09:00:00.000Z novamon db-01 METRIC pool.connections.in_use{pool="subscriptions-db"} 4/10
# 2026-07-09T09:00:12.000Z novamon api-01 HEALTHCHECK GET /healthz 200 OK latency_ms=8
# 2026-07-09T09:38:47.843Z novamon db-01 ALERT ConnectionPoolExhausted pool="subscriptions-db" waited_ms=5000 severity=critical
# ---------------------------------------------------------------------------
MON_RE = re.compile(
    r"^(?P<ts>\S+) novamon (?P<host>\S+) (?P<event_type>METRIC|HEALTHCHECK|ALERT) (?P<rest>.*)$"
)


def parse_monitoring_log(path: str) -> List[LogEntry]:
    entries: List[LogEntry] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue

            m = MON_RE.match(line)
            if not m:
                entries.append(LogEntry(
                    timestamp=datetime.now(timezone.utc),
                    source="monitoring",
                    level="UNKNOWN",
                    component="novamon.unknown",
                    message=line,
                    raw={"raw_line": line},
                ))
                continue

            ts = datetime.strptime(m["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            entries.append(LogEntry(
                timestamp=ts,
                source="monitoring",
                level=m["event_type"],       # METRIC / HEALTHCHECK / ALERT
                component=m["host"],         # api-01 / db-01
                message=m["rest"],
                user_id=None,                # not present at this layer
                txn_id=None,                 # correlated by time window, see search.py
                raw={"raw_line": line},
            ))
    return entries


# ---------------------------------------------------------------------------
# transactions.json — array of gateway settlement records
# ---------------------------------------------------------------------------
def parse_transactions(path: str) -> List[LogEntry]:
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    entries: List[LogEntry] = []
    for r in records:
        ts = datetime.strptime(r["settled_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        message = (
            f"{r['gateway']} {r['event_type']}: {r['status']} "
            f"{r['amount']} {r['currency']} (plan {r['plan_code']})"
        )
        entries.append(LogEntry(
            timestamp=ts,
            source="transaction",
            level="PAYMENT_EVENT",
            component=r.get("gateway", "unknown_gateway"),
            message=message,
            user_id=r.get("user_id"),
            txn_id=r.get("transaction_id"),
            raw=r,
        ))
    return entries