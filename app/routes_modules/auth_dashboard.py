from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the auth dashboard area."""

@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    db = get_db()
    active_users = db.execute(
        "SELECT username, full_name FROM users WHERE is_active=1 ORDER BY full_name, username"
    ).fetchall()
    selected_username = request.form.get("username", "").strip() if request.method == "POST" else ""
    if request.method == "POST":
        password = request.form.get("password", "")
        user = db.execute("SELECT * FROM users WHERE username = ?", (selected_username,)).fetchone()
        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            audit("تسجيل دخول", f"المستخدم: {selected_username}")
            if "must_change_password" in user.keys() and user["must_change_password"]:
                return redirect(url_for("change_initial_password"))
            return redirect(url_for("dashboard"))
        flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "danger")
    return render_template(
        "login.html",
        active_users=active_users,
        selected_username=selected_username,
    )

@app.route("/change-initial-password", methods=["GET", "POST"])
@login_required
def change_initial_password() -> Any:
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not check_password_hash(user["password_hash"], current_password):
            flash("كلمة المرور الحالية غير صحيحة.", "danger")
        elif len(new_password) < 12 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
            flash("كلمة المرور الجديدة يجب أن تكون 12 خانة على الأقل وتحتوي حروفًا وأرقامًا.", "danger")
        elif new_password != confirm_password:
            flash("تأكيد كلمة المرور غير مطابق.", "danger")
        elif check_password_hash(user["password_hash"], new_password):
            flash("اختر كلمة مرور مختلفة عن الحالية.", "danger")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?", (generate_password_hash(new_password), user["id"]))
            db.commit()
            audit("تغيير كلمة المرور الأولية", "تم تأمين حساب المستخدم")
            flash("تم تغيير كلمة المرور بنجاح.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_initial_password.html")

@app.route("/attendance-portal", methods=["GET", "POST"])
def attendance_portal() -> Any:
    """Public, restricted kiosk page for employee check-in/check-out."""
    db = get_db()
    result = None
    portal_employees = db.execute(
        """SELECT id, employee_no, employee_code, full_name
           FROM employees
           WHERE is_active=1 AND employment_status='active' AND attendance_portal_enabled=1
           ORDER BY full_name, employee_code, employee_no"""
    ).fetchall()
    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        identifier = request.form.get("employee_identifier", "").strip()
        pin = request.form.get("pin", "").strip()
        action = request.form.get("action", "").strip()
        if action not in {"check_in", "check_out"}:
            flash("العملية المطلوبة غير صحيحة.", "danger")
            return redirect(url_for("attendance_portal"))
        if not identifier or not pin:
            flash("اسم أو كود الموظف والرمز السري مطلوبان.", "danger")
            return redirect(url_for("attendance_portal"))
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            flash("الرمز السري يجب أن يتكون من 4 إلى 8 أرقام.", "danger")
            return redirect(url_for("attendance_portal"))

        if employee_id.isdigit():
            employees = db.execute(
                """SELECT * FROM employees
                   WHERE id=? AND is_active=1 AND employment_status='active'
                     AND attendance_portal_enabled=1""",
                (int(employee_id),),
            ).fetchall()
        else:
            employees = db.execute(
                """SELECT * FROM employees
                   WHERE is_active=1 AND employment_status='active' AND attendance_portal_enabled=1
                     AND (employee_no=? OR employee_code=? OR full_name=?)
                   ORDER BY id""",
                (identifier, identifier, identifier),
            ).fetchall()
        if len(employees) > 1:
            flash("يوجد أكثر من موظف بهذا الاسم. استخدم كود الموظف.", "danger")
            return redirect(url_for("attendance_portal"))
        employee = employees[0] if employees else None
        if not employee or not employee["attendance_pin_hash"] or not check_password_hash(employee["attendance_pin_hash"], pin):
            flash("بيانات الموظف أو الرمز السري غير صحيحة، أو البوابة غير مفعلة لهذا الموظف.", "danger")
            return redirect(url_for("attendance_portal"))

        current = datetime.now()
        work_date = current.date().isoformat()
        time_value = current.strftime("%H:%M")
        record = db.execute(
            "SELECT * FROM employee_attendance WHERE employee_id=? AND work_date=?",
            (employee["id"], work_date),
        ).fetchone()
        creator_id = employee["created_by"]

        if action == "check_in":
            if record and record["check_in"]:
                flash(f"تم تسجيل حضور {employee['full_name']} مسبقًا الساعة {record['check_in']}.", "warning")
                return redirect(url_for("attendance_portal"))
            if record:
                db.execute(
                    "UPDATE employee_attendance SET status='present',check_in=?,notes=COALESCE(notes,'تسجيل من بوابة الموظفين') WHERE id=?",
                    (time_value, record["id"]),
                )
            else:
                db.execute(
                    """INSERT INTO employee_attendance(employee_id,work_date,status,check_in,check_out,overtime_hours,notes,created_by,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (employee["id"], work_date, "present", time_value, None, 0, "تسجيل من بوابة الموظفين", creator_id, now()),
                )
            operation_name = "الحضور"
        else:
            if not record or not record["check_in"]:
                flash("لا يمكن تسجيل الانصراف قبل تسجيل الحضور.", "danger")
                return redirect(url_for("attendance_portal"))
            if record["check_out"]:
                flash(f"تم تسجيل انصراف {employee['full_name']} مسبقًا الساعة {record['check_out']}.", "warning")
                return redirect(url_for("attendance_portal"))
            db.execute("UPDATE employee_attendance SET check_out=? WHERE id=?", (time_value, record["id"]))
            operation_name = "الانصراف"

        db.commit()
        result = {"employee_name": employee["full_name"], "operation": operation_name, "time": time_value}
    return render_template("attendance_portal.html", result=result, portal_employees=portal_employees)

@app.route("/logout")
def logout() -> Any:
    if current_user():
        audit("تسجيل خروج")
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
@permission_required("view_dashboard")
def dashboard() -> Any:
    db = get_db()
    today = datetime.now().date()
    period = request.args.get("period", "month")
    if period == "today":
        start_date = end_date = today
        period_label = "اليوم"
    elif period == "week":
        start_date = today - timedelta(days=6)
        end_date = today
        period_label = "آخر 7 أيام"
    elif period == "year":
        start_date = today.replace(month=1, day=1)
        end_date = today
        period_label = "السنة الحالية"
    elif period == "custom":
        try:
            start_date = datetime.strptime(request.args.get("start_date", ""), "%Y-%m-%d").date()
            end_date = datetime.strptime(request.args.get("end_date", ""), "%Y-%m-%d").date()
            if start_date > end_date:
                start_date, end_date = end_date, start_date
            period_label = f"من {start_date} إلى {end_date}"
        except ValueError:
            start_date = today.replace(day=1)
            end_date = today
            period = "month"
            period_label = "الشهر الحالي"
    else:
        start_date = today.replace(day=1)
        end_date = today
        period = "month"
        period_label = "الشهر الحالي"

    user = current_user()
    available_branches = db.execute("SELECT id,name FROM branches ORDER BY name").fetchall()
    selected_branch_id: int | None = None
    if user and user["role"] != "admin" and user["branch_id"]:
        selected_branch_id = int(user["branch_id"])
    elif user and user["role"] == "admin":
        requested_branch = request.args.get("branch_id", "").strip()
        if requested_branch.isdigit():
            selected_branch_id = int(requested_branch)

    branch_clause = ""
    branch_params: list[Any] = []
    if selected_branch_id:
        branch_clause = " AND branch_id=?"
        branch_params = [selected_branch_id]

    date_params = [start_date.isoformat(), end_date.isoformat()] + branch_params
    revenue_total = db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM revenues WHERE revenue_date BETWEEN ? AND ?" + branch_clause,
        date_params,
    ).fetchone()["total"]
    expense_total = db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM expenses WHERE expense_date BETWEEN ? AND ?" + branch_clause,
        date_params,
    ).fetchone()["total"]
    payment_total = db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM supplier_payments WHERE payment_date BETWEEN ? AND ?" + branch_clause,
        date_params,
    ).fetchone()["total"]
    net_total = revenue_total - expense_total - payment_total

    admin_stats = {
        "users": db.execute("SELECT COUNT(*) c FROM users WHERE is_active=1").fetchone()["c"],
        "employees": db.execute("SELECT COUNT(*) c FROM employees WHERE is_active=1").fetchone()["c"],
        "branches": db.execute("SELECT COUNT(*) c FROM branches").fetchone()["c"],
        "departments": db.execute("SELECT COUNT(*) c FROM departments WHERE is_active=1").fetchone()["c"],
        "jobs": db.execute("SELECT COUNT(*) c FROM jobs WHERE is_active=1").fetchone()["c"],
        "roles": db.execute("SELECT COUNT(*) c FROM roles WHERE is_active=1").fetchone()["c"],
    }
    recent = db.execute(
        """SELECT a.*, COALESCE(u.full_name,'النظام') user_name
           FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
           ORDER BY a.id DESC LIMIT 8"""
    ).fetchall()
    recent_notifications = db.execute(
        "SELECT * FROM notifications ORDER BY is_read ASC, id DESC LIMIT 5"
    ).fetchall()
    pending_approvals = db.execute(
        "SELECT COUNT(*) c FROM approval_requests WHERE status='PENDING'"
    ).fetchone()["c"] if has_permission("view_approvals") else 0
    open_tasks = db.execute(
        "SELECT COUNT(*) c FROM tasks WHERE status='OPEN'"
    ).fetchone()["c"]

    backup_dir = BASE_DIR / "backups"
    backups = sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    latest_backup = backups[0] if backups else None
    latest_backup_at = datetime.fromtimestamp(latest_backup.stat().st_mtime) if latest_backup else None
    backup_age_days = (datetime.now() - latest_backup_at).days if latest_backup_at else None
    db_ok = True
    try:
        db.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        db_ok = False
    health_summary = {
        "database": db_ok,
        "audit": _table_exists(db, "audit_log"),
        "events": _table_exists(db, "event_history"),
        "notifications": _table_exists(db, "notifications"),
        "approvals": _table_exists(db, "approval_requests"),
        "backup": latest_backup is not None and backup_age_days is not None and backup_age_days <= 7,
        "latest_backup_at": latest_backup_at,
        "backup_age_days": backup_age_days,
    }

    # v5.8.0 — Smart Branch Manager Dashboard
    today_iso = today.isoformat()
    branch_id = selected_branch_id

    employee_where = " WHERE e.is_active=1"
    employee_params: list[Any] = []
    if branch_id:
        employee_where += " AND e.branch_id=?"
        employee_params.append(branch_id)
    active_employee_count = db.execute("SELECT COUNT(*) c FROM employees e" + employee_where, employee_params).fetchone()["c"]
    attendance_where = " WHERE a.work_date=?"
    attendance_params: list[Any] = [today_iso]
    if branch_id:
        attendance_where += " AND e.branch_id=?"
        attendance_params.append(branch_id)
    present_today = db.execute(
        "SELECT COUNT(*) c FROM employee_attendance a JOIN employees e ON e.id=a.employee_id" + attendance_where + " AND a.check_in IS NOT NULL",
        attendance_params,
    ).fetchone()["c"]
    checked_out_today = db.execute(
        "SELECT COUNT(*) c FROM employee_attendance a JOIN employees e ON e.id=a.employee_id" + attendance_where + " AND a.check_out IS NOT NULL",
        attendance_params,
    ).fetchone()["c"]
    branch_manager_stats = {
        "active_employees": active_employee_count,
        "present": present_today,
        "inside_now": max(0, present_today - checked_out_today),
        "absent": max(0, active_employee_count - present_today),
    }

    sales_where = " WHERE invoice_date=? AND status IN ('APPROVED','POSTED')"
    sales_params: list[Any] = [today_iso]
    if branch_id:
        sales_where += " AND branch_id=?"
        sales_params.append(branch_id)
    sales_today = db.execute(
        "SELECT COUNT(*) invoices,COALESCE(SUM(total_amount),0) total FROM sales_invoices" + sales_where, sales_params
    ).fetchone()
    branch_manager_stats["sales_total"] = sales_today["total"]
    branch_manager_stats["sales_invoices"] = sales_today["invoices"]

    inventory_where = " WHERE ib.quantity<=COALESCE(p.minimum_stock,0) AND COALESCE(p.minimum_stock,0)>0"
    inventory_params: list[Any] = []
    if branch_id:
        inventory_where += " AND ib.location_id=?"
        inventory_params.append(branch_id)
    try:
        low_stock_count = db.execute(
            "SELECT COUNT(*) c FROM inventory_balances ib JOIN products p ON p.id=ib.product_id" + inventory_where, inventory_params
        ).fetchone()["c"]
    except sqlite3.Error:
        low_stock_count = 0
    branch_manager_stats["low_stock"] = low_stock_count

    # Stage 5.0 — Enterprise analytics dashboard
    range_days = max(1, (end_date - start_date).days + 1)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=range_days - 1)
    previous_params = [previous_start.isoformat(), previous_end.isoformat()] + branch_params
    previous_revenue = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM revenues WHERE revenue_date BETWEEN ? AND ?" + branch_clause,
        previous_params,
    ).fetchone()["total"] or 0)
    previous_expense = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM expenses WHERE expense_date BETWEEN ? AND ?" + branch_clause,
        previous_params,
    ).fetchone()["total"] or 0)
    previous_payments = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) total FROM supplier_payments WHERE payment_date BETWEEN ? AND ?" + branch_clause,
        previous_params,
    ).fetchone()["total"] or 0)
    previous_net = previous_revenue - previous_expense - previous_payments

    def percent_change(current: float, previous: float) -> float | None:
        if abs(previous) < 0.005:
            return None if abs(current) < 0.005 else 100.0
        return round(((current - previous) / abs(previous)) * 100, 1)

    analytics_changes = {
        "revenue": percent_change(float(revenue_total), previous_revenue),
        "expense": percent_change(float(expense_total), previous_expense),
        "payments": percent_change(float(payment_total), previous_payments),
        "net": percent_change(float(net_total), previous_net),
    }

    daily_rows = db.execute(
        "SELECT revenue_date AS revenue_day, COALESCE(SUM(amount),0) AS total FROM revenues "
        "WHERE revenue_date BETWEEN ? AND ?" + branch_clause + " GROUP BY revenue_date ORDER BY revenue_date",
        date_params,
    ).fetchall()
    daily_map = {str(row["revenue_day"])[:10]: float(row["total"] or 0) for row in daily_rows}
    chart_labels: list[str] = []
    chart_values: list[float] = []
    cursor_day = start_date
    while cursor_day <= end_date:
        iso_day = cursor_day.isoformat()
        chart_labels.append(cursor_day.strftime("%d/%m"))
        chart_values.append(round(daily_map.get(iso_day, 0), 2))
        cursor_day += timedelta(days=1)

    payment_rows = db.execute(
        "SELECT payment_method,COALESCE(SUM(amount),0) total FROM revenues "
        "WHERE revenue_date BETWEEN ? AND ?" + branch_clause + " GROUP BY payment_method ORDER BY total DESC",
        date_params,
    ).fetchall()
    payment_method_names = {"CASH": "نقدي", "CARD": "بطاقة", "BANK": "تحويل مصرفي", "CREDIT": "آجل", "POS": "شبكة"}
    payment_breakdown = [
        {"name": payment_method_names.get(str(row["payment_method"]).upper(), str(row["payment_method"])), "value": float(row["total"] or 0)}
        for row in payment_rows
    ]

    sales_analytics_clause = ""
    sales_analytics_params: list[Any] = [start_date.isoformat(), end_date.isoformat()]
    if selected_branch_id:
        sales_analytics_clause = " AND s.branch_id=?"
        sales_analytics_params.append(selected_branch_id)
    sales_period = db.execute(
        "SELECT COUNT(*) invoices,COALESCE(SUM(total_amount),0) total FROM sales_invoices s "
        "WHERE s.invoice_date BETWEEN ? AND ? AND s.status IN ('APPROVED','POSTED')" + sales_analytics_clause,
        sales_analytics_params,
    ).fetchone()
    top_products = db.execute(
        "SELECT i.item_name,COALESCE(SUM(i.quantity),0) quantity,COALESCE(SUM(i.line_total),0) total "
        "FROM sales_invoice_items i JOIN sales_invoices s ON s.id=i.invoice_id "
        "WHERE s.invoice_date BETWEEN ? AND ? AND s.status IN ('APPROVED','POSTED')" + sales_analytics_clause +
        " GROUP BY i.item_name ORDER BY total DESC LIMIT 5",
        sales_analytics_params,
    ).fetchall()
    revenue_average = float(revenue_total) / range_days
    best_day = max(zip(chart_values, chart_labels), default=(0, "—"))

    t_scope, t_params = task_scope()
    manager_tasks = db.execute(
        "SELECT t.*,b.name location_name FROM tasks t LEFT JOIN branches b ON b.id=t.location_id "
        "WHERE t.status='OPEN'" + t_scope +
        " ORDER BY CASE t.priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END, "
        "CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END,t.due_at,t.id DESC LIMIT 12", t_params
    ).fetchall()
    task_summary = {
        "open": len(manager_tasks),
        "high": sum(1 for item in manager_tasks if item["priority"] == "HIGH"),
        "overdue": sum(1 for item in manager_tasks if item["due_at"] and str(item["due_at"])[:10] < today_iso),
    }

    note_params: list[Any] = [user["id"]]
    note_sql = "SELECT * FROM quick_notes WHERE user_id=?"
    if branch_id:
        note_sql += " AND (location_id=? OR location_id IS NULL)"
        note_params.append(branch_id)
    quick_notes = db.execute(note_sql + " ORDER BY id DESC LIMIT 20", note_params).fetchall()

    return render_template(
        "dashboard.html",
        period=period,
        period_label=period_label,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        revenue_total=revenue_total,
        expense_total=expense_total,
        payment_total=payment_total,
        net_total=net_total,
        admin_stats=admin_stats,
        recent=recent,
        recent_notifications=recent_notifications,
        pending_approvals=pending_approvals,
        dashboard_open_tasks=open_tasks,
        health_summary=health_summary,
        branch_manager_stats=branch_manager_stats,
        manager_tasks=manager_tasks,
        task_summary=task_summary,
        quick_notes=quick_notes,
        dashboard_today=today_iso,
        available_branches=available_branches,
        selected_branch_id=selected_branch_id,
        analytics_changes=analytics_changes,
        previous_revenue=previous_revenue,
        previous_expense=previous_expense,
        previous_payments=previous_payments,
        previous_net=previous_net,
        chart_labels=chart_labels,
        chart_values=chart_values,
        payment_breakdown=payment_breakdown,
        sales_period=sales_period,
        top_products=top_products,
        revenue_average=revenue_average,
        best_revenue_day={"value": best_day[0], "label": best_day[1]},
    )

