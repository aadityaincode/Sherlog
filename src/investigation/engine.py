"""
LLM investigation engine: drives Gemini's function-calling loop over the
tools in tools.py to reconstruct a failure from a complaint or txn_id.
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from client import get_client
from tools import TOOL_SCHEMAS, execute_tool

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Free-tier availability varies a lot by model on this key: gemini-2.5-flash
# is deprecated for new keys (404), gemini-2.0-flash has a 0 free quota, and
# gemini-flash-latest (-> gemini-3.5-flash) only gets 20 requests/day. This
# one actually works. If you swap models, verify free-tier quota first.
MODEL = "gemini-flash-lite-latest"

SYSTEM_PROMPT = """You are an incident investigator for NovaStream, a subscription billing service. You have tools to query its logs (app.log, transactions.json, monitoring.log, all normalized into one store) by txn_id, user_id, free text, or time window.

## Log catalog

Every renewal goes through some subset of these messages, identified by logger (component):

- novastream.api: "Renewal request received" (1), "Renewal request completed with status 200" (2), "Renewal aborted, payment declined" (3, WARN)
- novastream.payments: "Initiating charge..." (4), "Payment gateway responded APPROVED..." (5), "Unknown plan code..." (6, WARN)
- novastream.subscriptions: "Renewing subscription..." (7), "Subscription status set to ACTIVE..." (8)
- novastream.db: "Failed to acquire connection from pool 'subscriptions-db' after 5000ms: pool exhausted..." (9, ERROR)
- novastream.notifications: "Renewal confirmation email queued..." (10), "Payment failed email queued..." (11)

## Flow signatures

- Normal renewal: 1 -> 4 -> 5 -> 7 -> 8 -> 10 -> 2. Everything succeeds.
- Broken renewal (the bug you are hunting): 1 -> 4 -> 5 -> 7 -> 9 -> 2. Payment settles (4, 5), but message 8 and 10 never appear, and the API still answers 200 (message 2). The evidence is the ABSENCE of 8 and 10 plus the lone novastream.db ERROR (message 9). If a transaction's timeline has messages 1, 4, 5, 7, then a novastream.db ERROR, then 2, with no message 8 or 10, you already have complete evidence. Stop investigating and submit.
- Card declined (unrelated noise): 1 -> 4 -> 6 -> 3 -> 11, API returns 402. Not the bug you are looking for.

## Root cause

The bug lives in SubscriptionService.renew(): it wraps the database update in a try/except that swallows DatabaseConnectionError after the payment has already succeeded. The exact error raised is: "DatabaseConnectionError: connection pool 'subscriptions-db' exhausted (waited 5000ms)".

## How to investigate efficiently

get_timeline_by_txn already returns both the transaction's own events and correlated monitoring/error events in its time window, in one call. If you have a txn_id, start there, it is usually all the evidence you need. Only reach for full_text_search or errors_near if get_timeline_by_txn comes back empty or ambiguous. Do not call the same tool repeatedly with minor variations of the same query.

## Starting from a complaint instead of a txn_id

Customer complaints usually don't include a txn_id. Work backwards to one:
- If the complaint names a user id (USR-xxxxx), call search_by_user, find the renewal transaction matching the complaint (right plan, right rough time), take its txn_id, then get_timeline_by_txn.
- If there is no user id either, use full_text_search with distinctive details from the complaint (plan code, amount) or a symptom phrase, then narrow down to a txn_id the same way.
- A complaint like "I was charged but my subscription still shows expired and I got no confirmation email" is the classic symptom of the broken-renewal bug: the charge settled but message 8 (ACTIVE) and 10 (confirmation email) are missing from that user's timeline.

## Submitting

Call submit_investigation_result exactly once, as soon as you have concrete evidence, not before and not after searching further than necessary.

It is a valid, complete conclusion to report that nothing is wrong. Not every transaction is broken: some complete the normal flow successfully, some are ordinary card declines. Only set issue_found to true if you see the specific broken-renewal signature (message 7 followed by a novastream.db ERROR, with 8 and 10 missing). A card decline (messages 1, 4, 6, 3, 11, API returns 402) is expected behavior, not a bug, set issue_found to false for it.

If issue_found is true: failure_point should name the method (e.g. "SubscriptionService.renew"), service should be the logger of the component that owns that method (e.g. "novastream.subscriptions"), error_message should be the underlying exception.

If issue_found is false: you can omit failure_point, service, and error_message. evidence is always required, citing the log lines that support whatever you concluded, whether that's a failure or a clean result."""

SUBMIT_TOOL_NAME = "submit_investigation_result"

SUBMIT_SCHEMA = {
    "name": SUBMIT_TOOL_NAME,
    "description": (
        "Submit your final investigation conclusion. This ends the "
        "investigation. Call it exactly once, only after you have gathered "
        "concrete evidence, not before. Reporting no failure found is a "
        "valid conclusion, not a failure to investigate."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issue_found": {
                "type": "boolean",
                "description": "True if you found the broken-renewal bug. False if the transaction completed normally or was an ordinary card decline.",
            },
            "failure_point": {
                "type": "string",
                "description": "Required only if issue_found is true. The class/method where the failure occurred, e.g. SubscriptionService.renew",
            },
            "service": {
                "type": "string",
                "description": "Required only if issue_found is true. The logger/component name responsible, e.g. novastream.subscriptions",
            },
            "error_message": {
                "type": "string",
                "description": "Required only if issue_found is true. The underlying error, e.g. DatabaseConnectionError: pool exhausted",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The exact log lines (timestamp + message) that support this conclusion, in chronological order, whether the conclusion is a failure or a clean result",
            },
        },
        "required": ["issue_found", "evidence"],
    },
}

_JSON_TO_GEMINI_TYPE = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}


def get_llm_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _to_gemini_schema(json_schema: dict) -> types.Schema:
    gemini_type = _JSON_TO_GEMINI_TYPE[json_schema["type"]]
    description = json_schema.get("description", "")

    if gemini_type == "OBJECT":
        properties = {
            name: _to_gemini_schema(prop) for name, prop in json_schema.get("properties", {}).items()
        }
        return types.Schema(
            type="OBJECT",
            properties=properties,
            required=json_schema.get("required", []),
            description=description,
        )
    if gemini_type == "ARRAY":
        return types.Schema(type="ARRAY", items=_to_gemini_schema(json_schema["items"]), description=description)
    return types.Schema(type=gemini_type, description=description)


def to_gemini_tool(tool_schemas: list[dict]) -> types.Tool:
    declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_to_gemini_schema(t["input_schema"]),
        )
        for t in tool_schemas
    ]
    return types.Tool(function_declarations=declarations)


MAX_ROUNDS = 6

# Transient-error retry policy. 429s (free tier: 15 requests/min) and 5xx
# blips both resolve on their own; anything else fails fast. The last delay
# must span a full per-minute quota window or back-to-back investigations
# (like score.py) die mid-run.
RETRY_DELAYS_S = [5, 15, 45]
RETRYABLE_CODES = {429, 500, 502, 503, 504}


def _generate_with_retry(client, contents, config, verbose: bool):
    for attempt, delay in enumerate([*RETRY_DELAYS_S, None]):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except genai_errors.APIError as e:
            if e.code not in RETRYABLE_CODES or delay is None:
                raise
            if verbose:
                print(f"  transient {e.code}, retrying in {delay}s ({attempt + 1}/{len(RETRY_DELAYS_S)})")
            time.sleep(delay)


def investigate(prompt: str, verbose: bool = True) -> dict:
    """Run the tool-calling loop until the model calls submit_investigation_result.

    Returns the submitted result as a dict matching the agreed investigation
    output shape. Raises if the model stops without submitting (either it
    gave up and answered in plain text, or it never converged within
    MAX_ROUNDS) — both are failures worth seeing, not silently swallowing.
    """
    client = get_llm_client()
    es = get_client()
    tool = to_gemini_tool(TOOL_SCHEMAS + [SUBMIT_SCHEMA])
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=[tool])

    contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]

    for round_num in range(1, MAX_ROUNDS + 1):
        response = _generate_with_retry(client, contents, config, verbose)
        candidate = response.candidates[0]
        contents.append(candidate.content)

        calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not calls:
            text = "".join(p.text for p in candidate.content.parts if p.text)
            raise RuntimeError(f"Model stopped without calling {SUBMIT_TOOL_NAME}: {text!r}")

        submit_call = next((c for c in calls if c.name == SUBMIT_TOOL_NAME), None)
        if submit_call:
            result = dict(submit_call.args)
            if verbose:
                print(f"[round {round_num}] SUBMIT {result}")
            return result

        response_parts = []
        for fc in calls:
            args = dict(fc.args)
            if verbose:
                print(f"[round {round_num}] TOOL CALL {fc.name}({args})")
            result = execute_tool(es, fc.name, args)
            if verbose:
                print(f"  -> {len(result)} events returned")
            response_parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=response_parts))

    raise RuntimeError(f"Investigation did not conclude within {MAX_ROUNDS} rounds")


if __name__ == "__main__":
    # Step 4 smoke test: does the model wrap up via submit_investigation_result
    # with the right shape, instead of trailing off in free text?
    import json

    result = investigate("Investigate transaction TXN-6460C5FDD3E4.")
    print("\n--- FINAL ANSWER ---")
    print(json.dumps(result, indent=2))
