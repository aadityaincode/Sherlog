"""
Score the investigation engine three ways: does it correctly find the broken
transactions in the answer key (recall), does it correctly leave clean
transactions alone instead of hallucinating a failure (precision), and is
every evidence line it cites a real log event rather than a paraphrase or
invention (grounding). Testing only the first would hide false positives
and fabricated citations entirely.
"""
import json
import sys
from pathlib import Path

from engine import investigate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from parsers import parse_app_log, parse_monitoring_log, parse_transactions

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"
ANSWER_KEY_PATH = DATA_DIR / "answer_key.json"

# One known-normal and one known-declined txn_id from data/generated/app.log,
# spot-checked by hand. Neither is a bug, so issue_found must come back false.
NEGATIVE_TEST_IDS = ["TXN-EFD3B21E499C", "TXN-802F03FAC985"]

# Free-text complaints with no txn_id, phrased like a real support ticket.
# Both describe broken transactions from the answer key; the engine has to
# work backwards (user id or plan/amount details) to the right txn on its own.
COMPLAINT_CASES = [
    {
        "complaint": (
            "Customer USR-02406 called in: they renewed their subscription "
            "this afternoon, the card was charged, but the account still "
            "shows expired and no confirmation email ever arrived."
        ),
        "expected_txn": "TXN-CF6BDA433D27",
    },
    {
        "complaint": (
            "A customer on the BASIC_MONTHLY plan says they paid $9.99 "
            "around 10:18 this morning, the money left their account, but "
            "their subscription was never reactivated and they got no email."
        ),
        "expected_txn": "TXN-635C29787581",
    },
]


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
    label = case["expected_txn"]
    try:
        result = investigate(case["complaint"], verbose=False)
    except Exception as e:
        return {"txn_id": label, "ok": False, "reason": f"engine error: {e}"}

    evidence = result.get("evidence", [])
    ungrounded = verify_evidence(evidence)
    issue_found_ok = result.get("issue_found") is True
    # Proof it traced the complaint to the right transaction: the expected
    # txn_id must show up in the cited evidence.
    right_txn = any(case["expected_txn"] in line for line in evidence)
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
        print(f"[{i}/{len(COMPLAINT_CASES)}] investigating complaint -> {case['expected_txn']}...")
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
