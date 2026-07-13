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
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d