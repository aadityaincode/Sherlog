"""
Query layer used by the Investigation Engine (LLM + RAG) to pull evidence
out of Elasticsearch.

Key design point: most app.log and transactions.json lines carry an explicit
txn_id, but some don't. monitoring.log alerts never do, and a few app.log
ERROR lines (e.g. the novastream.db pool-exhaustion message) have no {txn}
placeholder either. So a transaction's timeline is reconstructed in two passes:
  1. exact match on txn_id (app + transaction events)
  2. time-window correlation against any ERROR/ALERT event with no txn_id
     that happened during that transaction's span (these signals don't know
     which transaction they broke, only *when* they happened)
"""
from datetime import timedelta
from dateutil import parser as dateparser

from client import get_client, INDEX_NAME

DEFAULT_CORRELATION_WINDOW_SECONDS = 15


def get_timeline_by_txn(es, txn_id: str, index: str = INDEX_NAME,
                         correlation_window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS):
    """Reconstruct the full story for one transaction, including nearby infra signals."""
    direct_query = {
        "query": {"term": {"txn_id": txn_id}},
        "sort": [{"timestamp": "asc"}],
        "size": 200,
    }
    direct_hits = es.search(index=index, body=direct_query)["hits"]["hits"]
    direct_events = [h["_source"] for h in direct_hits]

    if not direct_events:
        return []

    start = min(dateparser.isoparse(e["timestamp"]) for e in direct_events)
    end = max(dateparser.isoparse(e["timestamp"]) for e in direct_events)
    window_start = (start - timedelta(seconds=correlation_window_seconds)).isoformat()
    window_end = (end + timedelta(seconds=correlation_window_seconds)).isoformat()

    infra_query = {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"timestamp": {"gte": window_start, "lte": window_end}}},
                ],
                "must": [{"terms": {"level": ["ERROR", "ALERT"]}}],
                "must_not": [{"exists": {"field": "txn_id"}}],
            }
        },
        "sort": [{"timestamp": "asc"}],
        "size": 100,
    }
    infra_hits = es.search(index=index, body=infra_query)["hits"]["hits"]
    infra_events = [h["_source"] for h in infra_hits]

    timeline = direct_events + infra_events
    timeline.sort(key=lambda e: e["timestamp"])
    return timeline


def search_by_user(es, user_id: str, index: str = INDEX_NAME, size: int = 200):
    query = {"query": {"term": {"user_id": user_id}}, "sort": [{"timestamp": "asc"}], "size": size}
    return [h["_source"] for h in es.search(index=index, body=query)["hits"]["hits"]]


def full_text_search(es, query_string: str, index: str = INDEX_NAME,
                      level: str = None, source: str = None, size: int = 50):
    """Free-text search over log messages, e.g. an engineer typing
    'connection pool exhausted' or the LLM searching for a symptom phrase."""
    must = [{"multi_match": {"query": query_string, "fields": ["message", "component"]}}]
    filters = []
    if level:
        filters.append({"term": {"level": level}})
    if source:
        filters.append({"term": {"source": source}})

    body = {
        "query": {"bool": {"must": must, "filter": filters}},
        "sort": ["_score"],
        "size": size,
    }
    return [h["_source"] for h in es.search(index=index, body=body)["hits"]["hits"]]


def errors_near(es, timestamp_iso: str, window_seconds: int = 30, index: str = INDEX_NAME):
    """All ERROR/ALERT/WARN events within a time window of a given timestamp,
    regardless of correlation key. Useful when you only have a rough
    'something broke around this time' signal."""
    t = dateparser.isoparse(timestamp_iso)
    start = (t - timedelta(seconds=window_seconds)).isoformat()
    end = (t + timedelta(seconds=window_seconds)).isoformat()

    body = {
        "query": {
            "bool": {
                "filter": [{"range": {"timestamp": {"gte": start, "lte": end}}}],
                "must": [{"terms": {"level": ["ERROR", "ALERT", "WARN"]}}],
            }
        },
        "sort": [{"timestamp": "asc"}],
        "size": 100,
    }
    return [h["_source"] for h in es.search(index=index, body=body)["hits"]["hits"]]


if __name__ == "__main__":
    # quick manual smoke test once you've run ingest.py against a live ES instance.
    # Pulls the first broken txn_id from the answer key so this stays correct
    # across regenerated datasets (different --seed runs produce different txn_ids).
    import json
    from pathlib import Path

    answer_key_path = Path(__file__).resolve().parents[2] / "data" / "generated" / "answer_key.json"
    answer_key = json.loads(answer_key_path.read_text())
    txn_id = answer_key[0]["txn_id"]

    es = get_client()
    timeline = get_timeline_by_txn(es, txn_id)
    print(f"Timeline for {txn_id}:")
    for e in timeline:
        print(e["timestamp"], e["source"], e["level"], e["component"], "|", e["message"])