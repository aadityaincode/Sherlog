"""
Transaction journey diagram: render a reconstructed timeline as a Mermaid
sequence diagram.

Deterministic string templating over the timeline the query layer already
produces — no LLM. Each known log message maps to an arrow between the
services involved; unknown lines fall back to a self-note so nothing is
silently dropped. For a broken renewal the diagram also draws the steps
that SHOULD have happened but didn't (subscription activation, confirmation
email), because that absence is the story.
"""

PARTICIPANTS = [
    ("Customer", "Customer"),
    ("API", "novastream.api"),
    ("Payments", "novastream.payments"),
    ("StrivePay", "StrivePay gateway"),
    ("Subscriptions", "novastream.subscriptions"),
    ("DB", "subscriptions-db"),
    ("Notifications", "novastream.notifications"),
]

# (substring of the message) -> (from, to, short label). Checked in order,
# first match wins. Substrings come from docs/vocabulary.md's templates.
ARROW_RULES = [
    ("Renewal request received", ("Customer", "API", "POST /subscriptions/renew")),
    ("Initiating charge", ("API", "Payments", "charge()")),
    ("Payment gateway responded APPROVED", ("Payments", "StrivePay", "APPROVED")),
    ("charge.settled", ("StrivePay", "StrivePay", "settlement recorded")),
    ("Renewing subscription", ("API", "Subscriptions", "renew()")),
    ("Subscription status set to ACTIVE", ("Subscriptions", "DB", "set ACTIVE")),
    ("Failed to acquire connection", ("Subscriptions", "DB", "acquire() -> TIMEOUT")),
    ("Renewal confirmation email queued", ("Subscriptions", "Notifications", "confirmation email")),
    ("Payment failed email queued", ("API", "Notifications", "payment-failed email")),
    ("Renewal request completed with status 200", ("API", "Customer", "200 OK")),
    ("Renewal aborted, payment declined", ("API", "Customer", "402 declined")),
    ("Unknown plan code", ("Payments", "Payments", "unknown plan code")),
    ("ConnectionPoolExhausted", ("DB", "DB", "ALERT pool exhausted")),
]

# Steps that a healthy renewal must contain; if missing from a broken
# timeline they get drawn as dashed "never happened" arrows.
EXPECTED_STEPS = [
    ("Subscription status set to ACTIVE", ("Subscriptions", "DB", "set ACTIVE")),
    ("Renewal confirmation email queued", ("Subscriptions", "Notifications", "confirmation email")),
]


def _fmt_time(ts: str) -> str:
    return ts[11:19] if len(ts) >= 19 else ts


def timeline_to_mermaid(timeline: list[dict], issue_found: bool = False) -> str:
    """Render timeline events (as returned by get_timeline_by_txn) to a
    Mermaid sequenceDiagram."""
    used = {"Customer", "API"}
    steps = []
    seen_messages = []

    for event in timeline:
        msg = event["message"]
        seen_messages.append(msg)
        for needle, (src, dst, label) in ARROW_RULES:
            if needle in msg:
                arrow = "-x" if event["level"] in ("ERROR", "ALERT") else "->>"
                steps.append(f"    {src}{arrow}{dst}: [{_fmt_time(event['timestamp'])}] {label}")
                if event["level"] in ("ERROR", "ALERT"):
                    steps.append(f"    Note over {dst}: {event['level']}")
                used.update((src, dst))
                break
        else:
            comp = event.get("component", "unknown")
            steps.append(f"    Note over API: [{_fmt_time(event['timestamp'])}] {comp}: {msg[:60]}")

    if issue_found:
        for needle, (src, dst, label) in EXPECTED_STEPS:
            if not any(needle in m for m in seen_messages):
                steps.append(f"    {src}--x{dst}: {label} (NEVER HAPPENED)")
                used.update((src, dst))
        steps.append("    Note over Customer: charged, but never activated or notified")

    lines = ["sequenceDiagram"]
    for short, full in PARTICIPANTS:
        if short in used:
            lines.append(f"    participant {short} as {full}")
    return "\n".join(lines + steps)


if __name__ == "__main__":
    # Smoke test: render the diagram for the first broken txn in the answer key.
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "investigation"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
    from client import get_client
    from search import get_timeline_by_txn

    answer_key_path = Path(__file__).resolve().parents[2] / "data" / "generated" / "answer_key.json"
    txn_id = json.loads(answer_key_path.read_text())[0]["txn_id"]

    timeline = get_timeline_by_txn(get_client(), txn_id)
    print(timeline_to_mermaid(timeline, issue_found=True))
