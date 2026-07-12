# This file is to define the schema for log entries that will be stored in Elasticsearch. 
# It uses dataclasses to define the structure of a log entry, including fields for timestamp, source, level, component, message, user ID, transaction ID, and raw data.

from dataclasses import dataclass, field, asdict
from typing import Optional, Any, Dict
from datetime import datetime


@dataclass
class LogEntry:
    timestamp: datetime   
    source: str                
    level: str                   
    component: str             
    message: str                 
    user_id: Optional[str] = None
    txn_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_es_doc(self) -> dict:
        # Serialize to a JSON-safe dict for the Elasticsearch bulk API.
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d