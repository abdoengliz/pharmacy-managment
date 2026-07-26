from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the workflow area."""

@app.route("/notifications")
@login_required
def notifications_page() -> Any:
    db=get_db(); scope,params=notification_scope()
    rows=db.execute("SELECT * FROM notifications WHERE 1=1"+scope+" ORDER BY id DESC LIMIT 200",params).fetchall()
    return render_template("notifications.html",rows=rows)

@app.post("/notifications/read-all")
@login_required
def notifications_read_all() -> Any:
    db=get_db(); user=current_user()
    if user["role"]=="admin": db.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
    else:
        clauses=["user_id=?"]; params=[user["id"]]
        if user["branch_id"]: clauses.append("location_id=?"); params.append(user["branch_id"])
        db.execute("UPDATE notifications SET is_read=1 WHERE is_read=0 AND ("+" OR ".join(clauses)+")",params)
    db.commit(); return redirect(request.referrer or url_for("notifications_page"))

@app.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id:int) -> Any:
    db=get_db(); scope,params=notification_scope()
    row=db.execute("SELECT * FROM notifications WHERE id=?"+scope,[notification_id]+params).fetchone()
    if not row:
        flash("الإشعار غير موجود.","danger"); return redirect(url_for("notifications_page"))
    NotificationService.mark_read(db, notification_id, now()); db.commit()
    if row["action_url"]:
        return redirect(row["action_url"])
    if row["reference_type"]=="invoice_transfer" and row["reference_id"]:
        return redirect(url_for("invoice_transfer_detail",transfer_id=row["reference_id"]))
    if row["reference_type"] in {"external_debt_due","external_debt"} and row["reference_id"]:
        return redirect(url_for("external_debt_detail",debt_id=row["reference_id"]))
    if row["reference_type"]=="treasury_transfer" and row["reference_id"]:
        return redirect(url_for("treasury_transfer_detail",transfer_id=row["reference_id"]))
    return redirect(url_for("notifications_page"))

@app.route("/tasks")
@login_required
def tasks_page() -> Any:
    db=get_db(); scope,params=task_scope()
    rows=db.execute("SELECT t.*,b.name location_name FROM tasks t LEFT JOIN branches b ON b.id=t.location_id WHERE 1=1"+scope+" ORDER BY CASE t.status WHEN 'OPEN' THEN 0 ELSE 1 END,t.id DESC LIMIT 200",params).fetchall()
    return render_template("tasks.html",rows=rows)

@app.post("/tasks/<int:task_id>/complete")
@login_required
def complete_task(task_id:int) -> Any:
    db=get_db(); user=current_user(); scope,params=task_scope()
    task=db.execute("SELECT * FROM tasks WHERE id=?"+scope,[task_id]+params).fetchone()
    if not task:
        flash("المهمة غير موجودة.","danger"); return redirect(url_for("tasks_page"))
    if task["action_url"]:
        return redirect(task["action_url"])
    if task["task_type"]=="RECEIVE_INVOICE" and task["reference_id"]:
        return redirect(url_for("invoice_transfer_detail",transfer_id=task["reference_id"]))
    if task["task_type"]=="FOLLOW_EXTERNAL_DEBT" and task["reference_id"]:
        return redirect(url_for("external_debt_detail",debt_id=task["reference_id"]))
    TaskService.complete(db, task_id=task_id, user_id=user["id"], completed_at=now()); db.commit()
    flash("تم إكمال المهمة.","success"); return redirect(url_for("tasks_page"))

@app.post("/dashboard/tasks/add")
@login_required
def dashboard_add_task() -> Any:
    title = request.form.get("title", "").strip()
    if not title:
        flash("اكتب عنوان المهمة أولاً.", "danger")
        return redirect(url_for("dashboard"))
    user = current_user()
    priority = request.form.get("priority", "NORMAL")
    if priority not in {"HIGH", "NORMAL", "LOW"}:
        priority = "NORMAL"
    due_date = request.form.get("due_date", "").strip() or None
    due_time = request.form.get("due_time", "").strip()
    due_at = f"{due_date} {due_time}" if due_date and due_time else due_date
    create_task(
        title,
        request.form.get("description", "").strip(),
        location_id=user["branch_id"] if user else None,
        assigned_user_id=user["id"] if user else None,
        task_type="MANUAL",
        priority=priority,
        due_at=due_at,
        deduplicate=False,
    )
    get_db().commit()
    audit("إضافة مهمة يدوية", title)
    flash("تمت إضافة المهمة.", "success")
    return redirect(url_for("dashboard"))

@app.post("/dashboard/tasks/<int:task_id>/toggle")
@login_required
def dashboard_toggle_task(task_id: int) -> Any:
    db = get_db(); user = current_user(); scope, params = task_scope()
    task = db.execute("SELECT * FROM tasks WHERE id=?" + scope, [task_id] + params).fetchone()
    if not task:
        flash("المهمة غير موجودة.", "danger")
        return redirect(url_for("dashboard"))
    if task["status"] == "OPEN":
        TaskService.complete(db, task_id=task_id, user_id=user["id"], completed_at=now())
        flash("تم إنجاز المهمة.", "success")
    elif task["task_type"] == "MANUAL":
        db.execute("UPDATE tasks SET status='OPEN',completed_by=NULL,completed_at=NULL WHERE id=?", (task_id,))
        flash("تمت إعادة فتح المهمة.", "success")
    db.commit()
    return redirect(url_for("dashboard"))

@app.post("/dashboard/tasks/<int:task_id>/delete")
@login_required
def dashboard_delete_task(task_id: int) -> Any:
    db = get_db(); scope, params = task_scope()
    task = db.execute("SELECT * FROM tasks WHERE id=?" + scope, [task_id] + params).fetchone()
    if not task or task["task_type"] != "MANUAL":
        flash("لا يمكن حذف هذه المهمة.", "danger")
        return redirect(url_for("dashboard"))
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,)); db.commit()
    audit("حذف مهمة يدوية", task["title"])
    flash("تم حذف المهمة.", "success")
    return redirect(url_for("dashboard"))

@app.get("/approvals")
@login_required
@permission_required("view_approvals")
def approvals_center() -> Any:
    db = get_db()
    status = request.args.get("status", "PENDING").upper()
    if status not in {"PENDING", "APPROVED", "REJECTED", "CANCELLED", "ALL"}:
        status = "PENDING"
    where = "" if status == "ALL" else " WHERE r.status=?"
    params: list[Any] = [] if status == "ALL" else [status]
    rows = db.execute(
        """SELECT r.*,d.name definition_name,d.required_permission,
                  ru.full_name requester,du.full_name decider,b.name branch_name
           FROM approval_requests r
           JOIN approval_definitions d ON d.id=r.definition_id
           JOIN users ru ON ru.id=r.requested_by
           LEFT JOIN users du ON du.id=r.decided_by
           LEFT JOIN branches b ON b.id=r.branch_id""" + where + " ORDER BY r.id DESC LIMIT 200",
        params,
    ).fetchall()
    counts = {row["status"]: row["c"] for row in db.execute("SELECT status,COUNT(*) c FROM approval_requests GROUP BY status").fetchall()}
    return render_template("approvals.html", rows=rows, selected_status=status, counts=counts)

@app.get("/approvals/<int:request_id>")
@login_required
@permission_required("view_approvals")
def approval_detail(request_id: int) -> Any:
    db = get_db()
    row = db.execute(
        """SELECT r.*,d.name definition_name,d.description,d.required_permission,
                  ru.full_name requester,du.full_name decider,b.name branch_name
           FROM approval_requests r JOIN approval_definitions d ON d.id=r.definition_id
           JOIN users ru ON ru.id=r.requested_by LEFT JOIN users du ON du.id=r.decided_by
           LEFT JOIN branches b ON b.id=r.branch_id WHERE r.id=?""", (request_id,)
    ).fetchone()
    if not row:
        flash("طلب الاعتماد غير موجود.", "danger")
        return redirect(url_for("approvals_center"))
    history = db.execute(
        """SELECT h.*,u.full_name actor FROM approval_history h
           LEFT JOIN users u ON u.id=h.acted_by WHERE h.request_id=? ORDER BY h.id""", (request_id,)
    ).fetchall()
    return render_template("approval_detail.html", approval=row, history=history)

@app.post("/approvals/<int:request_id>/decide")
@login_required
@permission_required("approve_transactions")
def decide_approval(request_id: int) -> Any:
    db = get_db(); user = current_user()
    action = request.form.get("action", "")
    note = request.form.get("note", "").strip()
    if action not in {"approve", "reject"}:
        flash("قرار الاعتماد غير صحيح.", "danger")
        return redirect(url_for("approval_detail", request_id=request_id))
    try:
        decision_time = now()
        row = ApprovalService.decide(db, request_id, approve=action == "approve", user_id=user["id"], decided_at=decision_time, note=note)
        TaskService.close_reference(db, reference_type="approval_request", reference_id=request_id, user_id=user["id"], completed_at=decision_time, status="COMPLETED" if action == "approve" else "CANCELLED")
        NotificationService.notify_approval_decision(db, request_id=request_id, reference_no=row["reference_no"] or f"#{request_id}", approved=action == "approve", requester_id=row["requested_by"], note=note, created_at=decision_time)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("approval_detail", request_id=request_id))
    if row["entity_type"] == "treasury_transfer":
        transfer = db.execute("""SELECT t.*,fa.branch_id from_location_id,ta.branch_id to_location_id,
                                  fb.name from_location FROM treasury_transfers t
                                  JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
                                  JOIN financial_accounts ta ON ta.id=t.to_account_id WHERE t.id=?""", (row["entity_id"],)).fetchone()
        if transfer and action == "approve" and transfer["status"] == "DRAFT":
            db.execute("UPDATE treasury_transfers SET status='SENT',sent_by=?,sent_at=? WHERE id=?", (user["id"], now(), transfer["id"]))
            create_notification("توريد مالي جديد", f"تم اعتماد التحويل {transfer['transfer_number']} وهو بانتظار الاستلام.", location_id=transfer["to_location_id"], notification_type="SUCCESS", priority="HIGH", reference_type="treasury_transfer", reference_id=transfer["id"])
            create_task("استلام توريد مالي", f"استلام التحويل {transfer['transfer_number']} من {transfer['from_location']} بقيمة {transfer['amount']:.2f} د.ل", location_id=transfer["to_location_id"], task_type="RECEIVE_TREASURY_TRANSFER", reference_type="treasury_transfer", reference_id=transfer["id"], priority="HIGH")
        elif transfer and action == "reject":
            create_notification("رفض تحويل خزينة", f"تم رفض التحويل {transfer['transfer_number']}. {note}", user_id=transfer["created_by"], notification_type="ERROR", priority="HIGH", reference_type="treasury_transfer", reference_id=transfer["id"])
    db.commit()
    audit("اعتماد عملية" if action == "approve" else "رفض عملية", f"طلب رقم {request_id}: {note}")
    flash("تم اعتماد الطلب." if action == "approve" else "تم رفض الطلب.", "success" if action == "approve" else "warning")
    return redirect(url_for("approval_detail", request_id=request_id))

@app.route('/workflows')
@login_required
@permission_required('view_workflows')
def workflows_page() -> Any:
    db = get_db()
    rows = db.execute("""
        SELECT w.*,
               (SELECT COUNT(*) FROM workflow_states s WHERE s.workflow_id=w.id) state_count,
               (SELECT COUNT(*) FROM workflow_transitions t WHERE t.workflow_id=w.id AND t.is_active=1) transition_count,
               (SELECT COUNT(*) FROM workflow_instances i WHERE i.workflow_id=w.id) instance_count
        FROM workflow_definitions w ORDER BY w.name
    """).fetchall()
    return render_template('workflows.html', rows=rows)

@app.route('/workflows/<int:workflow_id>')
@login_required
@permission_required('view_workflows')
def workflow_detail(workflow_id: int) -> Any:
    db = get_db()
    workflow = db.execute("SELECT * FROM workflow_definitions WHERE id=?", (workflow_id,)).fetchone()
    if not workflow:
        flash('تعريف سير العمل غير موجود.', 'danger')
        return redirect(url_for('workflows_page'))
    states = db.execute("SELECT * FROM workflow_states WHERE workflow_id=? ORDER BY sort_order,id", (workflow_id,)).fetchall()
    transitions = db.execute("SELECT * FROM workflow_transitions WHERE workflow_id=? ORDER BY id", (workflow_id,)).fetchall()
    history = db.execute("""
        SELECT h.*, i.reference_no, u.full_name user_name
        FROM workflow_history h
        JOIN workflow_instances i ON i.id=h.instance_id
        LEFT JOIN users u ON u.id=h.changed_by
        WHERE i.workflow_id=? ORDER BY h.id DESC LIMIT 100
    """, (workflow_id,)).fetchall()
    return render_template('workflow_detail.html', workflow=workflow, states=states, transitions=transitions, history=history)

