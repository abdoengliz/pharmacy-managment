# تقرير تدقيق الأداء v6.5.2

## ما تم تنفيذه

- تقليل طلب تسجيل الدخول الناجح باستعلام واحد: قائمة المستخدمين لا تُجلب إلا عند عرض صفحة الدخول.
- دمج بيانات الرسم اليومي وطرق الدفع في لوحة التحكم داخل استعلام واحد باستخدام `UNION ALL`، ما يزيل رحلة شبكة كاملة إلى Supabase.
- الإبقاء على تجميع الصلاحيات في استعلام واحد لكل طلب، وعلى ترويسة `Server-Timing` لقياس الزمن والعدد الفعلي بعد النشر.
- تنظيف ملفات Git وملفات Python المؤقتة من نسخة التسليم.

## ملاحظة القياس

الأرقام التالية تحليل ثابت لعدد مواضع `execute()` داخل كل Route، وليست عدد الاستعلامات الفعلي دائمًا؛ الفروع الشرطية والكاش قد تقلله. القياس الحقيقي يظهر بعد النشر في ترويسة `Server-Timing`.

## أكثر Routes احتواءً على استعلامات محتملة

| المواضع | الدالة | الملف |
|---:|---|---|
| 13 | `merge_supplier` | `app/routes_modules/suppliers.py:396` |
| 12 | `dashboard` | `app/routes_modules/auth_dashboard.py:218` |
| 10 | `suppliers` | `app/routes_modules/suppliers.py:12` |
| 10 | `system_management` | `app/routes_modules/operations.py:12` |
| 10 | `edit_treasury_transfer` | `app/routes_modules/finance.py:864` |
| 9 | `edit_user` | `app/routes_modules/users_access.py:64` |
| 8 | `supplier_detail` | `app/routes_modules/suppliers.py:201` |
| 8 | `edit_revenue` | `app/routes_modules/finance.py:354` |
| 8 | `payments` | `app/routes_modules/finance.py:303` |
| 7 | `send_stock_transfer` | `app/routes_modules/inventory.py:219` |
| 7 | `inventory` | `app/routes_modules/inventory.py:12` |
| 7 | `external_debts` | `app/routes_modules/finance.py:666` |
| 7 | `edit_payment` | `app/routes_modules/finance.py:446` |
| 7 | `attendance_portal` | `app/routes_modules/auth_dashboard.py:121` |
| 6 | `delete_supplier` | `app/routes_modules/suppliers.py:487` |
| 6 | `locations` | `app/routes_modules/organization.py:12` |
| 6 | `global_search` | `app/routes_modules/operations.py:110` |
| 6 | `receive_stock_transfer` | `app/routes_modules/inventory.py:261` |
| 6 | `stock_transfers` | `app/routes_modules/inventory.py:103` |
| 6 | `edit_employee` | `app/routes_modules/hr.py:347` |
| 6 | `employees` | `app/routes_modules/hr.py:12` |
| 6 | `receive_treasury_transfer` | `app/routes_modules/finance.py:1059` |
| 6 | `delete_treasury_transfer` | `app/routes_modules/finance.py:966` |
| 6 | `edit_external_debt` | `app/routes_modules/finance.py:718` |
| 6 | `edit_expense` | `app/routes_modules/finance.py:587` |

## النتيجة المتوقعة

- تسجيل الدخول الناجح: استعلام أقل من السابق.
- لوحة التحكم عند عدم إصابة الكاش: استعلام أقل من السابق.
- عند إصابة كاش لوحة التحكم: تبقى الاستجابة أخف بكثير لأن التجميعات الرئيسية لا تتكرر.

## كيفية قراءة القياس الحقيقي

من أدوات المطور في المتصفح افتح Network ثم طلب الصفحة، وستجد مثالًا مثل:

`Server-Timing: app;dur=420.0, db;dur=300.0;desc="9 queries"`

العدد داخل `queries` هو العدد الفعلي لذلك الطلب، و`db;dur` زمن قاعدة البيانات بالمللي ثانية.