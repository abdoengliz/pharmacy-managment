from __future__ import annotations

from typing import Any, Iterable

from app.events import Event, EventBus
from app.core import insert_and_get_id


class CustomerService:
    @staticmethod
    def next_number(db: Any) -> str:
        row = db.execute("SELECT customer_no FROM customers ORDER BY id DESC LIMIT 1").fetchone()
        seq = int(row["customer_no"].split("-")[-1]) + 1 if row else 1
        return f"CUS-{seq:06d}"

    @classmethod
    def create(cls, db: Any, *, name: str, user_id: int, phone: str = "", email: str = "",
               address: str = "", credit_limit: float = 0, notes: str = "", group_id: int | None = None,
               created_at: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("اسم العميل مطلوب.")
        customer_no = cls.next_number(db)
        customer_id = insert_and_get_id(
            db,
            """INSERT INTO customers(customer_no,name,phone,email,address,customer_group_id,credit_limit,notes,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (customer_no, name, phone.strip(), email.strip(), address.strip(), group_id,
             max(float(credit_limit or 0), 0), notes.strip(), user_id, created_at),
        )
        EventBus.publish(db, Event("CUSTOMER_CREATED", "customer", customer_id, "تم إنشاء عميل", name,
                                   {"customer_no": customer_no}, user_id, None))
        return customer_id


class SalesService:
    @staticmethod
    def next_invoice_number(db: Any, year: str) -> str:
        prefix = f"SAL-{year}-"
        row = db.execute("SELECT invoice_no FROM sales_invoices WHERE invoice_no LIKE ? ORDER BY id DESC LIMIT 1", (prefix + "%",)).fetchone()
        seq = int(row["invoice_no"].rsplit("-", 1)[-1]) + 1 if row else 1
        return f"{prefix}{seq:06d}"

    @classmethod
    def create_draft(cls, db: Any, *, branch_id: int, customer_id: int | None, invoice_date: str,
                     items: Iterable[dict[str, Any]], user_id: int, created_at: str,
                     payment_method: str = "CASH", notes: str = "") -> int:
        normalized = []
        subtotal = discount_amount = tax_amount = 0.0
        for item in items:
            name = str(item.get("item_name", "")).strip()
            if not name:
                continue
            qty = float(item.get("quantity", 0))
            price = float(item.get("unit_price", 0))
            discount = float(item.get("discount_percent", 0) or 0)
            tax = float(item.get("tax_percent", 0) or 0)
            if qty <= 0 or price < 0 or not 0 <= discount <= 100 or not 0 <= tax <= 100:
                raise ValueError("بيانات بند الفاتورة غير صحيحة.")
            gross = qty * price
            disc = gross * discount / 100
            taxable = gross - disc
            tax_value = taxable * tax / 100
            line_total = taxable + tax_value
            subtotal += gross; discount_amount += disc; tax_amount += tax_value
            normalized.append((name, str(item.get("item_code", "")).strip(), qty, price, discount, tax, line_total))
        if not normalized:
            raise ValueError("يجب إضافة بند واحد على الأقل.")
        invoice_no = cls.next_invoice_number(db, invoice_date[:4])
        total = subtotal - discount_amount + tax_amount
        invoice_id = insert_and_get_id(
            db,
            """INSERT INTO sales_invoices(invoice_no,branch_id,customer_id,invoice_date,status,subtotal,discount_amount,tax_amount,total_amount,payment_method,notes,created_by,created_at)
               VALUES(?,?,?,?,'DRAFT',?,?,?,?,?,?,?,?)""",
            (invoice_no, branch_id, customer_id, invoice_date, subtotal, discount_amount, tax_amount, total,
             payment_method, notes.strip(), user_id, created_at),
        )
        db.executemany(
            """INSERT INTO sales_invoice_items(invoice_id,item_name,item_code,quantity,unit_price,discount_percent,tax_percent,line_total)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(invoice_id, *row) for row in normalized],
        )
        EventBus.publish(db, Event("SALES_INVOICE_DRAFT_CREATED", "sales_invoice", invoice_id,
                                   "تم إنشاء مسودة فاتورة بيع", invoice_no,
                                   {"invoice_no": invoice_no, "total_amount": total}, user_id, branch_id))
        return invoice_id
