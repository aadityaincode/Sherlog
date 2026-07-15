"""
End-to-end pipeline: customer complaint (or txn_id) in, RCA report + journey
diagram out.

    python src/pipeline.py "Customer USR-02406 says they were charged but
                            their subscription still shows expired"

Stages: investigate (LLM agent over the log store) -> correlate (GitPython,
failure_point -> file:line) -> report (LLM writeup) + diagram (Mermaid).
Writes reports/rca_<txn>.md and prints it.
"""
import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
for sub in ("investigation", "ingestion", "correlation", "reporting"):
    sys.path.insert(0, str(SRC / sub))

from client import get_client
from correlator import correlate
from diagram import timeline_to_mermaid
from engine import investigate
from rca import generate_rca, render_markdown
from search import get_timeline_by_txn

REPORTS_DIR = SRC.parent / "reports"

TXN_RE = re.compile(r"TXN-[0-9A-F]{12}")


def _txn_from_evidence(evidence: list[str]) -> str | None:
    for line in evidence:
        m = TXN_RE.search(line)
        if m:
            return m.group(0)
    return None


def run(complaint: str) -> Path | None:
    print(f"complaint: {complaint}\n")

    print("[1/4] investigating...")
    try:
        investigation = investigate(complaint)
    except RuntimeError as e:
        print(f"\nInvestigation failed: {e}")
        print("If the complaint references a user/txn id, check it exists in the")
        print("currently ingested dataset (data/generated/answer_key.json).")
        sys.exit(2)

    if not investigation.get("issue_found"):
        print("\nInvestigation concluded: no failure found for this complaint.")
        print("Evidence reviewed:")
        for line in investigation.get("evidence", []):
            print(f"  {line}")
        return None

    print("\n[2/4] correlating to source code...")
    correlation = correlate(investigation["failure_point"])
    loc = f"{correlation['file']}:{correlation['line']}" if correlation else "not found"
    print(f"  -> {loc}")

    print("[3/4] generating RCA report...")
    report = generate_rca(investigation, correlation, verbose=False)

    print("[4/4] rendering journey diagram...")
    txn_id = _txn_from_evidence(investigation.get("evidence", []))
    mermaid = None
    if txn_id:
        timeline = get_timeline_by_txn(get_client(), txn_id)
        mermaid = timeline_to_mermaid(timeline, issue_found=True)

    markdown = render_markdown(report)
    if mermaid:
        markdown += "\n\n## Transaction journey\n\n```mermaid\n" + mermaid + "\n```\n"

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"rca_{txn_id or 'unknown'}.md"
    out_path.write_text(markdown, encoding="utf-8")
    (REPORTS_DIR / f"rca_{txn_id or 'unknown'}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(f"\nreport written to {out_path}\n")
    print("=" * 72)
    print(markdown)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python src/pipeline.py "<complaint or txn_id>"')
        sys.exit(1)
    run(" ".join(sys.argv[1:]))
