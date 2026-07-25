from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class PharmaERPSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        import app.core as core
        core.DB_PATH = Path(self.temp_dir.name) / "test.db"
        from app import create_app
        self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def login(self) -> None:
        response = self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 302)

    def test_login_opens_administration_dashboard(self) -> None:
        response = self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))


    def test_operational_sales_links_are_hidden_but_routes_remain_available(self) -> None:
        self.login()
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn("فواتير البيع".encode("utf-8"), dashboard.data)
        self.assertNotIn("العملاء".encode("utf-8"), dashboard.data)
        self.assertNotIn("تحويلات الفواتير".encode("utf-8"), dashboard.data)
        sales = self.client.get("/sales-invoices")
        self.assertEqual(sales.status_code, 200)

    def test_login_and_dashboard(self) -> None:
        self.login()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("مركز القيادة".encode("utf-8"), response.data)

    def test_cashbox_opening_and_ledger(self) -> None:
        self.login()
        response = self.client.post(
            "/cashbox",
            data={"branch_id": "1", "date": "2026-07-14", "opening_amount": "1000", "notes": "اختبار"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("1000.00".encode(), response.data)
        self.assertIn("الحسابات والأرصدة".encode("utf-8"), response.data)

    def test_permissions_page_renders(self) -> None:
        self.login()
        response = self.client.get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn("إدارة رصيد افتتاح الخزينة".encode("utf-8"), response.data)

    def test_day_closing_blocks_new_revenue(self) -> None:
        self.login()
        close = self.client.post(
            "/day-closing",
            data={"branch_id": "1", "closing_date": "2026-07-14", "action": "close"},
            follow_redirects=True,
        )
        self.assertEqual(close.status_code, 200)
        response = self.client.post(
            "/revenues",
            data={"branch_id": "1", "amount": "500", "revenue_date": "2026-07-14", "payment_method": "نقدي", "notes": ""},
            follow_redirects=True,
        )
        self.assertIn("هذا اليوم مقفل".encode("utf-8"), response.data)

    def test_reopen_day_allows_revenue(self) -> None:
        self.login()
        self.client.post("/day-closing", data={"branch_id": "1", "closing_date": "2026-07-14", "action": "close"})
        self.client.post("/day-closing", data={"branch_id": "1", "closing_date": "2026-07-14", "action": "reopen"})
        response = self.client.post(
            "/revenues",
            data={"branch_id": "1", "amount": "500", "revenue_date": "2026-07-14", "payment_method": "نقدي", "notes": ""},
            follow_redirects=True,
        )
        self.assertIn("تمت إضافة الإيراد".encode("utf-8"), response.data)


    def test_locations_management(self) -> None:
        self.login()
        response = self.client.post(
            "/locations",
            data={"action": "add", "name": "فرع الاختبار", "code": "TEST-BR", "location_type": "BRANCH"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("فرع الاختبار".encode("utf-8"), response.data)

    def test_edit_financial_operations_resyncs_ledger(self) -> None:
        self.login()
        import app.core as core
        with self.app.app_context():
            db = core.get_db()
            account_id = db.execute("SELECT id FROM financial_accounts WHERE branch_id=1 ORDER BY id LIMIT 1").fetchone()["id"]
            supplier = db.execute("INSERT INTO suppliers(name,phone,total_due,notes,created_at) VALUES(?,?,?,?,?)", ("مورد اختبار", "", 1000, "", core.now()))
            supplier_id = supplier.lastrowid
            supplier_account = db.execute("INSERT INTO supplier_location_accounts(supplier_id,location_id,opening_due,credit_limit,notes,is_active,created_at) VALUES(?,?,?,?,?,?,?)", (supplier_id,1,1000,0,"",1,core.now()))
            supplier_account_id = supplier_account.lastrowid
            db.commit()

        common = {"branch_id":"1", "split_account_id":str(account_id), "split_amount":"100"}
        self.client.post("/revenues", data={**common,"amount":"100","revenue_date":"2026-07-13","notes":""})
        self.client.post("/expenses", data={**common,"amount":"100","expense_date":"2026-07-13","category":"اختبار","notes":""})
        self.client.post("/payments", data={"supplier_account_id":str(supplier_account_id),"branch_id":"1","amount":"100","payment_date":"2026-07-13","split_account_id":str(account_id),"split_amount":"100","notes":""})

        with self.app.app_context():
            db = core.get_db()
            revenue_id=db.execute("SELECT id FROM revenues ORDER BY id DESC LIMIT 1").fetchone()["id"]
            expense_id=db.execute("SELECT id FROM expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
            payment_id=db.execute("SELECT id FROM supplier_payments ORDER BY id DESC LIMIT 1").fetchone()["id"]

        self.client.post(f"/revenues/{revenue_id}/edit", data={"branch_id":"1","amount":"200","revenue_date":"2026-07-13","split_account_id":str(account_id),"split_amount":"200","notes":"معدل"})
        self.client.post(f"/expenses/{expense_id}/edit", data={"branch_id":"1","amount":"210","expense_date":"2026-07-13","category":"اختبار","split_account_id":str(account_id),"split_amount":"210","notes":"معدل"})
        self.client.post(f"/payments/{payment_id}/edit", data={"supplier_account_id":str(supplier_account_id),"branch_id":"1","amount":"220","payment_date":"2026-07-13","split_account_id":str(account_id),"split_amount":"220","notes":"معدل"})

        with self.app.app_context():
            db = core.get_db()
            checks=(("revenues",revenue_id,200),("expenses",expense_id,210),("supplier_payments",payment_id,220))
            for reference_type, reference_id, expected in checks:
                row=db.execute("SELECT SUM(amount) total FROM financial_ledger WHERE reference_type=? AND reference_id=?",(reference_type,reference_id)).fetchone()
                self.assertEqual(row["total"], expected)

    def test_treasury_transfer_moves_balance_only_on_receive(self) -> None:
        self.login()
        import app.core as core
        with self.app.app_context():
            db=core.get_db()
            warehouse=db.execute("SELECT id FROM branches WHERE location_type='MAIN_WAREHOUSE' LIMIT 1").fetchone()["id"]
            branch=1
            source=db.execute("SELECT id FROM financial_accounts WHERE branch_id=? ORDER BY id LIMIT 1",(branch,)).fetchone()["id"]
            target=db.execute("SELECT id FROM financial_accounts WHERE branch_id=? ORDER BY id LIMIT 1",(warehouse,)).fetchone()["id"]
            core.sync_ledger("test_open",999,branch,source,"OPENING_BALANCE","IN",1000,"2026-07-14","",1)
            db.commit()
        response=self.client.post("/treasury-transfers",data={"from_account_id":source,"to_account_id":target,"amount":"400","transfer_date":"2026-07-14","notes":"توريد اختبار"})
        self.assertEqual(response.status_code,302)
        with self.app.app_context():
            db=core.get_db(); transfer_id=db.execute("SELECT id FROM treasury_transfers ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.client.post(f"/treasury-transfers/{transfer_id}/send")
        with self.app.app_context():
            db=core.get_db(); before=db.execute("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) b FROM financial_ledger WHERE account_id=?",(source,)).fetchone()["b"]
            self.assertEqual(before,1000)
        self.client.post(f"/treasury-transfers/{transfer_id}/receive")
        with self.app.app_context():
            db=core.get_db()
            source_balance=db.execute("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) b FROM financial_ledger WHERE account_id=?",(source,)).fetchone()["b"]
            target_balance=db.execute("SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN amount ELSE -amount END),0) b FROM financial_ledger WHERE account_id=?",(target,)).fetchone()["b"]
            self.assertEqual(source_balance,600)
            self.assertEqual(target_balance,400)

    def test_expense_financial_classification_and_asset_type(self) -> None:
        self.login()
        import app.core as core
        with self.app.app_context():
            db=core.get_db()
            account_id=db.execute("SELECT id FROM financial_accounts WHERE branch_id=1 ORDER BY id LIMIT 1").fetchone()["id"]
        response=self.client.post("/expenses", data={
            "branch_id":"1","amount":"1500","expense_date":"2026-07-13","category":"شراء مكتب",
            "financial_classification":"ASSET","asset_type":"FURNITURE",
            "split_account_id":str(account_id),"split_amount":"1500","notes":"أصل اختباري"
        }, follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with self.app.app_context():
            row=core.get_db().execute("SELECT financial_classification,asset_type FROM expenses ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["financial_classification"],"ASSET")
            self.assertEqual(row["asset_type"],"FURNITURE")
    def test_financial_classifications_table_and_expense_link(self) -> None:
        self.login()
        import app.core as core
        with self.app.app_context():
            db = core.get_db()
            rows = db.execute("SELECT code,name,statement_section FROM financial_classifications ORDER BY sort_order").fetchall()
            self.assertEqual([r["code"] for r in rows[:3]], ["OPERATING", "ASSET", "LIABILITY"])
            account_id = db.execute("SELECT id FROM financial_accounts WHERE branch_id=1 AND is_active=1 ORDER BY id LIMIT 1").fetchone()["id"]
        response = self.client.post(
            "/expenses",
            data={
                "branch_id": "1", "amount": "250", "expense_date": "2026-07-14",
                "category": "كرسي مكتب", "financial_classification": "ASSET",
                "asset_type": "FURNITURE", "account_id[]": str(account_id),
                "split_amount[]": "250", "notes": "اختبار الربط"
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            row = core.get_db().execute("""SELECT e.financial_classification,fc.code
                FROM expenses e JOIN financial_classifications fc ON fc.id=e.classification_id
                ORDER BY e.id DESC LIMIT 1""").fetchone()
            self.assertEqual(row["financial_classification"], "ASSET")
            self.assertEqual(row["code"], "ASSET")

    def test_business_rules_engine_and_policy_ui(self) -> None:
        self.login()
        import app.core as core
        from app.rules import RulesService
        with self.app.app_context():
            db = core.get_db()
            self.assertEqual(RulesService.get(db, "sales.max_discount_percent"), 15.0)
            count = db.execute("SELECT COUNT(*) c FROM system_policies").fetchone()["c"]
            self.assertGreaterEqual(count, 14)
        response = self.client.get("/system-policies")
        self.assertEqual(response.status_code, 200)
        self.assertIn("الحد الأقصى للخصم".encode("utf-8"), response.data)
        response = self.client.post("/system-policies", data={
            "rule_key": "sales.max_discount_percent", "value": "12.5",
            "action": "save", "reason": "اختبار"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            db = core.get_db()
            self.assertEqual(RulesService.get(db, "sales.max_discount_percent"), 12.5)
            history = db.execute("SELECT COUNT(*) c FROM policy_change_log").fetchone()["c"]
            self.assertEqual(history, 1)


    def test_approval_engine_request_and_decision(self) -> None:
        self.login()
        import app.core as core
        from app.approvals import ApprovalService
        with self.app.app_context():
            db = core.get_db()
            definition = db.execute("SELECT id FROM approval_definitions WHERE code='TREASURY_TRANSFER'").fetchone()
            self.assertIsNotNone(definition)
            request_id = ApprovalService.request(
                db, entity_type="treasury_transfer", entity_id=987654,
                reference_no="TRF-TEST", amount=250.0, branch_id=1,
                requested_by=1, requested_at=core.now(),
            )
            row = db.execute("SELECT status FROM approval_requests WHERE id=?", (request_id,)).fetchone()
            self.assertEqual(row["status"], "PENDING")
            ApprovalService.decide(db, request_id, approve=True, user_id=1, decided_at=core.now(), note="اختبار")
            db.commit()
            row = db.execute("SELECT status,decision_note FROM approval_requests WHERE id=?", (request_id,)).fetchone()
            self.assertEqual(row["status"], "APPROVED")
            self.assertEqual(row["decision_note"], "اختبار")
            history = db.execute("SELECT COUNT(*) c FROM approval_history WHERE request_id=?", (request_id,)).fetchone()["c"]
            self.assertEqual(history, 2)
        response = self.client.get("/approvals")
        self.assertEqual(response.status_code, 200)
        self.assertIn("مركز الاعتمادات".encode("utf-8"), response.data)


    def test_notification_and_task_engine(self) -> None:
        self.login()
        import app.core as core
        from app.notifications import NotificationService
        from app.tasks import TaskService
        with self.app.app_context():
            db = core.get_db()
            first = NotificationService.create(
                db, title="اختبار إشعار", message="رسالة اختبار", created_at=core.now(),
                user_id=1, event_key="test.notification", action_url="/notifications", deduplicate=True,
            )
            second = NotificationService.create(
                db, title="اختبار إشعار", message="رسالة اختبار", created_at=core.now(),
                user_id=1, event_key="test.notification", action_url="/notifications", deduplicate=True,
            )
            self.assertEqual(first, second)
            task_id = TaskService.create(
                db, title="مهمة اختبار", created_at=core.now(), created_by=1,
                assigned_user_id=1, task_type="GENERAL", reference_type="test", reference_id=77,
                action_url="/tasks", event_key="test.task", deduplicate=True,
            )
            duplicate = TaskService.create(
                db, title="مهمة اختبار", created_at=core.now(), created_by=1,
                assigned_user_id=1, task_type="GENERAL", reference_type="test", reference_id=77,
                action_url="/tasks", event_key="test.task", deduplicate=True,
            )
            self.assertEqual(task_id, duplicate)
            TaskService.close_reference(db, reference_type="test", reference_id=77, user_id=1, completed_at=core.now())
            db.commit()
            status = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"]
            self.assertEqual(status, "COMPLETED")
        self.assertEqual(self.client.get("/notifications").status_code, 200)
        self.assertEqual(self.client.get("/tasks").status_code, 200)


    def test_event_bus_audit_and_timeline(self) -> None:
        self.login()
        import app.core as core
        from app.events import EventBus, Event
        with self.app.app_context():
            db=core.get_db()
            event_id=EventBus.publish(db,Event("TEST_EVENT","test_document",77,"اختبار حدث","تفاصيل",{"value":1},1,1))
            self.assertGreater(event_id,0)
            self.assertIsNotNone(db.execute("SELECT id FROM event_history WHERE id=?",(event_id,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT id FROM activity_timeline WHERE entity_type='test_document' AND entity_id=77").fetchone())
            core.audit("اختبار احترافي","تفاصيل",action_code="TEST",entity_type="test_document",entity_id=77,before={"x":1},after={"x":2})
            row=db.execute("SELECT before_json,after_json FROM audit_log WHERE action_code='TEST' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIn('"x": 1',row["before_json"]); self.assertIn('"x": 2',row["after_json"])
        self.assertEqual(self.client.get("/events").status_code,200)
        self.assertEqual(self.client.get("/errors").status_code,200)
        self.assertEqual(self.client.get("/timeline/test_document/77").status_code,200)


    def test_sales_customer_and_draft_invoice(self) -> None:
        self.login()
        customer = self.client.post('/customers', data={
            'name':'عميل مبيعات','phone':'0910000000','credit_limit':'5000'
        }, follow_redirects=True)
        self.assertEqual(customer.status_code, 200)
        self.assertIn('عميل مبيعات'.encode('utf-8'), customer.data)
        import app.core as core
        with self.app.app_context():
            db=core.get_db(); customer_id=db.execute("SELECT id FROM customers WHERE name='عميل مبيعات'").fetchone()['id']
        invoice = self.client.post('/sales-invoices', data={
            'branch_id':'1','customer_id':str(customer_id),'invoice_date':'2026-07-21','payment_method':'CASH',
            'item_name[]':['دواء اختبار'],'item_code[]':['MED-001'],'quantity[]':['2'],'unit_price[]':['10'],
            'discount_percent[]':['10'],'tax_percent[]':['5'],'notes':'مسودة اختبار'
        }, follow_redirects=True)
        self.assertEqual(invoice.status_code, 200)
        self.assertIn('SAL-2026-'.encode(), invoice.data)
        with self.app.app_context():
            db=core.get_db(); row=db.execute('SELECT status,total_amount FROM sales_invoices ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(row['status'],'DRAFT'); self.assertAlmostEqual(row['total_amount'],18.9)
            self.assertIsNotNone(db.execute("SELECT id FROM event_history WHERE event_type='SALES_INVOICE_DRAFT_CREATED'").fetchone())


    def test_sales_page_uses_fullscreen_f12_menu(self) -> None:
        self.login()
        response = self.client.get("/sales-invoices")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sales-focus-mode", response.data)
        self.assertIn(b"event.key===\'F12\'", response.data)
        self.assertIn(b"sales-menu-open", response.data)

    def test_administration_center_dashboard(self) -> None:
        self.login()
        response = self.client.get("/system-management")
        self.assertEqual(response.status_code, 200)
        self.assertIn("مركز الإدارة".encode("utf-8"), response.data)
        self.assertIn("اعتمادات معلقة".encode("utf-8"), response.data)

    def test_clone_role_copies_permissions(self) -> None:
        self.login()
        import app.core as core
        with self.app.app_context():
            db=core.get_db()
            source=db.execute("SELECT id FROM roles WHERE code='ACCOUNTANT'").fetchone()["id"]
            db.execute("INSERT OR IGNORE INTO role_permissions(role_id,permission) VALUES(?,?)",(source,"view_reports"))
            db.commit()
        response=self.client.post(f"/roles/{source}/clone",data={"name":"محاسب اختبار","code":"TEST_ACCOUNTANT"},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with self.app.app_context():
            db=core.get_db(); cloned=db.execute("SELECT id FROM roles WHERE code='TEST_ACCOUNTANT'").fetchone()
            self.assertIsNotNone(cloned)
            permission=db.execute("SELECT 1 FROM role_permissions WHERE role_id=? AND permission='view_reports'",(cloned["id"],)).fetchone()
            self.assertIsNotNone(permission)

    def test_department_can_be_linked_to_branch(self) -> None:
        self.login()
        response=self.client.post("/departments",data={"name":"قسم فرعي اختبار","branch_id":"1","description":"اختبار"},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        import app.core as core
        with self.app.app_context():
            row=core.get_db().execute("SELECT branch_id FROM departments WHERE name='قسم فرعي اختبار'").fetchone()
            self.assertEqual(row["branch_id"],1)

if __name__ == "__main__":
    unittest.main()

# v3.1 exports are exercised manually in release QA because they generate binary files.

# v5.6.3 POS UI regression tests are attached dynamically to keep unittest discovery compatibility.
def _test_sales_pos_v2_and_product_search(self):
    self.login()
    import app.core as core
    with self.app.app_context():
        db = core.get_db()
        db.execute("""INSERT INTO products(name,sku,unit,is_active,notes,created_at,sale_price,default_discount_percent,tax_percent)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                   ("باراسيتامول 500", "622100000001", "علبة", 1, "", core.now(), 12.5, 2, 0))
        db.commit()
    response = self.client.get('/sales-invoices')
    self.assertEqual(response.status_code, 200)
    self.assertIn('الرقم التجاري'.encode('utf-8'), response.data)
    self.assertIn("event.key==='F1'".encode(), response.data)
    self.assertIn("event.key==='Delete'".encode(), response.data)
    self.assertIn("event.key==='F8'".encode(), response.data)
    exact = self.client.get('/api/products/search?mode=barcode&q=622100000001&branch_id=1').get_json()
    self.assertEqual(exact['items'][0]['name'], 'باراسيتامول 500')
    named = self.client.get('/api/products/search?mode=name&q=باراسيتامول&branch_id=1').get_json()
    self.assertEqual(named['items'][0]['sku'], '622100000001')

PharmaERPSmokeTests.test_sales_pos_v2_and_product_search = _test_sales_pos_v2_and_product_search

