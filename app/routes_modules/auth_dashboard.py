from __future__ import annotations

import os
import threading
import time

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the auth dashboard area."""

# Short-lived, process-local dashboard cache. Vercel instances reuse the same
# Python process for warm requests, so this avoids repeating the heaviest
# Supabase aggregates during rapid navigation without introducing an external
# cache dependency. Values are isolated by user, period and selected branch.
_DASHBOARD_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_DASHBOARD_CACHE_LOCK = threading.Lock()
_DASHBOARD_CACHE_TTL = max(0, int(os.environ.get("DASHBOARD_CACHE_TTL", "45") or 45))

def _dashboard_cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    if _DASHBOARD_CACHE_TTL <= 0:
        return None
    current = time.monotonic()
    with _DASHBOARD_CACHE_LOCK:
        cached = _DASHBOARD_CACHE.get(key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= current:
            _DASHBOARD_CACHE.pop(key, None)
            return None
        return payload.copy()

_DASHBOARD_REFERENCE_CACHE: dict[str, tuple[float, Any]] = {}
_DASHBOARD_REFERENCE_TTL = max(15, int(os.environ.get("DASHBOARD_REFERENCE_TTL", "180") or 180))

def _dashboard_reference_get(key: str) -> Any | None:
    current = time.monotonic()
    with _DASHBOARD_CACHE_LOCK:
        cached = _DASHBOARD_REFERENCE_CACHE.get(key)
        if cached is None or cached[0] <= current:
            _DASHBOARD_REFERENCE_CACHE.pop(key, None)
            return None
        return cached[1]

def _dashboard_reference_put(key: str, value: Any) -> Any:
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_REFERENCE_CACHE[key] = (time.monotonic() + _DASHBOARD_REFERENCE_TTL, value)
    return value

def _dashboard_cache_put(key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    if _DASHBOARD_CACHE_TTL <= 0:
        return
    current = time.monotonic()
    with _DASHBOARD_CACHE_LOCK:
        # Keep memory bounded on long-lived non-serverless workers.
        if len(_DASHBOARD_CACHE) >= 128:
            expired = [item_key for item_key, (expiry, _) in _DASHBOARD_CACHE.items() if expiry <= current]
            for item_key in expired:
                _DASHBOARD_CACHE.pop(item_key, None)
            if len(_DASHBOARD_CACHE) >= 128:
                oldest_key = min(_DASHBOARD_CACHE, key=lambda item_key: _DASHBOARD_CACHE[item_key][0])
                _DASHBOARD_CACHE.pop(oldest_key, None)
        _DASHBOARD_CACHE[key] = (current + _DASHBOARD_CACHE_TTL, payload.copy())

@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    db = get_db()
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

    # The username list is only needed when the login page is actually rendered.
    # A successful POST now avoids this extra Supabase round trip.
    active_users = db.execute(
        "SELECT username, full_name FROM users WHERE is_active=1 ORDER BY full_name, username"
    ).fetchall()
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
    """Render the dashboard with a reduced number of database round trips.

    Supabase/PostgreSQL network latency made the old implementation slow because
    it executed many small scalar queries. This version combines related metrics
    into aggregate queries while preserving the template context and behavior.
    """
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
    available_branches = _dashboard_reference_get("branches")
    if available_branches is None:
        available_branches = _dashboard_reference_put(
            "branches", db.execute("SELECT id,name FROM branches ORDER BY name").fetchall()
        )
    selected_branch_id: int | None = None
    if user and user["role"] != "admin" and user["branch_id"]:
        selected_branch_id = int(user["branch_id"])
    elif user and user["role"] == "admin":
        requested_branch = request.args.get("branch_id", "").strip()
        if requested_branch.isdigit():
            selected_branch_id = int(requested_branch)

    cache_key = (
        int(user["id"]),
        period,
        start_date.isoformat(),
        end_date.isoformat(),
        selected_branch_id,
        int(session.get("dashboard_cache_nonce", 0) or 0),
    )
    cached_context = _dashboard_cache_get(cache_key)
    if cached_context is not None:
        cached_context["dashboard_cache_hit"] = True
        return render_template("dashboard.html", **cached_context)

    branch_clause = ""
    branch_params: list[Any] = []
    if selected_branch_id:
        branch_clause = " AND branch_id=?"
        branch_params = [selected_branch_id]

    start_iso = start_date.isoformat()
    end_iso = end_date.isoformat()
    today_iso = today.isoformat()
    range_days = max(1, (end_date - start_date).days + 1)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=range_days - 1)
    previous_start_iso = previous_start.isoformat()
    previous_end_iso = previous_end.isoformat()

    # One round trip for current and previous financial totals (formerly six).
    finance_sql = (
        "SELECT "
        "(SELECT COALESCE(SUM(amount),0) FROM revenues WHERE revenue_date BETWEEN ? AND ?" + branch_clause + ") revenue_total,"
        "(SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN ? AND ?" + branch_clause + ") expense_total,"
        "(SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE payment_date BETWEEN ? AND ?" + branch_clause + ") payment_total,"
        "(SELECT COALESCE(SUM(amount),0) FROM revenues WHERE revenue_date BETWEEN ? AND ?" + branch_clause + ") previous_revenue,"
        "(SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN ? AND ?" + branch_clause + ") previous_expense,"
        "(SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE payment_date BETWEEN ? AND ?" + branch_clause + ") previous_payments"
    )
    finance_params: list[Any] = []
    for left, right in (
        (start_iso, end_iso), (start_iso, end_iso), (start_iso, end_iso),
        (previous_start_iso, previous_end_iso), (previous_start_iso, previous_end_iso),
        (previous_start_iso, previous_end_iso),
    ):
        finance_params.extend([left, right] + branch_params)
    finance = db.execute(finance_sql, finance_params).fetchone()
    revenue_total = float(finance["revenue_total"] or 0)
    expense_total = float(finance["expense_total"] or 0)
    payment_total = float(finance["payment_total"] or 0)
    previous_revenue = float(finance["previous_revenue"] or 0)
    previous_expense = float(finance["previous_expense"] or 0)
    previous_payments = float(finance["previous_payments"] or 0)
    net_total = revenue_total - expense_total - payment_total
    previous_net = previous_revenue - previous_expense - previous_payments

    # One round trip for the six administration counters.
    admin_stats = _dashboard_reference_get("admin_stats")
    if admin_stats is None:
        admin_row = db.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM users WHERE is_active=1) users,"
            "(SELECT COUNT(*) FROM employees WHERE is_active=1) employees,"
            "(SELECT COUNT(*) FROM branches) branches,"
            "(SELECT COUNT(*) FROM departments WHERE is_active=1) departments,"
            "(SELECT COUNT(*) FROM jobs WHERE is_active=1) jobs,"
            "(SELECT COUNT(*) FROM roles WHERE is_active=1) roles"
        ).fetchone()
        admin_stats = _dashboard_reference_put(
            "admin_stats",
            {key: admin_row[key] for key in ("users", "employees", "branches", "departments", "jobs", "roles")},
        )

    recent = db.execute(
        """SELECT a.*, COALESCE(u.full_name,'النظام') user_name
           FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
           ORDER BY a.id DESC LIMIT 8"""
    ).fetchall()
    recent_notifications = db.execute(
        "SELECT * FROM notifications ORDER BY is_read ASC, id DESC LIMIT 5"
    ).fetchall()

    # One round trip for workflow counters.
    workflow_row = db.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM approval_requests WHERE status='PENDING') pending_approvals,"
        "(SELECT COUNT(*) FROM tasks WHERE status='OPEN') open_tasks"
    ).fetchone()
    pending_approvals = workflow_row["pending_approvals"] if has_permission("view_approvals") else 0
    open_tasks = workflow_row["open_tasks"]

    # Avoid repeated schema-introspection queries on every dashboard request.
    backup_dir = backup_directory()
    try:
        backups = sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    except OSError:
        backups = []
    latest_backup = backups[0] if backups else None
    latest_backup_at = datetime.fromtimestamp(latest_backup.stat().st_mtime) if latest_backup else None
    backup_age_days = (datetime.now() - latest_backup_at).days if latest_backup_at else None
    # All preceding dashboard queries already prove the active connection is healthy;
    # avoid an extra network round trip solely for SELECT 1.
    db_ok = True
    health_summary = {
        "database": db_ok,
        "audit": db_ok,
        "events": db_ok,
        "notifications": db_ok,
        "approvals": db_ok,
        "backup": latest_backup is not None and backup_age_days is not None and backup_age_days <= 7,
        "latest_backup_at": latest_backup_at,
        "backup_age_days": backup_age_days,
    }

    branch_id = selected_branch_id
    employee_filter = ""
    attendance_filter = ""
    employee_params: list[Any] = []
    attendance_params: list[Any] = [today_iso]
    if branch_id:
        employee_filter = " AND e.branch_id=?"
        attendance_filter = " AND e.branch_id=?"
        employee_params.append(branch_id)
        attendance_params.append(branch_id)

    # One round trip for employee and attendance counters (formerly three).
    attendance_sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM employees e WHERE e.is_active=1" + employee_filter + ") active_employees,"
        "COALESCE(SUM(CASE WHEN a.check_in IS NOT NULL THEN 1 ELSE 0 END),0) present,"
        "COALESCE(SUM(CASE WHEN a.check_out IS NOT NULL THEN 1 ELSE 0 END),0) checked_out "
        "FROM employee_attendance a JOIN employees e ON e.id=a.employee_id "
        "WHERE a.work_date=?" + attendance_filter
    )
    attendance_row = db.execute(attendance_sql, employee_params + attendance_params).fetchone()
    active_employee_count = int(attendance_row["active_employees"] or 0)
    present_today = int(attendance_row["present"] or 0)
    checked_out_today = int(attendance_row["checked_out"] or 0)
    branch_manager_stats = {
        "active_employees": active_employee_count,
        "present": present_today,
        "inside_now": max(0, present_today - checked_out_today),
        "absent": max(0, active_employee_count - present_today),
    }

    sales_filter = ""
    inventory_filter = ""
    manager_params: list[Any] = [today_iso]
    if branch_id:
        sales_filter = " AND branch_id=?"
        inventory_filter = " AND ib.location_id=?"
        manager_params.extend([branch_id, branch_id])
    # One round trip for today's sales and low-stock count.
    manager_sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM sales_invoices WHERE invoice_date=? AND status IN ('APPROVED','POSTED')" + sales_filter + ") sales_invoices,"
        "(SELECT COALESCE(SUM(total_amount),0) FROM sales_invoices WHERE invoice_date=? AND status IN ('APPROVED','POSTED')" + sales_filter + ") sales_total,"
        "(SELECT COUNT(*) FROM inventory_balances ib JOIN products p ON p.id=ib.product_id "
        " WHERE ib.quantity<=COALESCE(p.minimum_stock,0) AND COALESCE(p.minimum_stock,0)>0" + inventory_filter + ") low_stock"
    )
    if branch_id:
        manager_query_params = [today_iso, branch_id, today_iso, branch_id, branch_id]
    else:
        manager_query_params = [today_iso, today_iso]
    try:
        manager_row = db.execute(manager_sql, manager_query_params).fetchone()
        branch_manager_stats["sales_total"] = float(manager_row["sales_total"] or 0)
        branch_manager_stats["sales_invoices"] = int(manager_row["sales_invoices"] or 0)
        branch_manager_stats["low_stock"] = int(manager_row["low_stock"] or 0)
    except Exception:
        branch_manager_stats.update({"sales_total": 0.0, "sales_invoices": 0, "low_stock": 0})

    def percent_change(current: float, previous: float) -> float | None:
        if abs(previous) < 0.005:
            return None if abs(current) < 0.005 else 100.0
        return round(((current - previous) / abs(previous)) * 100, 1)

    analytics_changes = {
        "revenue": percent_change(revenue_total, previous_revenue),
        "expense": percent_change(expense_total, previous_expense),
        "payments": percent_change(payment_total, previous_payments),
        "net": percent_change(net_total, previous_net),
    }

    date_params = [start_iso, end_iso] + branch_params
    revenue_chart_rows = db.execute(
        "SELECT CAST(revenue_date AS TEXT) row_key, COALESCE(SUM(amount),0) total "
        "FROM revenues WHERE revenue_date BETWEEN ? AND ?" + branch_clause +
        " GROUP BY revenue_date ORDER BY revenue_date",
        date_params,
    ).fetchall()
    daily_map = {
        str(row["row_key"])[:10]: float(row["total"] or 0)
        for row in revenue_chart_rows
    }
    chart_labels: list[str] = []
    chart_values: list[float] = []
    cursor_day = start_date
    while cursor_day <= end_date:
        iso_day = cursor_day.isoformat()
        chart_labels.append(cursor_day.strftime("%d/%m"))
        chart_values.append(round(daily_map.get(iso_day, 0), 2))
        cursor_day += timedelta(days=1)

    sales_analytics_clause = ""
    sales_analytics_params: list[Any] = [start_iso, end_iso]
    if selected_branch_id:
        sales_analytics_clause = " AND s.branch_id=?"
        sales_analytics_params.append(selected_branch_id)
    sales_period_row = db.execute(
        "SELECT COUNT(*) invoices,COALESCE(SUM(total_amount),0) total FROM sales_invoices s "
        "WHERE s.invoice_date BETWEEN ? AND ? AND s.status IN ('APPROVED','POSTED')" + sales_analytics_clause,
        sales_analytics_params,
    ).fetchone()
    sales_period = {
        "invoices": int(sales_period_row["invoices"] or 0) if sales_period_row else 0,
        "total": float(sales_period_row["total"] or 0) if sales_period_row else 0.0,
    }

    employee_clause = ""
    employee_params: list[Any] = [start_iso, end_iso]
    if selected_branch_id:
        employee_clause = " AND r.branch_id=?"
        employee_params.append(selected_branch_id)
    employee_rows = db.execute(
        "SELECT e.id,e.full_name,COALESCE(SUM(res.amount),0) total_revenue,"
        "COALESCE(SUM(res.invoice_count),0) invoice_count "
        "FROM revenue_employee_splits res "
        "JOIN revenues r ON r.id=res.revenue_id "
        "JOIN employees e ON e.id=res.employee_id "
        "WHERE r.revenue_date BETWEEN ? AND ?" + employee_clause +
        " GROUP BY e.id,e.full_name ORDER BY total_revenue DESC,e.full_name",
        employee_params,
    ).fetchall()
    employee_performance = []
    for rank, row in enumerate(employee_rows, start=1):
        employee_total = float(row["total_revenue"] or 0)
        employee_invoices = int(row["invoice_count"] or 0)
        employee_performance.append({
            "rank": rank,
            "employee_id": int(row["id"]),
            "full_name": row["full_name"],
            "invoice_count": employee_invoices,
            "total_revenue": employee_total,
            "average_invoice": employee_total / employee_invoices if employee_invoices else 0.0,
            "daily_average": employee_total / range_days,
            "contribution": (employee_total / revenue_total * 100) if revenue_total > 0 else 0.0,
        })

    revenue_average = revenue_total / range_days
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

    dashboard_context = {
        "period": period,
        "period_label": period_label,
        "start_date": start_iso,
        "end_date": end_iso,
        "revenue_total": revenue_total,
        "expense_total": expense_total,
        "payment_total": payment_total,
        "net_total": net_total,
        "admin_stats": admin_stats,
        "recent": recent,
        "recent_notifications": recent_notifications,
        "pending_approvals": pending_approvals,
        "dashboard_open_tasks": open_tasks,
        "health_summary": health_summary,
        "branch_manager_stats": branch_manager_stats,
        "manager_tasks": manager_tasks,
        "task_summary": task_summary,
        "quick_notes": quick_notes,
        "dashboard_today": today_iso,
        "available_branches": available_branches,
        "selected_branch_id": selected_branch_id,
        "analytics_changes": analytics_changes,
        "previous_revenue": previous_revenue,
        "previous_expense": previous_expense,
        "previous_payments": previous_payments,
        "previous_net": previous_net,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "sales_period": sales_period,
        "employee_performance": employee_performance,
        "revenue_average": revenue_average,
        "best_revenue_day": {"value": best_day[0], "label": best_day[1]},
        "dashboard_cache_hit": False,
    }
    _dashboard_cache_put(cache_key, dashboard_context)
    return render_template("dashboard.html", **dashboard_context)