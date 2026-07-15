"""
Score the investigation engine three ways: does it correctly find the broken
transactions in the answer key (recall), does it correctly leave clean
transactions alone instead of hallucinating a failure (precision), and is
every evidence line it cites a real log event rather than a paraphrase or
invention (grounding). Testing only the first would hide false positives
and fabricated citations entirely.
"""
import json
import re
import sys
from pathlib import Path

from engine import investigate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from parsers import parse_app_log, parse_monitoring_log, parse_transactions

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"
ANSWER_KEY_PATH = DATA_DIR / "answer_key.json"

# Everything below is derived from whatever dataset is currently on disk, so
# regenerating with a different --seed can't silently break the suite (it
# did once: hardcoded seed-99 ids all went stale after a default-seed rerun).

_RECEIVED_RE = re.compile(
    r"^(?P<date>\S+) (?P<time>\S+) INFO \[novastream\.api\] Renewal request received "
    r"\[user: (?P<user>USR-\d+), plan: (?P<plan>\w+), txn: (?P<txn>TXN-[0-9A-F]+)\]"
)


def _dataset_cases() -> tuple[list[str], list[dict]]:
    """Pick negative txn ids (one normal, one declined) and build complaint
    cases (one with a user id, one with only plan/amount/time) from the
    current app.log + answer key."""
    answer_key = json.loads(ANSWER_KEY_PATH.read_text())
    broken = {e["txn_id"]: e for e in answer_key}

    app_log = (DATA_DIR / "app.log").read_text().splitlines()
    received = {}  # txn -> match
    activated, declined_txns = set(), set()
    amounts = {}  # txn -> amount

    for line in app_log:
        m = _RECEIVED_RE.match(line)
        if m:
            received[m["txn"]] = m
        if "Subscription status set to ACTIVE" in line:
            t = re.search(r"txn: (TXN-[0-9A-F]+)", line)
            if t:
                activated.add(t.group(1))
        if "Unknown plan code" in line:
            t = re.search(r"txn: (TXN-[0-9A-F]+)", line)
            if t:
                declined_txns.add(t.group(1))
        a = re.search(r"APPROVED \[txn: (TXN-[0-9A-F]+), auth: \S+, amount: \$(\S+)\]", line)
        if a:
            amounts[a.group(1)] = a.group(2)

    normal_id = next(t for t in received if t in activated and t not in broken)
    declined_id = next(iter(declined_txns))

    first, second = answer_key[0], answer_key[1 % len(answer_key)]
    m2 = received[second["txn_id"]]

    # Broken renewals cluster into incident windows, so several broken txns
    # can genuinely match one plan/amount/time-of-day description. Any of
    # them is a correct answer to the ambiguous complaint; demanding one
    # specific txn failed a run where the engine picked an equally valid
    # sibling from the same window.
    def _minutes(match) -> int:
        h, m = match["time"].split(":")[:2]
        return int(h) * 60 + int(m)

    ambiguous_matches = {
        txn
        for txn in broken
        if received[txn]["plan"] == m2["plan"]
        and amounts.get(txn) == amounts[m2["txn"]]
        and abs(_minutes(received[txn]) - _minutes(m2)) <= 2
    }

    complaints = [
        {
            "complaint": (
                f"Customer {first['user_id']} called in: they renewed their "
                "subscription, the card was charged, but the account still "
                "shows expired and no confirmation email ever arrived."
            ),
            "expected_txns": {first["txn_id"]},
        },
        {
            "complaint": (
                f"A customer on the {m2['plan']} plan says they paid "
                f"${amounts[m2['txn']]} around {m2['time'][:5]}, the money left "
                "their account, but their subscription was never reactivated "
                "and they got no email."
            ),
            "expected_txns": ambiguous_matches,
        },
    ]
    return [normal_id, declined_id], complaints


NEGATIVE_TEST_IDS, COMPLAINT_CASES = _dataset_cases()


def load_message_corpus() -> list[str]:
    """Every real log message in the dataset, via the same parsers the
    pipeline uses. A cited evidence line is grounded iff it contains one
    of these messages verbatim."""
    entries = (
        parse_app_log(DATA_DIR / "app.log")
        + parse_monitoring_log(DATA_DIR / "monitoring.log")
        + parse_transactions(DATA_DIR / "transactions.json")
    )
    return [e.message for e in entries]


_CORPUS: list[str] | None = None


def verify_evidence(evidence: list[str]) -> list[str]:
    """Return the cited lines that do NOT match any real log message."""
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = load_message_corpus()
    return [line for line in evidence if not any(msg in line for msg in _CORPUS)]


def score_positive(expected: dict) -> dict:
    txn_id = expected["txn_id"]
    try:
        result = investigate(f"Investigate transaction {txn_id}.", verbose=False)
    except Exception as e:
        return {"txn_id": txn_id, "ok": False, "reason": f"engine error: {e}"}

    issue_found_ok = result.get("issue_found") is True
    failure_point_ok = result.get("failure_point") == expected["failure_point"]
    error_ok = expected["error"] in (result.get("error_message") or "")
    evidence = result.get("evidence", [])
    ungrounded = verify_evidence(evidence)
    evidence_ok = len(evidence) > 0 and not ungrounded

    ok = issue_found_ok and failure_point_ok and error_ok and evidence_ok
    return {
        "txn_id": txn_id,
        "ok": ok,
        "issue_found_ok": issue_found_ok,
        "failure_point_ok": failure_point_ok,
        "error_ok": error_ok,
        "evidence_ok": evidence_ok,
        "ungrounded_evidence": ungrounded,
        "got": result,
    }


def score_negative(txn_id: str) -> dict:
    try:
        result = investigate(f"Investigate transaction {txn_id}.", verbose=False)
    except Exception as e:
        return {"txn_id": txn_id, "ok": False, "reason": f"engine error: {e}"}

    ungrounded = verify_evidence(result.get("evidence", []))
    ok = result.get("issue_found") is False and not ungrounded
    return {"txn_id": txn_id, "ok": ok, "ungrounded_evidence": ungrounded, "got": result}


def score_complaint(case: dict) -> dict:
    label = "/".join(sorted(case["expected_txns"]))
    try:
        result = investigate(case["complaint"], verbose=False)
    except Exception as e:
        return {"txn_id": label, "ok": False, "reason": f"engine error: {e}"}

    evidence = result.get("evidence", [])
    ungrounded = verify_evidence(evidence)
    issue_found_ok = result.get("issue_found") is True
    # Proof it traced the complaint to a right transaction: one of the
    # acceptable txn_ids must show up in the cited evidence.
    right_txn = any(txn in line for txn in case["expected_txns"] for line in evidence)
    failure_point_ok = result.get("failure_point") == "SubscriptionService.renew"

    ok = issue_found_ok and right_txn and failure_point_ok and not ungrounded
    return {
        "txn_id": label,
        "ok": ok,
        "issue_found_ok": issue_found_ok,
        "right_txn": right_txn,
        "failure_point_ok": failure_point_ok,
        "ungrounded_evidence": ungrounded,
        "got": result,
    }


if __name__ == "__main__":
    answer_key = json.loads(ANSWER_KEY_PATH.read_text())

    print("--- positive cases (should find the bug) ---")
    positive_results = []
    for i, expected in enumerate(answer_key, 1):
        print(f"[{i}/{len(answer_key)}] investigating {expected['txn_id']}...")
        positive_results.append(score_positive(expected))

    print("\n--- negative cases (should NOT find a bug) ---")
    negative_results = []
    for i, txn_id in enumerate(NEGATIVE_TEST_IDS, 1):
        print(f"[{i}/{len(NEGATIVE_TEST_IDS)}] investigating {txn_id}...")
        negative_results.append(score_negative(txn_id))

    print("\n--- complaint cases (free text, no txn_id given) ---")
    complaint_results = []
    for i, case in enumerate(COMPLAINT_CASES, 1):
        print(f"[{i}/{len(COMPLAINT_CASES)}] investigating complaint -> {'/'.join(sorted(case['expected_txns']))}...")
        complaint_results.append(score_complaint(case))

    pos_passed = sum(r["ok"] for r in positive_results)
    neg_passed = sum(r["ok"] for r in negative_results)
    com_passed = sum(r["ok"] for r in complaint_results)
    print(
        f"\n--- positive: {pos_passed}/{len(positive_results)}"
        f" | negative: {neg_passed}/{len(negative_results)}"
        f" | complaint: {com_passed}/{len(complaint_results)} ---"
    )

    for r in positive_results + negative_results + complaint_results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"{status}  {r['txn_id']}")
        if not r["ok"]:
            print(f"      {r}")
