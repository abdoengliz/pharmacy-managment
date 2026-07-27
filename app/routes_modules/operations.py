from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the operations area."""

@app.route("/system-management")
@login_required
@permission_required("manage_users")
def system_management() -> Any:
    db=get_db()
    stats={
        "users": db.execute("SELECT COUNT(*) c FROM users WHERE is_active=1").fetchone()["c"],
        "employees": db.execute("SELECT COUNT(*) c FROM employees WHERE is_active=1").fetchone()["c"],
        "roles": db.execute("SELECT COUNT(*) c FROM roles WHERE is_active=1").fetchone()["c"],
        "departments": db.execute("SELECT COUNT(*) c FROM departments WHERE is_active=1").fetchone()["c"],
        "jobs": db.execute("SELECT COUNT(*) c FROM jobs WHERE is_active=1").fetchone()["c"],
        "branches": db.execute("SELECT COUNT(*) c FROM branches WHERE is_active=1").fetchone()["c"],
        "approvals": db.execute("SELECT COUNT(*) c FROM approval_requests WHERE status='PENDING'").fetchone()["c"],
        "tasks": db.execute("SELECT COUNT(*) c FROM tasks WHERE status NOT IN ('COMPLETED','CANCELLED')").fetchone()["c"],
        "notifications": db.execute("SELECT COUNT(*) c FROM notifications WHERE is_read=0").fetchone()["c"],
    }
    recent_audit=db.execute("""SELECT a.*,COALESCE(u.full_name,'النظام') user_name
        FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 8""").fetchall()
    return render_template("system_management.html", stats=stats, recent_audit=recent_audit)

@app.route("/code-generator", methods=["GET","POST"])
@login_required
@permission_required("manage_code_generator")
def code_generator() -> Any:
    db=get_db()
    if request.method=="POST":
        for row in db.execute("SELECT entity_key FROM code_sequences").fetchall():
            key=row["entity_key"]; prefix=request.form.get(f"prefix_{key}","").strip().upper(); padding=request.form.get(f"padding_{key}",type=int) or 1
            if prefix: db.execute("UPDATE code_sequences SET prefix=?,padding=?,updated_at=? WHERE entity_key=?",(prefix,max(1,min(padding,12)),now(),key))
        db.commit(); audit("تعديل مولد الأكواد","تحديث البادئات والخانات"); flash("تم حفظ إعدادات الأكواد.","success"); return redirect(url_for("code_generator"))
    rows=db.execute("SELECT * FROM code_sequences ORDER BY label").fetchall()
    return render_template("code_generator.html",rows=rows)

@app.get("/events")
@login_required
@permission_required("view_event_history")
def event_history_page() -> Any:
    rows=get_db().execute("""SELECT e.*,COALESCE(u.full_name,'النظام') user_name,COALESCE(b.name,'-') branch_name
        FROM event_history e LEFT JOIN users u ON u.id=e.user_id LEFT JOIN branches b ON b.id=e.branch_id
        ORDER BY e.id DESC LIMIT 500""").fetchall()
    return render_template("event_history.html",rows=rows)

@app.get("/errors")
@login_required
@permission_required("view_error_center")
def error_center_page() -> Any:
    rows=get_db().execute("""SELECT e.*,COALESCE(u.full_name,'النظام') user_name
        FROM error_log e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.resolved ASC,e.id DESC LIMIT 500""").fetchall()
    return render_template("error_center.html",rows=rows)

@app.post("/errors/<int:error_id>/resolve")
@login_required
@permission_required("view_error_center")
def resolve_error(error_id:int) -> Any:
    user=current_user(); get_db().execute("UPDATE error_log SET resolved=1,resolved_by=?,resolved_at=? WHERE id=?",(user["id"],now(),error_id)); get_db().commit()
    audit("إغلاق خطأ مسجل",f"Error #{error_id}",action_code="ERROR_RESOLVED",entity_type="error_log",entity_id=error_id)
    flash("تم تعليم الخطأ كمحلول.","success"); return redirect(url_for("error_center_page"))

@app.get("/timeline/<entity_type>/<int:entity_id>")
@login_required
def activity_timeline_page(entity_type:str,entity_id:int) -> Any:
    rows=get_db().execute("""SELECT t.*,COALESCE(u.full_name,'النظام') user_name,COALESCE(b.name,'-') branch_name
        FROM activity_timeline t LEFT JOIN users u ON u.id=t.user_id LEFT JOIN branches b ON b.id=t.branch_id
        WHERE t.entity_type=? AND t.entity_id=? ORDER BY t.id""",(entity_type,entity_id)).fetchall()
    return render_template("activity_timeline.html",rows=rows,entity_type=entity_type,entity_id=entity_id)

@app.post("/dashboard/notes/add")
@login_required
def dashboard_add_note() -> Any:
    text = request.form.get("note_text", "").strip()
    if not text:
        flash("اكتب الملاحظة أولاً.", "danger")
        return redirect(url_for("dashboard"))
    user = current_user(); db = get_db()
    db.execute("INSERT INTO quick_notes(note_text,user_id,location_id,created_at) VALUES(?,?,?,?)",
               (text, user["id"], user["branch_id"], now()))
    db.commit(); flash("تم حفظ الملاحظة السريعة.", "success")
    return redirect(url_for("dashboard"))

@app.post("/dashboard/notes/<int:note_id>/update")
@login_required
def dashboard_update_note(note_id: int) -> Any:
    text = request.form.get("note_text", "").strip(); user = current_user(); db = get_db()
    if not text:
        flash("لا يمكن حفظ ملاحظة فارغة.", "danger")
        return redirect(url_for("dashboard"))
    cur = db.execute("UPDATE quick_notes SET note_text=?,updated_at=? WHERE id=? AND user_id=?",
                     (text, now(), note_id, user["id"]))
    db.commit(); flash("تم تعديل الملاحظة." if cur.rowcount else "الملاحظة غير موجودة.", "success" if cur.rowcount else "danger")
    return redirect(url_for("dashboard"))

@app.post("/dashboard/notes/<int:note_id>/delete")
@login_required
def dashboard_delete_note(note_id: int) -> Any:
    user = current_user(); db = get_db()
    db.execute("DELETE FROM quick_notes WHERE id=? AND user_id=?", (note_id, user["id"]))
    db.commit(); flash("تم حذف الملاحظة.", "success")
    return redirect(url_for("dashboard"))

@app.route("/search")
@login_required
def global_search() -> Any:
    db = get_db()
    user = current_user()
    query = request.args.get("q", "").strip()
    results: list[dict[str, Any]] = []
    if len(query) < 2:
        return render_template("search_results.html", query=query, results=results)

    like = f"%{query}%"
    branch_id = user["branch_id"] if user and user["role"] != "admin" else None

    # الموردون
    if has_permission("view_suppliers"):
        rows = db.execute(
            "SELECT id,name,phone FROM suppliers WHERE name LIKE ? OR COALESCE(phone,'') LIKE ? ORDER BY name LIMIT 20",
            (like, like),
        ).fetchall()
        for row in rows:
            results.append({"group":"الموردون","title":row["name"],"subtitle":row["phone"] or "مورد","url":url_for("suppliers")})

    # المواقع
    if has_permission("manage_locations") or (user and user["role"] == "admin"):
        rows = db.execute(
            "SELECT id,name,code,location_type FROM branches WHERE name LIKE ? OR COALESCE(code,'') LIKE ? ORDER BY name LIMIT 20",
            (like, like),
        ).fetchall()
        for row in rows:
            kind = "مخزن رئيسي" if row["location_type"] == "MAIN_WAREHOUSE" else "فرع"
            results.append({"group":"المواقع","title":row["name"],"subtitle":f"{kind} — {row['code'] or 'بدون كود'}","url":url_for("locations")})

    # المستخدمون
    if has_permission("manage_users"):
        rows = db.execute(
            "SELECT id,username,full_name FROM users WHERE username LIKE ? OR full_name LIKE ? ORDER BY full_name LIMIT 20",
            (like, like),
        ).fetchall()
        for row in rows:
            target = url_for("edit_user", user_id=row["id"]) if has_permission("edit_users") else url_for("users")
            results.append({"group":"المستخدمون","title":row["full_name"],"subtitle":f"اسم الدخول: {row['username']}","url":target})

    # تحويلات الفواتير
    if has_permission("view_invoice_transfers"):
        sql = """SELECT t.id,t.invoice_number,t.transfer_number,t.supplier_name,t.status,f.name from_name,d.name to_name
                 FROM invoice_transfers t JOIN branches f ON f.id=t.from_location_id JOIN branches d ON d.id=t.to_location_id
                 WHERE (t.invoice_number LIKE ? OR t.transfer_number LIKE ? OR COALESCE(t.supplier_name,'') LIKE ? OR f.name LIKE ? OR d.name LIKE ?)"""
        params: list[Any] = [like, like, like, like, like]
        if branch_id:
            sql += " AND (t.from_location_id=? OR t.to_location_id=?)"
            params += [branch_id, branch_id]
        sql += " ORDER BY t.id DESC LIMIT 30"
        for row in db.execute(sql, params).fetchall():
            results.append({"group":"تحويلات الفواتير","title":f"فاتورة {row['invoice_number']}","subtitle":f"{row['from_name']} ← {row['to_name']} — {row['status']}","url":url_for("invoice_transfer_detail", transfer_id=row["id"])})

    if has_permission("view_external_debts"):
        sql="""SELECT d.id,d.reference_no,d.party_name,d.party_type,d.debt_type,d.amount,d.due_date,b.name branch_name FROM external_debts d JOIN branches b ON b.id=d.branch_id WHERE (d.reference_no LIKE ? OR d.party_name LIKE ? OR COALESCE(d.phone,'') LIKE ? OR COALESCE(d.notes,'') LIKE ?)"""
        qparams=[like,like,like,like]
        if branch_id: sql+=" AND d.branch_id=?"; qparams.append(branch_id)
        sql+=" ORDER BY d.id DESC LIMIT 30"
        for row in db.execute(sql,qparams).fetchall():
            results.append({"group":"الديون الخارجية","title":f"{row['reference_no']} — {row['party_name']}","subtitle":f"{DEBT_TYPES.get(row['debt_type'])} — {float(row['amount']):,.2f} — {row['branch_name']}","url":url_for("external_debt_detail",debt_id=row["id"])})

    # الإيرادات والمصروفات والسدادات: استعلام UNION واحد بدل ثلاث رحلات منفصلة.
    financial_parts: list[str] = []
    financial_params: list[Any] = []
    branch_sql = " AND branch_id=?" if branch_id else ""

    financial_specs = [
        ("الإيرادات", "revenues", "revenue_date", "إيراد", "revenues", has_permission("view_revenue")),
        ("المصروفات", "expenses", "expense_date", "مصروف", "expenses", has_permission("view_expenses")),
        ("السدادات", "supplier_payments", "payment_date", "سداد مورد", "payments", has_permission("view_suppliers")),
    ]
    for group, table, date_col, label, endpoint, allowed in financial_specs:
        if not allowed:
            continue
        financial_parts.append(
            f"SELECT id,branch_id,amount,{date_col} AS tx_date,notes,"
            "? AS result_group,? AS result_label,? AS result_endpoint "
            f"FROM {table} WHERE (CAST(id AS TEXT) LIKE ? OR CAST(amount AS TEXT) LIKE ? OR COALESCE(notes,'') LIKE ?)"
            + branch_sql
            + " ORDER BY id DESC LIMIT 20"
        )
        financial_params.extend([group, label, endpoint, like, like, like])
        if branch_id:
            financial_params.append(branch_id)

    if financial_parts:
        union_sql = " UNION ALL ".join(f"SELECT * FROM ({part}) AS financial_search" for part in financial_parts)
        for row in db.execute(union_sql, financial_params).fetchall():
            results.append({
                "group": row["result_group"],
                "title": f"{row['result_label']} #{row['id']} — {float(row['amount']):,.2f}",
                "subtitle": f"التاريخ: {row['tx_date']} — {row['notes'] or 'بدون ملاحظات'}",
                "url": url_for(row["result_endpoint"]),
            })

    return render_template("search_results.html", query=query, results=results)

@app.route("/invoice-transfers", methods=["GET", "POST"])
@login_required
@permission_required("view_invoice_transfers")
def invoice_transfers() -> Any:
    db=get_db(); user=current_user()
    if request.method=="POST":
        if not has_permission("manage_invoice_transfers"):
            flash("ليس لديك صلاحية إدارة تحويلات الفواتير.","danger"); return redirect(url_for("invoice_transfers"))
        try:
            from_location=int(request.form["from_location_id"]); to_location=int(request.form["to_location_id"])
            if from_location==to_location: raise ValueError("يجب اختيار موقعين مختلفين.")
            invoice_number=request.form.get("invoice_number","").strip()
            if not invoice_number: raise ValueError("رقم الفاتورة مطلوب.")
            total_amount=float(request.form.get("total_amount") or 0)
            if total_amount<0: raise ValueError("قيمة الفاتورة غير صحيحة.")
            invoice_date=request.form.get("invoice_date") or datetime.now().date().isoformat()
            transfer_date=request.form.get("transfer_date") or datetime.now().date().isoformat()
            transfer_id = insert_and_get_id(
                db,
                """INSERT INTO invoice_transfers(transfer_number,invoice_number,supplier_name,invoice_date,total_amount,from_location_id,to_location_id,transfer_date,status,notes,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (f"INVTRF-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",invoice_number,request.form.get("supplier_name","").strip(),invoice_date,total_amount,from_location,to_location,transfer_date,"DRAFT",request.form.get("notes","").strip(),user["id"],now()))
            db.commit(); audit("إنشاء تحويل فاتورة",f"الفاتورة {invoice_number}"); flash("تم إنشاء تحويل الفاتورة كمسودة.","success")
            return redirect(url_for("invoice_transfer_detail",transfer_id=transfer_id))
        except (ValueError,sqlite3.Error) as exc:
            db.rollback(); flash(str(exc),"danger")
    rows=db.execute("""SELECT t.*,f.name from_name,d.name to_name,u.full_name creator
                       FROM invoice_transfers t JOIN branches f ON f.id=t.from_location_id JOIN branches d ON d.id=t.to_location_id
                       JOIN users u ON u.id=t.created_by ORDER BY t.id DESC""").fetchall()
    locations=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY CASE location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,id").fetchall()
    today=datetime.now().date().isoformat()
    return render_template("invoice_transfers.html",rows=rows,locations=locations,today=today)

@app.route("/invoice-transfers/<int:transfer_id>")
@login_required
@permission_required("view_invoice_transfers")
def invoice_transfer_detail(transfer_id:int)->Any:
    db=get_db()
    transfer=db.execute("""SELECT t.*,f.name from_name,d.name to_name,u.full_name creator,
                          su.full_name sender,ru.full_name receiver
                          FROM invoice_transfers t JOIN branches f ON f.id=t.from_location_id JOIN branches d ON d.id=t.to_location_id
                          JOIN users u ON u.id=t.created_by LEFT JOIN users su ON su.id=t.sent_by LEFT JOIN users ru ON ru.id=t.received_by
                          WHERE t.id=?""",(transfer_id,)).fetchone()
    if not transfer:
        flash("تحويل الفاتورة غير موجود.","danger"); return redirect(url_for("invoice_transfers"))
    return render_template("invoice_transfer_detail.html",transfer=transfer)

@app.post("/invoice-transfers/<int:transfer_id>/send")
@login_required
@permission_required("manage_invoice_transfers")
def send_invoice_transfer(transfer_id:int)->Any:
    db=get_db(); user=current_user(); t=db.execute("SELECT * FROM invoice_transfers WHERE id=?",(transfer_id,)).fetchone()
    if not t or t["status"]!="DRAFT":
        flash("لا يمكن إرسال هذا التحويل.","danger"); return redirect(url_for("invoice_transfer_detail",transfer_id=transfer_id))
    db.execute("UPDATE invoice_transfers SET status='SENT',sent_by=?,sent_at=? WHERE id=?",(user["id"],now(),transfer_id))
    source=db.execute("SELECT name FROM branches WHERE id=?",(t["from_location_id"],)).fetchone()
    title=f"فاتورة جديدة {t['invoice_number']}"
    message=f"وصلت فاتورة جاهزة من {source['name'] if source else 'موقع آخر'} بقيمة {float(t['total_amount']):,.2f}."
    create_notification(title,message,location_id=t["to_location_id"],notification_type="INFO",priority="HIGH",reference_type="invoice_transfer",reference_id=transfer_id)
    create_task(f"استلام الفاتورة {t['invoice_number']}",message,location_id=t["to_location_id"],task_type="RECEIVE_INVOICE",reference_type="invoice_transfer",reference_id=transfer_id,priority="HIGH")
    db.commit()
    audit("إرسال فاتورة بين المواقع",t["invoice_number"]); flash("تم إرسال الفاتورة وإنشاء إشعار ومهمة للموقع المستلم.","success")
    return redirect(url_for("invoice_transfer_detail",transfer_id=transfer_id))

@app.post("/invoice-transfers/<int:transfer_id>/receive")
@login_required
@permission_required("manage_invoice_transfers")
def receive_invoice_transfer(transfer_id:int)->Any:
    db=get_db(); user=current_user(); t=db.execute("SELECT * FROM invoice_transfers WHERE id=?",(transfer_id,)).fetchone()
    if not t or t["status"]!="SENT":
        flash("لا يمكن استلام هذا التحويل.","danger"); return redirect(url_for("invoice_transfer_detail",transfer_id=transfer_id))
    db.execute("UPDATE invoice_transfers SET status='RECEIVED',received_by=?,received_at=? WHERE id=?",(user["id"],now(),transfer_id))
    db.execute("UPDATE tasks SET status='COMPLETED',completed_by=?,completed_at=? WHERE reference_type='invoice_transfer' AND reference_id=? AND task_type='RECEIVE_INVOICE' AND status='OPEN'",(user["id"],now(),transfer_id))
    destination=db.execute("SELECT name FROM branches WHERE id=?",(t["to_location_id"],)).fetchone()
    create_notification(f"تم استلام الفاتورة {t['invoice_number']}",f"تم استلام الفاتورة بواسطة {destination['name'] if destination else 'الموقع المستلم'}.",location_id=t["from_location_id"],notification_type="SUCCESS",reference_type="invoice_transfer",reference_id=transfer_id)
    db.commit()
    audit("استلام فاتورة بين المواقع",t["invoice_number"]); flash("تم تأكيد الاستلام وإكمال المهمة وإشعار الجهة المرسلة.","success")
    return redirect(url_for("invoice_transfer_detail",transfer_id=transfer_id))

@app.post("/invoice-transfers/<int:transfer_id>/cancel")
@login_required
@permission_required("manage_invoice_transfers")
def cancel_invoice_transfer(transfer_id:int)->Any:
    db=get_db(); t=db.execute("SELECT * FROM invoice_transfers WHERE id=?",(transfer_id,)).fetchone()
    if t and t["status"]=="DRAFT":
        db.execute("UPDATE invoice_transfers SET status='CANCELLED' WHERE id=?",(transfer_id,)); db.commit(); audit("إلغاء تحويل فاتورة",t["invoice_number"]); flash("تم إلغاء التحويل.","success")
    else: flash("يمكن إلغاء المسودات فقط.","danger")
    return redirect(url_for("invoice_transfers"))

@app.get("/approval-definitions")
@login_required
@permission_required("manage_approvals")
def approval_definitions_page() -> Any:
    rows = get_db().execute("SELECT * FROM approval_definitions ORDER BY name").fetchall()
    return render_template("approval_definitions.html", rows=rows)

