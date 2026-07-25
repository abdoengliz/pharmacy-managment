# Phase 8 — توافق تجميع القيم المنطقية مع PostgreSQL

- إصلاح خطأ `function sum(boolean) does not exist` في صفحة الموظفين.
- تحويل أنماط SQLite من نوع `SUM(condition)` إلى `SUM(CASE WHEN condition THEN 1 ELSE 0 END)`.
- إصلاح إحصاءات الموظفين والحضور في المصدر مباشرة.
- إضافة اختبارات تمنع تعديل `SUM(amount)` أو `SUM(CASE ...)` السليمين.
- لا حاجة لإعادة ترحيل قاعدة البيانات أو حذف أي جدول.
