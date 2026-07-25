from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass(slots=True)
class Event:
    event_type: str
    entity_type: str | None = None
    entity_id: int | None = None
    title: str | None = None
    description: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    user_id: int | None = None
    branch_id: int | None = None

class EventBus:
    _subscribers: dict[str, list[Callable[[Any, Event], None]]] = {}

    @classmethod
    def subscribe(cls, event_type: str, handler: Callable[[Any, Event], None]) -> None:
        cls._subscribers.setdefault(event_type, []).append(handler)

    @classmethod
    def publish(cls, db: Any, event: Event) -> int:
        from app.core import insert_and_get_id, now
        event_id = insert_and_get_id(
            db,
            """INSERT INTO event_history(event_type,entity_type,entity_id,payload_json,user_id,branch_id,status,created_at)
               VALUES(?,?,?,?,?,?,'PUBLISHED',?)""",
            (event.event_type,event.entity_type,event.entity_id,json.dumps(event.payload,ensure_ascii=False,default=str),
             event.user_id,event.branch_id,now()),
        )
        if event.entity_type and event.entity_id is not None:
            db.execute(
                """INSERT INTO activity_timeline(entity_type,entity_id,event_type,title,description,user_id,branch_id,metadata_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (event.entity_type,event.entity_id,event.event_type,event.title or event.event_type,event.description,
                 event.user_id,event.branch_id,json.dumps(event.payload,ensure_ascii=False,default=str),now()),
            )
        try:
            for handler in cls._subscribers.get(event.event_type, []) + cls._subscribers.get("*", []):
                handler(db, event)
        except Exception as exc:
            db.execute("UPDATE event_history SET status='FAILED',error_message=? WHERE id=?",(str(exc),event_id))
            db.execute(
                """INSERT INTO error_log(source,error_type,message,traceback_text,user_id,branch_id,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ("event_bus",type(exc).__name__,str(exc),traceback.format_exc(),event.user_id,event.branch_id,now()),
            )
            raise
        db.commit()
        return event_id
