"""
Ingestion entrypoint: parse all 3 sources -> normalize -> bulk index into ES.

Usage:
    python ingest.py app.log monitoring.log transactions.json
"""
import sys
from elasticsearch.helpers import bulk

from client import get_client, create_index, INDEX_NAME
from parsers import parse_app_log, parse_monitoring_log, parse_transactions


def build_actions(entries, index_name: str = INDEX_NAME):
    for e in entries:
        yield {"_index": index_name, "_source": e.to_es_doc()}


def run(app_log_path: str, monitoring_log_path: str, transactions_path: str,
        recreate_index: bool = True) -> int:
    es = get_client()
    create_index(es, recreate=recreate_index)

    entries = []
    entries += parse_app_log(app_log_path)
    entries += parse_monitoring_log(monitoring_log_path)
    entries += parse_transactions(transactions_path)

    success, errors = bulk(es, build_actions(entries), stats_only=False, raise_on_error=False)
    print(f"Indexed {success} documents ({len(errors)} errors)")

    es.indices.refresh(index=INDEX_NAME)
    return len(entries)


if __name__ == "__main__":
    args = sys.argv[1:] or ["app.log", "monitoring.log", "transactions.json"]
    if len(args) != 3:
        print("Usage: python ingest.py <app.log> <monitoring.log> <transactions.json>")
        sys.exit(1)
    run(*args)