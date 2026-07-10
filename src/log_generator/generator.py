"""Synthetic log generator for the NovaStream renewal demo (Scenario C).

Produces three deliberately different log sources that tell one story per
transaction id, plus a hidden answer key for scoring the investigation engine:

  app.log            plain-text application logs (the five novastream loggers)
  transactions.json  StrivePay gateway settlement records (JSON array)
  monitoring.log     infra metrics/alerts for subscriptions-db and the API
  answer_key.json    the planted broken transactions — never fed to the engine

Flow signatures come from docs/vocabulary.md:

  normal    1 -> 4 -> 5 -> 7 -> 8 -> 10 -> 2
  broken    1 -> 4 -> 5 -> 7 -> 9 -> 2      (8 and 10 absent, API still 200)
  declined  1 -> 4 -> 6 -> 3 -> 11          (402 noise, never hits the gateway)

Broken transactions are clustered into short incident windows during which the
subscriptions-db connection pool is exhausted; monitoring.log shows the pool
pinned at 10/10 with critical alerts over exactly those windows, so the three
sources corroborate each other. Timestamps are intentionally messy across
sources (app.log local-style with comma millis, the other two ISO-8601 UTC)
— normalizing that is the ingestion pipeline's job.
"""

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

from . import vocab

# Pool-exhaustion wait baked into the demo app (see message 9 / vocabulary doc)
POOL_WAIT_MS = 5000

# Plan codes that don't exist — used by the declined-noise flow (message 6)
UNKNOWN_PLANS = ("STUDENT_MONTHLY", "PREMIUM_MONTHLY_V2", "FAMILY_ANNUAL", "BASIC_WEEKLY")


@dataclass
class LogEvent:
    ts: datetime
    logger: str
    level: str
    message: str


@dataclass
class Transaction:
    kind: str  # "normal" | "broken" | "declined"
    txn_id: str
    user_id: str
    events: list  # list[LogEvent]
    gateway_record: dict | None  # entry for transactions.json, if the charge settled


def _new_identifiers(fake):
    return {
        "user": fake.numerify("USR-#####"),
        "txn": fake.hexify("TXN-^^^^^^^^^^^^").upper(),
        "method": fake.numerify("PM-#####"),
        "auth": fake.hexify("AUTH-^^^^^^^^").upper(),
    }


def _emit(msg_no, ts, **fields):
    logger, level, template = vocab.MESSAGES[msg_no]
    return LogEvent(ts, logger, level, template.format(smtp=vocab.SMTP_RELAY, **fields))


def _jitter_ms(rng, low, high):
    return timedelta(milliseconds=rng.randint(low, high))


def _iso_utc(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def _gateway_record(ids, plan, amount, settled_at):
    return {
        "gateway": vocab.GATEWAY_NAME,
        "event_type": "charge.settled",
        "transaction_id": ids["txn"],
        "auth_code": ids["auth"],
        "user_id": ids["user"],
        "payment_method_id": ids["method"],
        "plan_code": plan,
        "amount": amount,
        "currency": "USD",
        "status": "APPROVED",
        "settled_at": _iso_utc(settled_at),
    }


def generate_normal_transaction(fake, rng, start):
    """Renewal that works end to end: messages 1-4-5-7-8-10-2, one txn id."""
    ids = _new_identifiers(fake)
    plan = rng.choice(list(vocab.PLANS))
    amount = f"{vocab.PLANS[plan]:.2f}"

    ts = start
    events = [_emit(1, ts, plan=plan, **ids)]
    ts += _jitter_ms(rng, 5, 40)
    events.append(_emit(4, ts, amount=amount, **ids))
    ts += _jitter_ms(rng, 200, 900)  # gateway round trip
    settled_at = ts
    events.append(_emit(5, ts, amount=amount, **ids))
    ts += _jitter_ms(rng, 5, 30)
    events.append(_emit(7, ts, plan=plan, **ids))
    ts += _jitter_ms(rng, 10, 80)
    events.append(_emit(8, ts, **ids))
    ts += _jitter_ms(rng, 20, 100)
    events.append(_emit(10, ts, plan=plan, **ids))
    ts += _jitter_ms(rng, 5, 20)
    events.append(_emit(2, ts, **ids))

    record = _gateway_record(ids, plan, vocab.PLANS[plan], settled_at)
    return Transaction("normal", ids["txn"], ids["user"], events, record)


def generate_broken_transaction(fake, rng, start):
    """Scenario C: payment settles, then the subscriptions-db pool is exhausted.

    SubscriptionService.renew() swallows the DatabaseConnectionError, so
    messages 8 and 10 never appear and the API still answers 200 (message 2).
    The lone novastream.db ERROR (message 9) lands POOL_WAIT_MS after the
    renewal attempt started — the acquire() timeout.
    """
    ids = _new_identifiers(fake)
    plan = rng.choice(list(vocab.PLANS))
    amount = f"{vocab.PLANS[plan]:.2f}"

    ts = start
    events = [_emit(1, ts, plan=plan, **ids)]
    ts += _jitter_ms(rng, 5, 40)
    events.append(_emit(4, ts, amount=amount, **ids))
    ts += _jitter_ms(rng, 200, 900)
    settled_at = ts
    events.append(_emit(5, ts, amount=amount, **ids))
    ts += _jitter_ms(rng, 5, 30)
    events.append(_emit(7, ts, plan=plan, **ids))
    ts += timedelta(milliseconds=POOL_WAIT_MS) + _jitter_ms(rng, 0, 40)
    events.append(_emit(9, ts))
    ts += _jitter_ms(rng, 5, 20)
    events.append(_emit(2, ts, **ids))

    record = _gateway_record(ids, plan, vocab.PLANS[plan], settled_at)
    return Transaction("broken", ids["txn"], ids["user"], events, record)


def generate_declined_transaction(fake, rng, start):
    """Visible-failure noise: unknown plan code, charge rejected before the
    gateway is reached, API returns 402. No transactions.json record."""
    ids = _new_identifiers(fake)
    plan = rng.choice(UNKNOWN_PLANS)

    ts = start
    events = [_emit(1, ts, plan=plan, **ids)]
    ts += _jitter_ms(rng, 5, 40)
    events.append(_emit(4, ts, amount="0.00", **ids))
    ts += _jitter_ms(rng, 2, 15)
    events.append(_emit(6, ts, plan=plan, **ids))
    ts += _jitter_ms(rng, 2, 15)
    events.append(_emit(3, ts, reason=f"unknown plan code '{plan}'", **ids))
    ts += _jitter_ms(rng, 20, 100)
    events.append(_emit(11, ts, **ids))

    return Transaction("declined", ids["txn"], ids["user"], events, None)


def _incident_windows(rng, day_start, day_end, count=2, duration_s=(75, 150)):
    """Short outage windows for the subscriptions-db pool, spread over the day."""
    windows = []
    span = (day_end - day_start).total_seconds()
    slot = span / count
    for i in range(count):
        length = rng.randint(*duration_s)
        offset = rng.uniform(slot * 0.2, slot * 0.8 - length)
        start = day_start + timedelta(seconds=slot * i + offset)
        windows.append((start, start + timedelta(seconds=length)))
    return windows


def generate_monitoring_log(rng, day_start, day_end, incident_windows):
    """Metric-style infra lines: pool utilisation, API latency, health checks.

    Baseline noise every ~60s; inside incident windows the pool reads 10/10
    every ~10s and ConnectionPoolExhausted alerts fire.
    """

    def in_incident(ts):
        return any(s <= ts <= e for s, e in incident_windows)

    lines = []
    ts = day_start
    while ts < day_end:
        in_use = 10 if in_incident(ts) else rng.randint(2, 7)
        lines.append(
            f'{_iso_utc(ts)} novamon db-01 METRIC pool.connections.in_use'
            f'{{pool="{vocab.DB_POOL}"}} {in_use}/10'
        )
        p95 = rng.randint(2200, 6100) if in_incident(ts) else rng.randint(280, 620)
        lines.append(
            f'{_iso_utc(ts + timedelta(seconds=rng.randint(1, 8)))} novamon api-01 METRIC '
            f'http.request.duration_ms{{route="/api/v1/subscriptions/renew"}} p95={p95}'
        )
        lines.append(
            f'{_iso_utc(ts + timedelta(seconds=rng.randint(10, 20)))} novamon api-01 '
            f'HEALTHCHECK GET /healthz 200 OK latency_ms={rng.randint(3, 14)}'
        )
        ts += timedelta(seconds=rng.randint(55, 65))

    for w_start, w_end in incident_windows:
        ts = w_start + timedelta(seconds=rng.randint(3, 10))
        while ts < w_end:
            lines.append(
                f'{_iso_utc(ts)} novamon db-01 METRIC pool.connections.in_use'
                f'{{pool="{vocab.DB_POOL}"}} 10/10'
            )
            lines.append(
                f'{_iso_utc(ts + timedelta(seconds=rng.randint(1, 4)))} novamon db-01 ALERT '
                f'ConnectionPoolExhausted pool="{vocab.DB_POOL}" waited_ms={POOL_WAIT_MS} '
                f'severity=critical'
            )
            ts += timedelta(seconds=rng.randint(8, 15))

    lines.sort()  # ISO-8601 prefixes sort chronologically
    return lines


def generate_dataset(normal=400, broken=8, declined=20, seed=42, day="2026-07-09"):
    """Build the full interleaved dataset. Returns (transactions, monitoring_lines,
    answer_key, incident_windows)."""
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)

    day_start = datetime.fromisoformat(f"{day}T09:00:00")
    day_end = datetime.fromisoformat(f"{day}T15:00:00")
    windows = _incident_windows(rng, day_start, day_end)

    def random_start():
        return day_start + timedelta(seconds=rng.uniform(0, (day_end - day_start).total_seconds() - 30))

    transactions = []
    for _ in range(normal):
        transactions.append(generate_normal_transaction(fake, rng, random_start()))
    for _ in range(declined):
        transactions.append(generate_declined_transaction(fake, rng, random_start()))

    # Broken renewals start inside the pool-outage windows so monitoring agrees
    for i in range(broken):
        w_start, w_end = windows[i % len(windows)]
        margin = timedelta(milliseconds=POOL_WAIT_MS + 2000)
        start = w_start + timedelta(
            seconds=rng.uniform(1, max(2.0, (w_end - margin - w_start).total_seconds()))
        )
        transactions.append(generate_broken_transaction(fake, rng, start))

    answer_key = [
        {"txn_id": t.txn_id, "user_id": t.user_id, **vocab.ANSWER_KEY_STATIC}
        for t in transactions
        if t.kind == "broken"
    ]
    monitoring = generate_monitoring_log(rng, day_start, day_end, windows)
    return transactions, monitoring, answer_key, windows


def write_dataset(out_dir, transactions, monitoring_lines, answer_key):
    """Write app.log, transactions.json, monitoring.log and answer_key.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    events = sorted(
        (e for t in transactions for e in t.events),
        key=lambda e: e.ts,
    )
    with open(out / "app.log", "w", encoding="utf-8", newline="\n") as f:
        for e in events:
            stamp = e.ts.strftime("%Y-%m-%d %H:%M:%S") + f",{e.ts.microsecond // 1000:03d}"
            f.write(f"{stamp} {e.level} [{e.logger}] {e.message}\n")

    records = sorted(
        (t.gateway_record for t in transactions if t.gateway_record),
        key=lambda r: r["settled_at"],
    )
    with open(out / "transactions.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(records, f, indent=2)
        f.write("\n")

    with open(out / "monitoring.log", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(monitoring_lines) + "\n")

    with open(out / "answer_key.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(answer_key, f, indent=2)
        f.write("\n")

    return {
        "app.log lines": len(events),
        "transactions.json records": len(records),
        "monitoring.log lines": len(monitoring_lines),
        "answer_key.json entries": len(answer_key),
    }
