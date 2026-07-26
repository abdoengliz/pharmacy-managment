from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the users access area."""

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

