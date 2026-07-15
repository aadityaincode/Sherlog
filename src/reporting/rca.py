"""
RCA report generator: combine the investigation engine's findings with the
code correlator's file:line evidence into the final structured report.

Single non-agentic LLM call. The investigation and correlation stages have
already pinned down the facts; this stage only writes them up and proposes
fixes, with every claim anchored to evidence produced upstream (log lines
from the engine, file:line from the correlator), never invented here.
"""
import json
import sys
from pathlib import Path

from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "investigation"))
from engine import MODEL, _generate_with_retry, get_llm_client

REPORT_PROMPT = """You are writing the final root-cause-analysis report for a production incident at NovaStream, a subscription billing service.

You are given two verified inputs, produced by an automated investigation:
1. The investigation result: failure point, error, and the exact log lines as evidence.
2. The code correlation: the file, line, and snippet of the responsible source code.

Write the report from these inputs only. Do not invent log lines, code, or metrics. Requirements:
- root_cause_statement: 2-4 sentences. What happened, where in the code, and why the customer saw what they saw. Reference the file:line.
- short_term_fix: the immediate, low-risk change (think: log the swallowed exception, alert on it, retry the update, reconcile affected transactions).
- long_term_fix: the architectural fix (think: make payment + subscription update atomic, outbox pattern, transactional boundaries), 2-4 sentences.
- customer_impact: 1-2 sentences, plain language, what the affected customer experienced.

## Investigation result
{investigation_json}

## Code correlation
{correlation_json}"""

REPORT_SCHEMA = types.Schema(
    type="OBJECT",
    properties={
        "root_cause_statement": types.Schema(type="STRING"),
        "customer_impact": types.Schema(type="STRING"),
        "short_term_fix": types.Schema(type="STRING"),
        "long_term_fix": types.Schema(type="STRING"),
    },
    required=["root_cause_statement", "customer_impact", "short_term_fix", "long_term_fix"],
)


def generate_rca(investigation: dict, correlation: dict | None, verbose: bool = True) -> dict:
    """Produce the full RCA report dict: LLM-written analysis sections plus
    the verbatim evidence (log lines + code location) from the earlier stages."""
    client = get_llm_client()
    prompt = REPORT_PROMPT.format(
        investigation_json=json.dumps(investigation, indent=2),
        correlation_json=json.dumps(correlation, indent=2) if correlation else "unavailable",
    )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=REPORT_SCHEMA,
    )
    response = _generate_with_retry(client, prompt, config, verbose)
    report = json.loads(response.text)

    # Evidence passes through verbatim from the stages that produced it; the
    # LLM writes the analysis but never the citations.
    report["evidence"] = {
        "log_lines": investigation.get("evidence", []),
        "failure_point": investigation.get("failure_point"),
        "service": investigation.get("service"),
        "error_message": investigation.get("error_message"),
        "code": correlation,
    }
    return report


def render_markdown(report: dict) -> str:
    """Render the report dict as the human-facing markdown document."""
    ev = report["evidence"]
    code = ev.get("code") or {}
    lines = [
        "# Root Cause Analysis",
        "",
        "## Root cause",
        report["root_cause_statement"],
        "",
        "## Customer impact",
        report["customer_impact"],
        "",
        "## Evidence",
        f"- **Failure point:** `{ev['failure_point']}` ({ev['service']})",
        f"- **Error:** {ev['error_message']}",
    ]
    if code:
        lines.append(f"- **Code location:** `{code['file']}:{code['line']}`")
        lines += ["", "```python", code["code_snippet"], "```"]
    lines += ["", "### Log evidence", "```"]
    lines += ev["log_lines"]
    lines += ["```", "", "## Short-term fix", report["short_term_fix"], "", "## Long-term fix", report["long_term_fix"]]
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test with a canned investigation result (matches answer key entry
    # for TXN-6460C5FDD3E4) so this stage can be tested without re-running
    # the engine.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "correlation"))
    from correlator import correlate

    investigation = {
        "issue_found": True,
        "failure_point": "SubscriptionService.renew",
        "service": "novastream.subscriptions",
        "error_message": "DatabaseConnectionError: connection pool 'subscriptions-db' exhausted (waited 5000ms)",
        "evidence": [
            "2026-07-09 10:17:16,287 INFO [novastream.api] Renewal request received [user: USR-81183, plan: BASIC_MONTHLY, txn: TXN-6460C5FDD3E4]",
            "2026-07-09 10:17:16,652 INFO [novastream.payments] Payment gateway responded APPROVED [txn: TXN-6460C5FDD3E4, auth: AUTH-EA37DD37, amount: $9.99]",
            "2026-07-09 10:17:21,679 ERROR [novastream.db] Failed to acquire connection from pool 'subscriptions-db' after 5000ms: pool exhausted (10/10 connections in use)",
            "2026-07-09 10:17:21,695 INFO [novastream.api] Renewal request completed with status 200 [user: USR-81183, txn: TXN-6460C5FDD3E4]",
        ],
    }
    correlation = correlate(investigation["failure_point"])
    report = generate_rca(investigation, correlation)
    print(render_markdown(report))
