from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the hr area."""

@app.route("/employees", methods=["GET", "POST"])
@login_required
@permission_required("view_employees")
def employees() -> Any:
    db = get_db()
    if request.method == "POST":
        if not has_permission("add_employees"):
            flash("ليس لديك صلاحية إضافة موظفين.", "danger")
            return redirect(url_for("employees"))
        employee_no = request.form.get("employee_no", "").strip() or next_employee_number(db)
        full_name = request.form.get("full_name", "").strip()
        hire_date = request.form.get("hire_date", "").strip()
        employment_status = request.form.get("employment_status", "active")
        if employment_status not in {"active", "leave", "suspended", "resigned"}:
            employment_status = "active"
        contract_type = request.form.get("contract_type", "permanent")
        if contract_type not in {"permanent", "temporary", "part_time", "training"}:
            contract_type = "permanent"
        shift_type = request.form.get("shift_type", "full")
        if shift_type not in {"morning", "evening", "full", "flexible"}:
            shift_type = "full"
        if not full_name or not hire_date:
            flash("اسم الموظف وتاريخ التعيين مطلوبان.", "danger")
            return redirect(url_for("employees"))
        attendance_pin = request.form.get("attendance_pin", "").strip()
        if attendance_pin and (not attendance_pin.isdigit() or not 4 <= len(attendance_pin) <= 8):
            flash("رمز الحضور يجب أن يتكون من 4 إلى 8 أرقام.", "danger")
            return redirect(url_for("employees"))
        values = (
            employee_no, full_name,
            request.form.get("phone", "").strip() or None,
            request.form.get("address", "").strip() or None,
            request.form.get("emergency_contact_name", "").strip() or None,
            request.form.get("emergency_contact_phone", "").strip() or None,
            request.form.get("employee_code", "").strip() or None,
            request.form.get("biometric_no", "").strip() or None,
            generate_password_hash(attendance_pin) if attendance_pin else None,
            1 if request.form.get("attendance_portal_enabled") == "1" else 0,
            request.form.get("branch_id", type=int),
            request.form.get("department", "").strip() or None,
            request.form.get("job_title", "").strip() or None,
            request.form.get("direct_manager", "").strip() or None,
            hire_date,
            request.form.get("work_start_date", "").strip() or hire_date,
            contract_type, employment_status, shift_type,
            request.form.get("daily_hours", type=float) or 8,
            request.form.get("work_days", "").strip() or None,
            request.form.get("basic_salary", type=float) or 0,
            request.form.get("fixed_allowances", type=float) or 0,
            request.form.get("fixed_deductions", type=float) or 0,
            request.form.get("bank_name", "").strip() or None,
            request.form.get("bank_account", "").strip() or None,
            0 if employment_status in {"suspended", "resigned"} else 1,
            request.form.get("notes", "").strip() or None,
            current_user()["id"], now(),
        )
        try:
            employee_id = insert_and_get_id(
                db,
                """INSERT INTO employees(
                    employee_no,full_name,phone,address,emergency_contact_name,emergency_contact_phone,
                    employee_code,biometric_no,attendance_pin_hash,attendance_portal_enabled,branch_id,department,job_title,direct_manager,hire_date,
                    work_start_date,contract_type,employment_status,shift_type,daily_hours,work_days,
                    basic_salary,fixed_allowances,fixed_deductions,bank_name,bank_account,is_active,
                    notes,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            if employee_no == next_employee_number(db):
                next_code(db, "employee", consume=True)
            db.commit()
            audit("إضافة موظف", f"{employee_no} - {full_name}")
            flash("تمت إضافة الموظف بنجاح.", "success")
            return redirect(url_for("employee_detail", employee_id=employee_id))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("الرقم الوظيفي مستخدم مسبقًا.", "danger")
            return redirect(url_for("employees"))

    q = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    branch_id = request.args.get("branch_id", type=int)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    per_page = 50
    where = ["1=1"]
    scope_where = ["1=1"]
    params: list[Any] = []
    scope_params: list[Any] = []
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"]:
        where.append("e.branch_id=?")
        scope_where.append("branch_id=?")
        params.append(user["branch_id"])
        scope_params.append(user["branch_id"])
    elif branch_id:
        where.append("e.branch_id=?")
        scope_where.append("branch_id=?")
        params.append(branch_id)
        scope_params.append(branch_id)
    if q:
        where.append("(e.full_name LIKE ? OR e.employee_no LIKE ? OR e.employee_code LIKE ? OR e.phone LIKE ? OR e.job_title LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    if status != "all":
        where.append("e.employment_status=?")
        params.append(status)

    total_rows = db.execute(
        f"SELECT COUNT(*) total FROM employees e WHERE {' AND '.join(where)}", params
    ).fetchone()["total"]
    total_pages = max((total_rows + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    rows = db.execute(
        f"""SELECT e.*, b.name branch_name FROM employees e
            LEFT JOIN branches b ON b.id=e.branch_id
            WHERE {' AND '.join(where)}
            ORDER BY e.is_active DESC,e.full_name
            LIMIT ? OFFSET ?""", [*params, per_page, offset]
    ).fetchall()
    branches = db.execute("SELECT id,name FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    departments = db.execute("SELECT id,name FROM departments WHERE is_active=1 ORDER BY name").fetchall()
    jobs = db.execute("SELECT id,name FROM jobs WHERE is_active=1 ORDER BY name").fetchall()
    stats = db.execute(f"""SELECT COUNT(*) total,
        COALESCE(SUM(CASE WHEN employment_status='active' THEN 1 ELSE 0 END),0) active,
        COALESCE(SUM(CASE WHEN employment_status='leave' THEN 1 ELSE 0 END),0) on_leave,
        COALESCE(SUM(CASE WHEN employment_status='suspended' THEN 1 ELSE 0 END),0) suspended,
        COALESCE(SUM(CASE WHEN employment_status='resigned' THEN 1 ELSE 0 END),0) resigned
        FROM employees WHERE {' AND '.join(scope_where)}""", scope_params).fetchone()
    return render_template("employees.html", rows=rows, branches=branches, stats=stats,
                           selected_branch=branch_id, selected_status=status, q=q,
                           departments=departments, jobs=jobs,
                           page=page, per_page=per_page, total_rows=total_rows, total_pages=total_pages,
                           next_employee_no=next_employee_number(db), today=datetime.now().date().isoformat())

@app.route("/employees/<int:employee_id>")
@login_required
@permission_required("view_employees")
def employee_detail(employee_id: int) -> Any:
    db = get_db()
    month = datetime.now().strftime("%Y-%m")
    row = db.execute(
        """SELECT e.*, b.name branch_name, u.full_name created_by_name,
           COALESCE(ms.present_days,0) present_days,
           COALESCE(ms.absent_days,0) absent_days,
           COALESCE(ms.late_days,0) late_days,
           COALESCE(ms.overtime,0) overtime,
           COALESCE(ls.approved_days,0) approved_days,
           COALESCE(ls.pending_requests,0) pending_requests,
           COALESCE(fs.advances,0) advances,
           COALESCE(fs.bonuses,0) bonuses,
           COALESCE(fs.deductions,0) deductions
           FROM employees e
           LEFT JOIN branches b ON b.id=e.branch_id
           LEFT JOIN users u ON u.id=e.created_by
           LEFT JOIN (
               SELECT employee_id,
                 SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present_days,
                 SUM(CASE WHEN status='absent' THEN 1 ELSE 0 END) absent_days,
                 SUM(CASE WHEN status='late' THEN 1 ELSE 0 END) late_days,
                 COALESCE(SUM(overtime_hours),0) overtime
               FROM employee_attendance
               WHERE employee_id=? AND substr(work_date,1,7)=?
               GROUP BY employee_id
           ) ms ON ms.employee_id=e.id
           LEFT JOIN (
               SELECT employee_id,
                 COALESCE(SUM(CASE WHEN status='approved' THEN days ELSE 0 END),0) approved_days,
                 COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending_requests
               FROM employee_leaves WHERE employee_id=? GROUP BY employee_id
           ) ls ON ls.employee_id=e.id
           LEFT JOIN (
               SELECT employee_id,
                 COALESCE(SUM(CASE WHEN adjustment_type='advance' AND status='approved' THEN amount ELSE 0 END),0) advances,
                 COALESCE(SUM(CASE WHEN adjustment_type='bonus' AND status='approved' THEN amount ELSE 0 END),0) bonuses,
                 COALESCE(SUM(CASE WHEN adjustment_type='deduction' AND status='approved' THEN amount ELSE 0 END),0) deductions
               FROM employee_adjustments WHERE employee_id=? GROUP BY employee_id
           ) fs ON fs.employee_id=e.id
           WHERE e.id=?""",
        (employee_id, month, employee_id, employee_id, employee_id),
    ).fetchone()
    if not row:
        flash("الموظف غير موجود.", "danger")
        return redirect(url_for("employees"))
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك عرض موظفي موقع آخر.", "danger")
        return redirect(url_for("employees"))

    attendance = db.execute("SELECT * FROM employee_attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 31", (employee_id,)).fetchall()
    leaves = db.execute("SELECT * FROM employee_leaves WHERE employee_id=? ORDER BY start_date DESC,id DESC LIMIT 20", (employee_id,)).fetchall()
    adjustments = db.execute("SELECT * FROM employee_adjustments WHERE employee_id=? ORDER BY adjustment_date DESC,id DESC LIMIT 30", (employee_id,)).fetchall()
    payroll = db.execute("SELECT * FROM employee_payroll WHERE employee_id=? ORDER BY payroll_month DESC LIMIT 18", (employee_id,)).fetchall()

    month_stats = {"present_days": row["present_days"], "absent_days": row["absent_days"],
                   "late_days": row["late_days"], "overtime": row["overtime"]}
    leave_stats = {"approved_days": row["approved_days"], "pending_requests": row["pending_requests"]}
    finance_stats = {"advances": row["advances"], "bonuses": row["bonuses"], "deductions": row["deductions"]}
    latest_payroll = payroll[0] if payroll else None

    history = [{"date": row["created_at"] or row["hire_date"], "type": "hire", "title": "إضافة الموظف إلى النظام", "details": f"الرقم الوظيفي {row['employee_no']}"}]
    for item in attendance[:12]:
        label = {"present": "حضور", "absent": "غياب", "late": "تأخير", "leave": "إجازة", "holiday": "عطلة"}.get(item["status"], item["status"])
        history.append({"date": item["work_date"], "type": "attendance", "title": f"تسجيل {label}", "details": item["notes"] or "سجل حضور وانصراف"})
    for item in leaves[:10]:
        leave_label = {"annual": "سنوية", "sick": "مرضية", "emergency": "طارئة", "unpaid": "بدون مرتب", "other": "أخرى"}.get(item["leave_type"], item["leave_type"])
        history.append({"date": item["start_date"], "type": "leave", "title": f"إجازة {leave_label}", "details": f"{item['days']} يوم — {item['status']}"})
    for item in adjustments[:12]:
        adjustment_label = {"bonus": "مكافأة", "deduction": "خصم", "advance": "سلفة"}.get(item["adjustment_type"], item["adjustment_type"])
        history.append({"date": item["adjustment_date"], "type": "finance", "title": adjustment_label, "details": f"{item['amount']:.2f} — {item['notes'] or item['status']}"})
    for item in payroll[:8]:
        history.append({"date": item["created_at"] or (item["payroll_month"] + "-01"), "type": "payroll", "title": f"راتب {item['payroll_month']}", "details": f"الصافي {item['net_salary']:.2f} — {item['status']}"})
    history.sort(key=lambda item: str(item["date"] or ""), reverse=True)

    return render_template("employee_detail.html", row=row, attendance=attendance, leaves=leaves,
                           adjustments=adjustments, payroll=payroll, month=month, month_stats=month_stats,
                           leave_stats=leave_stats, finance_stats=finance_stats, latest_payroll=latest_payroll,
                           employee_history=history[:40], today=datetime.now().date().isoformat())

@app.post("/employees/<int:employee_id>/attendance")
@login_required
@permission_required("manage_attendance")
def add_employee_attendance(employee_id: int) -> Any:
    db = get_db()
    employee = db.execute("SELECT employee_no,full_name FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not employee:
        flash("الموظف غير موجود.", "danger")
        return redirect(url_for("employees"))
    work_date = request.form.get("work_date", "").strip()
    status = request.form.get("status", "present")
    if status not in {"present", "absent", "late", "leave", "holiday"}:
        status = "present"
    if not work_date:
        flash("تاريخ الحضور مطلوب.", "danger")
        return redirect(url_for("employee_detail", employee_id=employee_id))
    values = (status, request.form.get("check_in") or None, request.form.get("check_out") or None,
              max(request.form.get("overtime_hours", type=float) or 0, 0),
              request.form.get("notes", "").strip() or None, current_user()["id"], now(), employee_id, work_date)
    db.execute("""INSERT INTO employee_attendance(employee_id,work_date,status,check_in,check_out,overtime_hours,notes,created_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(employee_id,work_date) DO UPDATE SET status=excluded.status,check_in=excluded.check_in,
                  check_out=excluded.check_out,overtime_hours=excluded.overtime_hours,notes=excluded.notes""",
               (employee_id, work_date, *values[:-2]))
    db.commit()
    audit("تسجيل حضور موظف", f"{employee['employee_no']} - {work_date} - {status}")
    flash("تم حفظ سجل الحضور.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id) + "#attendance")

@app.post("/employees/<int:employee_id>/leaves")
@login_required
@permission_required("manage_attendance")
def add_employee_leave(employee_id: int) -> Any:
    db = get_db()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip() or start_date
    if not start_date or end_date < start_date:
        flash("راجع تاريخ بداية ونهاية الإجازة.", "danger")
        return redirect(url_for("employee_detail", employee_id=employee_id) + "#leaves")
    days = (datetime.fromisoformat(end_date).date() - datetime.fromisoformat(start_date).date()).days + 1
    db.execute("""INSERT INTO employee_leaves(employee_id,leave_type,start_date,end_date,days,status,notes,created_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?)""",
               (employee_id, request.form.get("leave_type", "annual"), start_date, end_date, days,
                request.form.get("status", "approved"), request.form.get("notes", "").strip() or None,
                current_user()["id"], now()))
    db.commit()
    audit("إضافة إجازة موظف", f"موظف #{employee_id} من {start_date} إلى {end_date}")
    flash("تمت إضافة الإجازة.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id) + "#leaves")

@app.post("/employees/<int:employee_id>/adjustments")
@login_required
@permission_required("manage_employee_finance")
def add_employee_adjustment(employee_id: int) -> Any:
    db = get_db()
    kind = request.form.get("adjustment_type", "bonus")
    if kind not in {"bonus", "deduction", "advance"}:
        kind = "bonus"
    amount = request.form.get("amount", type=float) or 0
    date = request.form.get("adjustment_date", "").strip()
    if amount <= 0 or not date:
        flash("المبلغ والتاريخ مطلوبان.", "danger")
        return redirect(url_for("employee_detail", employee_id=employee_id) + "#finance")
    db.execute("""INSERT INTO employee_adjustments(employee_id,adjustment_type,amount,adjustment_date,status,notes,created_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?)""",
               (employee_id, kind, amount, date, request.form.get("status", "approved"),
                request.form.get("notes", "").strip() or None, current_user()["id"], now()))
    db.commit()
    audit("إضافة حركة مالية لموظف", f"موظف #{employee_id} - {kind} - {amount:.2f}")
    flash("تمت إضافة الحركة المالية.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id) + "#finance")

@app.post("/employees/<int:employee_id>/payroll")
@login_required
@permission_required("manage_payroll")
def generate_employee_payroll(employee_id: int) -> Any:
    db = get_db()
    employee = db.execute("SELECT basic_salary,fixed_allowances,fixed_deductions,employee_no FROM employees WHERE id=?", (employee_id,)).fetchone()
    month = request.form.get("payroll_month", "").strip()
    if not employee or len(month) != 7:
        flash("الموظف أو شهر الراتب غير صالح.", "danger")
        return redirect(url_for("employee_detail", employee_id=employee_id) + "#payroll")
    sums = db.execute("""SELECT
        COALESCE(SUM(CASE WHEN adjustment_type='bonus' THEN amount ELSE 0 END),0) bonuses,
        COALESCE(SUM(CASE WHEN adjustment_type='deduction' THEN amount ELSE 0 END),0) deductions,
        COALESCE(SUM(CASE WHEN adjustment_type='advance' THEN amount ELSE 0 END),0) advances
        FROM employee_adjustments WHERE employee_id=? AND substr(adjustment_date,1,7)=? AND status='approved'""",
        (employee_id, month)).fetchone()
    basic = float(employee["basic_salary"] or 0)
    bonuses, deductions, advances = float(sums["bonuses"]), float(sums["deductions"]), float(sums["advances"])
    net = basic + bonuses - deductions - advances
    db.execute("""INSERT INTO employee_payroll(employee_id,payroll_month,basic_salary,bonuses,deductions,advances,net_salary,status,notes,created_by,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(employee_id,payroll_month) DO UPDATE SET basic_salary=excluded.basic_salary,
                  bonuses=excluded.bonuses,deductions=excluded.deductions,advances=excluded.advances,
                  net_salary=excluded.net_salary,notes=excluded.notes,updated_at=excluded.updated_at""",
               (employee_id, month, basic, bonuses, deductions, advances, net, "draft",
                request.form.get("notes", "").strip() or None, current_user()["id"], now(), now()))
    db.commit()
    audit("إعداد راتب موظف", f"{employee['employee_no']} - {month} - صافي {net:.2f}")
    flash("تم حساب كشف الراتب وتحديثه.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id) + "#payroll")

@app.post("/employees/payroll/<int:payroll_id>/paid")
@login_required
@permission_required("manage_payroll")
def mark_employee_payroll_paid(payroll_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT employee_id,status FROM employee_payroll WHERE id=?", (payroll_id,)).fetchone()
    if not row:
        flash("كشف الراتب غير موجود.", "danger")
        return redirect(url_for("employees"))
    db.execute("UPDATE employee_payroll SET status='paid',paid_date=?,updated_at=? WHERE id=?", (datetime.now().date().isoformat(), now(), payroll_id))
    db.commit()
    audit("صرف راتب موظف", f"كشف راتب #{payroll_id}")
    flash("تم اعتماد الراتب كمصروف.", "success")
    return redirect(url_for("employee_detail", employee_id=row["employee_id"]) + "#payroll")

@app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("edit_employees")
def edit_employee(employee_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not row:
        flash("الموظف غير موجود.", "danger")
        return redirect(url_for("employees"))
    if request.method == "POST":
        employment_status = request.form.get("employment_status", "active")
        if employment_status not in {"active", "leave", "suspended", "resigned"}:
            employment_status = "active"
        new_attendance_pin = request.form.get("attendance_pin", "").strip()
        if new_attendance_pin and (not new_attendance_pin.isdigit() or not 4 <= len(new_attendance_pin) <= 8):
            flash("رمز الحضور يجب أن يتكون من 4 إلى 8 أرقام.", "danger")
            return redirect(url_for("edit_employee", employee_id=employee_id))
        attendance_pin_hash = generate_password_hash(new_attendance_pin) if new_attendance_pin else row["attendance_pin_hash"]
        values = (
            request.form.get("employee_no", "").strip(), request.form.get("full_name", "").strip(),
            request.form.get("phone", "").strip() or None, request.form.get("address", "").strip() or None,
            request.form.get("emergency_contact_name", "").strip() or None,
            request.form.get("emergency_contact_phone", "").strip() or None,
            request.form.get("employee_code", "").strip() or None,
            request.form.get("biometric_no", "").strip() or None,
            attendance_pin_hash,
            1 if request.form.get("attendance_portal_enabled") == "1" else 0,
            request.form.get("branch_id", type=int), request.form.get("department", "").strip() or None,
            request.form.get("job_title", "").strip() or None, request.form.get("direct_manager", "").strip() or None,
            request.form.get("hire_date", "").strip(), request.form.get("work_start_date", "").strip() or None,
            request.form.get("contract_type", "permanent"), employment_status,
            request.form.get("shift_type", "full"), request.form.get("daily_hours", type=float) or 8,
            request.form.get("work_days", "").strip() or None,
            request.form.get("basic_salary", type=float) or 0,
            request.form.get("fixed_allowances", type=float) or 0,
            request.form.get("fixed_deductions", type=float) or 0,
            request.form.get("bank_name", "").strip() or None, request.form.get("bank_account", "").strip() or None,
            0 if employment_status in {"suspended", "resigned"} else 1,
            request.form.get("notes", "").strip() or None, now(), employee_id,
        )
        if not values[0] or not values[1] or not values[14]:
            flash("الرقم الوظيفي والاسم وتاريخ التعيين مطلوبة.", "danger")
        else:
            try:
                db.execute("""UPDATE employees SET employee_no=?,full_name=?,phone=?,address=?,
                    emergency_contact_name=?,emergency_contact_phone=?,employee_code=?,biometric_no=?,
                    attendance_pin_hash=?,attendance_portal_enabled=?,branch_id=?,department=?,job_title=?,direct_manager=?,hire_date=?,work_start_date=?,
                    contract_type=?,employment_status=?,shift_type=?,daily_hours=?,work_days=?,basic_salary=?,
                    fixed_allowances=?,fixed_deductions=?,bank_name=?,bank_account=?,is_active=?,notes=?,updated_at=?
                    WHERE id=?""", values)
                db.commit()
                audit("تعديل موظف", f"{values[0]} - {values[1]}")
                flash("تم تحديث بيانات الموظف.", "success")
                return redirect(url_for("employee_detail", employee_id=employee_id))
            except sqlite3.IntegrityError:
                db.rollback()
                flash("الرقم الوظيفي مستخدم مسبقًا.", "danger")
    row = db.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    branches = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    departments = db.execute("SELECT * FROM departments WHERE is_active=1 ORDER BY name").fetchall()
    jobs = db.execute("SELECT * FROM jobs WHERE is_active=1 ORDER BY name").fetchall()
    return render_template("edit_employee.html", row=row, branches=branches, departments=departments, jobs=jobs)

@app.post("/employees/<int:employee_id>/toggle")
@login_required
@permission_required("edit_employees")
def toggle_employee(employee_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not row:
        flash("الموظف غير موجود.", "danger")
        return redirect(url_for("employees"))
    new_status = 0 if row["is_active"] else 1
    db.execute("UPDATE employees SET is_active=?,employment_status=?,updated_at=? WHERE id=?", (new_status, "active" if new_status else "suspended", now(), employee_id))
    db.commit()
    audit("تغيير حالة موظف", f"{row['employee_no']} - {'تفعيل' if new_status else 'إيقاف'}")
    flash("تم تفعيل الموظف." if new_status else "تم إيقاف الموظف.", "success")
    return redirect(request.referrer or url_for("employees"))

@app.route("/departments", methods=["GET","POST"])
@login_required
@permission_required("manage_departments")
def departments_page() -> Any:
    db=get_db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); description=request.form.get("description","").strip() or None; branch_id=request.form.get("branch_id") or None
        if name:
            try: db.execute("INSERT INTO departments(name,description,is_active,created_at,branch_id) VALUES(?,?,1,?,?)",(name,description,now(),branch_id)); db.commit(); audit("إضافة قسم",name); flash("تمت إضافة القسم.","success")
            except sqlite3.IntegrityError: db.rollback(); flash("القسم موجود مسبقًا.","danger")
        return redirect(url_for("departments_page"))
    rows=db.execute("""SELECT d.*,b.name branch_name,(SELECT COUNT(*) FROM employees e WHERE e.department_id=d.id OR (e.department_id IS NULL AND e.department=d.name)) employee_count
        FROM departments d LEFT JOIN branches b ON b.id=d.branch_id ORDER BY d.name""").fetchall()
    branches=db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    return render_template("departments.html",rows=rows,branches=branches)

@app.post("/departments/<int:department_id>/toggle")
@login_required
@permission_required("manage_departments")
def toggle_department(department_id:int) -> Any:
    db=get_db(); row=db.execute("SELECT * FROM departments WHERE id=?",(department_id,)).fetchone()
    if row: db.execute("UPDATE departments SET is_active=? WHERE id=?",(0 if row["is_active"] else 1,department_id)); db.commit(); audit("تغيير حالة قسم",row["name"]); flash("تم تحديث حالة القسم.","success")
    return redirect(url_for("departments_page"))

@app.route("/jobs", methods=["GET","POST"])
@login_required
@permission_required("manage_jobs")
def jobs_page() -> Any:
    db=get_db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); code=request.form.get("code","").strip().upper() or next_code(db,"job"); description=request.form.get("description","").strip() or None
        if not name: flash("اسم الوظيفة مطلوب.","danger")
        else:
            try:
                db.execute("INSERT INTO jobs(code,name,description,is_active,created_at) VALUES(?,?,?,1,?)",(code,name,description,now()))
                if code == next_code(db,"job"): next_code(db,"job",consume=True)
                db.commit(); audit("إضافة وظيفة",name); flash("تمت إضافة الوظيفة.","success")
            except sqlite3.IntegrityError: db.rollback(); flash("اسم الوظيفة أو رمزها موجود مسبقًا.","danger")
        return redirect(url_for("jobs_page"))
    rows=db.execute("SELECT j.*,(SELECT COUNT(*) FROM employees e WHERE e.job_title=j.name) employee_count FROM jobs j ORDER BY j.name").fetchall()
    return render_template("jobs.html",rows=rows,next_code=next_code(db,"job"))

@app.post("/jobs/<int:job_id>/toggle")
@login_required
@permission_required("manage_jobs")
def toggle_job(job_id:int) -> Any:
    db=get_db(); row=db.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
    if row: db.execute("UPDATE jobs SET is_active=? WHERE id=?",(0 if row["is_active"] else 1,job_id)); db.commit(); audit("تغيير حالة وظيفة",row["name"]); flash("تم تحديث حالة الوظيفة.","success")
    return redirect(url_for("jobs_page"))

