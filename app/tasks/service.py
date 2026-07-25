from __future__ import annotations
from typing import Any

from app.core import insert_and_get_id


class TaskService:
    @staticmethod
    def create(db: Any, *, title: str, created_at: str, description: str = "",
               location_id: int | None = None, assigned_user_id: int | None = None,
               assigned_permission: str | None = None, task_type: str = "GENERAL",
               reference_type: str | None = None, reference_id: int | None = None,
               priority: str = "NORMAL", created_by: int | None = None,
               due_at: str | None = None, action_url: str | None = None,
               event_key: str | None = None, deduplicate: bool = True) -> int:
        if deduplicate and event_key:
            row = db.execute(
                "SELECT id FROM tasks WHERE event_key=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
                (event_key,),
            ).fetchone()
            if row:
                return int(row["id"])
        new_id = insert_and_get_id(
            db,
            """INSERT INTO tasks
               (title,description,location_id,assigned_user_id,task_type,reference_type,reference_id,
                status,priority,created_by,created_at,due_at,action_url,event_key,assigned_permission)
               VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?)""",
            (title, description, location_id, assigned_user_id, task_type, reference_type,
             reference_id, priority, created_by, created_at, due_at, action_url, event_key,
             assigned_permission),
        )
        return new_id

    @staticmethod
    def complete(db: Any, *, task_id: int, user_id: int, completed_at: str) -> None:
        db.execute("UPDATE tasks SET status='COMPLETED',completed_by=?,completed_at=? WHERE id=? AND status='OPEN'",
                   (user_id, completed_at, task_id))

    @staticmethod
    def close_reference(db: Any, *, reference_type: str, reference_id: int,
                        user_id: int, completed_at: str, status: str = "COMPLETED") -> int:
        cur = db.execute(
            "UPDATE tasks SET status=?,completed_by=?,completed_at=? WHERE reference_type=? AND reference_id=? AND status='OPEN'",
            (status, user_id, completed_at, reference_type, reference_id),
        )
        return int(cur.rowcount)

    @staticmethod
    def create_approval_task(db: Any, *, request_id: int, reference_no: str,
                             amount: float, branch_id: int | None, created_by: int,
                             created_at: str) -> int:
        return TaskService.create(
            db, title=f"اعتماد العملية {reference_no}",
            description=f"مراجعة واعتماد عملية بقيمة {amount:.2f} د.ل.",
            created_at=created_at, location_id=branch_id, assigned_permission="approve_transactions",
            task_type="APPROVAL", reference_type="approval_request", reference_id=request_id,
            priority="HIGH", created_by=created_by, action_url=f"/approvals/{request_id}",
            event_key=f"approval.task:{request_id}", deduplicate=True,
        )
