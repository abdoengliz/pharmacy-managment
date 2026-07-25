from __future__ import annotations
from typing import Any

from app.core import insert_and_get_id


class NotificationService:
    @staticmethod
    def create(db: Any, *, title: str, message: str, created_at: str,
               location_id: int | None = None, user_id: int | None = None,
               notification_type: str = "INFO", priority: str = "NORMAL",
               reference_type: str | None = None, reference_id: int | None = None,
               action_url: str | None = None, event_key: str | None = None,
               deduplicate: bool = False) -> int:
        if deduplicate and event_key:
            row = db.execute(
                "SELECT id FROM notifications WHERE event_key=? AND is_read=0 ORDER BY id DESC LIMIT 1",
                (event_key,),
            ).fetchone()
            if row:
                return int(row["id"])
        new_id = insert_and_get_id(
            db,
            """INSERT INTO notifications
               (title,message,notification_type,priority,location_id,user_id,reference_type,reference_id,
                is_read,created_at,action_url,event_key)
               VALUES(?,?,?,?,?,?,?,?,0,?,?,?)""",
            (title, message, notification_type, priority, location_id, user_id,
             reference_type, reference_id, created_at, action_url, event_key),
        )
        return new_id

    @staticmethod
    def mark_read(db: Any, notification_id: int, read_at: str) -> None:
        db.execute("UPDATE notifications SET is_read=1,read_at=? WHERE id=?", (read_at, notification_id))

    @staticmethod
    def notify_approval_requested(db: Any, *, request_id: int, reference_no: str,
                                  amount: float, branch_id: int | None, created_at: str) -> int:
        return NotificationService.create(
            db, title="طلب اعتماد جديد",
            message=f"العملية {reference_no} بقيمة {amount:.2f} د.ل تنتظر الاعتماد.",
            created_at=created_at, location_id=branch_id, notification_type="WARNING",
            priority="HIGH", reference_type="approval_request", reference_id=request_id,
            action_url=f"/approvals/{request_id}", event_key=f"approval.requested:{request_id}", deduplicate=True,
        )

    @staticmethod
    def notify_approval_decision(db: Any, *, request_id: int, reference_no: str,
                                 approved: bool, requester_id: int, note: str,
                                 created_at: str) -> int:
        status = "تم اعتماد" if approved else "تم رفض"
        return NotificationService.create(
            db, title=f"{status} الطلب",
            message=f"{status} العملية {reference_no}." + (f" الملاحظة: {note}" if note else ""),
            created_at=created_at, user_id=requester_id,
            notification_type="SUCCESS" if approved else "DANGER",
            priority="HIGH", reference_type="approval_request", reference_id=request_id,
            action_url=f"/approvals/{request_id}", event_key=f"approval.decision:{request_id}", deduplicate=True,
        )
