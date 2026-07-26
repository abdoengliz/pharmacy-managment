from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the reports area."""

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

