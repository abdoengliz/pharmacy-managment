# PostgreSQL Native — Stage 3

تم في هذه المرحلة:

- تحويل تعريفات مفاتيح الجداول داخل `core.py` من `AUTOINCREMENT` إلى PostgreSQL Identity.
- إزالة استخدام `PRAGMA table_info` من كود التطبيق واستبداله بدالة metadata أصلية.
- إزالة فحص `sqlite_master` و`PRAGMA quick_check` من مسار فحص صحة النظام على PostgreSQL.
- إضافة `table_columns` و`table_exists` و`database_healthcheck`.
- فحص صيغ SQLite scalar من نوع `MAX(expr, 0)` و`MIN(expr, 0)` وتحويلها عند وجودها.

ملاحظة: ما زالت طبقة التوافق تدعم SQLite اختيارياً، ولذلك ستظهر كلمات SQLite داخل `db_compat.py` فقط. كما أن تحويل `lastrowid` و`INSERT OR IGNORE` داخل جميع الوحدات سيستكمل في المرحلة التالية.
