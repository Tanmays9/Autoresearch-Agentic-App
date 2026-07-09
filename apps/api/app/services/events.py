import json

from sqlalchemy.orm import Session

from ..models import RunEvent


def add_event(db: Session, run_id: str, event_type: str, message: str, data: dict | None = None) -> RunEvent:
    event = RunEvent(
        run_id=run_id,
        event_type=event_type,
        message=message,
        data_json=json.dumps(data or {}, ensure_ascii=False),
    )
    db.add(event)
    return event

