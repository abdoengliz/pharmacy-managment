from __future__ import annotations

from typing import Any

from app.core import insert_and_get_id


class ApprovalService:
    @staticmethod
    def get_definition(db: Any, entity_type: str, amount: float | None = None) -> Any:
        rows = db.execute(
            """SELECT * FROM approval_definitions
               WHERE entity_type=? AND is_active=1
               ORDER BY CASE WHEN min_amount IS NULL THEN 1 ELSE 0 END, min_amount DESC, id""",
            (entity_type,),
        ).fetchall()
        for row in rows:
            value = float(amount or 0)
            if row["min_amount"] is not None and value < float(row["min_amount"]):
                continue
            if row["max_amount"] is not None and value > float(row["max_amount"]):
                continue
            return row
        return None

    @staticmethod
    def pending_for(db: Any, entity_type: str, entity_id: int) -> Any:
        return db.execute(
            "SELECT * FROM approval_requests WHERE entity_type=? AND entity_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1",
            (entity_type, entity_id),
        ).fetchone()

    @staticmethod
    def request(db: Any, *, entity_type: str, entity_id: int, reference_no: str,
                amount: float | None, branch_id: int | None, requested_by: int,
                requested_at: str) -> int:
        existing = ApprovalService.pending_for(db, entity_type, entity_id)
        if existing:
            return int(existing["id"])
        definition = ApprovalService.get_definition(db, entity_type, amount)
        if definition is None:
            raise LookupError(f"No approval definition for {entity_type}")
        request_id = insert_and_get_id(
            db,
            """INSERT INTO approval_requests
               (definition_id,entity_type,entity_id,reference_no,amount,branch_id,status,current_step,requested_by,requested_at)
               VALUES(?,?,?,?,?,?,'PENDING',1,?,?)""",
            (definition["id"], entity_type, entity_id, reference_no, amount, branch_id, requested_by, requested_at),
        )
        db.execute(
            """INSERT INTO approval_history(request_id,action,from_status,to_status,note,acted_by,acted_at)
               VALUES(?,'REQUESTED',NULL,'PENDING',NULL,?,?)""",
            (request_id, requested_by, requested_at),
        )
        return request_id

    @staticmethod
    def decide(db: Any, request_id: int, *, approve: bool, user_id: int,
               decided_at: str, note: str = "") -> Any:
        row = db.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or row["status"] != "PENDING":
            raise ValueError("طلب الاعتماد غير متاح.")
        status = "APPROVED" if approve else "REJECTED"
        action = status
        db.execute(
            """UPDATE approval_requests SET status=?,decided_by=?,decided_at=?,decision_note=? WHERE id=?""",
            (status, user_id, decided_at, note or None, request_id),
        )
        db.execute(
            """INSERT INTO approval_history(request_id,action,from_status,to_status,note,acted_by,acted_at)
               VALUES(?,?,?,?,?,?,?)""",
            (request_id, action, "PENDING", status, note or None, user_id, decided_at),
        )
        return row
