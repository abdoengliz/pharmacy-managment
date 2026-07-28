from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the finance area."""

@app.post("/day-closing")
@login_required
@permission_required("manage_day_closing")
def day_closing() -> Any:
    db = get_db()
    user = current_user()
    branch_id = request.form.get("branch_id", type=int)
    closing_date = request.form.get("closing_date", "")
    action = request.form.get("action", "close")
    notes = request.form.get("notes", "").strip()
    branch = db.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    try:
        datetime.strptime(closing_date, "%Y-%m-%d")
    except ValueError:
        flash("التاريخ غير صالح.", "danger")
        return redirect(url_for("dashboard", period="today"))
    if not branch:
        flash("الفرع غير موجود.", "danger")
        return redirect(url_for("dashboard", period="today"))
    if action == "reopen":
        db.execute(
            """INSERT INTO day_closings(branch_id, closing_date, is_closed, closed_by, closed_at, reopened_by, reopened_at, notes)
               VALUES(?,?,0,?,?,?,?,?)
               ON CONFLICT(branch_id, closing_date) DO UPDATE SET
               is_closed=0, reopened_by=excluded.reopened_by, reopened_at=excluded.reopened_at, notes=excluded.notes""",
            (branch_id, closing_date, user["id"], now(), user["id"], now(), notes),
        )
        db.commit()
        audit("إعادة فتح يوم", f"{branch['name']} — {closing_date}")
        flash("تمت إعادة فتح اليوم ويمكن تعديل حركاته.", "success")
    else:
        db.execute(
            """INSERT INTO day_closings(branch_id, closing_date, is_closed, closed_by, closed_at, notes)
               VALUES(?,?,1,?,?,?)
               ON CONFLICT(branch_id, closing_date) DO UPDATE SET
               is_closed=1, closed_by=excluded.closed_by, closed_at=excluded.closed_at, notes=excluded.notes""",
            (branch_id, closing_date, user["id"], now(), notes),
        )
        db.commit()
        audit("إقفال يوم", f"{branch['name']} — {closing_date}")
        flash("تم إقفال اليوم ومنع التعديل على حركاته.", "success")
    return redirect(url_for("dashboard", period="today"))

@app.route("/cashbox", methods=["GET", "POST"])
@login_required
@permission_required("view_cashbox")
def cashbox() -> Any:
    db=get_db(); user=current_user(); today=datetime.now().date().isoformat()
    branches=db.execute("SELECT * FROM branches ORDER BY id").fetchall()
    if user and user["role"]!="admin" and user["branch_id"]: branches=[b for b in branches if b["id"]==user["branch_id"]]
    selected_branch=request.values.get("branch_id",type=int) or (branches[0]["id"] if branches else None)
    selected_date=request.values.get("date",today)
    accounts=db.execute("SELECT * FROM financial_accounts WHERE branch_id=? ORDER BY is_active DESC,account_type,name",(selected_branch,)).fetchall() if selected_branch else []
    selected_account=request.values.get("account_id",type=int) or (accounts[0]["id"] if accounts else None)
    if request.method=="POST":
        if not has_permission("manage_cashbox"): flash("ليس لديك صلاحية إدارة الرصيد الافتتاحي.","danger"); return redirect(url_for("cashbox",branch_id=selected_branch,date=selected_date,account_id=selected_account))
        if reject_if_day_closed(selected_branch,selected_date): return redirect(url_for("cashbox",branch_id=selected_branch,date=selected_date,account_id=selected_account))
        amount=float(request.form.get("opening_amount",0)); notes=request.form.get("notes","").strip()
        # الرصيد الافتتاحي يسجل كحركة تسوية مستقلة للحساب
        ref_id=selected_account*100000000+int(selected_date.replace('-',''))
        existing=db.execute("SELECT id FROM financial_ledger WHERE reference_type='account_opening' AND reference_id=?",(ref_id,)).fetchone()
        if amount>0:
            sync_ledger("account_opening",ref_id,selected_branch,selected_account,"OPENING_BALANCE","IN",amount,selected_date,notes,user["id"])
        elif existing: delete_ledger("account_opening",ref_id)
        db.commit(); audit("تحديث رصيد افتتاح حساب",f"الحساب: {selected_account}، التاريخ: {selected_date}، القيمة: {amount}"); flash("تم حفظ الرصيد الافتتاحي للحساب.","success")
        return redirect(url_for("cashbox",branch_id=selected_branch,date=selected_date,account_id=selected_account))
    rows=db.execute("""SELECT l.*,a.name account_name FROM financial_ledger l JOIN financial_accounts a ON a.id=l.account_id
                       WHERE l.account_id=? AND l.transaction_date=? ORDER BY l.created_at,l.id""",(selected_account,selected_date)).fetchall() if selected_account else []
    balance=0; ledger=[]; total_incoming=total_outgoing=0
    labels={'REVENUE':'إيراد','EXPENSE':'مصروف','SUPPLIER_PAYMENT':'سداد مورد','OPENING_BALANCE':'رصيد افتتاحي'}
    for r in rows:
        d=dict(r); incoming=r['amount'] if r['direction']=='IN' else 0; outgoing=r['amount'] if r['direction']=='OUT' else 0
        total_incoming+=incoming; total_outgoing+=outgoing; balance+=incoming-outgoing
        d.update(incoming=incoming,outgoing=outgoing,balance=balance,movement_type=labels.get(r['transaction_type'],r['transaction_type']),method=r['account_name'],movement_time=r['created_at']) ; ledger.append(d)
    summaries=db.execute("""SELECT a.id,a.name,a.account_type,a.is_active,COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
                            FROM financial_accounts a LEFT JOIN financial_ledger l ON l.account_id=a.id
                            WHERE a.branch_id=? GROUP BY a.id ORDER BY a.account_type,a.name""",(selected_branch,)).fetchall() if selected_branch else []
    return render_template("cashbox.html",branches=branches,selected_branch=selected_branch,selected_date=selected_date,accounts=accounts,selected_account=selected_account,ledger=ledger,total_incoming=total_incoming,total_outgoing=total_outgoing,closing_balance=balance,summary_rows=summaries,opening_amount=next((r['amount'] for r in rows if r['transaction_type']=='OPENING_BALANCE'),0))

@app.route("/revenues", methods=["GET", "POST"])
@login_required
@permission_required("view_revenue")
def revenues() -> Any:
    db = get_db()
    user = current_user()
    if request.method == "POST":
        if not has_permission("add_revenue"):
            flash("ليس لديك صلاحية إضافة إيراد.", "danger")
            return redirect(url_for("revenues"))
        branch_id = int(request.form["branch_id"])
        if user["role"] != "admin" and user["branch_id"] and branch_id != user["branch_id"]:
            flash("لا يمكنك الإضافة لفرع آخر.", "danger")
            return redirect(url_for("revenues"))
        amount = float(request.form["amount"])
        invoice_count = int(request.form.get("invoice_count", 0))
        revenue_date = request.form["revenue_date"]
        if amount < 0 or invoice_count < 0:
            flash("القيمة وعدد الفواتير يجب ألا يكونا بالسالب.", "danger")
            return redirect(url_for("revenues"))
        if reject_if_day_closed(branch_id, revenue_date):
            return redirect(url_for("revenues"))
        notes = request.form.get("notes", "").strip()
        try:
            splits = parse_account_splits(request.form, branch_id, amount)
            employee_splits = _parse_employee_revenue_splits(
                request.form, db, branch_id, revenue_date, amount, invoice_count
            )
            account_names = [db.execute("SELECT name FROM financial_accounts WHERE id=?", (a,)).fetchone()["name"] for a, _ in splits]
            method = " + ".join(account_names)
            primary_account = splits[0][0]
            revenue_id = insert_and_get_id(
                db,
                """INSERT INTO revenues(branch_id,amount,invoice_count,revenue_date,payment_method,notes,created_by,created_at,account_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (branch_id, amount, invoice_count, revenue_date, method, notes, user["id"], now(), primary_account),
            )
            revenue_id = int(revenue_id)
            sync_ledger_splits("revenues", revenue_id, branch_id, "REVENUE", "IN", amount, revenue_date, notes, user["id"], splits)
            _save_employee_revenue_splits(db, revenue_id, employee_splits)
        except (ValueError, TypeError, sqlite3.Error) as exc:
            db.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("revenues"))
        db.commit()
        audit("إضافة إيراد", f"القيمة: {amount}، الفواتير: {invoice_count}، الفرع: {branch_id}")
        flash("تمت إضافة الإيراد وتوزيعه على الموظفين.", "success")
        return redirect(url_for("revenues"))

    extra, params = branch_filter_sql("r")
    rows = db.execute(
        """
        SELECT r.*, b.name branch_name, u.full_name creator,
               CASE WHEN r.invoice_count>0 THEN r.amount/r.invoice_count ELSE 0 END average_invoice
        FROM revenues r JOIN branches b ON b.id=r.branch_id JOIN users u ON u.id=r.created_by
        WHERE 1=1
        """ + extra + " ORDER BY r.revenue_date DESC, r.id DESC LIMIT 200",
        params,
    ).fetchall()
    branches = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall()
    accounts = db.execute("SELECT a.*, b.name branch_name FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.is_active=1 ORDER BY b.id,a.name").fetchall()
    employees = db.execute(
        """SELECT id,employee_no,full_name,branch_id FROM employees
           WHERE is_active=1 AND employment_status='active' ORDER BY branch_id,full_name"""
    ).fetchall()
    return render_template(
        "revenues.html", rows=rows, branches=branches, accounts=accounts,
        employees=employees, today=datetime.now().date().isoformat()
    )

@app.get("/financial-balances")
@login_required
@permission_required("view_cashbox")
def financial_balances() -> Any:
    db = get_db()
    location_clause, params = _allowed_location_clause("b")
    account_rows = db.execute(
        """SELECT b.id location_id,b.name location_name,b.location_type,
                  a.id account_id,a.name account_name,a.account_type,a.is_active,
                  COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM branches b
           JOIN financial_accounts a ON a.branch_id=b.id
           LEFT JOIN financial_ledger l ON l.account_id=a.id
           WHERE b.is_active=1""" + location_clause + """
           GROUP BY b.id,b.name,b.location_type,a.id
           ORDER BY CASE b.location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,b.name,a.account_type,a.name""",
        params,
    ).fetchall()

    locations: dict[int, dict[str, Any]] = {}
    type_totals = {key: 0.0 for key in ACCOUNT_TYPE_LABELS}
    grand_total = 0.0
    for row in account_rows:
        location = locations.setdefault(row["location_id"], {
            "id": row["location_id"],
            "name": row["location_name"],
            "location_type": row["location_type"],
            "accounts": [],
            "type_totals": {key: 0.0 for key in ACCOUNT_TYPE_LABELS},
            "total": 0.0,
        })
        balance = float(row["balance"] or 0)
        item = dict(row)
        item["type_label"] = ACCOUNT_TYPE_LABELS.get(row["account_type"], row["account_type"])
        item["balance"] = balance
        location["accounts"].append(item)
        location["type_totals"].setdefault(row["account_type"], 0.0)
        location["type_totals"][row["account_type"]] += balance
        location["total"] += balance
        type_totals.setdefault(row["account_type"], 0.0)
        type_totals[row["account_type"]] += balance
        grand_total += balance

    return render_template(
        "financial_balances.html",
        locations=list(locations.values()),
        type_totals=type_totals,
        type_labels=ACCOUNT_TYPE_LABELS,
        grand_total=grand_total,
    )

@app.get("/financial-balances/location/<int:location_id>")
@login_required
@permission_required("view_cashbox")
def location_financial_balance(location_id: int) -> Any:
    db = get_db()
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"] != location_id:
        flash("لا يمكنك عرض خزينة موقع آخر.", "danger")
        return redirect(url_for("financial_balances"))
    location = db.execute("SELECT * FROM branches WHERE id=?", (location_id,)).fetchone()
    if not location:
        flash("الموقع غير موجود.", "danger")
        return redirect(url_for("financial_balances"))
    accounts = db.execute(
        """SELECT a.*,COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM financial_accounts a LEFT JOIN financial_ledger l ON l.account_id=a.id
           WHERE a.branch_id=? GROUP BY a.id ORDER BY a.account_type,a.name""",
        (location_id,),
    ).fetchall()
    rows=[]
    type_totals={key:0.0 for key in ACCOUNT_TYPE_LABELS}
    total=0.0
    for account in accounts:
        item=dict(account); item["balance"]=float(account["balance"] or 0); item["type_label"]=ACCOUNT_TYPE_LABELS.get(account["account_type"],account["account_type"])
        rows.append(item); type_totals.setdefault(account["account_type"],0.0); type_totals[account["account_type"]]+=item["balance"]; total+=item["balance"]
    return render_template("location_financial_balance.html",location=location,accounts=rows,type_totals=type_totals,type_labels=ACCOUNT_TYPE_LABELS,total=total)

@app.get("/financial-accounts/<int:account_id>/statement")
@login_required
@permission_required("view_cashbox")
def account_statement(account_id:int)->Any:
    start_date,end_date,period=_statement_period()
    try: account,rows,opening,incoming,outgoing,closing=_account_statement_payload(account_id,start_date,end_date)
    except ValueError as exc:
        flash(str(exc),"danger"); return redirect(url_for("financial_balances"))
    return render_template("account_statement.html",account=account,rows=rows,opening_balance=opening,total_incoming=incoming,total_outgoing=outgoing,closing_balance=closing,start_date=start_date,end_date=end_date,period=period)

@app.get("/financial-accounts/<int:account_id>/statement/export/<file_format>")
@login_required
@permission_required("view_reports")
def export_account_statement(account_id:int,file_format:str)->Any:
    start_date,end_date,_=_statement_period()
    try: account,rows,opening,incoming,outgoing,closing=_account_statement_payload(account_id,start_date,end_date)
    except ValueError as exc:
        flash(str(exc),"danger"); return redirect(url_for("financial_balances"))
    columns=["التاريخ","العملية","الوارد","الصادر","الرصيد","الملاحظات"]
    report_rows=[[r["transaction_date"],r["movement_label"],r["incoming"],r["outgoing"],r["running_balance"],r.get("notes") or ""] for r in rows]
    title=f"كشف حساب {account['name']}"
    settings=all_settings(); metadata=[f"الموقع: {account['location_name']}",f"الفترة: من {start_date} إلى {end_date}",f"الرصيد قبل الفترة: {opening:.2f}",f"إجمالي الوارد: {incoming:.2f}",f"إجمالي الصادر: {outgoing:.2f}",f"الرصيد الختامي: {closing:.2f}",f"أصدره: {current_user()['full_name']}"]
    if file_format=="pdf": stream=build_pdf(title,settings.get("company_name","Pharma ERP"),settings.get("system_subtitle","الإدارة المالية"),columns,report_rows,metadata); mimetype="application/pdf"; ext="pdf"
    elif file_format=="docx": stream=build_docx(title,settings.get("company_name","Pharma ERP"),settings.get("system_subtitle","الإدارة المالية"),columns,report_rows,metadata); mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"; ext="docx"
    elif file_format=="xlsx": stream=build_excel(title,settings.get("company_name","Pharma ERP"),settings.get("system_subtitle","الإدارة المالية"),columns,report_rows,metadata); mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"; ext="xlsx"
    else:
        flash("صيغة التصدير غير مدعومة.","danger"); return redirect(url_for("account_statement",account_id=account_id,start_date=start_date,end_date=end_date,period="custom"))
    audit("تصدير كشف حساب",f"الحساب: {account['name']}، من {start_date} إلى {end_date}، الصيغة: {file_format}")
    return send_file(stream,mimetype=mimetype,as_attachment=True,download_name=f"account_statement_{account_id}_{start_date}_{end_date}.{ext}")

@app.route("/financial-accounts", methods=["GET", "POST"])
@login_required
@permission_required("view_cashbox")
def financial_accounts() -> Any:
    db=get_db(); user=current_user()
    if request.method=="POST":
        if not has_permission("manage_financial_accounts"):
            flash("ليس لديك صلاحية إدارة الحسابات المالية.","danger"); return redirect(url_for("financial_accounts"))
        branch_id=int(request.form["branch_id"])
        if user["role"]!="admin" and user["branch_id"] and branch_id!=user["branch_id"]:
            flash("لا يمكنك إضافة حساب لفرع آخر.","danger"); return redirect(url_for("financial_accounts"))
        try:
            db.execute("INSERT INTO financial_accounts(branch_id,name,account_type,is_active,notes,created_at) VALUES(?,?,?,?,?,?)",
                       (branch_id,request.form["name"].strip(),request.form["account_type"],1,request.form.get("notes","").strip(),now()))
            db.commit(); audit("إضافة حساب مالي",request.form["name"].strip()); flash("تمت إضافة الحساب المالي.","success")
        except sqlite3.IntegrityError: flash("اسم الحساب موجود في هذا الفرع.","danger")
        return redirect(url_for("financial_accounts"))
    extra,params=branch_filter_sql("a")
    rows=db.execute("""SELECT a.*,b.name branch_name,
        COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
        FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
        LEFT JOIN financial_ledger l ON l.account_id=a.id WHERE 1=1"""+extra+" GROUP BY a.id,b.id,b.name ORDER BY b.id,a.name",params).fetchall()
    branches=db.execute("SELECT * FROM branches ORDER BY id").fetchall()
    return render_template("financial_accounts.html",rows=rows,branches=branches)

def _financial_account_for_management(account_id: int):
    """Return an account only when the current user is allowed to manage its branch."""
    row = get_db().execute("SELECT * FROM financial_accounts WHERE id=?", (account_id,)).fetchone()
    user = current_user()
    if not row:
        return None
    if user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        return None
    return row


def _financial_account_usage(db, account_id: int) -> dict[str, int]:
    """Count every direct financial reference that makes permanent deletion unsafe."""
    checks = {
        "financial_ledger": ("account_id",),
        "revenues": ("account_id",),
        "expenses": ("account_id",),
        "supplier_payments": ("account_id",),
        "external_debts": ("account_id",),
        "external_debt_payments": ("account_id",),
        "treasury_transfers": ("from_account_id", "to_account_id"),
    }
    usage: dict[str, int] = {}
    for table, columns in checks.items():
        clauses = " OR ".join(f"{column}=?" for column in columns)
        params = tuple(account_id for _ in columns)
        try:
            count = int(db.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {clauses}", params).fetchone()["c"] or 0)
        except Exception:
            # Older installations may not have every optional table/column yet.
            count = 0
        if count:
            usage[table] = count
    return usage


@app.post("/financial-accounts/<int:account_id>/edit")
@login_required
@permission_required("manage_financial_accounts")
def edit_financial_account(account_id: int) -> Any:
    db = get_db()
    row = _financial_account_for_management(account_id)
    if not row:
        flash("الحساب المالي غير موجود أو لا تملك صلاحية تعديله.", "danger")
        return redirect(url_for("financial_accounts"))

    name = request.form.get("name", "").strip()
    account_type = request.form.get("account_type", "").strip().upper()
    notes = request.form.get("notes", "").strip()
    valid_types = {"CASH", "BANK", "WALLET", "CARD"}
    if not name:
        flash("اسم الحساب مطلوب.", "danger")
        return redirect(url_for("financial_accounts"))
    if account_type not in valid_types:
        flash("نوع الحساب غير صالح.", "danger")
        return redirect(url_for("financial_accounts"))

    try:
        db.execute(
            "UPDATE financial_accounts SET name=?, account_type=?, notes=? WHERE id=?",
            (name, account_type, notes, account_id),
        )
        db.commit()
        audit(
            "تعديل حساب مالي",
            f"{row['name']} ← {name}، النوع: {row['account_type']} ← {account_type}",
        )
        flash("تم تعديل الحساب المالي بنجاح.", "success")
    except sqlite3.IntegrityError:
        flash("يوجد حساب آخر بنفس الاسم في هذا الفرع.", "danger")
    return redirect(url_for("financial_accounts"))


@app.post("/financial-accounts/<int:account_id>/delete")
@login_required
@permission_required("manage_financial_accounts")
def delete_financial_account(account_id: int) -> Any:
    db = get_db()
    row = _financial_account_for_management(account_id)
    if not row:
        flash("الحساب المالي غير موجود أو لا تملك صلاحية حذفه.", "danger")
        return redirect(url_for("financial_accounts"))

    balance_row = db.execute(
        """SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) AS balance
           FROM financial_ledger WHERE account_id=?""",
        (account_id,),
    ).fetchone()
    balance = float(balance_row["balance"] or 0)
    usage = _financial_account_usage(db, account_id)
    if abs(balance) > 0.005 or usage:
        flash(
            "لا يمكن حذف هذا الحساب لأنه مرتبط برصيد أو حركات مالية. يمكنك إيقافه بدلًا من حذفه.",
            "danger",
        )
        return redirect(url_for("financial_accounts"))

    try:
        db.execute("DELETE FROM financial_accounts WHERE id=?", (account_id,))
        db.commit()
        audit("حذف حساب مالي", f"{row['name']} — حساب جديد غير مستخدم")
        flash("تم حذف الحساب المالي نهائيًا.", "success")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("لا يمكن حذف الحساب لأنه مرتبط ببيانات مالية أخرى.", "danger")
    return redirect(url_for("financial_accounts"))


@app.post("/financial-accounts/<int:account_id>/toggle")
@login_required
@permission_required("manage_financial_accounts")
def toggle_financial_account(account_id:int)->Any:
    db=get_db(); row=_financial_account_for_management(account_id)
    if row:
        db.execute("UPDATE financial_accounts SET is_active=CASE is_active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(account_id,)); db.commit(); audit("تغيير حالة حساب مالي",row["name"]); flash("تم تحديث حالة الحساب.","success")
    else:
        flash("الحساب المالي غير موجود أو لا تملك صلاحية إدارته.","danger")
    return redirect(url_for("financial_accounts"))

@app.route("/payments", methods=["GET", "POST"])
@login_required
@permission_required("view_suppliers")
def payments() -> Any:
    db=get_db(); user=current_user()
    if request.method=="POST":
        if not has_permission("add_payments"):
            flash("ليس لديك صلاحية تسجيل السدادات.","danger"); return redirect(url_for("payments"))
        supplier_account_id=int(request.form["supplier_account_id"])
        sa=db.execute("SELECT a.*,s.id supplier_id FROM supplier_location_accounts a JOIN suppliers s ON s.id=a.supplier_id WHERE a.id=? AND a.is_active=1",(supplier_account_id,)).fetchone()
        if not sa: flash("حساب المورد غير موجود.","danger"); return redirect(url_for("payments"))
        branch_id=sa["location_id"]
        if user["role"]!="admin" and user["branch_id"] and branch_id!=user["branch_id"]:
            flash("لا يمكنك التسجيل لموقع آخر.","danger"); return redirect(url_for("payments"))
        invoice_id=int(request.form.get("invoice_id") or 0) or None
        if invoice_id:
            inv=db.execute("SELECT * FROM supplier_invoices WHERE id=? AND supplier_account_id=?",(invoice_id,supplier_account_id)).fetchone()
            if not inv: flash("الفاتورة لا تتبع حساب المورد المختار.","danger"); return redirect(url_for("payments"))
        amount=float(request.form["amount"]); payment_date=request.form["payment_date"]
        if reject_if_day_closed(branch_id,payment_date): return redirect(url_for("payments"))
        notes=request.form.get("notes","").strip()
        try:
            splits=parse_account_splits(request.form,branch_id,amount)
            names=[db.execute("SELECT name FROM financial_accounts WHERE id=?",(a,)).fetchone()["name"] for a,_ in splits]
            payment_id = insert_and_get_id(
                db,
                """INSERT INTO supplier_payments(supplier_id,branch_id,amount,payment_date,payment_method,notes,created_by,created_at,account_id,supplier_account_id,invoice_id)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(sa["supplier_id"],branch_id,amount,payment_date," + ".join(names),notes,user["id"],now(),splits[0][0],supplier_account_id,invoice_id))
            sync_ledger_splits("supplier_payments",payment_id,branch_id,"SUPPLIER_PAYMENT","OUT",amount,payment_date,notes,user["id"],splits)
            db.commit(); audit("تسجيل سداد",f"حساب المورد: {supplier_account_id}، الفاتورة: {invoice_id or 'رصيد عام'}، القيمة: {amount}"); flash("تم تسجيل السداد على الموقع والفاتورة المختارة.","success")
        except (ValueError,TypeError,sqlite3.Error) as exc:
            db.rollback(); flash(str(exc),"danger")
        return redirect(url_for("payments"))
    extra,params=branch_filter_sql("p")
    rows=db.execute("""SELECT p.*,s.name supplier_name,b.name branch_name,u.full_name creator,i.invoice_number
        FROM supplier_payments p JOIN suppliers s ON s.id=p.supplier_id JOIN branches b ON b.id=p.branch_id JOIN users u ON u.id=p.created_by
        LEFT JOIN supplier_invoices i ON i.id=p.invoice_id WHERE 1=1"""+extra+" ORDER BY p.payment_date DESC,p.id DESC LIMIT 200",params).fetchall()
    clause=""; qparams=[]
    if user and user["role"]!="admin" and user["branch_id"]: clause=" AND a.location_id=?"; qparams=[user["branch_id"]]
    supplier_accounts=db.execute("""SELECT a.id,a.location_id,a.supplier_id,s.name supplier_name,b.name location_name,
        GREATEST(a.opening_due + COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_account_id=a.id),0)
          - COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_account_id=a.id),0),0) remaining
        FROM supplier_location_accounts a JOIN suppliers s ON s.id=a.supplier_id JOIN branches b ON b.id=a.location_id
        WHERE a.is_active=1"""+clause+" ORDER BY s.name,b.name",qparams).fetchall()
    invoices=db.execute("""SELECT i.id,i.supplier_account_id,i.invoice_number,i.amount,
        GREATEST(i.amount-COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.invoice_id=i.id),0),0) remaining
        FROM supplier_invoices i WHERE 1=1""" + (" AND i.branch_id=?" if qparams else "") + " ORDER BY i.invoice_date DESC,i.id DESC",qparams).fetchall()
    locations=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall()
    accounts=db.execute("SELECT a.*,b.name branch_name FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.is_active=1 ORDER BY b.id,a.name").fetchall()
    return render_template("payments.html",rows=rows,supplier_accounts=supplier_accounts,invoices=invoices,branches=locations,accounts=accounts,today=datetime.now().date().isoformat())

@app.route("/revenues/<int:revenue_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("edit_revenue")
def edit_revenue(revenue_id: int) -> Any:
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM revenues WHERE id=?", (revenue_id,)).fetchone()
    if not row:
        flash("الإيراد غير موجود.", "danger")
        return redirect(url_for("revenues"))
    if user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك تعديل بيانات فرع آخر.", "danger")
        return redirect(url_for("revenues"))
    if request.method == "POST":
        branch_id = int(request.form["branch_id"])
        amount = float(request.form["amount"])
        invoice_count = int(request.form.get("invoice_count", 0))
        revenue_date = request.form["revenue_date"]
        notes = request.form.get("notes", "").strip()
        if amount < 0 or invoice_count < 0:
            flash("القيمة وعدد الفواتير يجب ألا يكونا بالسالب.", "danger")
            return redirect(url_for("edit_revenue", revenue_id=revenue_id))
        if reject_if_day_closed(row["branch_id"], row["revenue_date"]) or reject_if_day_closed(branch_id, revenue_date):
            return redirect(url_for("revenues"))
        if user["role"] != "admin" and user["branch_id"] and branch_id != user["branch_id"]:
            flash("لا يمكنك نقل الإيراد إلى فرع آخر.", "danger")
            return redirect(url_for("revenues"))
        try:
            splits = parse_account_splits(request.form, branch_id, amount)
            employee_splits = _parse_employee_revenue_splits(
                request.form, db, branch_id, revenue_date, amount, invoice_count
            )
            names = [db.execute("SELECT name FROM financial_accounts WHERE id=?", (account_id,)).fetchone()["name"] for account_id, _ in splits]
            db.execute(
                """UPDATE revenues SET branch_id=?,amount=?,invoice_count=?,revenue_date=?,
                   payment_method=?,notes=?,account_id=? WHERE id=?""",
                (branch_id, amount, invoice_count, revenue_date, " + ".join(names), notes, splits[0][0], revenue_id),
            )
            sync_ledger_splits("revenues", revenue_id, branch_id, "REVENUE", "IN", amount, revenue_date, notes, user["id"], splits)
            _save_employee_revenue_splits(db, revenue_id, employee_splits)
            db.commit()
        except (ValueError, TypeError, sqlite3.Error) as exc:
            db.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("edit_revenue", revenue_id=revenue_id))
        audit("تعديل إيراد", f"رقم العملية: {revenue_id}، القيمة: {amount}، الفواتير: {invoice_count}")
        flash("تم تعديل الإيراد وتوزيع الموظفين.", "success")
        return redirect(url_for("revenues"))
    branches = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall()
    accounts = db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    employees = db.execute(
        """SELECT id,employee_no,full_name,branch_id FROM employees
           WHERE is_active=1 AND employment_status='active' ORDER BY branch_id,full_name"""
    ).fetchall()
    current_splits = db.execute(
        "SELECT account_id,amount FROM financial_ledger WHERE reference_type='revenues' AND reference_id=? ORDER BY id",
        (revenue_id,),
    ).fetchall()
    current_employee_splits = db.execute(
        """SELECT employee_id,amount,invoice_count,worked_hours
           FROM revenue_employee_splits WHERE revenue_id=? ORDER BY id""",
        (revenue_id,),
    ).fetchall()
    return render_template(
        "edit_revenue.html", row=row, branches=branches, accounts=accounts,
        employees=employees, current_splits=current_splits,
        current_employee_splits=current_employee_splits
    )

@app.post("/revenues/<int:revenue_id>/delete")
@login_required
@permission_required("delete_revenue")
def delete_revenue(revenue_id: int) -> Any:
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM revenues WHERE id=?", (revenue_id,)).fetchone()
    if not row:
        flash("الإيراد غير موجود.", "danger")
    elif user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك حذف بيانات فرع آخر.", "danger")
    elif reject_if_day_closed(row["branch_id"], row["revenue_date"]):
        pass
    else:
        ledger = [_row_dict(r) for r in db.execute("SELECT * FROM financial_ledger WHERE reference_type='revenues' AND reference_id=?", (revenue_id,)).fetchall()]
        move_to_trash("revenues", row, f"إيراد {row['amount']}", {"ledger": ledger}, request.form.get("reason", ""))
        delete_ledger("revenues", revenue_id)
        db.execute("DELETE FROM revenues WHERE id=?", (revenue_id,))
        db.commit()
        audit("نقل إيراد إلى سلة المحذوفات", f"رقم العملية: {revenue_id}، القيمة: {row['amount']}")
        flash("تم نقل الإيراد إلى سلة المحذوفات.", "success")
    return redirect(url_for("revenues"))

@app.route("/payments/<int:payment_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("add_payments")
def edit_payment(payment_id: int) -> Any:
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM supplier_payments WHERE id=?", (payment_id,)).fetchone()
    if not row:
        flash("السداد غير موجود.", "danger")
        return redirect(url_for("payments"))
    if user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك تعديل سدادات فرع آخر.", "danger")
        return redirect(url_for("payments"))
    if request.method == "POST":
        supplier_account_id = int(request.form["supplier_account_id"])
        supplier_account = db.execute(
            "SELECT * FROM supplier_location_accounts WHERE id=? AND is_active=1", (supplier_account_id,)
        ).fetchone()
        if not supplier_account:
            flash("حساب المورد غير موجود.", "danger")
            return redirect(url_for("edit_payment", payment_id=payment_id))
        branch_id = supplier_account["location_id"]
        amount = float(request.form["amount"])
        payment_date = request.form["payment_date"]
        notes = request.form.get("notes", "").strip()
        if reject_if_day_closed(row["branch_id"], row["payment_date"]) or reject_if_day_closed(branch_id, payment_date):
            return redirect(url_for("payments"))
        if user["role"] != "admin" and user["branch_id"] and branch_id != user["branch_id"]:
            flash("لا يمكنك نقل السداد إلى موقع آخر.", "danger")
            return redirect(url_for("payments"))
        try:
            splits = parse_account_splits(request.form, branch_id, amount)
            names = [db.execute("SELECT name FROM financial_accounts WHERE id=?", (account_id,)).fetchone()["name"] for account_id, _ in splits]
            db.execute(
                """UPDATE supplier_payments
                   SET supplier_id=?, branch_id=?, amount=?, payment_date=?, payment_method=?, notes=?, account_id=?, supplier_account_id=?
                   WHERE id=?""",
                (supplier_account["supplier_id"], branch_id, amount, payment_date, " + ".join(names), notes, splits[0][0], supplier_account_id, payment_id),
            )
            sync_ledger_splits("supplier_payments", payment_id, branch_id, "SUPPLIER_PAYMENT", "OUT", amount, payment_date, notes, user["id"], splits)
            db.commit()
        except (ValueError, TypeError, sqlite3.Error) as exc:
            db.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("edit_payment", payment_id=payment_id))
        audit("تعديل سداد", f"رقم العملية: {payment_id}، القيمة: {amount}")
        flash("تم تعديل السداد وتحديث دفتر الحركة المالية.", "success")
        return redirect(url_for("payments"))
    supplier_accounts = db.execute(
        """SELECT a.id,a.location_id,a.supplier_id,s.name supplier_name,b.name location_name
           FROM supplier_location_accounts a
           JOIN suppliers s ON s.id=a.supplier_id
           JOIN branches b ON b.id=a.location_id
           WHERE a.is_active=1 ORDER BY s.name,b.name"""
    ).fetchall()
    accounts = db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    current_splits = db.execute(
        "SELECT account_id,amount FROM financial_ledger WHERE reference_type='supplier_payments' AND reference_id=? ORDER BY id",
        (payment_id,),
    ).fetchall()
    return render_template("edit_payment.html", row=row, supplier_accounts=supplier_accounts, accounts=accounts, current_splits=current_splits)

@app.post("/payments/<int:payment_id>/delete")
@login_required
@permission_required("add_payments")
def delete_payment(payment_id: int) -> Any:
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM supplier_payments WHERE id=?", (payment_id,)).fetchone()
    if not row:
        flash("السداد غير موجود.", "danger")
    elif user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك حذف سدادات فرع آخر.", "danger")
    elif reject_if_day_closed(row["branch_id"], row["payment_date"]):
        pass
    else:
        ledger = [_row_dict(r) for r in db.execute("SELECT * FROM financial_ledger WHERE reference_type='supplier_payments' AND reference_id=?", (payment_id,)).fetchall()]
        move_to_trash("supplier_payments", row, f"سداد {row['amount']}", {"ledger": ledger}, request.form.get("reason", ""))
        delete_ledger("supplier_payments", payment_id)
        db.execute("DELETE FROM supplier_payments WHERE id=?", (payment_id,))
        db.commit()
        audit("نقل سداد إلى سلة المحذوفات", f"رقم العملية: {payment_id}، القيمة: {row['amount']}")
        flash("تم نقل السداد إلى سلة المحذوفات.", "success")
    return redirect(url_for("payments"))

@app.route("/expenses", methods=["GET", "POST"])
@login_required
@permission_required("view_expenses")
def expenses() -> Any:
    db = get_db()
    user = current_user()
    if request.method == "POST":
        if not has_permission("add_expense"):
            flash("ليس لديك صلاحية إضافة مصروف.", "danger")
            return redirect(url_for("expenses"))
        branch_id = int(request.form["branch_id"])
        if user["role"] != "admin" and user["branch_id"] and branch_id != user["branch_id"]:
            flash("لا يمكنك الإضافة لفرع آخر.", "danger")
            return redirect(url_for("expenses"))
        amount = float(request.form["amount"])
        if reject_if_day_closed(branch_id, request.form["expense_date"]):
            return redirect(url_for("expenses"))
        notes=request.form.get("notes", "").strip()
        available_classifications = load_financial_classifications()
        financial_classification = request.form.get("financial_classification", "OPERATING")
        if financial_classification not in available_classifications:
            financial_classification = "OPERATING"
        classification_id = financial_classification_id(financial_classification)
        asset_type = request.form.get("asset_type", "").strip() or None
        if financial_classification == "ASSET":
            if asset_type not in ASSET_TYPES:
                flash("يجب تحديد نوع أصل صحيح.", "danger")
                return redirect(url_for("expenses"))
        else:
            asset_type = None
        try:
            splits=parse_account_splits(request.form,branch_id,amount)
            account_names=[db.execute("SELECT name FROM financial_accounts WHERE id=?",(a,)).fetchone()["name"] for a,_ in splits]
            primary_account=splits[0][0]
            expense_id = insert_and_get_id(
                db,
                "INSERT INTO expenses(branch_id,amount,expense_date,category,financial_classification,classification_id,asset_type,payment_method,notes,created_by,created_at,account_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (branch_id, amount, request.form["expense_date"], request.form["category"].strip(),financial_classification,classification_id,asset_type," + ".join(account_names),notes,user["id"],now(),primary_account)
            )
            sync_ledger_splits("expenses",expense_id,branch_id,"EXPENSE","OUT",amount,request.form["expense_date"],notes,user["id"],splits)
        except (ValueError,TypeError,sqlite3.Error) as exc:
            db.rollback(); flash(str(exc),"danger"); return redirect(url_for("expenses"))
        db.commit()
        audit("إضافة مصروف", f"القيمة: {amount}، الفرع: {branch_id}")
        flash("تمت إضافة المصروف.", "success")
        return redirect(url_for("expenses"))
    extra, params = branch_filter_sql("e")
    rows = db.execute(
        """SELECT e.*, b.name branch_name, u.full_name creator
           FROM expenses e JOIN branches b ON b.id=e.branch_id JOIN users u ON u.id=e.created_by
           WHERE 1=1""" + extra + " ORDER BY e.expense_date DESC, e.id DESC LIMIT 200", params
    ).fetchall()
    branches = db.execute("SELECT * FROM branches ORDER BY id").fetchall()
    accounts=db.execute("SELECT a.*,b.name branch_name FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.is_active=1 ORDER BY b.id,a.name").fetchall()
    return render_template("expenses.html", rows=rows, branches=branches, accounts=accounts, today=datetime.now().date().isoformat(), financial_classifications=load_financial_classifications(), asset_types=ASSET_TYPES)

@app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("edit_expense")
def edit_expense(expense_id: int) -> Any:
    db = get_db()
    user = current_user()
    row = db.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    if not row:
        flash("المصروف غير موجود.", "danger")
        return redirect(url_for("expenses"))
    if user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك تعديل بيانات فرع آخر.", "danger")
        return redirect(url_for("expenses"))
    if request.method == "POST":
        branch_id = int(request.form["branch_id"])
        amount = float(request.form["amount"])
        expense_date = request.form["expense_date"]
        category = request.form["category"].strip()
        available_classifications = load_financial_classifications()
        financial_classification = request.form.get("financial_classification", "OPERATING")
        if financial_classification not in available_classifications:
            financial_classification = "OPERATING"
        classification_id = financial_classification_id(financial_classification)
        asset_type = request.form.get("asset_type", "").strip() or None
        if financial_classification == "ASSET":
            if asset_type not in ASSET_TYPES:
                flash("يجب تحديد نوع أصل صحيح.", "danger")
                return redirect(url_for("edit_expense", expense_id=expense_id))
        else:
            asset_type = None
        notes = request.form.get("notes", "").strip()
        if reject_if_day_closed(row["branch_id"], row["expense_date"]) or reject_if_day_closed(branch_id, expense_date):
            return redirect(url_for("expenses"))
        if user["role"] != "admin" and user["branch_id"] and branch_id != user["branch_id"]:
            flash("لا يمكنك نقل المصروف إلى فرع آخر.", "danger")
            return redirect(url_for("expenses"))
        try:
            splits = parse_account_splits(request.form, branch_id, amount)
            names = [db.execute("SELECT name FROM financial_accounts WHERE id=?", (account_id,)).fetchone()["name"] for account_id, _ in splits]
            db.execute(
                "UPDATE expenses SET branch_id=?,amount=?,expense_date=?,category=?,financial_classification=?,classification_id=?,asset_type=?,payment_method=?,notes=?,account_id=? WHERE id=?",
                (branch_id, amount, expense_date, category, financial_classification, classification_id, asset_type, " + ".join(names), notes, splits[0][0], expense_id),
            )
            sync_ledger_splits("expenses", expense_id, branch_id, "EXPENSE", "OUT", amount, expense_date, notes, user["id"], splits)
            db.commit()
        except (ValueError, TypeError, sqlite3.Error) as exc:
            db.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("edit_expense", expense_id=expense_id))
        audit("تعديل مصروف", f"رقم العملية: {expense_id}، القيمة: {amount}")
        flash("تم تعديل المصروف وتحديث دفتر الحركة المالية.", "success")
        return redirect(url_for("expenses"))
    branches = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall()
    accounts = db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    current_splits = db.execute(
        "SELECT account_id,amount FROM financial_ledger WHERE reference_type='expenses' AND reference_id=? ORDER BY id",
        (expense_id,),
    ).fetchall()
    return render_template("edit_expense.html", row=row, branches=branches, accounts=accounts, current_splits=current_splits, financial_classifications=load_financial_classifications(), asset_types=ASSET_TYPES)

@app.post("/expenses/<int:expense_id>/delete")
@login_required
@permission_required("delete_expense")
def delete_expense(expense_id: int) -> Any:
    db=get_db(); user=current_user(); row=db.execute("SELECT * FROM expenses WHERE id=?",(expense_id,)).fetchone()
    if not row: flash("المصروف غير موجود.", "danger")
    elif user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك حذف بيانات فرع آخر.", "danger")
    elif reject_if_day_closed(row["branch_id"], row["expense_date"]):
        pass
    else:
        ledger = [_row_dict(r) for r in db.execute("SELECT * FROM financial_ledger WHERE reference_type='expenses' AND reference_id=?", (expense_id,)).fetchall()]
        move_to_trash("expenses", row, f"مصروف {row['amount']}", {"ledger": ledger}, request.form.get("reason", ""))
        delete_ledger("expenses", expense_id)
        db.execute("DELETE FROM expenses WHERE id=?",(expense_id,)); db.commit()
        audit("نقل مصروف إلى سلة المحذوفات", f"رقم العملية: {expense_id}، القيمة: {row['amount']}")
        flash("تم نقل المصروف إلى سلة المحذوفات.", "success")
    return redirect(url_for("expenses"))

@app.route("/external-debts", methods=["GET", "POST"])
@login_required
@permission_required("view_external_debts")
def external_debts() -> Any:
    db=get_db(); user=current_user()
    if request.method=="POST":
        if not has_permission("add_external_debts"):
            flash("ليس لديك صلاحية إضافة دين خارجي.","danger"); return redirect(url_for("external_debts"))
        try:
            branch_id=int(request.form["branch_id"]); account_id=int(request.form["account_id"])
            if user["role"]!="admin" and user["branch_id"] and branch_id!=user["branch_id"]: raise ValueError("لا يمكنك الإضافة لموقع آخر.")
            account=db.execute("SELECT * FROM financial_accounts WHERE id=? AND branch_id=? AND is_active=1",(account_id,branch_id)).fetchone()
            if not account: raise ValueError("الحساب المالي غير صالح للموقع.")
            amount=float(request.form["amount"]); debt_date=request.form["debt_date"]; due_date=request.form.get("due_date") or None
            if reject_if_day_closed(branch_id,debt_date): return redirect(url_for("external_debts"))
            debt_type=request.form["debt_type"]
            if debt_type not in DEBT_TYPES: raise ValueError("نوع الدين غير صالح.")
            debt_id = insert_and_get_id(
                db,
                """INSERT INTO external_debts(reference_no,branch_id,party_name,party_type,debt_type,amount,account_id,debt_date,due_date,reason_type,reason_details,phone,notes,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(next_external_debt_reference(db),branch_id,request.form["party_name"].strip(),request.form["party_type"],debt_type,amount,account_id,debt_date,due_date,request.form.get("reason_type","OTHER"),request.form.get("reason_details","").strip(),request.form.get("phone","").strip(),request.form.get("notes","").strip(),user["id"],now()))
            direction="OUT" if debt_type=="RECEIVABLE" else "IN"
            sync_ledger("external_debts",debt_id,branch_id,account_id,"EXTERNAL_DEBT",direction,amount,debt_date,request.form.get("notes","").strip(),user["id"])
            audit("إضافة دين خارجي",f"{debt_id} - {amount}", commit=False)
            db.commit(); flash("تمت إضافة الدين الخارجي.","success")
        except (ValueError,TypeError,sqlite3.Error) as exc:
            db.rollback(); flash(str(exc),"danger")
        return redirect(url_for("external_debts"))
    # إنشاء تنبيهات ومهام استحقاق بدون تكرار
    due_rows=db.execute("""SELECT d.id,d.reference_no,d.party_name,d.branch_id,d.due_date,d.amount-COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id),0) remaining FROM external_debts d WHERE d.due_date IS NOT NULL""").fetchall()
    for due in due_rows:
        status,_=external_debt_status(due["due_date"],float(due["remaining"]))
        if status in {"DUE_SOON","OVERDUE"}:
            exists=db.execute("SELECT 1 FROM notifications WHERE reference_type='external_debt_due' AND reference_id=? AND message LIKE ? LIMIT 1",(due["id"],f"%{due['due_date']}%" )).fetchone()
            if not exists: create_notification("استحقاق دين خارجي",f"{due['reference_no']} — {due['party_name']} — الاستحقاق {due['due_date']}",location_id=due["branch_id"],notification_type="WARNING",priority="HIGH" if status=="OVERDUE" else "NORMAL",reference_type="external_debt_due",reference_id=due["id"])
            if status=="OVERDUE" and not db.execute("SELECT 1 FROM tasks WHERE task_type='FOLLOW_EXTERNAL_DEBT' AND reference_id=? AND status='OPEN'",(due["id"],)).fetchone():
                create_task("متابعة دين خارجي متأخر",f"{due['reference_no']} — {due['party_name']}",location_id=due["branch_id"],task_type="FOLLOW_EXTERNAL_DEBT",reference_type="external_debt",reference_id=due["id"],priority="HIGH")
    db.commit()
    scope=""; params=[]
    if user["role"]!="admin" and user["branch_id"]: scope=" WHERE d.branch_id=?"; params=[user["branch_id"]]
    rows=db.execute("""SELECT d.*,b.name branch_name,a.name account_name,u.full_name creator,
        COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id),0) paid
        FROM external_debts d JOIN branches b ON b.id=d.branch_id JOIN financial_accounts a ON a.id=d.account_id JOIN users u ON u.id=d.created_by"""+scope+" ORDER BY d.debt_date DESC,d.id DESC",params).fetchall()
    items=[]; totals={"receivable":0.0,"payable":0.0,"overdue":0,"due_soon":0}
    for row in rows:
        x=dict(row); x["remaining"]=max(float(row["amount"])-float(row["paid"]), 0); x["status"],x["status_label"]=external_debt_status(row["due_date"],x["remaining"]); x["party_type_label"]=PARTY_TYPES.get(row["party_type"],row["party_type"]); x["debt_type_label"]=DEBT_TYPES.get(row["debt_type"],row["debt_type"]); items.append(x)
        totals["receivable" if row["debt_type"]=="RECEIVABLE" else "payable"]+=x["remaining"]
        if x["status"]=="OVERDUE": totals["overdue"]+=1
        if x["status"]=="DUE_SOON": totals["due_soon"]+=1
    branches=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall(); accounts=db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    return render_template("external_debts.html",rows=items,branches=branches,accounts=accounts,today=datetime.now().date().isoformat(),party_types=PARTY_TYPES,debt_types=DEBT_TYPES,reason_types=REASON_TYPES,totals=totals)

@app.route("/external-debts/<int:debt_id>/edit", methods=["GET","POST"])
@login_required
@permission_required("edit_external_debts")
def edit_external_debt(debt_id:int)->Any:
    db=get_db(); user=current_user(); debt=db.execute("SELECT * FROM external_debts WHERE id=?",(debt_id,)).fetchone()
    if not debt: flash("الدين غير موجود.","danger"); return redirect(url_for("external_debts"))
    if user["role"]!="admin" and user["branch_id"] and debt["branch_id"]!=user["branch_id"]: flash("لا يمكنك تعديل هذا الدين.","danger"); return redirect(url_for("external_debts"))
    paid=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM external_debt_payments WHERE debt_id=?",(debt_id,)).fetchone()[0])
    if request.method=="POST":
        try:
            branch_id=int(request.form["branch_id"]); account_id=int(request.form["account_id"]); amount=float(request.form["amount"])
            if amount+0.005<paid: raise ValueError("قيمة الدين لا يمكن أن تكون أقل من المبلغ المسدد.")
            if user["role"]!="admin" and user["branch_id"] and branch_id!=user["branch_id"]: raise ValueError("لا يمكنك نقل الدين لموقع آخر.")
            account=db.execute("SELECT * FROM financial_accounts WHERE id=? AND branch_id=? AND is_active=1",(account_id,branch_id)).fetchone()
            if not account: raise ValueError("الحساب المالي غير صالح.")
            if reject_if_day_closed(debt["branch_id"],debt["debt_date"]) or reject_if_day_closed(branch_id,request.form["debt_date"]): return redirect(url_for("edit_external_debt",debt_id=debt_id))
            debt_type=request.form["debt_type"]
            db.execute("""UPDATE external_debts SET branch_id=?,party_name=?,party_type=?,debt_type=?,amount=?,account_id=?,debt_date=?,due_date=?,reason_type=?,reason_details=?,phone=?,notes=?,updated_at=? WHERE id=?""",(branch_id,request.form["party_name"].strip(),request.form["party_type"],debt_type,amount,account_id,request.form["debt_date"],request.form.get("due_date") or None,request.form.get("reason_type","OTHER"),request.form.get("reason_details","").strip(),request.form.get("phone","").strip(),request.form.get("notes","").strip(),now(),debt_id))
            direction="OUT" if debt_type=="RECEIVABLE" else "IN"
            sync_ledger("external_debts",debt_id,branch_id,account_id,"EXTERNAL_DEBT",direction,amount,request.form["debt_date"],request.form.get("notes","").strip(),user["id"])
            audit("تعديل دين خارجي",debt["reference_no"], commit=False)
            db.commit(); flash("تم تعديل الدين وتحديث دفتر الحركة.","success"); return redirect(url_for("external_debt_detail",debt_id=debt_id))
        except (ValueError,TypeError,sqlite3.Error) as exc: db.rollback(); flash(str(exc),"danger")
    branches=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall(); accounts=db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    return render_template("edit_external_debt.html",debt=debt,branches=branches,accounts=accounts,party_types=PARTY_TYPES,debt_types=DEBT_TYPES,reason_types=REASON_TYPES,paid=paid)

@app.route("/external-debts/<int:debt_id>", methods=["GET","POST"])
@login_required
@permission_required("view_external_debts")
def external_debt_detail(debt_id:int)->Any:
    db=get_db(); user=current_user(); debt=db.execute("""SELECT d.*,b.name branch_name,a.name account_name FROM external_debts d JOIN branches b ON b.id=d.branch_id JOIN financial_accounts a ON a.id=d.account_id WHERE d.id=?""",(debt_id,)).fetchone()
    if not debt: flash("الدين غير موجود.","danger"); return redirect(url_for("external_debts"))
    if user["role"]!="admin" and user["branch_id"] and debt["branch_id"]!=user["branch_id"]: flash("لا يمكنك عرض هذا الدين.","danger"); return redirect(url_for("external_debts"))
    paid=db.execute("SELECT COALESCE(SUM(amount),0) total FROM external_debt_payments WHERE debt_id=?",(debt_id,)).fetchone()["total"]; remaining=max(float(debt["amount"])-float(paid), 0); status,status_label=external_debt_status(debt["due_date"],remaining)
    if request.method=="POST":
        if not has_permission("pay_external_debts"): flash("ليس لديك صلاحية تسجيل السداد.","danger"); return redirect(url_for("external_debt_detail",debt_id=debt_id))
        try:
            amount=float(request.form["amount"])
            if amount<=0 or amount>remaining+0.005: raise ValueError("قيمة السداد غير صالحة أو أكبر من المتبقي.")
            account_id=int(request.form["account_id"]); account=db.execute("SELECT * FROM financial_accounts WHERE id=? AND branch_id=? AND is_active=1",(account_id,debt["branch_id"])).fetchone()
            if not account: raise ValueError("الحساب المالي غير صالح.")
            payment_date=request.form["payment_date"]
            if reject_if_day_closed(debt["branch_id"],payment_date): return redirect(url_for("external_debt_detail",debt_id=debt_id))
            payment_id = insert_and_get_id(
                db,
                "INSERT INTO external_debt_payments(debt_id,amount,account_id,payment_date,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(debt_id,amount,account_id,payment_date,request.form.get("notes","").strip(),user["id"],now()))
            direction="IN" if debt["debt_type"]=="RECEIVABLE" else "OUT"
            sync_ledger("external_debt_payments",payment_id,debt["branch_id"],account_id,"EXTERNAL_DEBT_PAYMENT",direction,amount,payment_date,request.form.get("notes","").strip(),user["id"])
            db.commit(); audit("سداد دين خارجي",f"{debt['reference_no']} - {amount}"); flash("تم تسجيل السداد.","success")
        except (ValueError,TypeError,sqlite3.Error) as exc: db.rollback(); flash(str(exc),"danger")
        return redirect(url_for("external_debt_detail",debt_id=debt_id))
    payments=db.execute("""SELECT p.*,a.name account_name,u.full_name creator FROM external_debt_payments p JOIN financial_accounts a ON a.id=p.account_id JOIN users u ON u.id=p.created_by WHERE p.debt_id=? ORDER BY p.payment_date DESC,p.id DESC""",(debt_id,)).fetchall(); accounts=db.execute("SELECT * FROM financial_accounts WHERE branch_id=? AND is_active=1 ORDER BY name",(debt["branch_id"],)).fetchall()
    return render_template("external_debt_detail.html",debt=debt,payments=payments,accounts=accounts,paid=paid,remaining=remaining,status=status,status_label=status_label,party_type_label=PARTY_TYPES.get(debt["party_type"]),debt_type_label=DEBT_TYPES.get(debt["debt_type"]),today=datetime.now().date().isoformat())

@app.post("/external-debts/<int:debt_id>/delete")
@login_required
@permission_required("delete_external_debts")
def delete_external_debt(debt_id:int)->Any:
    db=get_db(); debt=db.execute("SELECT * FROM external_debts WHERE id=?",(debt_id,)).fetchone()
    if not debt: flash("الدين غير موجود.","danger"); return redirect(url_for("external_debts"))
    if db.execute("SELECT 1 FROM external_debt_payments WHERE debt_id=? LIMIT 1",(debt_id,)).fetchone(): flash("لا يمكن حذف دين لديه سدادات مسجلة.","danger"); return redirect(url_for("external_debt_detail",debt_id=debt_id))
    ledger=[_row_dict(x) for x in db.execute("SELECT * FROM financial_ledger WHERE reference_type='external_debts' AND reference_id=?",(debt_id,)).fetchall()]
    move_to_trash("external_debts",debt,f"{debt['reference_no']} - {debt['party_name']}",{"ledger":ledger},request.form.get("reason","")); delete_ledger("external_debts",debt_id); db.execute("DELETE FROM external_debts WHERE id=?",(debt_id,)); db.commit(); audit("نقل دين خارجي إلى السلة",debt["reference_no"]); flash("تم نقل الدين إلى سلة المحذوفات.","success"); return redirect(url_for("external_debts"))

@app.get("/treasury-center")
@login_required
def treasury_center() -> Any:
    if not (has_permission("view_main_treasury") or has_permission("view_branch_treasuries")):
        flash("ليس لديك صلاحية عرض الخزائن.", "danger")
        return redirect(url_for("dashboard"))
    db = get_db()
    locations, totals = _treasury_locations_payload()
    user = current_user()
    transfer_clause = ""
    transfer_params: list[Any] = []
    if user and user["role"] != "admin" and user["branch_id"]:
        transfer_clause = " AND (fa.branch_id=? OR ta.branch_id=?)"
        transfer_params = [user["branch_id"], user["branch_id"]]
    transfers = db.execute(
        """SELECT t.*,fa.name from_account,fb.name from_location,ta.name to_account,tb.name to_location,
                  u.full_name creator
           FROM treasury_transfers t
           JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
           JOIN financial_accounts ta ON ta.id=t.to_account_id JOIN branches tb ON tb.id=ta.branch_id
           JOIN users u ON u.id=t.created_by
           WHERE 1=1""" + transfer_clause + " ORDER BY t.id DESC LIMIT 100",
        transfer_params,
    ).fetchall()
    accounts = db.execute(
        """SELECT a.*,b.name location_name,b.location_type,
                  COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
           LEFT JOIN financial_ledger l ON l.account_id=a.id
           WHERE a.is_active=1 AND b.is_active=1 GROUP BY a.id,b.id,b.name,b.location_type
           ORDER BY CASE b.location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,b.name,a.name"""
    ).fetchall()
    if user and user["role"] != "admin" and user["branch_id"]:
        accounts = [row for row in accounts if row["branch_id"] == user["branch_id"] or row["location_type"] == "MAIN_WAREHOUSE"]
    return render_template(
        "treasury_center.html", locations=locations, totals=totals, transfers=transfers,
        accounts=accounts, status_labels=TREASURY_STATUS_LABELS,
        today=datetime.now().date().isoformat(),
    )

@app.post("/treasury-transfers")
@login_required
@permission_required("create_treasury_transfers")
def create_treasury_transfer() -> Any:
    db = get_db(); user = current_user()
    try:
        from_account_id = int(request.form["from_account_id"])
        to_account_id = int(request.form["to_account_id"])
        amount = round(float(request.form["amount"]), 2)
        transfer_date = request.form.get("transfer_date", datetime.now().date().isoformat())
        notes = request.form.get("notes", "").strip()
    except (KeyError, TypeError, ValueError):
        flash("بيانات التحويل غير صحيحة.", "danger")
        return redirect(url_for("treasury_center"))
    if amount <= 0 or from_account_id == to_account_id:
        flash("المبلغ والحسابات غير صحيحة.", "danger")
        return redirect(url_for("treasury_center"))
    source = db.execute("""SELECT a.*,b.name location_name,b.location_type FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.id=? AND a.is_active=1""", (from_account_id,)).fetchone()
    target = db.execute("""SELECT a.*,b.name location_name,b.location_type FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.id=? AND a.is_active=1""", (to_account_id,)).fetchone()
    if not source or not target or source["branch_id"] == target["branch_id"]:
        flash("يجب اختيار حسابين تابعين لموقعين مختلفين.", "danger")
        return redirect(url_for("treasury_center"))
    if user["role"] != "admin" and user["branch_id"] != source["branch_id"]:
        flash("لا يمكنك إنشاء تحويل من خزينة موقع آخر.", "danger")
        return redirect(url_for("treasury_center"))
    if source["location_type"] == "MAIN_WAREHOUSE" and not has_permission("transfer_from_main_treasury"):
        flash("ليس لديك صلاحية التحويل من الخزينة الرئيسية.", "danger")
        return redirect(url_for("treasury_center"))
    balance = float(db.execute("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) balance FROM financial_ledger WHERE account_id=?", (from_account_id,)).fetchone()["balance"] or 0)
    if amount > balance + 0.005:
        flash(f"الرصيد المتاح في الحساب المصدر هو {balance:.2f} فقط.", "danger")
        return redirect(url_for("treasury_center"))
    transfer_id = insert_and_get_id(
        db,
        """INSERT INTO treasury_transfers(transfer_number,from_account_id,to_account_id,amount,transfer_date,status,notes,created_by,created_at)
           VALUES(?,?,?,?,?,'DRAFT',?,?,?)""",
        (_treasury_transfer_number(), from_account_id, to_account_id, amount, transfer_date, notes, user["id"], now()),
    )
    db.commit(); audit("إنشاء تحويل خزينة", f"رقم التحويل: {transfer_id}، القيمة: {amount:.2f}")
    flash("تم إنشاء التحويل كمسودة.", "success")
    return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))

@app.route("/treasury-transfers/<int:transfer_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("create_treasury_transfers")
def edit_treasury_transfer(transfer_id: int) -> Any:
    db = get_db(); user = current_user()
    transfer = db.execute(
        """SELECT t.*,fa.branch_id from_location_id,ta.branch_id to_location_id
           FROM treasury_transfers t
           JOIN financial_accounts fa ON fa.id=t.from_account_id
           JOIN financial_accounts ta ON ta.id=t.to_account_id
           WHERE t.id=?""", (transfer_id,)
    ).fetchone()
    if not transfer:
        flash("التحويل غير موجود.", "danger")
        return redirect(url_for("treasury_center"))
    if transfer["status"] == "CANCELLED":
        flash("لا يمكن تعديل تحويل ملغي.", "danger")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    if user["role"] != "admin" and transfer["status"] != "DRAFT":
        flash("تعديل التحويلات المرسلة أو المستلمة متاح لمدير النظام فقط.", "danger")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    if user["role"] != "admin" and user["branch_id"] != transfer["from_location_id"]:
        flash("لا يمكنك تعديل تحويل تابع لموقع آخر.", "danger")
        return redirect(url_for("treasury_center"))

    accounts = db.execute(
        """SELECT a.*,b.name location_name,b.location_type,
                  COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
           LEFT JOIN financial_ledger l ON l.account_id=a.id
           WHERE a.is_active=1 AND b.is_active=1
           GROUP BY a.id,b.id,b.name,b.location_type ORDER BY CASE b.location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,b.name,a.name"""
    ).fetchall()
    if user["role"] != "admin" and user["branch_id"]:
        accounts = [a for a in accounts if a["branch_id"] == user["branch_id"] or a["location_type"] == "MAIN_WAREHOUSE"]

    if request.method == "POST":
        try:
            from_account_id = int(request.form["from_account_id"])
            to_account_id = int(request.form["to_account_id"])
            amount = round(float(request.form["amount"]), 2)
            transfer_date = request.form.get("transfer_date") or datetime.now().date().isoformat()
            notes = request.form.get("notes", "").strip()
            if amount <= 0 or from_account_id == to_account_id:
                raise ValueError("المبلغ والحسابات غير صحيحة.")
            source = db.execute(
                """SELECT a.*,b.name location_name,b.location_type
                   FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
                   WHERE a.id=? AND a.is_active=1 AND b.is_active=1""", (from_account_id,)
            ).fetchone()
            target = db.execute(
                """SELECT a.*,b.name location_name,b.location_type
                   FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
                   WHERE a.id=? AND a.is_active=1 AND b.is_active=1""", (to_account_id,)
            ).fetchone()
            if not source or not target or source["branch_id"] == target["branch_id"]:
                raise ValueError("يجب اختيار حسابين تابعين لموقعين مختلفين.")
            if user["role"] != "admin" and user["branch_id"] != source["branch_id"]:
                raise ValueError("لا يمكنك جعل مصدر التحويل من موقع آخر.")
            if source["location_type"] == "MAIN_WAREHOUSE" and not has_permission("transfer_from_main_treasury"):
                raise ValueError("ليس لديك صلاحية التحويل من الخزينة الرئيسية.")

            available = float(db.execute(
                "SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) balance FROM financial_ledger WHERE account_id=?",
                (from_account_id,),
            ).fetchone()["balance"] or 0)
            # عند تعديل عملية مستلمة نضيف أثر القيد القديم مؤقتًا للتحقق العادل من الرصيد.
            if transfer["status"] == "RECEIVED" and int(transfer["from_account_id"]) == from_account_id:
                available += float(transfer["amount"])
            if amount > available + 0.005:
                raise ValueError(f"الرصيد المتاح في الحساب المصدر بعد عكس العملية القديمة هو {available:.2f} فقط.")

            db.execute(
                """UPDATE treasury_transfers
                   SET from_account_id=?,to_account_id=?,amount=?,transfer_date=?,notes=?
                   WHERE id=?""",
                (from_account_id, to_account_id, amount, transfer_date, notes, transfer_id),
            )

            if transfer["status"] == "RECEIVED":
                db.execute("DELETE FROM financial_ledger WHERE reference_type='treasury_transfers' AND reference_id=?", (transfer_id,))
                created = now()
                db.execute("""INSERT INTO financial_ledger(transaction_number,branch_id,account_id,transaction_type,direction,amount,transaction_date,reference_type,reference_id,notes,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (f"TXN-TRS-{transfer_id:06d}-OUT", source["branch_id"], from_account_id, "TREASURY_TRANSFER", "OUT", amount, transfer_date, "treasury_transfers", transfer_id, notes, user["id"], created))
                db.execute("""INSERT INTO financial_ledger(transaction_number,branch_id,account_id,transaction_type,direction,amount,transaction_date,reference_type,reference_id,notes,created_by,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (f"TXN-TRS-{transfer_id:06d}-IN", target["branch_id"], to_account_id, "TREASURY_TRANSFER", "IN", amount, transfer_date, "treasury_transfers", transfer_id, notes, user["id"], created))
            elif transfer["status"] == "SENT":
                db.execute("""UPDATE tasks SET location_id=?,description=?
                              WHERE reference_type='treasury_transfer' AND reference_id=? AND status='OPEN'""",
                           (target["branch_id"], f"استلام التحويل {transfer['transfer_number']} من {source['location_name']} بقيمة {amount:.2f} د.ل", transfer_id))

            db.commit()
            audit("تعديل تحويل خزينة", f"{transfer['transfer_number']} — الحالة {transfer['status']} — القيمة {amount:.2f}")
            flash("تم تعديل التوريد أو التحويل المالي وتحديث أثره المالي.", "success")
            return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
        except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
            db.rollback()
            flash(str(exc) or "تعذر تعديل التحويل.", "danger")
    return render_template("edit_treasury_transfer.html", transfer=transfer, accounts=accounts)

@app.post("/treasury-transfers/<int:transfer_id>/delete")
@login_required
@permission_required("create_treasury_transfers")
def delete_treasury_transfer(transfer_id: int) -> Any:
    db = get_db(); user = current_user()
    transfer = db.execute(
        """SELECT t.*,a.branch_id from_location_id
           FROM treasury_transfers t JOIN financial_accounts a ON a.id=t.from_account_id
           WHERE t.id=?""", (transfer_id,)
    ).fetchone()
    if not transfer:
        flash("التحويل غير موجود.", "danger")
        return redirect(url_for("treasury_center"))
    if transfer["status"] == "CANCELLED":
        flash("التحويل ملغي بالفعل ولا يمكن حذفه من هذه الشاشة.", "danger")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    if user["role"] != "admin" and transfer["status"] != "DRAFT":
        flash("حذف التحويلات المرسلة أو المستلمة متاح لمدير النظام فقط.", "danger")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    if user["role"] != "admin" and user["branch_id"] != transfer["from_location_id"]:
        flash("لا يمكنك حذف تحويل تابع لموقع آخر.", "danger")
        return redirect(url_for("treasury_center"))
    try:
        # حذف القيود يعكس أثر العملية المستلمة على الرصيد تلقائيًا لأن الرصيد محسوب من دفتر الأستاذ.
        db.execute("DELETE FROM financial_ledger WHERE reference_type='treasury_transfers' AND reference_id=?", (transfer_id,))
        db.execute("DELETE FROM tasks WHERE reference_type='treasury_transfer' AND reference_id=?", (transfer_id,))
        db.execute("DELETE FROM notifications WHERE reference_type='treasury_transfer' AND reference_id=?", (transfer_id,))
        db.execute("DELETE FROM approval_requests WHERE entity_type='treasury_transfer' AND entity_id=?", (transfer_id,))
        db.execute("DELETE FROM treasury_transfers WHERE id=?", (transfer_id,))
        db.commit()
        audit("حذف تحويل خزينة", f"{transfer['transfer_number']} — الحالة {transfer['status']} — القيمة {float(transfer['amount']):.2f}")
        flash("تم حذف التوريد أو التحويل المالي وعكس أثره من الأرصدة.", "success")
    except sqlite3.Error as exc:
        db.rollback()
        flash(f"تعذر حذف التحويل: {exc}", "danger")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    return redirect(url_for("treasury_center"))

@app.get("/treasury-transfers/<int:transfer_id>")
@login_required
def treasury_transfer_detail(transfer_id: int) -> Any:
    if not (has_permission("view_main_treasury") or has_permission("view_branch_treasuries")):
        flash("ليس لديك صلاحية عرض الخزائن.", "danger")
        return redirect(url_for("dashboard"))
    db = get_db(); user = current_user()
    row = db.execute(
        """SELECT t.*,fa.name from_account,fb.name from_location,fb.id from_location_id,fb.location_type from_location_type,
                  ta.name to_account,tb.name to_location,tb.id to_location_id,tb.location_type to_location_type,
                  cu.full_name creator,su.full_name sender,ru.full_name receiver
           FROM treasury_transfers t
           JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
           JOIN financial_accounts ta ON ta.id=t.to_account_id JOIN branches tb ON tb.id=ta.branch_id
           JOIN users cu ON cu.id=t.created_by LEFT JOIN users su ON su.id=t.sent_by LEFT JOIN users ru ON ru.id=t.received_by
           WHERE t.id=?""", (transfer_id,)
    ).fetchone()
    if not row:
        flash("التحويل غير موجود.", "danger"); return redirect(url_for("treasury_center"))
    if user["role"] != "admin" and user["branch_id"] not in {row["from_location_id"], row["to_location_id"]}:
        flash("لا يمكنك عرض هذا التحويل.", "danger"); return redirect(url_for("treasury_center"))
    return render_template("treasury_transfer_detail.html", transfer=row, status_labels=TREASURY_STATUS_LABELS)

@app.post("/treasury-transfers/<int:transfer_id>/send")
@login_required
@permission_required("create_treasury_transfers")
def send_treasury_transfer(transfer_id: int) -> Any:
    db = get_db(); user = current_user()
    row = db.execute("""SELECT t.*,fa.branch_id from_location_id,ta.branch_id to_location_id,fb.name from_location,tb.name to_location
                        FROM treasury_transfers t JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
                        JOIN financial_accounts ta ON ta.id=t.to_account_id JOIN branches tb ON tb.id=ta.branch_id WHERE t.id=?""", (transfer_id,)).fetchone()
    if not row or row["status"] != "DRAFT":
        flash("لا يمكن إرسال هذا التحويل.", "danger"); return redirect(url_for("treasury_center"))
    if user["role"] != "admin" and user["branch_id"] != row["from_location_id"]:
        flash("لا يمكنك إرسال تحويل تابع لموقع آخر.", "danger"); return redirect(url_for("treasury_center"))
    # المدير يستطيع تجاوز الاعتماد لأعمال الطوارئ والاختبارات الإدارية.
    if user["role"] != "admin":
        request_id = ApprovalService.request(
            db, entity_type="treasury_transfer", entity_id=transfer_id,
            reference_no=row["transfer_number"], amount=float(row["amount"]),
            branch_id=row["from_location_id"], requested_by=user["id"], requested_at=now(),
        )
        created_at = now()
        NotificationService.notify_approval_requested(db, request_id=request_id, reference_no=row["transfer_number"], amount=float(row["amount"]), branch_id=row["from_location_id"], created_at=created_at)
        TaskService.create_approval_task(db, request_id=request_id, reference_no=row["transfer_number"], amount=float(row["amount"]), branch_id=row["from_location_id"], created_by=user["id"], created_at=created_at)
        audit("طلب اعتماد تحويل خزينة", row["transfer_number"], commit=False)
        db.commit(); flash("تم إرسال التحويل إلى مركز الاعتمادات.", "success")
        return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    db.execute("UPDATE treasury_transfers SET status='SENT',sent_by=?,sent_at=? WHERE id=?", (user["id"], now(), transfer_id))
    create_notification("توريد مالي جديد", f"يوجد تحويل مالي من {row['from_location']} بقيمة {row['amount']:.2f} د.ل", location_id=row["to_location_id"], notification_type="INFO", priority="HIGH", reference_type="treasury_transfer", reference_id=transfer_id)
    create_task("استلام توريد مالي", f"اعتماد التحويل {row['transfer_number']} من {row['from_location']} بقيمة {row['amount']:.2f} د.ل", location_id=row["to_location_id"], task_type="RECEIVE_TREASURY_TRANSFER", reference_type="treasury_transfer", reference_id=transfer_id, priority="HIGH")
    audit("إرسال تحويل خزينة", row["transfer_number"], commit=False)
    db.commit(); flash("تم إرسال التحويل وهو بانتظار الاستلام.", "success")
    return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))

@app.post("/treasury-transfers/<int:transfer_id>/receive")
@login_required
@permission_required("receive_treasury_transfers")
def receive_treasury_transfer(transfer_id: int) -> Any:
    db = get_db(); user = current_user()
    row = db.execute("""SELECT t.*,fa.branch_id from_location_id,ta.branch_id to_location_id,fb.name from_location,tb.name to_location
                        FROM treasury_transfers t JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
                        JOIN financial_accounts ta ON ta.id=t.to_account_id JOIN branches tb ON tb.id=ta.branch_id WHERE t.id=?""", (transfer_id,)).fetchone()
    if not row or row["status"] != "SENT":
        flash("التحويل ليس بانتظار الاستلام.", "danger"); return redirect(url_for("treasury_center"))
    if user["role"] != "admin" and user["branch_id"] != row["to_location_id"]:
        flash("لا يمكنك استلام تحويل تابع لموقع آخر.", "danger"); return redirect(url_for("treasury_center"))
    balance = float(db.execute("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) balance FROM financial_ledger WHERE account_id=?", (row["from_account_id"],)).fetchone()["balance"] or 0)
    if float(row["amount"]) > balance + 0.005:
        flash("رصيد الحساب المصدر لم يعد كافيًا لاعتماد التحويل.", "danger"); return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))
    created = now()
    db.execute("""INSERT INTO financial_ledger(transaction_number,branch_id,account_id,transaction_type,direction,amount,transaction_date,reference_type,reference_id,notes,created_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (f"TXN-TRS-{transfer_id:06d}-OUT", row["from_location_id"], row["from_account_id"], "TREASURY_TRANSFER", "OUT", row["amount"], row["transfer_date"], "treasury_transfers", transfer_id, row["notes"], user["id"], created))
    db.execute("""INSERT INTO financial_ledger(transaction_number,branch_id,account_id,transaction_type,direction,amount,transaction_date,reference_type,reference_id,notes,created_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (f"TXN-TRS-{transfer_id:06d}-IN", row["to_location_id"], row["to_account_id"], "TREASURY_TRANSFER", "IN", row["amount"], row["transfer_date"], "treasury_transfers", transfer_id, row["notes"], user["id"], created))
    db.execute("UPDATE treasury_transfers SET status='RECEIVED',received_by=?,received_at=? WHERE id=?", (user["id"], created, transfer_id))
    EventBus.publish(db, Event("TREASURY_TRANSFER_RECEIVED", "treasury_transfer", transfer_id,
        "تم استلام تحويل الخزينة", f"تم استلام التحويل {row['transfer_number']}",
        {"transfer_number": row["transfer_number"], "amount": row["amount"], "status": "RECEIVED"},
        user["id"], row["to_location_id"]))
    db.execute("UPDATE tasks SET status='COMPLETED',completed_by=?,completed_at=? WHERE reference_type='treasury_transfer' AND reference_id=? AND status='OPEN'", (user["id"], created, transfer_id))
    create_notification("تم استلام التوريد المالي", f"تم استلام التحويل {row['transfer_number']} بواسطة {row['to_location']}", location_id=row["from_location_id"], notification_type="SUCCESS", reference_type="treasury_transfer", reference_id=transfer_id)
    audit("استلام تحويل خزينة", row["transfer_number"], commit=False)
    db.commit(); flash("تم اعتماد الاستلام ونقل الرصيد بين الخزينتين.", "success")
    return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))

@app.post("/treasury-transfers/<int:transfer_id>/cancel")
@login_required
@permission_required("create_treasury_transfers")
def cancel_treasury_transfer(transfer_id: int) -> Any:
    db = get_db(); user = current_user()
    row = db.execute("""SELECT t.*,a.branch_id from_location_id FROM treasury_transfers t JOIN financial_accounts a ON a.id=t.from_account_id WHERE t.id=?""", (transfer_id,)).fetchone()
    if not row or row["status"] not in {"DRAFT", "SENT"}:
        flash("لا يمكن إلغاء هذا التحويل.", "danger"); return redirect(url_for("treasury_center"))
    if user["role"] != "admin" and user["branch_id"] != row["from_location_id"]:
        flash("لا يمكنك إلغاء تحويل تابع لموقع آخر.", "danger"); return redirect(url_for("treasury_center"))
    db.execute("UPDATE treasury_transfers SET status='CANCELLED' WHERE id=?", (transfer_id,))
    db.execute("UPDATE tasks SET status='CANCELLED' WHERE reference_type='treasury_transfer' AND reference_id=? AND status='OPEN'", (transfer_id,))
    db.commit(); audit("إلغاء تحويل خزينة", row["transfer_number"]); flash("تم إلغاء التحويل.", "success")
    return redirect(url_for("treasury_transfer_detail", transfer_id=transfer_id))

@app.get("/treasury-center/export/<file_format>")
@login_required
@permission_required("export_treasury_reports")
def export_treasury_center(file_format: str) -> Any:
    db = get_db(); user = current_user()
    clause = ""; params: list[Any] = []
    if user and user["role"] != "admin" and user["branch_id"]:
        clause = " AND (fa.branch_id=? OR ta.branch_id=?)"; params = [user["branch_id"], user["branch_id"]]
    rows = db.execute("""SELECT t.transfer_number,fb.name from_location,fa.name from_account,tb.name to_location,ta.name to_account,
                                t.amount,t.transfer_date,t.status
                         FROM treasury_transfers t JOIN financial_accounts fa ON fa.id=t.from_account_id JOIN branches fb ON fb.id=fa.branch_id
                         JOIN financial_accounts ta ON ta.id=t.to_account_id JOIN branches tb ON tb.id=ta.branch_id
                         WHERE 1=1""" + clause + " ORDER BY t.id DESC", params).fetchall()
    columns = ["المرجع", "من الموقع", "من الحساب", "إلى الموقع", "إلى الحساب", "المبلغ", "التاريخ", "الحالة"]
    data = [(r["transfer_number"], r["from_location"], r["from_account"], r["to_location"], r["to_account"], float(r["amount"]), r["transfer_date"], TREASURY_STATUS_LABELS.get(r["status"], r["status"])) for r in rows]
    company = all_settings().get("company_name", "Pharma ERP")
    subtitle = "تقرير التوريدات والتحويلات المالية بين الخزائن"
    metadata = [f"تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}", f"المستخدم: {user['full_name']}"]
    builders = {"pdf": (build_pdf, "application/pdf", "pdf"), "docx": (build_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"), "xlsx": (build_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")}
    if file_format not in builders:
        flash("صيغة التصدير غير مدعومة.", "danger"); return redirect(url_for("treasury_center"))
    builder, mimetype, ext = builders[file_format]
    stream = builder("تقرير الخزائن والتوريدات", company, subtitle, columns, data, metadata)
    return send_file(stream, mimetype=mimetype, as_attachment=True, download_name=f"treasury_transfers.{ext}")

@app.route('/financial-reports/<report_name>')
@login_required
@permission_required('view_financial_reports')
def financial_reports(report_name:str)->Any:
    if report_name not in {'balance-sheet','income-statement','cash-flow','adjustments'}: report_name='balance-sheet'
    db=get_db(); user=current_user(); today=datetime.now().date(); start=request.args.get('start_date') or today.replace(day=1).isoformat(); end=request.args.get('end_date') or today.isoformat(); location=request.args.get('location_id',type=int)
    if user and user['role']!='admin' and user['branch_id']: location=user['branch_id']
    locations=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    if report_name=='adjustments':
        clause=''; params:list[Any]=[]
        if location: clause=' WHERE (a.branch_id=? OR a.branch_id IS NULL)'; params=[location]
        rows=db.execute("""SELECT a.*,COALESCE(b.name,'الشركة بالكامل') location_name,u.full_name creator FROM financial_report_adjustments a LEFT JOIN branches b ON b.id=a.branch_id JOIN users u ON u.id=a.created_by"""+clause+" ORDER BY a.adjustment_date DESC,a.id DESC",params).fetchall()
        return render_template('financial_reports.html',report_name=report_name,report=None,adjustments=rows,adjustment_labels=FINANCIAL_ADJUSTMENT_LABELS,locations=locations,selected_location=location,start_date=start,end_date=end)
    report=_financial_report_data(report_name,start,end,location)
    return render_template('financial_reports.html',report_name=report_name,report=report,adjustments=[],adjustment_labels=FINANCIAL_ADJUSTMENT_LABELS,locations=locations,selected_location=location,start_date=start,end_date=end)

@app.post('/financial-reports/adjustments')
@login_required
@permission_required('manage_financial_adjustments')
def add_financial_adjustment()->Any:
    db=get_db(); user=current_user()
    try:
        typ=request.form['adjustment_type']; amount=float(request.form['amount']); direction=request.form.get('direction','INCREASE')
        if typ not in FINANCIAL_ADJUSTMENT_LABELS or amount<=0 or direction not in {'INCREASE','DECREASE'}: raise ValueError('بيانات التعديل غير صالحة.')
        branch_id=request.form.get('branch_id',type=int)
        if user['role']!='admin' and user['branch_id']: branch_id=user['branch_id']
        db.execute("INSERT INTO financial_report_adjustments(title,adjustment_type,amount,direction,branch_id,adjustment_date,notes,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(request.form['title'].strip(),typ,amount,direction,branch_id,request.form['adjustment_date'],request.form.get('notes','').strip(),'ACTIVE',user['id'],now()))
        db.commit(); audit('إضافة تعديل يدوي للتقارير المالية',f"{request.form['title']} — {amount}"); flash('تم حفظ التعديل اليدوي.','success')
    except (ValueError,KeyError,sqlite3.Error) as exc:
        db.rollback(); flash(str(exc),'danger')
    return redirect(url_for('financial_reports',report_name='adjustments'))

@app.post('/financial-reports/adjustments/<int:adjustment_id>/cancel')
@login_required
@permission_required('manage_financial_adjustments')
def cancel_financial_adjustment(adjustment_id:int)->Any:
    db=get_db(); user=current_user(); row=db.execute("SELECT * FROM financial_report_adjustments WHERE id=?",(adjustment_id,)).fetchone()
    if row and row['status']=='ACTIVE':
        db.execute("UPDATE financial_report_adjustments SET status='CANCELLED',cancelled_by=?,cancelled_at=?,cancel_reason=? WHERE id=?",(user['id'],now(),request.form.get('reason','').strip(),adjustment_id)); db.commit(); audit('إلغاء تعديل يدوي للتقارير المالية',row['title']); flash('تم إلغاء التعديل مع الاحتفاظ بسجله.','success')
    return redirect(url_for('financial_reports',report_name='adjustments'))

@app.get('/financial-reports/<report_name>/export/<file_format>')
@login_required
@permission_required('export_financial_reports')
def export_financial_report(report_name:str,file_format:str)->Any:
    if report_name not in {'balance-sheet','income-statement','cash-flow'}: return redirect(url_for('financial_reports',report_name='balance-sheet'))
    today=datetime.now().date(); start=request.args.get('start_date') or today.replace(day=1).isoformat(); end=request.args.get('end_date') or today.isoformat(); location=request.args.get('location_id',type=int); data=_financial_report_data(report_name,start,end,location)
    rows=[[label,float(value)] for label,value in data['rows']]; company=all_settings().get('company_name','Pharma ERP'); user=current_user(); meta=[f'الفترة: {start} إلى {end}',f"المستخدم: {user['full_name']}",f"تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    builders={'pdf':(build_pdf,'application/pdf','pdf'),'docx':(build_docx,'application/vnd.openxmlformats-officedocument.wordprocessingml.document','docx'),'xlsx':(build_excel,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','xlsx')}
    if file_format not in builders: return redirect(url_for('financial_reports',report_name=report_name))
    builder,mime,ext=builders[file_format]; stream=builder(data['title'],company,'تقرير مالي مستخرج من بيانات النظام والتعديلات اليدوية',['البند','القيمة'],rows,meta)
    return send_file(stream,mimetype=mime,as_attachment=True,download_name=f"financial_{report_name}.{ext}")

