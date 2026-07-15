"""
Tool definitions and dispatcher exposing search.py's query functions to the
LLM investigation engine's function-calling API.
"""
from search import (
    errors_near,
    events_in_window,
    full_text_search,
    get_timeline_by_txn,
    search_by_user,
)

TOOL_SCHEMAS = [
    {
        "name": "get_timeline_by_txn",
        "description": (
            "Reconstruct the full event timeline for one transaction id, "
            "including nearby infra errors/alerts correlated by time window. "
            "Use this once you have a specific txn_id to investigate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string", "description": "Transaction id, e.g. TXN-6460C5FDD3E4"},
            },
            "required": ["txn_id"],
        },
    },
    {
        "name": "search_by_user",
        "description": (
            "Get all log events for a given user id, sorted by time. Use "
            "this to find candidate transactions when the complaint names "
            "a user but not a specific txn_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User id, e.g. USR-81183"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "full_text_search",
        "description": (
            "Free-text search over log messages and component names, e.g. "
            "'connection pool exhausted' or 'payment declined'. Use this to "
            "find candidate transactions or errors when you only have a "
            "symptom description, not an id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_string": {"type": "string", "description": "Free-text search phrase"},
                "level": {"type": "string", "description": "Optional level filter, e.g. ERROR, ALERT, INFO"},
                "source": {"type": "string", "description": "Optional source filter: app, monitoring, or transaction"},
            },
            "required": ["query_string"],
        },
    },
    {
        "name": "events_in_window",
        "description": (
            "Get ALL log events (every level, including INFO) within a time "
            "window of a given timestamp, sorted by time. Use this to turn a "
            "time-of-day lead into a concrete txn_id: the renewal-request "
            "INFO lines around a failure carry the user and txn ids that "
            "infra errors lack."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timestamp_iso": {"type": "string", "description": "ISO-8601 timestamp to search around"},
                "window_seconds": {"type": "integer", "description": "Half-width of the search window in seconds, default 30"},
            },
            "required": ["timestamp_iso"],
        },
    },
    {
        "name": "errors_near",
        "description": (
            "Get all ERROR/ALERT/WARN events within a time window of a given "
            "timestamp, regardless of which transaction they belong to. Use "
            "this when you have a rough 'something broke around this time' "
            "signal but no txn_id or user_id yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timestamp_iso": {"type": "string", "description": "ISO-8601 timestamp to search around"},
                "window_seconds": {"type": "integer", "description": "Half-width of the search window in seconds, default 30"},
            },
            "required": ["timestamp_iso"],
        },
    },
]

_DISPATCH = {
    "get_timeline_by_txn": lambda es, args: get_timeline_by_txn(es, args["txn_id"]),
    "search_by_user": lambda es, args: search_by_user(es, args["user_id"]),
    "full_text_search": lambda es, args: full_text_search(
        es, args["query_string"], level=args.get("level"), source=args.get("source")
    ),
    "errors_near": lambda es, args: errors_near(
        es, args["timestamp_iso"], window_seconds=args.get("window_seconds", 30)
    ),
    "events_in_window": lambda es, args: events_in_window(
        es, args["timestamp_iso"], window_seconds=args.get("window_seconds", 30)
    ),
}


def execute_tool(es, name, args):
    """Run the named tool against Elasticsearch and return its result."""
    if name not in _DISPATCH:
        raise ValueError(f"Unknown tool: {name}")
    return _DISPATCH[name](es, args)


if __name__ == "__main__":
    # Smoke test: run each tool once against a real broken txn from the answer key.
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
    from client import get_client

    answer_key_path = Path(__file__).resolve().parents[2] / "data" / "generated" / "answer_key.json"
    answer_key = json.loads(answer_key_path.read_text())
    txn_id = answer_key[0]["txn_id"]
    user_id = answer_key[0]["user_id"]

    es = get_client()

    print(f"--- get_timeline_by_txn({txn_id}) ---")
    timeline = execute_tool(es, "get_timeline_by_txn", {"txn_id": txn_id})
    for e in timeline:
        print(e["timestamp"], e["source"], e["level"], "|", e["message"])

    print(f"\n--- search_by_user({user_id}) ---")
    print(f"{len(execute_tool(es, 'search_by_user', {'user_id': user_id}))} events found")

    print("\n--- full_text_search('pool exhausted') ---")
    for e in execute_tool(es, "full_text_search", {"query_string": "pool exhausted"})[:3]:
        print(e["timestamp"], e["level"], "|", e["message"])

    print("\n--- errors_near(first timeline event) ---")
    first_ts = timeline[0]["timestamp"]
    for e in execute_tool(es, "errors_near", {"timestamp_iso": first_ts})[:3]:
        print(e["timestamp"], e["level"], "|", e["message"])
