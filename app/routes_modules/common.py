from __future__ import annotations

"""Shared imports, constants and helper functions extracted from the legacy routes module.

This module contains no Flask route registration. Route modules load this namespace
to preserve the existing behavior while keeping each business area isolated.
"""
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

def _table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    return table_exists(db, table_name)

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

def _next_stock_transfer_number() -> str:
    """Generate a collision-resistant stock transfer reference."""
    return f"STR-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

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

from .sales import CustomerService, SalesService


# Explicitly expose private helpers too; route modules rely on several _name helpers.
__all__ = [name for name in globals() if not name.startswith("__")]
