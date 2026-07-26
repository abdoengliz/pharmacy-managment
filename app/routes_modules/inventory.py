from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the inventory area."""

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

