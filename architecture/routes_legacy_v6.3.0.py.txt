from __future__ import annotations

import sqlite3
import json
import shutil
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import flash, redirect, render_template, request, session, url_for, send_file, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from . import app
from .core import *  # noqa: F401,F403
from .transaction_utils import atomic

FINANCIAL_CLASSIFICATIONS = {
    "OPERATING": "مصروف تشغيلي",
    "ASSET": "أصل",
    "LIABILITY": "التزام / خصم",
}
ASSET_TYPES = {
    "FURNITURE": "أثاث",
    "EQUIPMENT": "أجهزة ومعدات",
    "VEHICLES": "سيارات",
    "REAL_ESTATE": "عقارات",
    "IMPROVEMENTS": "تحسينات وتجهيزات",
    "OTHER": "أخرى",
}


def backup_directory() -> Path:
    """Return a writable backup directory on local and serverless hosts.

    Vercel mounts the deployed application under /var/task as read-only. Its
    only writable filesystem location is /tmp, which is temporary and may be
    cleared between invocations. An explicit BACKUPS_DIR environment variable
    takes precedence for non-Vercel deployments.
    """
    configured = os.environ.get("BACKUPS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.environ.get("VERCEL") or str(BASE_DIR).startswith("/var/task"):
        return Path("/tmp/pharma_erp_backups")
    return BASE_DIR / "backups"


def load_financial_classifications(active_only: bool = True) -> dict[str, str]:
    where = " WHERE is_active=1" if active_only else ""
    rows = get_db().execute(
        "SELECT code,name FROM financial_classifications" + where + " ORDER BY sort_order,id"
    ).fetchall()
    return {row["code"]: row["name"] for row in rows} or dict(FINANCIAL_CLASSIFICATIONS)


def financial_classification_id(code: str) -> int:
    row = get_db().execute("SELECT id FROM financial_classifications WHERE code=? AND is_active=1", (code,)).fetchone()
    if not row:
        raise ValueError("التصنيف المالي غير موجود أو موقوف.")
    return int(row["id"])
from .report_engine import build_pdf, build_docx, build_excel
from .approvals import ApprovalService
from .notifications import NotificationService
from .tasks import TaskService
from .events import EventBus, Event



def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


PARTY_TYPES = {"INDIVIDUAL":"فرد","EMPLOYEE":"موظف","COMPANY":"شركة","GOVERNMENT":"جهة حكومية","OTHER":"أخرى"}
DEBT_TYPES = {"RECEIVABLE":"لنا","PAYABLE":"علينا"}
REASON_TYPES = {"ADVANCE":"سلفة","LOAN":"قرض","PURCHASE":"شراء","SERVICES":"خدمات","OTHER":"أخرى"}

def external_debt_status(due_date: str | None, remaining: float) -> tuple[str,str]:
    if remaining <= 0.005: return "CLOSED", "مغلق"
    if not due_date: return "ACTIVE", "نشط"
    try:
        days=(datetime.strptime(due_date,"%Y-%m-%d").date()-datetime.now().date()).days
    except ValueError:
        return "ACTIVE", "نشط"
    if days < 0: return "OVERDUE", "متأخر"
    if days <= 3: return "DUE_SOON", "قارب الاستحقاق"
    return "ACTIVE", "نشط"

def next_external_debt_reference(db: sqlite3.Connection) -> str:
    year=datetime.now().year
    row=db.execute("SELECT reference_no FROM external_debts WHERE reference_no LIKE ? ORDER BY id DESC LIMIT 1",(f"EXT-{year}-%",)).fetchone()
    seq=int(row[0].rsplit('-',1)[-1])+1 if row else 1
    return f"EXT-{year}-{seq:06d}"

def move_to_trash(module: str, row: sqlite3.Row, record_name: str, related: dict[str, Any] | None = None, reason: str = "") -> None:
    user = current_user()
    get_db().execute(
        """INSERT INTO trash_bin(module,record_id,record_name,data_json,related_json,deleted_by,deleted_at,reason)
           VALUES(?,?,?,?,?,?,?,?)""",
        (module, row["id"], record_name, json.dumps(_row_dict(row), ensure_ascii=False),
         json.dumps(related or {}, ensure_ascii=False), user["id"], now(), reason),
    )


def _insert_snapshot(table: str, data: dict[str, Any]) -> None:
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    get_db().execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        [data[column] for column in columns],
    )


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


def _table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    return table_exists(db, table_name)


@app.route("/system-health")
@login_required
@permission_required("manage_settings")
def system_health() -> Any:
    db = get_db()
    integrity_result = "غير متاح"
    database_ok = False
    try:
        integrity_result = database_healthcheck(db)
        database_ok = integrity_result == "ok"
    except sqlite3.Error as exc:
        integrity_result = str(exc)

    required_tables = [
        "users", "branches", "employees", "departments", "jobs", "roles",
        "revenues", "expenses", "financial_accounts", "financial_ledger",
        "audit_log", "event_history", "notifications", "tasks", "approval_requests",
    ]
    table_checks = {name: _table_exists(db, name) for name in required_tables}
    service_checks = {
        "Audit Center": table_checks["audit_log"],
        "Event Bus": table_checks["event_history"],
        "Notification Engine": table_checks["notifications"],
        "Task Engine": table_checks["tasks"],
        "Approval Engine": table_checks["approval_requests"],
    }
    entity_counts = {
        "المستخدمون": db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "الموظفون": db.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"],
        "الفروع": db.execute("SELECT COUNT(*) c FROM branches").fetchone()["c"],
        "الأقسام": db.execute("SELECT COUNT(*) c FROM departments").fetchone()["c"],
        "الوظائف": db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"],
    }
    backup_dir = BASE_DIR / "backups"
    backups = sorted(backup_dir.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    backup_items = [
        {
            "name": item.name,
            "size_mb": item.stat().st_size / (1024 * 1024),
            "modified_at": datetime.fromtimestamp(item.stat().st_mtime),
        }
        for item in backups[:5]
    ]
    disk = shutil.disk_usage(BASE_DIR)
    environment_info = {
        "database_file": DB_PATH.name,
        "database_size_mb": DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0,
        "free_disk_gb": disk.free / (1024 ** 3),
        "debug": bool(app.debug),
    }
    overall_ok = database_ok and all(table_checks.values()) and all(service_checks.values())
    return render_template(
        "system_health.html",
        overall_ok=overall_ok,
        database_ok=database_ok,
        integrity_result=integrity_result,
        table_checks=table_checks,
        service_checks=service_checks,
        entity_counts=entity_counts,
        backup_items=backup_items,
        environment_info=environment_info,
        checked_at=datetime.now(),
    )


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


def _attendance_hours(db: sqlite3.Connection, employee_id: int, work_date: str) -> float:
    row = db.execute(
        "SELECT check_in,check_out FROM employee_attendance WHERE employee_id=? AND work_date=?",
        (employee_id, work_date),
    ).fetchone()
    if not row or not row["check_in"] or not row["check_out"]:
        return 0.0
    try:
        start = datetime.fromisoformat(str(row["check_in"]))
        end = datetime.fromisoformat(str(row["check_out"]))
        return round(max((end - start).total_seconds() / 3600, 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_employee_revenue_splits(form: Any, db: sqlite3.Connection, branch_id: int,
                                   revenue_date: str, amount: float, invoice_count: int) -> list[tuple[int, float, int, float]]:
    employee_ids = form.getlist("employee_id")
    employee_amounts = form.getlist("employee_amount")
    employee_invoices = form.getlist("employee_invoice_count")
    if not employee_ids:
        raise ValueError("يجب تقسيم الإيراد وعدد الفواتير على موظف واحد على الأقل.")
    if not (len(employee_ids) == len(employee_amounts) == len(employee_invoices)):
        raise ValueError("بيانات تقسيم الموظفين غير مكتملة.")
    result: list[tuple[int, float, int, float]] = []
    seen: set[int] = set()
    for employee_id_raw, employee_amount_raw, employee_invoice_raw in zip(employee_ids, employee_amounts, employee_invoices):
        employee_id = int(employee_id_raw)
        employee_amount = float(employee_amount_raw)
        employee_invoice_count = int(employee_invoice_raw)
        if employee_id in seen:
            raise ValueError("لا يمكن إضافة نفس الموظف أكثر من مرة.")
        if employee_amount < 0 or employee_invoice_count < 0:
            raise ValueError("قيمة الموظف وعدد فواتيره لا يمكن أن يكونا بالسالب.")
        employee = db.execute(
            "SELECT id FROM employees WHERE id=? AND branch_id=? AND is_active=1 AND employment_status='active'",
            (employee_id, branch_id),
        ).fetchone()
        if not employee:
            raise ValueError("أحد الموظفين غير نشط أو لا يتبع الفرع المحدد.")
        seen.add(employee_id)
        result.append((employee_id, employee_amount, employee_invoice_count, _attendance_hours(db, employee_id, revenue_date)))
    if abs(sum(item[1] for item in result) - amount) > 0.009:
        raise ValueError("مجموع الإيراد الموزع على الموظفين يجب أن يساوي قيمة إيراد اليوم.")
    if sum(item[2] for item in result) != invoice_count:
        raise ValueError("مجموع الفواتير الموزعة على الموظفين يجب أن يساوي عدد فواتير اليوم.")
    return result


def _save_employee_revenue_splits(db: sqlite3.Connection, revenue_id: int,
                                  splits: list[tuple[int, float, int, float]]) -> None:
    db.execute("DELETE FROM revenue_employee_splits WHERE revenue_id=?", (revenue_id,))
    db.executemany(
        """INSERT INTO revenue_employee_splits
           (revenue_id,employee_id,amount,invoice_count,worked_hours,created_at)
           VALUES(?,?,?,?,?,?)""",
        [(revenue_id, employee_id, amount, invoice_count, hours, now())
         for employee_id, amount, invoice_count, hours in splits],
    )


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


ACCOUNT_TYPE_LABELS = {
    "CASH": "نقدي",
    "BANK": "مصرفي",
    "CARD": "بطاقة",
    "WALLET": "محفظة إلكترونية",
}

LEDGER_TYPE_LABELS = {
    "REVENUE": "إيراد",
    "EXPENSE": "مصروف",
    "SUPPLIER_PAYMENT": "سداد مورد",
    "OPENING_BALANCE": "رصيد افتتاحي",
    "EXTERNAL_DEBT": "دين خارجي",
    "EXTERNAL_DEBT_PAYMENT": "سداد دين خارجي",
    "TREASURY_TRANSFER": "تحويل خزينة",
}


def _allowed_location_clause(alias: str = "b") -> tuple[str, list[Any]]:
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"]:
        return f" AND {alias}.id=?", [user["branch_id"]]
    return "", []


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


def _statement_period() -> tuple[str, str, str]:
    today=datetime.now().date()
    period=request.args.get("period","month")
    if period=="today": start=end=today
    elif period=="week": start=today-timedelta(days=6); end=today
    elif period=="year": start=today.replace(month=1,day=1); end=today
    elif period=="custom":
        try:
            start=datetime.strptime(request.args.get("start_date",""),"%Y-%m-%d").date(); end=datetime.strptime(request.args.get("end_date",""),"%Y-%m-%d").date()
            if start>end: start,end=end,start
        except ValueError:
            start=today.replace(day=1); end=today; period="month"
    else: start=today.replace(day=1); end=today; period="month"
    return start.isoformat(),end.isoformat(),period


def _account_statement_payload(account_id:int,start_date:str,end_date:str) -> tuple[sqlite3.Row,list[dict[str,Any]],float,float,float,float]:
    db=get_db(); user=current_user()
    account=db.execute("""SELECT a.*,b.name location_name FROM financial_accounts a JOIN branches b ON b.id=a.branch_id WHERE a.id=?""",(account_id,)).fetchone()
    if not account: raise ValueError("الحساب المالي غير موجود.")
    if user and user["role"]!="admin" and user["branch_id"]!=account["branch_id"]: raise ValueError("لا يمكنك عرض حساب تابع لموقع آخر.")
    opening=float(db.execute("""SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) balance FROM financial_ledger WHERE account_id=? AND transaction_date<?""",(account_id,start_date)).fetchone()["balance"] or 0)
    raw=db.execute("""SELECT * FROM financial_ledger WHERE account_id=? AND transaction_date BETWEEN ? AND ? ORDER BY transaction_date,created_at,id""",(account_id,start_date,end_date)).fetchall()
    running=opening; incoming_total=outgoing_total=0.0; rows=[]
    for row in raw:
        incoming=float(row["amount"]) if row["direction"]=="IN" else 0.0; outgoing=float(row["amount"]) if row["direction"]=="OUT" else 0.0
        incoming_total+=incoming; outgoing_total+=outgoing; running+=incoming-outgoing
        item=dict(row); item.update(incoming=incoming,outgoing=outgoing,running_balance=running,movement_label=LEDGER_TYPE_LABELS.get(row["transaction_type"],row["transaction_type"]))
        rows.append(item)
    return account,rows,opening,incoming_total,outgoing_total,running


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

@app.post("/financial-accounts/<int:account_id>/toggle")
@login_required
@permission_required("manage_financial_accounts")
def toggle_financial_account(account_id:int)->Any:
    db=get_db(); row=db.execute("SELECT * FROM financial_accounts WHERE id=?",(account_id,)).fetchone()
    if row:
        db.execute("UPDATE financial_accounts SET is_active=CASE is_active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(account_id,)); db.commit(); audit("تغيير حالة حساب مالي",row["name"]); flash("تم تحديث حالة الحساب.","success")
    return redirect(url_for("financial_accounts"))

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

@app.route("/trash")
@login_required
@permission_required("manage_trash")
def trash_page() -> Any:
    rows = get_db().execute(
        """SELECT t.*,u.full_name deleted_by_name FROM trash_bin t
           JOIN users u ON u.id=t.deleted_by ORDER BY t.id DESC"""
    ).fetchall()
    counts = get_db().execute("SELECT module,COUNT(*) count FROM trash_bin GROUP BY module").fetchall()
    return render_template("trash.html", rows=rows, counts=counts)


@app.post("/trash/<int:trash_id>/restore")
@login_required
@permission_required("manage_trash")
def restore_trash(trash_id: int) -> Any:
    db = get_db()
    item = db.execute("SELECT * FROM trash_bin WHERE id=?", (trash_id,)).fetchone()
    if not item:
        flash("السجل غير موجود في السلة.", "danger")
        return redirect(url_for("trash_page"))
    data = json.loads(item["data_json"]); related = json.loads(item["related_json"] or "{}")
    try:
        _insert_snapshot(item["module"], data)
        if item["module"] == "suppliers":
            for account in related.get("accounts", []): _insert_snapshot("supplier_location_accounts", account)
        else:
            for ledger in related.get("ledger", []): _insert_snapshot("financial_ledger", ledger)
        db.execute("DELETE FROM trash_bin WHERE id=?", (trash_id,))
        db.commit()
        audit("استرجاع من سلة المحذوفات", f"{item['module']} #{item['record_id']} — {item['record_name']}")
        flash("تم استرجاع السجل بنجاح.", "success")
    except sqlite3.IntegrityError as exc:
        db.rollback(); flash(f"تعذر الاسترجاع بسبب تعارض في البيانات: {exc}", "danger")
    return redirect(url_for("trash_page"))


@app.post("/trash/<int:trash_id>/purge")
@login_required
@permission_required("manage_trash")
def purge_trash(trash_id: int) -> Any:
    user = current_user()
    if not user or user["role"] != "admin":
        flash("الحذف النهائي متاح لمدير النظام فقط.", "danger")
        return redirect(url_for("trash_page"))
    item = get_db().execute("SELECT * FROM trash_bin WHERE id=?", (trash_id,)).fetchone()
    if item:
        get_db().execute("DELETE FROM trash_bin WHERE id=?", (trash_id,)); get_db().commit()
        audit("حذف نهائي من السلة", f"{item['module']} #{item['record_id']} — {item['record_name']}")
        flash("تم الحذف النهائي.", "success")
    return redirect(url_for("trash_page"))


@app.route("/locations", methods=["GET", "POST"])
@login_required
@permission_required("manage_locations")
def locations() -> Any:
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "add")
        if action == "add":
            name = request.form.get("name", "").strip()
            code = request.form.get("code", "").strip() or None
            location_type = request.form.get("location_type", "BRANCH")
            if location_type not in {"BRANCH", "MAIN_WAREHOUSE"}:
                location_type = "BRANCH"
            if not name:
                flash("اسم الموقع مطلوب.", "danger")
            else:
                try:
                    db.execute(
                        "INSERT INTO branches(name, code, location_type, is_active) VALUES(?,?,?,1)",
                        (name, code, location_type),
                    )
                    db.commit()
                    audit("إضافة موقع", f"{name} — {code or 'بدون كود'}")
                    flash("تمت إضافة الموقع بنجاح.", "success")
                except sqlite3.IntegrityError:
                    flash("اسم الموقع أو الكود مستخدم مسبقًا.", "danger")
        elif action == "edit":
            location_id = request.form.get("location_id", type=int)
            name = request.form.get("name", "").strip()
            code = request.form.get("code", "").strip() or None
            location_type = request.form.get("location_type", "BRANCH")
            if location_type not in {"BRANCH", "MAIN_WAREHOUSE"}:
                location_type = "BRANCH"
            old = db.execute("SELECT * FROM branches WHERE id=?", (location_id,)).fetchone()
            if not old or not name:
                flash("بيانات الموقع غير صالحة.", "danger")
            else:
                try:
                    db.execute(
                        "UPDATE branches SET name=?, code=?, location_type=? WHERE id=?",
                        (name, code, location_type, location_id),
                    )
                    db.commit()
                    audit("تعديل موقع", f"{old['name']} ← {name}")
                    flash("تم تحديث بيانات الموقع.", "success")
                except sqlite3.IntegrityError:
                    flash("اسم الموقع أو الكود مستخدم مسبقًا.", "danger")
        elif action == "toggle":
            location_id = request.form.get("location_id", type=int)
            row = db.execute("SELECT * FROM branches WHERE id=?", (location_id,)).fetchone()
            if row:
                new_status = 0 if row["is_active"] else 1
                db.execute("UPDATE branches SET is_active=? WHERE id=?", (new_status, location_id))
                db.commit()
                audit("تغيير حالة موقع", f"{row['name']} — {'نشط' if new_status else 'موقوف'}")
                flash("تم تغيير حالة الموقع.", "success")
        return redirect(url_for("locations"))

    rows = db.execute(
        """SELECT b.*,
                  (SELECT COUNT(*) FROM users u WHERE u.branch_id=b.id) users_count,
                  (SELECT COUNT(*) FROM financial_accounts a WHERE a.branch_id=b.id) accounts_count,
                  (SELECT COUNT(*) FROM supplier_location_accounts s WHERE s.location_id=b.id) supplier_accounts_count,
                  (SELECT COUNT(*) FROM financial_ledger l WHERE l.branch_id=b.id) ledger_count
           FROM branches b
           ORDER BY CASE b.location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END, b.id"""
    ).fetchall()
    return render_template("locations.html", rows=rows)


def next_code(db: sqlite3.Connection, entity_key: str, *, consume: bool = False) -> str:
    row = db.execute("SELECT prefix,padding,next_number FROM code_sequences WHERE entity_key=?", (entity_key,)).fetchone()
    if not row:
        return ""
    code = f"{row['prefix']}{int(row['next_number']):0{int(row['padding'])}d}"
    if consume:
        db.execute("UPDATE code_sequences SET next_number=next_number+1,updated_at=? WHERE entity_key=?", (now(), entity_key))
    return code


def next_employee_number(db: sqlite3.Connection) -> str:
    return next_code(db, "employee") or "EMP00001"


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
    where = ["1=1"]
    params: list[Any] = []
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"]:
        where.append("e.branch_id=?")
        params.append(user["branch_id"])
    elif branch_id:
        where.append("e.branch_id=?")
        params.append(branch_id)
    if q:
        where.append("(e.full_name LIKE ? OR e.employee_no LIKE ? OR e.employee_code LIKE ? OR e.phone LIKE ? OR e.job_title LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    if status != "all":
        where.append("e.employment_status=?")
        params.append(status)
    rows = db.execute(
        f"""SELECT e.*, b.name branch_name FROM employees e
            LEFT JOIN branches b ON b.id=e.branch_id
            WHERE {' AND '.join(where)} ORDER BY e.is_active DESC,e.full_name""", params
    ).fetchall()
    branches = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()
    departments = db.execute("SELECT * FROM departments WHERE is_active=1 ORDER BY name").fetchall()
    jobs = db.execute("SELECT * FROM jobs WHERE is_active=1 ORDER BY name").fetchall()
    stats = db.execute("""SELECT COUNT(*) total,
        SUM(CASE WHEN employment_status='active' THEN 1 ELSE 0 END) active,
        SUM(CASE WHEN employment_status='leave' THEN 1 ELSE 0 END) on_leave,
        SUM(CASE WHEN employment_status='suspended' THEN 1 ELSE 0 END) suspended,
        SUM(CASE WHEN employment_status='resigned' THEN 1 ELSE 0 END) resigned FROM employees""").fetchone()
    return render_template("employees.html", rows=rows, branches=branches, stats=stats,
                           selected_branch=branch_id, selected_status=status, q=q,
                           departments=departments, jobs=jobs,
                           next_employee_no=next_employee_number(db), today=datetime.now().date().isoformat())


@app.route("/employees/<int:employee_id>")
@login_required
@permission_required("view_employees")
def employee_detail(employee_id: int) -> Any:
    row = get_db().execute(
        """SELECT e.*,b.name branch_name,u.full_name created_by_name FROM employees e
           LEFT JOIN branches b ON b.id=e.branch_id LEFT JOIN users u ON u.id=e.created_by WHERE e.id=?""",
        (employee_id,),
    ).fetchone()
    if not row:
        flash("الموظف غير موجود.", "danger")
        return redirect(url_for("employees"))
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"] and row["branch_id"] != user["branch_id"]:
        flash("لا يمكنك عرض موظفي موقع آخر.", "danger")
        return redirect(url_for("employees"))
    db = get_db()
    attendance = db.execute("SELECT * FROM employee_attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 31", (employee_id,)).fetchall()
    leaves = db.execute("SELECT * FROM employee_leaves WHERE employee_id=? ORDER BY start_date DESC,id DESC LIMIT 20", (employee_id,)).fetchall()
    adjustments = db.execute("SELECT * FROM employee_adjustments WHERE employee_id=? ORDER BY adjustment_date DESC,id DESC LIMIT 30", (employee_id,)).fetchall()
    payroll = db.execute("SELECT * FROM employee_payroll WHERE employee_id=? ORDER BY payroll_month DESC LIMIT 18", (employee_id,)).fetchall()
    month = datetime.now().strftime("%Y-%m")
    month_stats = db.execute("""SELECT
        SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present_days,
        SUM(CASE WHEN status='absent' THEN 1 ELSE 0 END) absent_days,
        SUM(CASE WHEN status='late' THEN 1 ELSE 0 END) late_days,
        COALESCE(SUM(overtime_hours),0) overtime
        FROM employee_attendance WHERE employee_id=? AND substr(work_date,1,7)=?""", (employee_id, month)).fetchone()
    leave_stats = db.execute("""SELECT
        COALESCE(SUM(CASE WHEN status='approved' THEN days ELSE 0 END),0) approved_days,
        COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending_requests
        FROM employee_leaves WHERE employee_id=?""", (employee_id,)).fetchone()
    finance_stats = db.execute("""SELECT
        COALESCE(SUM(CASE WHEN adjustment_type='advance' AND status='approved' THEN amount ELSE 0 END),0) advances,
        COALESCE(SUM(CASE WHEN adjustment_type='bonus' AND status='approved' THEN amount ELSE 0 END),0) bonuses,
        COALESCE(SUM(CASE WHEN adjustment_type='deduction' AND status='approved' THEN amount ELSE 0 END),0) deductions
        FROM employee_adjustments WHERE employee_id=?""", (employee_id,)).fetchone()
    latest_payroll = db.execute("SELECT * FROM employee_payroll WHERE employee_id=? ORDER BY payroll_month DESC LIMIT 1", (employee_id,)).fetchone()

    history = []
    history.append({"date": row["created_at"] or row["hire_date"], "type": "hire", "title": "إضافة الموظف إلى النظام", "details": f"الرقم الوظيفي {row['employee_no']}"})
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


@app.route("/users", methods=["GET", "POST"])
@login_required
@permission_required("manage_users")
def users() -> Any:
    db = get_db()
    actor = current_user()
    if request.method == "POST":
        if not has_permission("add_users"):
            flash("ليس لديك صلاحية إضافة مستخدمين.", "danger")
            return redirect(url_for("users"))
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        password = request.form["password"]
        role = request.form.get("role", "user")
        if actor["role"] != "admin":
            role = "user"
        branch_id = request.form.get("branch_id") or None
        employee_id = request.form.get("employee_id") or None
        role_id = request.form.get("role_id") or None
        try:
            user_id = insert_and_get_id(
                db,
                "INSERT INTO users(username,password_hash,full_name,role,branch_id,employee_id,role_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (username, generate_password_hash(password), full_name, role, branch_id, employee_id, role_id, now()),
            )
            if role != "admin":
                for permission in request.form.getlist("permissions"):
                    if permission in PERMISSIONS:
                        db.execute("INSERT INTO user_permissions(user_id, permission) VALUES(?,?)", (user_id, permission))
            db.commit()
            audit("إضافة مستخدم", username)
            flash("تم إنشاء المستخدم.", "success")
        except sqlite3.IntegrityError:
            flash("اسم المستخدم موجود مسبقًا.", "danger")
        return redirect(url_for("users"))

    rows = db.execute(
        """
        SELECT u.*, b.name branch_name, e.employee_no, r.name role_name,
               (SELECT STRING_AGG(up.permission::text, ',' ORDER BY up.permission)
                FROM user_permissions up WHERE up.user_id=u.id) permissions
        FROM users u LEFT JOIN branches b ON b.id=u.branch_id
        LEFT JOIN employees e ON e.id=u.employee_id
        LEFT JOIN roles r ON r.id=u.role_id
        ORDER BY u.id
        """
    ).fetchall()
    branches = db.execute("SELECT * FROM branches ORDER BY id").fetchall()
    employees = db.execute("SELECT id,employee_no,full_name FROM employees WHERE is_active=1 ORDER BY full_name").fetchall()
    roles = db.execute("SELECT * FROM roles WHERE is_active=1 ORDER BY name").fetchall()
    return render_template("users.html", rows=rows, branches=branches, employees=employees, roles=roles)


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("edit_users")
def edit_user(user_id: int) -> Any:
    db = get_db()
    actor = current_user()
    row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        flash("المستخدم غير موجود.", "danger")
        return redirect(url_for("users"))
    if row["role"] == "admin" and actor["role"] != "admin":
        flash("لا يمكنك تعديل حساب مدير النظام.", "danger")
        return redirect(url_for("users"))

    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        password = request.form.get("password", "")
        role = request.form.get("role", row["role"])
        if actor["role"] != "admin":
            role = "user"
        branch_id = request.form.get("branch_id") or None
        employee_id = request.form.get("employee_id") or None
        role_id = request.form.get("role_id") or None
        is_active = 1 if request.form.get("is_active") == "1" else 0
        if user_id == actor["id"] and not is_active:
            flash("لا يمكنك إيقاف حسابك الحالي.", "danger")
            return redirect(url_for("edit_user", user_id=user_id))
        try:
            if password:
                db.execute(
                    "UPDATE users SET username=?,full_name=?,password_hash=?,role=?,branch_id=?,employee_id=?,role_id=?,is_active=? WHERE id=?",
                    (username, full_name, generate_password_hash(password), role, branch_id, employee_id, role_id, is_active, user_id),
                )
            else:
                db.execute(
                    "UPDATE users SET username=?,full_name=?,role=?,branch_id=?,employee_id=?,role_id=?,is_active=? WHERE id=?",
                    (username, full_name, role, branch_id, employee_id, role_id, is_active, user_id),
                )
            db.execute("DELETE FROM user_permissions WHERE user_id=?", (user_id,))
            if role != "admin":
                for permission in request.form.getlist("permissions"):
                    if permission in PERMISSIONS:
                        db.execute("INSERT INTO user_permissions(user_id, permission) VALUES(?,?)", (user_id, permission))
            db.commit()
            audit("تعديل مستخدم", f"المستخدم: {username}")
            flash("تم تعديل بيانات المستخدم.", "success")
            return redirect(url_for("users"))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("اسم المستخدم مستخدم بالفعل.", "danger")

    selected_permissions = {
        r["permission"] for r in db.execute("SELECT permission FROM user_permissions WHERE user_id=?", (user_id,)).fetchall()
    }
    branches = db.execute("SELECT * FROM branches ORDER BY id").fetchall()
    employees = db.execute("SELECT id,employee_no,full_name FROM employees WHERE is_active=1 ORDER BY full_name").fetchall()
    roles = db.execute("SELECT * FROM roles WHERE is_active=1 ORDER BY name").fetchall()
    return render_template(
        "edit_user.html", row=row, branches=branches, employees=employees, roles=roles,
        selected_permissions=selected_permissions,
    )


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

@app.route("/roles", methods=["GET","POST"])
@login_required
@permission_required("manage_roles")
def roles_page() -> Any:
    db=get_db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); code=request.form.get("code","").strip().upper(); description=request.form.get("description","").strip() or None
        if not name or not code:
            flash("اسم الدور ورمزه مطلوبان.","danger")
        else:
            try:
                new_role_id = insert_and_get_id(
                    db,
                    "INSERT INTO roles(name,code,description,is_active,created_at) VALUES(?,?,?,1,?)",(name,code,description,now()))
                for perm in request.form.getlist("permissions"):
                    if perm in PERMISSIONS: db.execute("INSERT INTO role_permissions(role_id,permission) VALUES(?,?)",(new_role_id,perm))
                db.commit(); audit("إضافة دور",name); flash("تم إنشاء الدور.","success")
            except sqlite3.IntegrityError:
                db.rollback(); flash("اسم الدور أو رمزه موجود مسبقًا.","danger")
        return redirect(url_for("roles_page"))
    rows=db.execute("SELECT r.*,(SELECT COUNT(*) FROM role_permissions rp WHERE rp.role_id=r.id) permission_count FROM roles r ORDER BY r.name").fetchall()
    return render_template("roles.html",rows=rows)

@app.route("/roles/<int:role_id>/edit", methods=["GET","POST"])
@login_required
@permission_required("manage_roles")
def edit_role(role_id:int) -> Any:
    db=get_db(); row=db.execute("SELECT * FROM roles WHERE id=?",(role_id,)).fetchone()
    if not row: flash("الدور غير موجود.","danger"); return redirect(url_for("roles_page"))
    if request.method=="POST":
        name=request.form.get("name","").strip(); description=request.form.get("description","").strip() or None; active=1 if request.form.get("is_active")=="1" else 0
        try:
            db.execute("UPDATE roles SET name=?,description=?,is_active=? WHERE id=?",(name,description,active,role_id)); db.execute("DELETE FROM role_permissions WHERE role_id=?",(role_id,))
            for perm in request.form.getlist("permissions"):
                if perm in PERMISSIONS: db.execute("INSERT INTO role_permissions(role_id,permission) VALUES(?,?)",(role_id,perm))
            db.commit(); audit("تعديل دور",name); flash("تم تحديث الدور.","success"); return redirect(url_for("roles_page"))
        except sqlite3.IntegrityError: db.rollback(); flash("اسم الدور مستخدم مسبقًا.","danger")
    selected={r["permission"] for r in db.execute("SELECT permission FROM role_permissions WHERE role_id=?",(role_id,)).fetchall()}
    return render_template("edit_role.html",row=row,selected_permissions=selected)

@app.post("/roles/<int:role_id>/clone")
@login_required
@permission_required("manage_roles")
def clone_role(role_id:int) -> Any:
    db=get_db(); source=db.execute("SELECT * FROM roles WHERE id=?",(role_id,)).fetchone()
    if not source:
        flash("الدور المصدر غير موجود.","danger"); return redirect(url_for("roles_page"))
    name=request.form.get("name","").strip(); code=request.form.get("code","").strip().upper()
    if not name or not code:
        flash("اسم ورمز الدور الجديد مطلوبان.","danger"); return redirect(url_for("roles_page"))
    try:
        new_role_id = insert_and_get_id(
            db,
            "INSERT INTO roles(name,code,description,is_active,created_at) VALUES(?,?,?,?,?)",
            (name,code,f"نسخة من {source['name']}",1,now()),
        )
        db.execute("INSERT INTO role_permissions(role_id,permission) SELECT ?,permission FROM role_permissions WHERE role_id=?",
            (new_role_id,role_id))
        db.commit(); audit("نسخ دور",f"{source['name']} ← {name}"); flash("تم نسخ الدور وصلاحياته.","success")
    except sqlite3.IntegrityError:
        db.rollback(); flash("اسم الدور أو رمزه موجود مسبقًا.","danger")
    return redirect(url_for("roles_page"))


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

@app.route("/audit")
@login_required
@permission_required("view_audit")
def audit_page() -> Any:
    db=get_db(); entity_type=request.args.get("entity_type","").strip(); action_code=request.args.get("action_code","").strip()
    where=[]; params=[]
    if entity_type: where.append("a.entity_type=?"); params.append(entity_type)
    if action_code: where.append("a.action_code=?"); params.append(action_code)
    sql="""SELECT a.*,COALESCE(u.full_name,'النظام') user_name,COALESCE(b.name,'-') branch_name
             FROM audit_log a LEFT JOIN users u ON u.id=a.user_id LEFT JOIN branches b ON b.id=a.branch_id"""
    if where: sql += " WHERE " + " AND ".join(where)
    rows=db.execute(sql+" ORDER BY a.id DESC LIMIT 500",params).fetchall()
    return render_template("audit.html", rows=rows, entity_type=entity_type, action_code=action_code)

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


@app.route("/reports")
@login_required
@permission_required("view_reports")
def reports() -> Any:
    db = get_db()
    user = current_user()
    today = datetime.now().date()
    start_date = request.args.get("start_date") or today.replace(day=1).isoformat()
    end_date = request.args.get("end_date") or today.isoformat()
    location_id = request.args.get("location_id", type=int)
    if user and user["role"] != "admin" and user["branch_id"]:
        location_id = user["branch_id"]
    locations = db.execute("SELECT * FROM branches WHERE is_active=1 ORDER BY name").fetchall()

    location_filter = ""
    params: list[Any] = [start_date, end_date]
    if location_id:
        location_filter = " AND b.id=?"
        params.append(location_id)

    revenues_rows = db.execute(
        """SELECT r.revenue_date report_date,b.name location_name,r.amount,r.payment_method,
                  COALESCE(r.notes,'') notes,u.full_name creator
           FROM revenues r JOIN branches b ON b.id=r.branch_id JOIN users u ON u.id=r.created_by
           WHERE r.revenue_date BETWEEN ? AND ?""" + location_filter +
        " ORDER BY r.revenue_date DESC,r.id DESC", params,
    ).fetchall()
    expenses_rows = db.execute(
        """SELECT e.expense_date report_date,b.name location_name,e.category,e.financial_classification,e.asset_type,e.amount,e.payment_method,
                  COALESCE(e.notes,'') notes,u.full_name creator
           FROM expenses e JOIN branches b ON b.id=e.branch_id JOIN users u ON u.id=e.created_by
           WHERE e.expense_date BETWEEN ? AND ?""" + location_filter +
        " ORDER BY e.expense_date DESC,e.id DESC", params,
    ).fetchall()
    payments_rows = db.execute(
        """SELECT p.payment_date report_date,b.name location_name,s.name supplier_name,p.amount,p.payment_method,
                  COALESCE(p.notes,'') notes,u.full_name creator
           FROM supplier_payments p JOIN branches b ON b.id=p.branch_id
           JOIN suppliers s ON s.id=p.supplier_id JOIN users u ON u.id=p.created_by
           WHERE p.payment_date BETWEEN ? AND ?""" + location_filter +
        " ORDER BY p.payment_date DESC,p.id DESC", params,
    ).fetchall()

    accounts_params: list[Any] = []
    account_filter = ""
    if location_id:
        account_filter = " WHERE b.id=?"
        accounts_params.append(location_id)
    accounts_rows = db.execute(
        """SELECT b.name location_name,a.name account_name,a.account_type,
                  COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
           LEFT JOIN financial_ledger l ON l.account_id=a.id""" + account_filter +
        " GROUP BY a.id,b.id,b.name ORDER BY b.name,a.name", accounts_params,
    ).fetchall()

    totals = {
        "revenues": sum(float(r["amount"]) for r in revenues_rows),
        "expenses": sum(float(r["amount"]) for r in expenses_rows),
        "operating_expenses": sum(float(r["amount"]) for r in expenses_rows if r["financial_classification"] == "OPERATING"),
        "assets": sum(float(r["amount"]) for r in expenses_rows if r["financial_classification"] == "ASSET"),
        "liabilities": sum(float(r["amount"]) for r in expenses_rows if r["financial_classification"] == "LIABILITY"),
        "payments": sum(float(r["amount"]) for r in payments_rows),
    }
    return render_template(
        "reports.html", locations=locations, selected_location=location_id,
        start_date=start_date, end_date=end_date, revenues_rows=revenues_rows,
        expenses_rows=expenses_rows, payments_rows=payments_rows,
        accounts_rows=accounts_rows, totals=totals,
        financial_classifications=load_financial_classifications(active_only=False), asset_types=ASSET_TYPES,
    )


def _report_payload(report_type: str, start_date: str, end_date: str, location_id: int | None) -> tuple[str, list[str], list[list[object]], str]:
    db = get_db()
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"]:
        location_id = user["branch_id"]
    location_name = "جميع المواقع"
    if location_id:
        row = db.execute("SELECT name FROM branches WHERE id=?", (location_id,)).fetchone()
        location_name = row["name"] if row else "موقع غير معروف"
    location_filter = ""
    params: list[Any] = [start_date, end_date]
    if location_id:
        location_filter = " AND b.id=?"
        params.append(location_id)

    if report_type == "revenues":
        title = "تقرير الإيرادات"
        columns = ["التاريخ", "الموقع", "القيمة", "الحسابات", "الملاحظات", "المستخدم"]
        data = db.execute("""SELECT r.revenue_date,b.name,r.amount,r.payment_method,COALESCE(r.notes,''),u.full_name
            FROM revenues r JOIN branches b ON b.id=r.branch_id JOIN users u ON u.id=r.created_by
            WHERE r.revenue_date BETWEEN ? AND ?""" + location_filter + " ORDER BY r.revenue_date,r.id", params).fetchall()
    elif report_type == "expenses":
        title = "تقرير المصروفات"
        columns = ["التاريخ", "الموقع", "نوع المصروف", "التصنيف المالي", "نوع الأصل", "القيمة", "الحسابات", "الملاحظات", "المستخدم"]
        raw_data = db.execute("""SELECT e.expense_date,b.name,e.category,e.financial_classification,COALESCE(e.asset_type,''),e.amount,e.payment_method,COALESCE(e.notes,''),u.full_name
            FROM expenses e JOIN branches b ON b.id=e.branch_id JOIN users u ON u.id=e.created_by
            WHERE e.expense_date BETWEEN ? AND ?""" + location_filter + " ORDER BY e.expense_date,e.id", params).fetchall()
        classification_labels = load_financial_classifications(active_only=False)
        data = [[r[0], r[1], r[2], classification_labels.get(r[3], r[3]), ASSET_TYPES.get(r[4], r[4]) if r[4] else "-", r[5], r[6], r[7], r[8]] for r in raw_data]
    elif report_type == "payments":
        title = "تقرير سدادات الموردين"
        columns = ["التاريخ", "الموقع", "المورد", "القيمة", "الحسابات", "الملاحظات", "المستخدم"]
        data = db.execute("""SELECT p.payment_date,b.name,s.name,p.amount,p.payment_method,COALESCE(p.notes,''),u.full_name
            FROM supplier_payments p JOIN branches b ON b.id=p.branch_id JOIN suppliers s ON s.id=p.supplier_id
            JOIN users u ON u.id=p.created_by WHERE p.payment_date BETWEEN ? AND ?""" + location_filter +
            " ORDER BY p.payment_date,p.id", params).fetchall()
    elif report_type == "external_debts":
        title = "تقرير الديون الخارجية"
        columns = ["المرجع","التاريخ","الاستحقاق","الموقع","الجهة","نوع الجهة","نوع الدين","الإجمالي","المسدد","المتبقي"]
        data = db.execute("""SELECT d.reference_no,d.debt_date,COALESCE(d.due_date,''),b.name,d.party_name,d.party_type,d.debt_type,d.amount,
            COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id),0),
            GREATEST(d.amount-COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id),0),0)
            FROM external_debts d JOIN branches b ON b.id=d.branch_id WHERE d.debt_date BETWEEN ? AND ?""" + location_filter + " ORDER BY d.debt_date,d.id", params).fetchall()
        data = [[x[0],x[1],x[2],x[3],x[4],PARTY_TYPES.get(x[5],x[5]),DEBT_TYPES.get(x[6],x[6]),x[7],x[8],x[9]] for x in data]
    elif report_type == "accounts":
        title = "تقرير الحسابات المالية"
        columns = ["الموقع", "الحساب", "النوع", "الرصيد"]
        account_params: list[Any] = []
        account_filter = ""
        if location_id:
            account_filter = " WHERE b.id=?"
            account_params.append(location_id)
        data = db.execute("""SELECT b.name,a.name,a.account_type,
            COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0)
            FROM financial_accounts a JOIN branches b ON b.id=a.branch_id
            LEFT JOIN financial_ledger l ON l.account_id=a.id""" + account_filter +
            " GROUP BY a.id,b.id,b.name ORDER BY b.name,a.name", account_params).fetchall()
    else:
        raise ValueError("نوع التقرير غير مدعوم.")
    return title, columns, [list(row) for row in data], location_name


@app.get("/reports/export/<report_type>/<file_format>")
@login_required
@permission_required("view_reports")
def export_report(report_type: str, file_format: str) -> Any:
    today = datetime.now().date()
    start_date = request.args.get("start_date") or today.replace(day=1).isoformat()
    end_date = request.args.get("end_date") or today.isoformat()
    location_id = request.args.get("location_id", type=int)
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
        title, columns, rows, location_name = _report_payload(report_type, start_date, end_date, location_id)
    except (ValueError, sqlite3.Error) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("reports"))

    settings = all_settings()
    company_name = settings.get("company_name", "Pharma ERP")
    subtitle = settings.get("system_subtitle", "الإدارة المالية")
    metadata = [
        f"الفترة: من {start_date} إلى {end_date}",
        f"الموقع: {location_name}",
        f"تاريخ الإصدار: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"أصدره: {current_user()['full_name']}",
    ]
    safe_name = f"{report_type}_{start_date}_{end_date}"
    if file_format == "pdf":
        stream = build_pdf(title, company_name, subtitle, columns, rows, metadata)
        mimetype, extension = "application/pdf", "pdf"
    elif file_format == "docx":
        stream = build_docx(title, company_name, subtitle, columns, rows, metadata)
        mimetype, extension = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    elif file_format == "xlsx":
        stream = build_excel(title, company_name, subtitle, columns, rows, metadata)
        mimetype, extension = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        flash("صيغة التصدير غير مدعومة.", "danger")
        return redirect(url_for("reports"))
    audit("تصدير تقرير", f"{title} - {file_format} - {start_date} إلى {end_date}")
    return send_file(stream, mimetype=mimetype, as_attachment=True, download_name=f"{safe_name}.{extension}")


@app.route("/settings", methods=["GET", "POST"])
@login_required
@permission_required("manage_settings")
def settings_page() -> Any:
    db = get_db()
    sections = {
        "company": {
            "title": "بيانات الشركة",
            "keys": ("company_name", "system_subtitle", "phone", "address", "commercial_registration", "tax_number"),
        },
        "financial": {
            "title": "الإعدادات المالية",
            "keys": ("currency", "currency_decimals", "fiscal_year_start", "allow_negative_balance"),
        },
        "taxes": {
            "title": "الضرائب",
            "keys": ("tax_enabled", "default_tax_rate", "prices_include_tax", "tax_invoice_label"),
        },
        "notifications": {
            "title": "الإشعارات",
            "keys": ("notify_low_stock", "notify_expiry_days", "notify_pending_approvals", "notify_unpaid_payroll"),
        },
        "security": {
            "title": "الأمان",
            "keys": ("session_timeout_minutes", "password_min_length", "login_attempt_limit", "audit_retention_days"),
        },
        "appearance": {
            "title": "المظهر",
            "keys": ("default_theme", "sidebar_compact", "dashboard_refresh_minutes"),
        },
    }
    section = request.args.get("section", "company")
    if section not in sections and section not in {"backup", "version"}:
        section = "company"
    if request.method == "POST":
        section = request.form.get("section", "company")
        definition = sections.get(section)
        if not definition:
            flash("قسم الإعدادات غير صالح.", "danger")
            return redirect(url_for("settings_page"))
        checkbox_keys = {
            "allow_negative_balance", "tax_enabled", "prices_include_tax", "notify_low_stock",
            "notify_pending_approvals", "notify_unpaid_payroll", "sidebar_compact",
        }
        for key in definition["keys"]:
            value = ("1" if request.form.get(key) else "0") if key in checkbox_keys else request.form.get(key, "").strip()
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        db.commit()
        audit("تعديل إعدادات النظام", f"تم تحديث قسم: {definition['title']}")
        flash(f"تم حفظ {definition['title']} بنجاح.", "success")
        return redirect(url_for("settings_page", section=section))
    backups_dir = backup_directory()
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
        backups = sorted(
            (f for f in backups_dir.glob("*.db")),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        app.logger.exception("Could not prepare the backup directory: %s", backups_dir)
        backups = []
    return render_template(
        "settings.html", settings=all_settings(), backups=backups[:20], section=section, sections=sections,
    )


@app.post("/backup/create")
@login_required
@permission_required("manage_settings")
def create_backup() -> Any:
    # The built-in backup routine copies a local SQLite database. Production
    # uses PostgreSQL/Supabase, whose backups must be managed by Supabase rather
    # than by opening PHARMA_DB_PATH as a SQLite file.
    if DATABASE_URL:
        flash(
            "قاعدة البيانات سحابية على Supabase؛ تُدار النسخ الاحتياطية من لوحة Supabase ولا يمكن إنشاؤها كملف SQLite من Vercel.",
            "info",
        )
        return redirect(url_for("settings_page", section="backup"))

    backups_dir = backup_directory()
    backups_dir.mkdir(parents=True, exist_ok=True)
    filename = f"pharmacy_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    target = backups_dir / filename
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    finally:
        source.close()
        destination.close()
    audit("إنشاء نسخة احتياطية", filename)
    return send_file(target, as_attachment=True, download_name=filename)


@app.route("/backup/download/<path:filename>")
@login_required
@permission_required("manage_settings")
def download_backup(filename: str) -> Any:
    safe_name = Path(filename).name
    target = backup_directory() / safe_name
    if not target.exists() or target.suffix != ".db":
        flash("النسخة الاحتياطية غير موجودة.", "danger")
        return redirect(url_for("settings_page"))
    return send_file(target, as_attachment=True, download_name=safe_name)



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

    # الإيرادات والمصروفات والسدادات
    financial_specs = [
        ("الإيرادات", "revenues", "revenue_date", "إيراد", "revenues", "view_revenue"),
        ("المصروفات", "expenses", "expense_date", "مصروف", "expenses", "view_expenses"),
        ("السدادات", "supplier_payments", "payment_date", "سداد مورد", "payments", "view_suppliers"),
    ]
    numeric_query = query.replace(",", "").strip()
    for group, table, date_col, label, endpoint, permission in financial_specs:
        if not has_permission(permission):
            continue
        sql = f"SELECT id,branch_id,amount,{date_col} AS tx_date,notes FROM {table} WHERE (CAST(id AS TEXT) LIKE ? OR CAST(amount AS TEXT) LIKE ? OR COALESCE(notes,'') LIKE ?)"
        params = [like, like, like]
        if branch_id:
            sql += " AND branch_id=?"
            params.append(branch_id)
        sql += " ORDER BY id DESC LIMIT 20"
        for row in db.execute(sql, params).fetchall():
            results.append({"group":group,"title":f"{label} #{row['id']} — {float(row['amount']):,.2f}","subtitle":f"التاريخ: {row['tx_date']} — {row['notes'] or 'بدون ملاحظات'}","url":url_for(endpoint)})

    return render_template("search_results.html", query=query, results=results)



@app.route("/inventory", methods=["GET", "POST"])
@login_required
@permission_required("view_inventory")
def inventory() -> Any:
    """Manage products and per-location inventory balances."""
    db = get_db()
    if request.method == "POST":
        if not has_permission("manage_inventory"):
            flash("ليس لديك صلاحية إدارة المخزون.", "danger")
            return redirect(url_for("inventory"))
        action = request.form.get("action", "product")
        try:
            if action == "product":
                name = request.form.get("name", "").strip()
                sku = request.form.get("sku", "").strip() or None
                unit = request.form.get("unit", "علبة").strip() or "علبة"
                notes = request.form.get("notes", "").strip()
                if not name:
                    raise ValueError("اسم الصنف مطلوب.")
                db.execute(
                    "INSERT INTO products(name,sku,unit,is_active,notes,created_at) VALUES(?,?,?,?,?,?)",
                    (name, sku, unit, 1, notes, now()),
                )
                db.commit()
                audit("إضافة صنف", name)
                flash("تمت إضافة الصنف.", "success")
            elif action == "adjust":
                product_id = int(request.form["product_id"])
                location_id = int(request.form["location_id"])
                quantity = float(request.form["quantity"])
                if quantity < 0:
                    raise ValueError("الكمية لا يمكن أن تكون سالبة.")
                product = db.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
                location = db.execute("SELECT id FROM branches WHERE id=? AND is_active=1", (location_id,)).fetchone()
                if not product or not location:
                    raise ValueError("الصنف أو الموقع غير موجود.")
                db.execute(
                    """INSERT INTO inventory_balances(product_id,location_id,quantity,updated_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(product_id,location_id)
                       DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at""",
                    (product_id, location_id, quantity, now()),
                )
                db.commit()
                audit("تسوية رصيد مخزون", f"الصنف {product_id}، الموقع {location_id}، الكمية {quantity}")
                flash("تم تحديث رصيد الصنف.", "success")
            else:
                raise ValueError("عملية المخزون غير معروفة.")
        except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
            db.rollback()
            flash(str(exc) or "تعذر حفظ بيانات المخزون.", "danger")
        return redirect(url_for("inventory", location_id=request.form.get("location_id", "")))

    location_id = request.args.get("location_id", type=int)
    locations = db.execute(
        """SELECT * FROM branches WHERE is_active=1
           ORDER BY CASE location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,id"""
    ).fetchall()
    if not location_id and locations:
        location_id = locations[0]["id"]
    rows = db.execute(
        """SELECT p.id,p.name,p.sku,p.unit,p.is_active,COALESCE(i.quantity,0) quantity,i.updated_at
           FROM products p
           LEFT JOIN inventory_balances i ON i.product_id=p.id AND i.location_id=?
           ORDER BY p.name""",
        (location_id,),
    ).fetchall() if location_id else []
    products = db.execute("SELECT * FROM products WHERE is_active=1 ORDER BY name").fetchall()
    return render_template(
        "inventory.html", rows=rows, products=products, locations=locations,
        selected_location=location_id,
    )


@app.post("/products/<int:product_id>/toggle")
@login_required
@permission_required("manage_inventory")
def toggle_product(product_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not row:
        flash("الصنف غير موجود.", "danger")
        return redirect(url_for("inventory"))
    db.execute(
        "UPDATE products SET is_active=CASE is_active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
        (product_id,),
    )
    db.commit()
    audit("تغيير حالة صنف", row["name"])
    flash("تم تحديث حالة الصنف.", "success")
    return redirect(url_for("inventory"))


def _next_stock_transfer_number() -> str:
    """Generate a collision-resistant stock transfer reference."""
    return f"STR-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


@app.route("/stock-transfers", methods=["GET", "POST"])
@login_required
@permission_required("manage_stock_transfers")
def stock_transfers() -> Any:
    db = get_db()
    user = current_user()
    if request.method == "POST":
        try:
            from_location = int(request.form["from_location_id"])
            to_location = int(request.form["to_location_id"])
            if from_location == to_location:
                raise ValueError("يجب اختيار موقعين مختلفين.")
            valid_locations = db.execute(
                "SELECT COUNT(*) c FROM branches WHERE id IN (?,?) AND is_active=1",
                (from_location, to_location),
            ).fetchone()["c"]
            if valid_locations != 2:
                raise ValueError("أحد موقعي التحويل غير موجود أو موقوف.")

            product_ids = request.form.getlist("product_id")
            quantities = request.form.getlist("quantity_sent")
            costs = request.form.getlist("unit_cost")
            items: list[tuple[int, float, float]] = []
            seen_products: set[int] = set()
            for pid, qty, cost in zip(product_ids, quantities, costs):
                if not pid or not qty:
                    continue
                product_id = int(pid)
                quantity = float(qty)
                unit_cost = float(cost or 0)
                if quantity <= 0:
                    raise ValueError("كمية التحويل يجب أن تكون أكبر من صفر.")
                if unit_cost < 0:
                    raise ValueError("تكلفة الوحدة لا يمكن أن تكون سالبة.")
                if product_id in seen_products:
                    raise ValueError("لا يمكن تكرار الصنف نفسه داخل أمر التحويل.")
                if not db.execute("SELECT 1 FROM products WHERE id=? AND is_active=1", (product_id,)).fetchone():
                    raise ValueError("أحد الأصناف غير موجود أو موقوف.")
                seen_products.add(product_id)
                items.append((product_id, quantity, unit_cost))
            if not items:
                raise ValueError("أضف صنفًا واحدًا على الأقل.")

            transfer_date = request.form.get("transfer_date") or datetime.now().date().isoformat()
            transfer_id = insert_and_get_id(
                db,
                """INSERT INTO stock_transfers(
                       transfer_number,from_location_id,to_location_id,transfer_date,status,
                       notes,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (_next_stock_transfer_number(), from_location, to_location, transfer_date, "DRAFT",
                 request.form.get("notes", "").strip(), user["id"], now()),
            )
            for product_id, quantity, unit_cost in items:
                db.execute(
                    "INSERT INTO stock_transfer_items(transfer_id,product_id,quantity_sent,unit_cost) VALUES(?,?,?,?)",
                    (transfer_id, product_id, quantity, unit_cost),
                )
            db.commit()
            audit("إنشاء تحويل مخزني", f"رقم {transfer_id}")
            flash("تم إنشاء أمر التحويل كمسودة.", "success")
        except (ValueError, TypeError, sqlite3.Error) as exc:
            db.rollback()
            flash(str(exc) or "تعذر إنشاء أمر التحويل.", "danger")
        return redirect(url_for("stock_transfers"))

    rows = db.execute(
        """SELECT t.*,f.name from_name,d.name to_name,u.full_name creator,
                  (SELECT COUNT(*) FROM stock_transfer_items x WHERE x.transfer_id=t.id) items_count,
                  (SELECT COALESCE(SUM(quantity_sent),0) FROM stock_transfer_items x WHERE x.transfer_id=t.id) total_qty
           FROM stock_transfers t
           JOIN branches f ON f.id=t.from_location_id
           JOIN branches d ON d.id=t.to_location_id
           JOIN users u ON u.id=t.created_by
           ORDER BY t.id DESC"""
    ).fetchall()
    locations = db.execute(
        """SELECT * FROM branches WHERE is_active=1
           ORDER BY CASE location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,id"""
    ).fetchall()
    products = db.execute("SELECT * FROM products WHERE is_active=1 ORDER BY name").fetchall()
    return render_template(
        "stock_transfers.html", rows=rows, locations=locations, products=products,
        today=datetime.now().date().isoformat(),
    )


@app.route("/stock-transfers/<int:transfer_id>")
@login_required
@permission_required("manage_stock_transfers")
def stock_transfer_detail(transfer_id: int) -> Any:
    db = get_db()
    transfer = db.execute(
        """SELECT t.*,f.name from_name,d.name to_name,u.full_name creator,
                  su.full_name sender,ru.full_name receiver
           FROM stock_transfers t
           JOIN branches f ON f.id=t.from_location_id
           JOIN branches d ON d.id=t.to_location_id
           JOIN users u ON u.id=t.created_by
           LEFT JOIN users su ON su.id=t.sent_by
           LEFT JOIN users ru ON ru.id=t.received_by
           WHERE t.id=?""",
        (transfer_id,),
    ).fetchone()
    if not transfer:
        flash("أمر التحويل غير موجود.", "danger")
        return redirect(url_for("stock_transfers"))
    items = db.execute(
        """SELECT i.*,p.name product_name,p.sku,p.unit,
                  COALESCE((SELECT quantity FROM inventory_balances b
                            WHERE b.product_id=i.product_id AND b.location_id=?),0) source_balance
           FROM stock_transfer_items i JOIN products p ON p.id=i.product_id
           WHERE i.transfer_id=? ORDER BY i.id""",
        (transfer["from_location_id"], transfer_id),
    ).fetchall()
    return render_template("stock_transfer_detail.html", transfer=transfer, items=items)


@app.post("/stock-transfers/<int:transfer_id>/send")
@login_required
@permission_required("manage_stock_transfers")
def send_stock_transfer(transfer_id: int) -> Any:
    db = get_db()
    user = current_user()
    transfer = db.execute("SELECT * FROM stock_transfers WHERE id=?", (transfer_id,)).fetchone()
    if not transfer or transfer["status"] != "DRAFT":
        flash("لا يمكن إرسال هذا الأمر.", "danger")
        return redirect(url_for("stock_transfer_detail", transfer_id=transfer_id))
    items = db.execute("SELECT * FROM stock_transfer_items WHERE transfer_id=?", (transfer_id,)).fetchall()
    if not items:
        flash("أمر التحويل لا يحتوي على أصناف.", "danger")
        return redirect(url_for("stock_transfer_detail", transfer_id=transfer_id))
    try:
        db.execute("BEGIN IMMEDIATE")
        for item in items:
            balance = db.execute(
                "SELECT quantity FROM inventory_balances WHERE product_id=? AND location_id=?",
                (item["product_id"], transfer["from_location_id"]),
            ).fetchone()
            if not balance or float(balance["quantity"]) + 1e-9 < float(item["quantity_sent"]):
                product = db.execute("SELECT name FROM products WHERE id=?", (item["product_id"],)).fetchone()
                raise ValueError(f"الرصيد غير كافٍ للصنف: {product['name'] if product else item['product_id']}.")
        for item in items:
            db.execute(
                """UPDATE inventory_balances SET quantity=quantity-?,updated_at=?
                   WHERE product_id=? AND location_id=?""",
                (item["quantity_sent"], now(), item["product_id"], transfer["from_location_id"]),
            )
        db.execute(
            "UPDATE stock_transfers SET status='SENT',sent_by=?,sent_at=? WHERE id=? AND status='DRAFT'",
            (user["id"], now(), transfer_id),
        )
        db.commit()
        audit("إرسال تحويل مخزني", transfer["transfer_number"])
        flash("تم إرسال التحويل وخصم الكميات من الموقع المصدر.", "success")
    except (ValueError, sqlite3.Error) as exc:
        db.rollback()
        flash(str(exc) or "تعذر إرسال التحويل.", "danger")
    return redirect(url_for("stock_transfer_detail", transfer_id=transfer_id))


@app.post("/stock-transfers/<int:transfer_id>/receive")
@login_required
@permission_required("manage_stock_transfers")
def receive_stock_transfer(transfer_id: int) -> Any:
    db = get_db()
    user = current_user()
    transfer = db.execute("SELECT * FROM stock_transfers WHERE id=?", (transfer_id,)).fetchone()
    if not transfer or transfer["status"] != "SENT":
        flash("لا يمكن استلام هذا الأمر.", "danger")
        return redirect(url_for("stock_transfer_detail", transfer_id=transfer_id))
    items = db.execute("SELECT * FROM stock_transfer_items WHERE transfer_id=?", (transfer_id,)).fetchall()
    try:
        db.execute("BEGIN IMMEDIATE")
        for item in items:
            raw = request.form.get(f"received_{item['id']}")
            quantity = float(raw) if raw not in (None, "") else float(item["quantity_sent"])
            if quantity < 0 or quantity > float(item["quantity_sent"]):
                raise ValueError("الكمية المستلمة يجب أن تكون بين صفر والكمية المرسلة.")
            db.execute("UPDATE stock_transfer_items SET quantity_received=? WHERE id=?", (quantity, item["id"]))
            db.execute(
                """INSERT INTO inventory_balances(product_id,location_id,quantity,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(product_id,location_id)
                   DO UPDATE SET quantity=quantity+excluded.quantity,updated_at=excluded.updated_at""",
                (item["product_id"], transfer["to_location_id"], quantity, now()),
            )
        db.execute(
            "UPDATE stock_transfers SET status='RECEIVED',received_by=?,received_at=? WHERE id=? AND status='SENT'",
            (user["id"], now(), transfer_id),
        )
        db.commit()
        audit("استلام تحويل مخزني", transfer["transfer_number"])
        flash("تم استلام التحويل وإضافة الكميات للموقع المستلم.", "success")
    except (ValueError, TypeError, sqlite3.Error) as exc:
        db.rollback()
        flash(str(exc) or "تعذر استلام التحويل.", "danger")
    return redirect(url_for("stock_transfer_detail", transfer_id=transfer_id))


@app.post("/stock-transfers/<int:transfer_id>/cancel")
@login_required
@permission_required("manage_stock_transfers")
def cancel_stock_transfer(transfer_id: int) -> Any:
    db = get_db()
    transfer = db.execute("SELECT * FROM stock_transfers WHERE id=?", (transfer_id,)).fetchone()
    if transfer and transfer["status"] == "DRAFT":
        db.execute("UPDATE stock_transfers SET status='CANCELLED' WHERE id=? AND status='DRAFT'", (transfer_id,))
        db.commit()
        audit("إلغاء تحويل مخزني", transfer["transfer_number"])
        flash("تم إلغاء أمر التحويل.", "success")
    else:
        flash("يمكن إلغاء المسودات فقط.", "danger")
    return redirect(url_for("stock_transfers"))


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

# v3.8 — الخزينة الرئيسية وخزائن الفروع والتوريدات المالية
TREASURY_STATUS_LABELS = {
    "DRAFT": "مسودة",
    "SENT": "بانتظار الاستلام",
    "RECEIVED": "مستلم",
    "CANCELLED": "ملغي",
}


def _treasury_transfer_number() -> str:
    db = get_db()
    year = datetime.now().year
    prefix = f"TRS-{year}-"
    row = db.execute(
        "SELECT transfer_number FROM treasury_transfers WHERE transfer_number LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row:
        try:
            sequence = int(row["transfer_number"].rsplit("-", 1)[-1]) + 1
        except (TypeError, ValueError):
            sequence = 1
    return f"{prefix}{sequence:06d}"


def _treasury_locations_payload() -> tuple[list[dict[str, Any]], dict[str, float]]:
    db = get_db()
    user = current_user()
    clause = ""
    params: list[Any] = []
    if user and user["role"] != "admin" and user["branch_id"]:
        clause = " AND b.id=?"
        params = [user["branch_id"]]
    rows = db.execute(
        """SELECT b.id location_id,b.name location_name,b.location_type,
                  a.id account_id,a.name account_name,a.account_type,
                  COALESCE(SUM(CASE WHEN l.direction='IN' THEN l.amount ELSE -l.amount END),0) balance
           FROM branches b JOIN financial_accounts a ON a.branch_id=b.id
           LEFT JOIN financial_ledger l ON l.account_id=a.id
           WHERE b.is_active=1""" + clause + """
           GROUP BY b.id,b.name,b.location_type,a.id
           ORDER BY CASE b.location_type WHEN 'MAIN_WAREHOUSE' THEN 0 ELSE 1 END,b.name,a.account_type,a.name""",
        params,
    ).fetchall()
    locations: dict[int, dict[str, Any]] = {}
    totals = {"main": 0.0, "branches": 0.0, "company": 0.0}
    for row in rows:
        item = locations.setdefault(row["location_id"], {
            "id": row["location_id"], "name": row["location_name"],
            "location_type": row["location_type"], "accounts": [], "total": 0.0,
        })
        account = dict(row)
        account["balance"] = float(row["balance"] or 0)
        account["type_label"] = ACCOUNT_TYPE_LABELS.get(row["account_type"], row["account_type"])
        item["accounts"].append(account)
        item["total"] += account["balance"]
        totals["company"] += account["balance"]
        if row["location_type"] == "MAIN_WAREHOUSE":
            totals["main"] += account["balance"]
        else:
            totals["branches"] += account["balance"]
    return list(locations.values()), totals


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


@app.get("/approval-definitions")
@login_required
@permission_required("manage_approvals")
def approval_definitions_page() -> Any:
    rows = get_db().execute("SELECT * FROM approval_definitions ORDER BY name").fetchall()
    return render_template("approval_definitions.html", rows=rows)


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

# ===== v4.1 Financial Reports =====
FINANCIAL_ADJUSTMENT_LABELS = {
    "ASSET": "أصل", "LIABILITY": "خصم / التزام", "EQUITY": "حقوق ملكية",
    "REVENUE": "إيراد إضافي", "EXPENSE": "مصروف إضافي",
    "CASH_IN": "تدفق نقدي داخل", "CASH_OUT": "تدفق نقدي خارج",
}


def _financial_scope(alias: str, location_id: int | None) -> tuple[str, list[Any]]:
    user = current_user()
    if user and user["role"] != "admin" and user["branch_id"]:
        location_id = user["branch_id"]
    return ((f" AND {alias}.branch_id=?", [location_id]) if location_id else ("", []))


def _adjustment_total(types: tuple[str, ...], end_date: str, location_id: int | None, start_date: str | None = None) -> float:
    db=get_db(); placeholders=','.join('?' for _ in types)
    sql=f"SELECT COALESCE(SUM(CASE direction WHEN 'INCREASE' THEN amount ELSE -amount END),0) FROM financial_report_adjustments WHERE status='ACTIVE' AND adjustment_type IN ({placeholders}) AND adjustment_date<=?"
    params:list[Any]=list(types)+[end_date]
    if start_date:
        sql += " AND adjustment_date>=?"; params.append(start_date)
    user=current_user()
    if user and user['role']!='admin' and user['branch_id']:
        location_id=user['branch_id']
    if location_id:
        sql += " AND (branch_id=? OR branch_id IS NULL)"; params.append(location_id)
    return float(db.execute(sql,params).fetchone()[0] or 0)


def _financial_report_data(report_name: str, start_date: str, end_date: str, location_id: int | None) -> dict[str, Any]:
    db=get_db(); user=current_user()
    if user and user['role']!='admin' and user['branch_id']: location_id=user['branch_id']
    loc_clause=''; loc_params:list[Any]=[]
    if location_id: loc_clause=' AND branch_id=?'; loc_params=[location_id]

    if report_name=='income-statement':
        revenue=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM revenues WHERE revenue_date BETWEEN ? AND ?"+loc_clause,[start_date,end_date]+loc_params).fetchone()[0])
        operating=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN ? AND ? AND financial_classification='OPERATING'"+loc_clause,[start_date,end_date]+loc_params).fetchone()[0])
        manual_revenue=_adjustment_total(('REVENUE',),end_date,location_id,start_date)
        manual_expense=_adjustment_total(('EXPENSE',),end_date,location_id,start_date)
        rows=[('الإيرادات من النظام',revenue),('تعديلات الإيرادات',manual_revenue),('إجمالي الإيرادات',revenue+manual_revenue),('المصروفات التشغيلية',operating),('تعديلات المصروفات',manual_expense),('إجمالي المصروفات',operating+manual_expense),('صافي الربح / الخسارة',(revenue+manual_revenue)-(operating+manual_expense))]
        return {'title':'قائمة الدخل','rows':rows,'total':rows[-1][1]}

    if report_name=='cash-flow':
        scope,sp=_financial_scope('l',location_id)
        opening=float(db.execute("SELECT COALESCE(SUM(CASE direction WHEN 'IN' THEN amount ELSE -amount END),0) FROM financial_ledger l WHERE transaction_date<?"+scope,[start_date]+sp).fetchone()[0])
        incoming=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM financial_ledger l WHERE direction='IN' AND transaction_date BETWEEN ? AND ?"+scope,[start_date,end_date]+sp).fetchone()[0])
        outgoing=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM financial_ledger l WHERE direction='OUT' AND transaction_date BETWEEN ? AND ?"+scope,[start_date,end_date]+sp).fetchone()[0])
        manual_in=_adjustment_total(('CASH_IN',),end_date,location_id,start_date); manual_out=_adjustment_total(('CASH_OUT',),end_date,location_id,start_date)
        net=incoming+manual_in-outgoing-manual_out
        rows=[('رصيد أول الفترة',opening),('التدفقات الداخلة من النظام',incoming),('تعديلات تدفق داخل',manual_in),('التدفقات الخارجة من النظام',outgoing),('تعديلات تدفق خارج',manual_out),('صافي التدفق النقدي',net),('رصيد آخر الفترة',opening+net)]
        return {'title':'قائمة التدفق النقدي','rows':rows,'total':opening+net}

    # balance sheet as of end date
    scope,sp=_financial_scope('l',location_id)
    cash=float(db.execute("SELECT COALESCE(SUM(CASE l.direction WHEN 'IN' THEN l.amount ELSE -l.amount END),0) FROM financial_ledger l WHERE l.transaction_date<=?"+scope,[end_date]+sp).fetchone()[0])
    asset_clause=''; asset_params:list[Any]=[end_date]
    if location_id: asset_clause=' AND branch_id=?'; asset_params.append(location_id)
    fixed_rows=db.execute("SELECT COALESCE(asset_type,'OTHER') asset_type,COALESCE(SUM(amount),0) total FROM expenses WHERE financial_classification='ASSET' AND expense_date<=?"+asset_clause+" GROUP BY COALESCE(asset_type,'OTHER')",asset_params).fetchall()
    fixed_assets=sum(float(r['total']) for r in fixed_rows)
    debt_scope=''; debt_params:list[Any]=[end_date]
    if location_id: debt_scope=' AND d.branch_id=?'; debt_params.append(location_id)
    receivables=float(db.execute("""SELECT COALESCE(SUM(GREATEST(d.amount-COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id AND p.payment_date<=?),0),0)),0) FROM external_debts d WHERE d.debt_type='RECEIVABLE' AND d.debt_date<=?"""+debt_scope,[end_date,end_date]+([location_id] if location_id else [])).fetchone()[0])
    payables=float(db.execute("""SELECT COALESCE(SUM(GREATEST(d.amount-COALESCE((SELECT SUM(p.amount) FROM external_debt_payments p WHERE p.debt_id=d.id AND p.payment_date<=?),0),0)),0) FROM external_debts d WHERE d.debt_type='PAYABLE' AND d.debt_date<=?"""+debt_scope,[end_date,end_date]+([location_id] if location_id else [])).fetchone()[0])
    supplier_clause=''; supplier_params:list[Any]=[end_date]
    if location_id: supplier_clause=' AND a.location_id=?'; supplier_params.append(location_id)
    suppliers=float(db.execute("""SELECT COALESCE(SUM(GREATEST(a.opening_due-COALESCE((SELECT SUM(p.amount) FROM supplier_payments p WHERE p.supplier_account_id=a.id AND p.payment_date<=?),0),0)),0) FROM supplier_location_accounts a WHERE 1=1"""+supplier_clause,supplier_params).fetchone()[0])
    liability_exp=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE financial_classification='LIABILITY' AND expense_date<=?"+asset_clause,asset_params).fetchone()[0])
    manual_asset=_adjustment_total(('ASSET',),end_date,location_id); manual_liab=_adjustment_total(('LIABILITY',),end_date,location_id); manual_equity=_adjustment_total(('EQUITY',),end_date,location_id)
    rev=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM revenues WHERE revenue_date<=?"+loc_clause,[end_date]+loc_params).fetchone()[0])
    op_exp=float(db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE financial_classification='OPERATING' AND expense_date<=?"+loc_clause,[end_date]+loc_params).fetchone()[0])
    retained=rev-op_exp+_adjustment_total(('REVENUE',),end_date,location_id)-_adjustment_total(('EXPENSE',),end_date,location_id)
    assets=cash+receivables+fixed_assets+manual_asset; liabilities=suppliers+payables+liability_exp+manual_liab; equity=manual_equity+retained; difference=assets-(liabilities+equity)
    rows=[('النقدية والحسابات المالية',cash),('ديون خارجية لنا',receivables),('الأصول الثابتة',fixed_assets),('تعديلات الأصول',manual_asset),('إجمالي الأصول',assets),('أرصدة الموردين',suppliers),('ديون خارجية علينا',payables),('التزامات أخرى',liability_exp),('تعديلات الخصوم',manual_liab),('إجمالي الخصوم',liabilities),('رأس المال وحقوق الملكية اليدوية',manual_equity),('الأرباح المتراكمة المحسوبة',retained),('إجمالي حقوق الملكية',equity),('الخصوم + حقوق الملكية',liabilities+equity),('الفرق المحاسبي',difference)]
    return {'title':'المركز المالي','rows':rows,'total':assets,'difference':difference,'asset_details':[(ASSET_TYPES.get(r['asset_type'],r['asset_type']),float(r['total'])) for r in fixed_rows]}


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

@app.route('/system-version')
@login_required
@permission_required('manage_users')
def system_version() -> Any:
    db = get_db()
    workflow_count = db.execute("SELECT COUNT(*) c FROM workflow_definitions WHERE is_active=1").fetchone()['c']
    state_count = db.execute("SELECT COUNT(*) c FROM workflow_states").fetchone()['c']
    return render_template('system_version.html', settings=all_settings(), workflow_count=workflow_count, state_count=state_count)


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

POLICY_CATEGORY_LABELS = {
    "finance": "المالية",
    "inventory": "المخزون",
    "sales": "المبيعات",
    "purchases": "المشتريات",
    "hr": "الموارد البشرية",
    "system": "النظام",
}


def _policy_form_value(row: sqlite3.Row) -> Any:
    raw = request.form.get("value", "")
    if row["data_type"] == "boolean":
        return request.form.get("value") == "1"
    if row["data_type"] == "integer":
        return int(raw)
    if row["data_type"] == "decimal":
        return float(raw)
    return raw.strip()


@app.route("/system-policies", methods=["GET", "POST"])
@login_required
@permission_required("manage_system_policies")
def system_policies_page() -> Any:
    from .rules import RulesService
    db = get_db()
    if request.method == "POST":
        rule_key = request.form.get("rule_key", "").strip()
        action = request.form.get("action", "save")
        reason = request.form.get("reason", "").strip()
        row = db.execute("SELECT * FROM system_policies WHERE rule_key=?", (rule_key,)).fetchone()
        if not row:
            flash("السياسة المطلوبة غير موجودة.", "danger")
            return redirect(url_for("system_policies_page"))
        try:
            user = current_user()
            if action == "reset":
                old_value, new_value = RulesService.reset(db, rule_key, user["id"], now(), reason)
            else:
                old_value, new_value = RulesService.set(db, rule_key, _policy_form_value(row), user["id"], now(), reason)
            db.commit()
            audit("تعديل سياسة نظام", f"{rule_key}: {old_value} ← {new_value}")
            flash("تم حفظ سياسة النظام.", "success")
        except (ValueError, TypeError):
            db.rollback()
            flash("القيمة المدخلة لا تطابق نوع السياسة.", "danger")
        except PermissionError:
            db.rollback()
            flash("هذه السياسة غير قابلة للتعديل.", "danger")
        return redirect(url_for("system_policies_page", category=row["category"]))

    category = request.args.get("category", "").strip()
    search = request.args.get("q", "").strip()
    conditions = ["p.is_active=1"]
    params: list[Any] = []
    if category in POLICY_CATEGORY_LABELS:
        conditions.append("p.category=?")
        params.append(category)
    if search:
        conditions.append("(p.name LIKE ? OR p.rule_key LIKE ? OR p.description LIKE ?)")
        token = f"%{search}%"
        params.extend([token, token, token])
    rows = db.execute(
        f"""SELECT p.*,u.full_name updated_by_name
            FROM system_policies p LEFT JOIN users u ON u.id=p.updated_by
            WHERE {' AND '.join(conditions)}
            ORDER BY p.category,p.name""", params
    ).fetchall()
    history = db.execute(
        """SELECT h.*,u.full_name changed_by_name,p.name policy_name
           FROM policy_change_log h
           LEFT JOIN users u ON u.id=h.changed_by
           LEFT JOIN system_policies p ON p.id=h.policy_id
           ORDER BY h.id DESC LIMIT 20"""
    ).fetchall()
    return render_template("system_policies.html", rows=rows, history=history,
                           categories=POLICY_CATEGORY_LABELS, selected_category=category, search=search)

# --- Sales Module v5.6.0 Phase 1 ---
from .sales import CustomerService, SalesService

@app.route('/customers', methods=['GET', 'POST'])
@login_required
def customers_page():
    db = get_db(); user = current_user()
    if request.method == 'POST':
        try:
            customer_id = CustomerService.create(
                db, name=request.form.get('name',''), phone=request.form.get('phone',''),
                email=request.form.get('email',''), address=request.form.get('address',''),
                credit_limit=float(request.form.get('credit_limit') or 0), notes=request.form.get('notes',''),
                user_id=user['id'], created_at=now(),
            )
            audit('إضافة عميل', f'customer:{customer_id}', action_code='CUSTOMER_CREATE', entity_type='customer', entity_id=customer_id)
            db.commit(); flash('تمت إضافة العميل بنجاح.', 'success')
            return redirect(url_for('customers_page'))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.rollback(); flash(str(exc), 'danger')
    rows = db.execute('SELECT * FROM customers ORDER BY id DESC').fetchall()
    return render_template('customers.html', customers=rows)

@app.route('/api/products/search')
@login_required
def product_search_api():
    db = get_db()
    mode = (request.args.get('mode') or 'barcode').strip().lower()
    query = (request.args.get('q') or '').strip()
    branch_id = request.args.get('branch_id', type=int)
    if not query:
        return jsonify({'items': []})
    product_cols = table_columns(db, "products")
    price_expr = 'COALESCE(p.sale_price,0)' if 'sale_price' in product_cols else '0'
    discount_expr = 'COALESCE(p.default_discount_percent,0)' if 'default_discount_percent' in product_cols else '0'
    tax_expr = 'COALESCE(p.tax_percent,0)' if 'tax_percent' in product_cols else '0'
    stock_join = ''
    stock_expr = 'NULL'
    params = []
    if branch_id:
        stock_join = ' LEFT JOIN inventory_balances ib ON ib.product_id=p.id AND ib.location_id=? '
        stock_expr = 'COALESCE(ib.quantity,0)'
        params.append(branch_id)
    if mode == 'name':
        where = 'p.name LIKE ?'
        params.append('%' + query + '%')
        limit = 12
    else:
        where = 'p.sku = ?'
        params.append(query)
        limit = 1
    sql = f"""SELECT p.id,p.name,COALESCE(p.sku,'') sku,p.unit,
        {price_expr} sale_price,{discount_expr} discount_percent,{tax_expr} tax_percent,{stock_expr} stock_quantity
        FROM products p {stock_join} WHERE p.is_active=1 AND {where}
        ORDER BY CASE WHEN p.name=? THEN 0 ELSE 1 END,p.name LIMIT ?"""
    rows = db.execute(sql, (*params, query, limit)).fetchall()
    return jsonify({'items': [dict(row) for row in rows]})

@app.route('/sales-invoices', methods=['GET', 'POST'])
@login_required
def sales_invoices_page():
    db = get_db(); user = current_user()
    if request.method == 'POST':
        try:
            names=request.form.getlist('item_name[]'); codes=request.form.getlist('item_code[]')
            qtys=request.form.getlist('quantity[]'); prices=request.form.getlist('unit_price[]')
            discounts=request.form.getlist('discount_percent[]'); taxes=request.form.getlist('tax_percent[]')
            items=[]
            for i,name in enumerate(names):
                items.append({'item_name':name,'item_code':codes[i] if i<len(codes) else '',
                              'quantity':qtys[i] if i<len(qtys) else 0,'unit_price':prices[i] if i<len(prices) else 0,
                              'discount_percent':discounts[i] if i<len(discounts) else 0,'tax_percent':taxes[i] if i<len(taxes) else 0})
            invoice_id=SalesService.create_draft(
                db, branch_id=int(request.form.get('branch_id') or user['branch_id'] or 1),
                customer_id=int(request.form['customer_id']) if request.form.get('customer_id') else None,
                invoice_date=request.form.get('invoice_date') or datetime.now().strftime('%Y-%m-%d'),
                items=items,user_id=user['id'],created_at=now(),payment_method=request.form.get('payment_method','CASH'),
                notes=request.form.get('notes',''))
            audit('إنشاء مسودة فاتورة بيع', f'sales_invoice:{invoice_id}', action_code='SALES_INVOICE_DRAFT_CREATE', entity_type='sales_invoice', entity_id=invoice_id)
            db.commit(); flash('تم حفظ مسودة فاتورة البيع.', 'success')
            return redirect(url_for('sales_invoice_detail', invoice_id=invoice_id))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.rollback(); flash(str(exc), 'danger')
    invoices=db.execute('''SELECT i.*,c.name customer_name,b.name branch_name FROM sales_invoices i
                           LEFT JOIN customers c ON c.id=i.customer_id JOIN branches b ON b.id=i.branch_id ORDER BY i.id DESC''').fetchall()
    customers=db.execute('SELECT id,customer_no,name FROM customers WHERE is_active=1 ORDER BY name').fetchall()
    branches=db.execute('SELECT id,name FROM branches ORDER BY name').fetchall()
    return render_template('sales_invoices.html', invoices=invoices, customers=customers, branches=branches,
                           today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/sales-invoices/<int:invoice_id>')
@login_required
def sales_invoice_detail(invoice_id):
    db=get_db()
    invoice=db.execute('''SELECT i.*,c.name customer_name,b.name branch_name,u.full_name created_by_name
                          FROM sales_invoices i LEFT JOIN customers c ON c.id=i.customer_id
                          JOIN branches b ON b.id=i.branch_id JOIN users u ON u.id=i.created_by WHERE i.id=?''',(invoice_id,)).fetchone()
    if not invoice: flash('الفاتورة غير موجودة.','danger'); return redirect(url_for('sales_invoices_page'))
    items=db.execute('SELECT * FROM sales_invoice_items WHERE invoice_id=? ORDER BY id',(invoice_id,)).fetchall()
    return render_template('sales_invoice_detail.html', invoice=invoice, items=items)
