from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the system area."""

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

@app.route('/system-version')
@login_required
@permission_required('manage_users')
def system_version() -> Any:
    db = get_db()
    workflow_count = db.execute("SELECT COUNT(*) c FROM workflow_definitions WHERE is_active=1").fetchone()['c']
    state_count = db.execute("SELECT COUNT(*) c FROM workflow_states").fetchone()['c']
    return render_template('system_version.html', settings=all_settings(), workflow_count=workflow_count, state_count=state_count)

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

