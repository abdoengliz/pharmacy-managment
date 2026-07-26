from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the suppliers area."""

@app.route("/suppliers", methods=["GET", "POST"])
@login_required
@permission_required("view_suppliers")
def suppliers() -> Any:
    db = get_db()
    if request.method == "POST":
        if not has_permission("manage_suppliers"):
            flash("ليس لديك صلاحية إدارة الموردين.", "danger")
            return redirect(url_for("suppliers"))
        name = request.form["name"].strip()
        phone = request.form.get("phone", "").strip()
        representative_name = request.form.get("representative_name", "").strip()
        notes = request.form.get("notes", "").strip()
        category = request.form.get("category", "أخرى").strip() or "أخرى"
        rating = max(1, min(5, int(request.form.get("rating", 3) or 3)))
        grace_days = max(0, int(request.form.get("grace_days", 30) or 30))
        location_id = int(request.form["location_id"])
        opening_due = float(request.form.get("opening_due", 0) or 0)
        internal_notes = request.form.get("internal_notes", "").strip()
        payment_methods = ",".join(request.form.getlist("payment_methods")) or "نقدي,تحويل,صك"
        credit_limit = max(0, float(request.form.get("credit_limit", 0) or 0))
        try:
            supplier = db.execute("SELECT * FROM suppliers WHERE lower(trim(name))=lower(trim(?))", (name,)).fetchone()
            if supplier:
                supplier_id = supplier["id"]
                db.execute("""UPDATE suppliers SET phone=CASE WHEN ?<>'' THEN ? ELSE phone END,
                    representative_name=CASE WHEN ?<>'' THEN ? ELSE representative_name END,
                    notes=CASE WHEN ?<>'' THEN ? ELSE notes END, category=?,rating=?,grace_days=?,
                    internal_notes=CASE WHEN ?<>'' THEN ? ELSE internal_notes END,payment_methods=?,credit_limit=?,is_archived=0 WHERE id=?""",
                    (phone, phone, representative_name, representative_name, notes, notes, category, rating,
                     grace_days, internal_notes, internal_notes, payment_methods, credit_limit, supplier_id))
                db.execute("""INSERT INTO supplier_location_accounts(supplier_id,location_id,opening_due,credit_limit,notes,is_active,created_at)
                              VALUES(?,?,?,?,?,?,?)""", (supplier_id, location_id, opening_due, credit_limit, notes, 1, now()))
            else:
                supplier_id = insert_and_get_id(
                    db,
                    """INSERT INTO suppliers(name,phone,representative_name,total_due,notes,created_at,category,rating,grace_days,
                    internal_notes,payment_methods,credit_limit,is_archived) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (name, phone, representative_name, opening_due, notes, now(), category, rating, grace_days,
                     internal_notes, payment_methods, credit_limit))
                db.execute("UPDATE suppliers SET supplier_code=? WHERE id=?", (f"SUP-{supplier_id:06d}", supplier_id))
                db.execute("""INSERT INTO supplier_location_accounts(supplier_id,location_id,opening_due,credit_limit,notes,is_active,created_at)
                              VALUES(?,?,?,?,?,?,?)""", (supplier_id, location_id, opening_due, credit_limit, notes, 1, now()))
            db.execute("UPDATE suppliers SET total_due=(SELECT COALESCE(SUM(opening_due),0) FROM supplier_location_accounts WHERE supplier_id=?) WHERE id=?", (supplier_id, supplier_id))
            db.commit()
            audit("إضافة / ربط مورد", f"المورد: {name}، الموقع: {location_id}", entity_type="supplier", entity_id=supplier_id)
            flash("تم حفظ المورد وربطه بالموقع.", "success")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("المورد مرتبط بهذا الموقع بالفعل أو توجد بيانات مكررة.", "danger")
        return redirect(url_for("suppliers"))

    category = request.args.get("category", "").strip()
    location_id = int(request.args.get("location_id") or 0)
    status = request.args.get("status", "").strip()
    sort = request.args.get("sort", "name")
    archived = int(request.args.get("archived") or 0)
    where = ["COALESCE(s.is_archived,0)=?"]
    params: list[Any] = [archived]
    if category:
        where.append("s.category=?"); params.append(category)
    if location_id:
        where.append("EXISTS(SELECT 1 FROM supplier_location_accounts al WHERE al.supplier_id=s.id AND al.location_id=?)"); params.append(location_id)
    balance_expr = "(COALESCE((SELECT SUM(a.opening_due) FROM supplier_location_accounts a WHERE a.supplier_id=s.id),0)+COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_id=s.id),0)-COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_id=s.id),0))"
    overdue_expr = "(SELECT COUNT(*) FROM supplier_invoices io WHERE io.supplier_id=s.id AND NULLIF(io.due_date,'')::date < CURRENT_DATE AND io.amount>COALESCE((SELECT SUM(po.amount) FROM supplier_payments po WHERE po.invoice_id=io.id),0))"
    if status == "due": where.append(balance_expr + ">0.005")
    elif status == "paid": where.append(balance_expr + "<=0.005")
    elif status == "overdue": where.append(overdue_expr + ">0")
    order = {"balance_desc":"remaining DESC", "balance_asc":"remaining ASC", "rating_desc":"s.rating DESC,s.name",
             "rating_asc":"s.rating ASC,s.name", "name":"s.name"}.get(sort, "s.name")
    sql = "SELECT s.*, " + \
          "COALESCE((SELECT SUM(a.opening_due) FROM supplier_location_accounts a WHERE a.supplier_id=s.id),0)+COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_id=s.id),0) total_due," + \
          "COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_id=s.id),0) paid," + balance_expr + " remaining," + \
          "(SELECT COUNT(*) FROM supplier_location_accounts ax WHERE ax.supplier_id=s.id) location_count," + \
          "(SELECT COUNT(*) FROM supplier_invoices ix WHERE ix.supplier_id=s.id) invoice_count," + overdue_expr + " overdue_count " + \
          "FROM suppliers s WHERE " + " AND ".join(where) + " ORDER BY " + order
    rows = db.execute(sql, params).fetchall()
    locations = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    accounts = db.execute("""SELECT a.*,s.name supplier_name,b.name location_name,
        a.opening_due+COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_account_id=a.id),0) total_due,
        COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_account_id=a.id),0) paid
        FROM supplier_location_accounts a JOIN suppliers s ON s.id=a.supplier_id JOIN branches b ON b.id=a.location_id
        WHERE COALESCE(s.is_archived,0)=0 ORDER BY s.name,b.name""").fetchall()
    kpi_sql = "SELECT COUNT(*) suppliers,COALESCE(SUM(total_due),0) total_due,COALESCE(SUM(paid),0) paid,COALESCE(SUM(remaining),0) remaining,COALESCE(SUM(overdue_count),0) overdue FROM (SELECT s.id," + \
        "COALESCE((SELECT SUM(a.opening_due) FROM supplier_location_accounts a WHERE a.supplier_id=s.id),0)+COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_id=s.id),0) total_due," + \
        "COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_id=s.id),0) paid," + balance_expr + " remaining," + overdue_expr + " overdue_count FROM suppliers s WHERE COALESCE(s.is_archived,0)=0)"
    kpis = db.execute(kpi_sql).fetchone()
    return render_template("suppliers.html", rows=rows, locations=locations, supplier_accounts=accounts, kpis=kpis, filters=request.args)

@app.post("/suppliers/<int:supplier_id>/accounts")
@login_required
@permission_required("manage_suppliers")
def add_supplier_account(supplier_id:int)->Any:
    db=get_db(); location_id=int(request.form["location_id"]); opening_due=float(request.form.get("opening_due",0) or 0)
    try:
        db.execute("""INSERT INTO supplier_location_accounts(supplier_id,location_id,opening_due,credit_limit,notes,is_active,created_at)
                      VALUES(?,?,?,?,?,?,?)""",(supplier_id,location_id,opening_due,float(request.form.get("credit_limit",0) or 0),request.form.get("notes","").strip(),1,now()))
        db.execute("UPDATE suppliers SET total_due=(SELECT COALESCE(SUM(opening_due),0) FROM supplier_location_accounts WHERE supplier_id=?) WHERE id=?",(supplier_id,supplier_id))
        db.commit(); audit("إضافة حساب مورد في موقع",f"المورد: {supplier_id}، الموقع: {location_id}"); flash("تمت إضافة حساب المورد في الموقع.","success")
    except sqlite3.IntegrityError:
        db.rollback(); flash("هذا المورد لديه حساب في الموقع المختار بالفعل.","danger")
    return redirect(url_for("supplier_detail",supplier_id=supplier_id))

@app.route("/suppliers/<int:supplier_id>")
@login_required
@permission_required("view_suppliers")
def supplier_detail(supplier_id:int)->Any:
    db=get_db()
    supplier=db.execute("SELECT * FROM suppliers WHERE id=?",(supplier_id,)).fetchone()
    if not supplier:
        flash("المورد غير موجود.","danger"); return redirect(url_for("suppliers"))
    locations=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY id").fetchall()
    accounts=db.execute("""SELECT a.*,b.name location_name,
        a.opening_due + COALESCE((SELECT SUM(i.amount) FROM supplier_invoices i WHERE i.supplier_account_id=a.id),0) total_due,
        COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_account_id=a.id),0) paid,
        (SELECT COUNT(*) FROM supplier_invoices i WHERE i.supplier_account_id=a.id) invoice_count
        FROM supplier_location_accounts a JOIN branches b ON b.id=a.location_id
        WHERE a.supplier_id=? ORDER BY b.name""",(supplier_id,)).fetchall()
    invoices=db.execute("""SELECT i.*,b.name location_name,
        COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.invoice_id=i.id),0) paid,
        CASE WHEN NULLIF(i.due_date,'')::date < CURRENT_DATE AND i.amount>COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.invoice_id=i.id),0) THEN 1 ELSE 0 END overdue
        FROM supplier_invoices i JOIN branches b ON b.id=i.branch_id
        WHERE i.supplier_id=? ORDER BY i.invoice_date DESC,i.id DESC""",(supplier_id,)).fetchall()
    payments=db.execute("""SELECT p.*,b.name location_name,i.invoice_number,u.full_name creator
        FROM supplier_payments p JOIN branches b ON b.id=p.branch_id
        LEFT JOIN supplier_invoices i ON i.id=p.invoice_id JOIN users u ON u.id=p.created_by
        WHERE p.supplier_id=? ORDER BY p.payment_date DESC,p.id DESC""",(supplier_id,)).fetchall()
    totals=db.execute("""SELECT
        COALESCE((SELECT SUM(opening_due) FROM supplier_location_accounts WHERE supplier_id=?),0)
          + COALESCE((SELECT SUM(amount) FROM supplier_invoices WHERE supplier_id=?),0) total_due,
        COALESCE((SELECT SUM(amount) FROM supplier_payments WHERE supplier_id=?),0) paid""",(supplier_id,supplier_id,supplier_id)).fetchone()
    overdue_invoices=[i for i in invoices if i["overdue"]]
    financial_accounts=db.execute("SELECT * FROM financial_accounts WHERE is_active=1 ORDER BY branch_id,name").fetchall()
    last_invoice=db.execute("SELECT MAX(invoice_date) d FROM supplier_invoices WHERE supplier_id=?",(supplier_id,)).fetchone()["d"]
    communications=db.execute("""SELECT c.*,u.full_name creator FROM supplier_communications c JOIN users u ON u.id=c.created_by WHERE c.supplier_id=? ORDER BY c.communication_date DESC,c.id DESC""",(supplier_id,)).fetchall()
    timeline=[]
    for i in invoices:
        timeline.append({"date":i["invoice_date"],"type":"فاتورة","text":f"فاتورة {i['invoice_number']} بقيمة {float(i['amount']):.2f} في {i['location_name']}"})
    for pmt in payments:
        timeline.append({"date":pmt["payment_date"],"type":"سداد","text":f"سداد {float(pmt['amount']):.2f} في {pmt['location_name']}"})
    for c in communications:
        timeline.append({"date":c["communication_date"],"type":c["communication_type"],"text":c["notes"]})
    timeline.sort(key=lambda x:x["date"],reverse=True)
    merge_candidates=db.execute("SELECT id,name,supplier_code FROM suppliers WHERE id<>? AND COALESCE(is_archived,0)=0 ORDER BY name",(supplier_id,)).fetchall()
    return render_template("supplier_detail.html",supplier=supplier,accounts=accounts,locations=locations,invoices=invoices,payments=payments,totals=totals,today=datetime.now().date().isoformat(),overdue_invoices=overdue_invoices,financial_accounts=financial_accounts,last_invoice=last_invoice,communications=communications,timeline=timeline[:100],merge_candidates=merge_candidates)

@app.post("/suppliers/<int:supplier_id>/invoices")
@login_required
@permission_required("manage_suppliers")
def add_supplier_invoice(supplier_id:int)->Any:
    db=get_db(); user=current_user()
    account_id=int(request.form["supplier_account_id"])
    account=db.execute("SELECT * FROM supplier_location_accounts WHERE id=? AND supplier_id=? AND is_active=1",(account_id,supplier_id)).fetchone()
    if not account:
        flash("حساب المورد في الموقع غير موجود.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    if user["role"]!="admin" and user["branch_id"] and account["location_id"]!=user["branch_id"]:
        flash("لا يمكنك إضافة فاتورة لموقع آخر.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    invoice_number=request.form["invoice_number"].strip(); invoice_date=request.form["invoice_date"]; amount=float(request.form["amount"])
    grace=db.execute("SELECT grace_days FROM suppliers WHERE id=?",(supplier_id,)).fetchone()["grace_days"]
    due_date=(datetime.strptime(invoice_date,"%Y-%m-%d")+timedelta(days=int(grace or 0))).date().isoformat()
    if reject_if_day_closed(account["location_id"],invoice_date): return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    try:
        invoice_id = insert_and_get_id(
            db,
            """INSERT INTO supplier_invoices(supplier_id,supplier_account_id,branch_id,invoice_number,invoice_date,amount,notes,created_by,created_at,due_date)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",(supplier_id,account_id,account["location_id"],invoice_number,invoice_date,amount,request.form.get("notes","").strip(),user["id"],now(),due_date))
        db.commit(); audit("إضافة فاتورة مورد",f"المورد: {supplier_id}، الفاتورة: {invoice_number}، القيمة: {amount}",entity_type="supplier_invoice",entity_id=invoice_id)
        flash("تمت إضافة الفاتورة وربطها بالمورد والموقع.","success")
    except sqlite3.IntegrityError:
        db.rollback(); flash("رقم الفاتورة موجود مسبقًا لهذا المورد في نفس الموقع.","danger")
    return redirect(url_for("supplier_detail",supplier_id=supplier_id))

@app.route("/supplier-invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("manage_suppliers")
def edit_supplier_invoice(invoice_id:int)->Any:
    db=get_db(); user=current_user()
    row=db.execute("""SELECT i.*,s.name supplier_name,b.name location_name
        FROM supplier_invoices i JOIN suppliers s ON s.id=i.supplier_id
        JOIN branches b ON b.id=i.branch_id WHERE i.id=?""",(invoice_id,)).fetchone()
    if not row:
        flash("فاتورة المورد غير موجودة.","danger"); return redirect(url_for("suppliers"))
    if user["role"]!="admin" and user["branch_id"] and row["branch_id"]!=user["branch_id"]:
        flash("لا يمكنك تعديل فاتورة تخص موقعًا آخر.","danger"); return redirect(url_for("supplier_detail",supplier_id=row["supplier_id"]))
    if request.method=="POST":
        invoice_number=request.form["invoice_number"].strip(); invoice_date=request.form["invoice_date"]; amount=float(request.form["amount"])
        grace=db.execute("SELECT grace_days FROM suppliers WHERE id=?",(row["supplier_id"],)).fetchone()["grace_days"]
        due_date=(datetime.strptime(invoice_date,"%Y-%m-%d")+timedelta(days=int(grace or 0))).date().isoformat()
        if reject_if_day_closed(row["branch_id"],invoice_date): return redirect(url_for("supplier_detail",supplier_id=row["supplier_id"]))
        paid=db.execute("SELECT COALESCE(SUM(amount),0) total FROM supplier_payments WHERE invoice_id=?",(invoice_id,)).fetchone()["total"]
        if amount < paid:
            flash("لا يمكن جعل قيمة الفاتورة أقل من المبلغ المسدّد عليها.","danger")
        else:
            try:
                db.execute("UPDATE supplier_invoices SET invoice_number=?,invoice_date=?,amount=?,notes=?,due_date=? WHERE id=?",(invoice_number,invoice_date,amount,request.form.get("notes","").strip(),due_date,invoice_id))
                db.commit(); audit("تعديل فاتورة مورد",f"الفاتورة: {invoice_id}، الرقم: {invoice_number}، القيمة: {amount}",entity_type="supplier_invoice",entity_id=invoice_id)
                flash("تم تعديل فاتورة المورد.","success"); return redirect(url_for("supplier_detail",supplier_id=row["supplier_id"]))
            except sqlite3.IntegrityError:
                db.rollback(); flash("رقم الفاتورة مستخدم بالفعل لهذا المورد في نفس الموقع.","danger")
    return render_template("edit_supplier_invoice.html",row=row)

@app.post("/supplier-invoices/<int:invoice_id>/delete")
@login_required
@permission_required("manage_suppliers")
def delete_supplier_invoice(invoice_id:int)->Any:
    db=get_db(); user=current_user()
    row=db.execute("SELECT * FROM supplier_invoices WHERE id=?",(invoice_id,)).fetchone()
    if not row:
        flash("فاتورة المورد غير موجودة.","danger"); return redirect(url_for("suppliers"))
    if user["role"]!="admin" and user["branch_id"] and row["branch_id"]!=user["branch_id"]:
        flash("لا يمكنك حذف فاتورة تخص موقعًا آخر.","danger"); return redirect(url_for("supplier_detail",supplier_id=row["supplier_id"]))
    has_payments=db.execute("SELECT 1 FROM supplier_payments WHERE invoice_id=? LIMIT 1",(invoice_id,)).fetchone()
    if has_payments:
        flash("لا يمكن حذف الفاتورة لأن عليها سدادات مسجلة. احذف أو عدّل السداد أولًا.","danger")
    else:
        move_to_trash("supplier_invoices",row,row["invoice_number"],reason=request.form.get("reason",""))
        db.execute("DELETE FROM supplier_invoices WHERE id=?",(invoice_id,)); db.commit()
        audit("نقل فاتورة مورد إلى سلة المحذوفات",f"رقم الفاتورة: {row['invoice_number']}",entity_type="supplier_invoice",entity_id=invoice_id)
        flash("تم نقل الفاتورة إلى سلة المحذوفات.","success")
    return redirect(url_for("supplier_detail",supplier_id=row["supplier_id"]))

@app.post("/suppliers/<int:supplier_id>/direct-payment")
@login_required
@permission_required("add_payments")
def supplier_direct_payment(supplier_id:int)->Any:
    db=get_db(); user=current_user()
    account_id=int(request.form["supplier_account_id"]); invoice_id=int(request.form.get("invoice_id") or 0) or None
    sa=db.execute("SELECT * FROM supplier_location_accounts WHERE id=? AND supplier_id=? AND is_active=1",(account_id,supplier_id)).fetchone()
    if not sa: flash("حساب المورد في الموقع غير موجود.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    branch_id=sa["location_id"]
    if user["role"]!="admin" and user["branch_id"] and branch_id!=user["branch_id"]:
        flash("لا يمكنك تسجيل سداد لموقع آخر.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    amount=float(request.form["amount"]); payment_date=request.form["payment_date"]
    if amount<=0: flash("قيمة السداد يجب أن تكون أكبر من صفر.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    if invoice_id:
        inv=db.execute("SELECT i.*,i.amount-COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.invoice_id=i.id),0) remaining FROM supplier_invoices i WHERE i.id=? AND i.supplier_account_id=?",(invoice_id,account_id)).fetchone()
        if not inv or amount>float(inv["remaining"])+0.009:
            flash("قيمة السداد أكبر من المتبقي على الفاتورة أو الفاتورة غير صحيحة.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    if reject_if_day_closed(branch_id,payment_date): return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    try:
        splits=parse_account_splits(request.form,branch_id,amount)
        names=[db.execute("SELECT name FROM financial_accounts WHERE id=?",(a,)).fetchone()["name"] for a,_ in splits]
        notes=request.form.get("notes","").strip()
        payment_id = insert_and_get_id(
            db,
            """INSERT INTO supplier_payments(supplier_id,branch_id,amount,payment_date,payment_method,notes,created_by,created_at,account_id,supplier_account_id,invoice_id)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(supplier_id,branch_id,amount,payment_date," + ".join(names),notes,user["id"],now(),splits[0][0],account_id,invoice_id))
        sync_ledger_splits("supplier_payments",payment_id,branch_id,"SUPPLIER_PAYMENT","OUT",amount,payment_date,notes,user["id"],splits)
        db.commit(); audit("سداد مباشر للمورد",f"المورد: {supplier_id}، الموقع: {branch_id}، القيمة: {amount}"); flash("تم تسجيل السداد مباشرة من ملف المورد.","success")
    except (ValueError,TypeError,sqlite3.Error) as exc:
        db.rollback(); flash(str(exc),"danger")
    return redirect(url_for("supplier_detail",supplier_id=supplier_id))

@app.post("/suppliers/<int:supplier_id>/archive")
@login_required
@permission_required("manage_suppliers")
def archive_supplier(supplier_id:int)->Any:
    db=get_db(); row=db.execute("SELECT * FROM suppliers WHERE id=?",(supplier_id,)).fetchone()
    if not row:
        flash("المورد غير موجود.","danger"); return redirect(url_for("suppliers"))
    new_state=0 if row["is_archived"] else 1
    db.execute("UPDATE suppliers SET is_archived=? WHERE id=?",(new_state,supplier_id)); db.commit()
    audit("إلغاء أرشفة مورد" if not new_state else "أرشفة مورد",row["name"],entity_type="supplier",entity_id=supplier_id)
    flash("تم تحديث حالة أرشفة المورد.","success")
    return redirect(url_for("suppliers",archived=new_state))

@app.post("/suppliers/<int:supplier_id>/communications")
@login_required
@permission_required("manage_suppliers")
def add_supplier_communication(supplier_id:int)->Any:
    db=get_db(); user=current_user(); d=request.form.get("communication_date") or datetime.now().date().isoformat(); typ=request.form.get("communication_type","اتصال"); notes=request.form.get("notes","").strip()
    if not notes:
        flash("اكتب تفاصيل التواصل.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    db.execute("INSERT INTO supplier_communications(supplier_id,communication_date,communication_type,contact_person,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(supplier_id,d,typ,request.form.get("contact_person","").strip(),notes,user["id"],now())); db.commit()
    audit("إضافة تواصل مع مورد",f"{typ}: {notes}",entity_type="supplier",entity_id=supplier_id)
    flash("تم حفظ التواصل.","success"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))

@app.post("/suppliers/<int:supplier_id>/merge")
@login_required
@permission_required("manage_suppliers")
def merge_supplier(supplier_id:int)->Any:
    db=get_db(); source_id=int(request.form.get("source_supplier_id") or 0)
    if not source_id or source_id==supplier_id:
        flash("اختر موردًا صحيحًا للدمج.","danger"); return redirect(url_for("supplier_detail",supplier_id=supplier_id))
    target=db.execute("SELECT * FROM suppliers WHERE id=?",(supplier_id,)).fetchone(); source=db.execute("SELECT * FROM suppliers WHERE id=?",(source_id,)).fetchone()
    if not target or not source:
        flash("أحد الموردين غير موجود.","danger"); return redirect(url_for("suppliers"))
    try:
        for a in db.execute("SELECT * FROM supplier_location_accounts WHERE supplier_id=?",(source_id,)).fetchall():
            existing=db.execute("SELECT * FROM supplier_location_accounts WHERE supplier_id=? AND location_id=?",(supplier_id,a["location_id"])).fetchone()
            if existing:
                db.execute("UPDATE supplier_invoices SET supplier_id=?,supplier_account_id=? WHERE supplier_account_id=?",(supplier_id,existing["id"],a["id"]))
                db.execute("UPDATE supplier_payments SET supplier_id=?,supplier_account_id=? WHERE supplier_account_id=?",(supplier_id,existing["id"],a["id"]))
                db.execute("UPDATE supplier_location_accounts SET opening_due=opening_due+? WHERE id=?",(a["opening_due"],existing["id"]))
                db.execute("DELETE FROM supplier_location_accounts WHERE id=?",(a["id"],))
            else:
                db.execute("UPDATE supplier_location_accounts SET supplier_id=? WHERE id=?",(supplier_id,a["id"]))
        db.execute("UPDATE supplier_invoices SET supplier_id=? WHERE supplier_id=?",(supplier_id,source_id))
        db.execute("UPDATE supplier_payments SET supplier_id=? WHERE supplier_id=?",(supplier_id,source_id))
        db.execute("UPDATE supplier_communications SET supplier_id=? WHERE supplier_id=?",(supplier_id,source_id))
        db.execute("UPDATE suppliers SET is_archived=1 WHERE id=?",(source_id,)); db.commit()
        audit("دمج موردين",f"تم دمج {source['name']} داخل {target['name']}",entity_type="supplier",entity_id=supplier_id)
        flash("تم دمج الموردين وأرشفة السجل المكرر.","success")
    except sqlite3.Error as exc:
        db.rollback(); flash(f"تعذر الدمج: {exc}","danger")
    return redirect(url_for("supplier_detail",supplier_id=supplier_id))

@app.post("/supplier-accounts/<int:account_id>/opening-balance")
@login_required
@permission_required("manage_suppliers")
def update_supplier_opening_balance(account_id:int)->Any:
    db=get_db(); a=db.execute("SELECT * FROM supplier_location_accounts WHERE id=?",(account_id,)).fetchone()
    if not a:
        flash("الحساب غير موجود.","danger"); return redirect(url_for("suppliers"))
    value=max(0,float(request.form.get("opening_due",0) or 0)); old=float(a["opening_due"] or 0)
    db.execute("UPDATE supplier_location_accounts SET opening_due=? WHERE id=?",(value,account_id)); db.commit()
    audit("تعديل رصيد أول المدة",f"من {old:.2f} إلى {value:.2f}",entity_type="supplier",entity_id=a["supplier_id"])
    flash("تم تحديث رصيد أول المدة.","success"); return redirect(url_for("supplier_detail",supplier_id=a["supplier_id"]))

@app.get("/suppliers/<int:supplier_id>/statement")
@login_required
@permission_required("view_suppliers")
def supplier_statement(supplier_id:int)->Any:
    db=get_db(); supplier=db.execute("SELECT * FROM suppliers WHERE id=?",(supplier_id,)).fetchone()
    if not supplier: flash("المورد غير موجود.","danger"); return redirect(url_for("suppliers"))
    account_filter=int(request.args.get("account_id") or 0)
    accounts=db.execute("SELECT a.*,b.name location_name FROM supplier_location_accounts a JOIN branches b ON b.id=a.location_id WHERE a.supplier_id=? ORDER BY b.name",(supplier_id,)).fetchall()
    selected=[a for a in accounts if not account_filter or a["id"]==account_filter]
    movements=[]
    for a in selected:
        if float(a["opening_due"] or 0): movements.append({"date":"0000-00-00","kind":"رصيد افتتاحي","reference":"-","location":a["location_name"],"debit":float(a["opening_due"]),"credit":0.0,"sort":0})
    q="SELECT i.*,b.name location_name FROM supplier_invoices i JOIN branches b ON b.id=i.branch_id WHERE i.supplier_id=?"+(" AND i.supplier_account_id=?" if account_filter else "")
    params=(supplier_id,account_filter) if account_filter else (supplier_id,)
    for i in db.execute(q+" ORDER BY i.invoice_date,i.id",params).fetchall(): movements.append({"date":i["invoice_date"],"kind":"فاتورة","reference":i["invoice_number"],"location":i["location_name"],"debit":float(i["amount"]),"credit":0.0,"sort":1})
    q="SELECT p.*,b.name location_name,i.invoice_number FROM supplier_payments p JOIN branches b ON b.id=p.branch_id LEFT JOIN supplier_invoices i ON i.id=p.invoice_id WHERE p.supplier_id=?"+(" AND p.supplier_account_id=?" if account_filter else "")
    for p in db.execute(q+" ORDER BY p.payment_date,p.id",params).fetchall(): movements.append({"date":p["payment_date"],"kind":"سداد","reference":p["invoice_number"] or "رصيد عام","location":p["location_name"],"debit":0.0,"credit":float(p["amount"]),"sort":2})
    movements.sort(key=lambda x:(x["date"],x["sort"]))
    balance=0.0
    for m in movements: balance+=m["debit"]-m["credit"]; m["balance"]=balance
    return render_template("supplier_statement.html",supplier=supplier,accounts=accounts,movements=movements,account_filter=account_filter,balance=balance)

@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("manage_suppliers")
def edit_supplier(supplier_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if not row:
        flash("المورد غير موجود.", "danger")
        return redirect(url_for("suppliers"))
    if request.method == "POST":
        try:
            category=request.form.get("category","أخرى").strip() or "أخرى"
            rating=max(1,min(5,int(request.form.get("rating",3) or 3)))
            grace_days=max(0,int(request.form.get("grace_days",30) or 30))
            payment_methods=",".join(request.form.getlist("payment_methods")) or "نقدي,تحويل,صك"
            credit_limit=max(0,float(request.form.get("credit_limit",0) or 0))
            db.execute("UPDATE suppliers SET name=?, phone=?, representative_name=?, notes=?, category=?, rating=?, grace_days=?, internal_notes=?, payment_methods=?, credit_limit=? WHERE id=?",
                       (request.form["name"].strip(), request.form.get("phone", "").strip(), request.form.get("representative_name", "").strip(), request.form.get("notes", "").strip(), category, rating, grace_days, request.form.get("internal_notes","").strip(), payment_methods, credit_limit, supplier_id))
            db.execute("UPDATE supplier_invoices SET due_date=(CAST(invoice_date AS date) + (? * INTERVAL '1 day')) WHERE supplier_id=? AND NOT EXISTS (SELECT 1 FROM supplier_payments p WHERE p.invoice_id=supplier_invoices.id)",(grace_days,supplier_id))
            db.commit()
            audit("تعديل مورد", f"رقم المورد: {supplier_id}")
            flash("تم تعديل المورد.", "success")
            return redirect(url_for("supplier_detail", supplier_id=supplier_id))
        except sqlite3.IntegrityError:
            flash("اسم المورد مستخدم بالفعل.", "danger")
    return render_template("edit_supplier.html", row=row)

@app.post("/suppliers/<int:supplier_id>/delete")
@login_required
@permission_required("manage_suppliers")
def delete_supplier(supplier_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    if not row:
        flash("المورد غير موجود.", "danger")
        return redirect(url_for("suppliers"))
    has_payments = db.execute("SELECT 1 FROM supplier_payments WHERE supplier_id=? LIMIT 1", (supplier_id,)).fetchone()
    has_invoices = db.execute("SELECT 1 FROM supplier_invoices WHERE supplier_id=? LIMIT 1", (supplier_id,)).fetchone()
    if has_payments or has_invoices:
        flash("لا يمكن حذف المورد لأن لديه فواتير أو سدادات مسجلة. احذف السجلات المرتبطة أولًا أو عدّل بياناته.", "danger")
    else:
        accounts = [_row_dict(r) for r in db.execute("SELECT * FROM supplier_location_accounts WHERE supplier_id=?", (supplier_id,)).fetchall()]
        move_to_trash("suppliers", row, row["name"], {"accounts": accounts}, request.form.get("reason", ""))
        db.execute("DELETE FROM supplier_location_accounts WHERE supplier_id=?", (supplier_id,))
        db.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
        db.commit()
        audit("نقل مورد إلى سلة المحذوفات", row["name"])
        flash("تم نقل المورد إلى سلة المحذوفات.", "success")
    return redirect(url_for("suppliers"))

