"""
Elasticsearch connection + index mapping for the unified log store.
Run `docker-compose up -d` (see docker-compose.yml) to get a local
single-node cluster at http://localhost:9200 before using this.
"""
from elasticsearch import Elasticsearch

INDEX_NAME = "incident-logs"

MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "source":    {"type": "keyword"},   # app | monitoring | transaction
            "level":     {"type": "keyword"},   # INFO/WARN/ERROR/METRIC/HEALTHCHECK/ALERT/PAYMENT_EVENT
            "component": {"type": "keyword"},   # novastream.payments / db-01 / StrivePay
            "user_id":   {"type": "keyword"},
            "txn_id":    {"type": "keyword"},
            "message": {
                "type": "text",                 # full-text searchable
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            # Keep the original line/record for evidence, but don't index it
            # as a bunch of separate searchable fields — it's for display only.
            "raw": {"type": "object", "enabled": False},
        }
    },
}


def get_client(hosts=("http://localhost:9200",)) -> Elasticsearch:
    return Elasticsearch(hosts=list(hosts))


def create_index(es: Elasticsearch, index_name: str = INDEX_NAME, recreate: bool = False) -> None:
    exists = es.indices.exists(index=index_name)
    if exists:
        if not recreate:
            return
        es.indices.delete(index=index_name)
    es.indices.create(index=index_name, body=MAPPING)