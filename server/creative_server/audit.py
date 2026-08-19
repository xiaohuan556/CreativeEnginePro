import json

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog, User


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def record_audit(db: Session, request: Request, action: str, actor: User | None = None, target_type: str = "", target_id: str = "", detail: dict | None = None) -> None:
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail_json=json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
        ip_address=client_ip(request),
    ))
