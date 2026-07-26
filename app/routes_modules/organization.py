from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the organization area."""

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

