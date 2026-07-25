# v6.1.0 Supabase Phase 3 Pilot

- تشغيل التطبيق باستخدام DATABASE_URL الخاص بـSupabase.
- تحسين توافق INSERT OR IGNORE مع ON CONFLICT DO NOTHING.
- ترجمة GROUP_CONCAT وdate('now') وبعض دوال التاريخ الخاصة بـSQLite.
- توافق فحوص sqlite_master أثناء بدء التطبيق.
- تحويل أخطاء psycopg إلى أخطاء متوافقة مع معالجة المشروع الحالية.
- إضافة مشغل Windows آمن يطلب كلمة المرور دون تخزينها.
