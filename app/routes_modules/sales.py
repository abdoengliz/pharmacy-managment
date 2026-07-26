from __future__ import annotations

# Load the complete shared namespace, including private helper names.
from . import common as _common
globals().update({name: value for name, value in vars(_common).items() if not name.startswith("__")})

"""Flask routes for the sales area."""

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

