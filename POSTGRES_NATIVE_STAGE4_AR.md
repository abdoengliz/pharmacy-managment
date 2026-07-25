# PostgreSQL Native — Stage 4.1

تم في هذه المرحلة:

- إزالة `INSERT OR IGNORE` من كود التطبيق واستبداله بصيغة PostgreSQL `ON CONFLICT DO NOTHING`.
- منع تنفيذ فحص `sqlite_master` الخاص بـ SQLite عند العمل على PostgreSQL.
- إضافة تدقيق آلي يمنع رجوع `GREATEST(...)` كاستدعاء Python بالخطأ.
- الإبقاء مؤقتًا على `lastrowid` في 24 موضعًا؛ هذه المواضع تعمل حاليًا عبر `RETURNING id` الآمن داخل طبقة التوافق، وسيتم تحويلها صراحةً في Stage 4.2.

هذه مرحلة تنظيف آمنة وليست إعلانًا بأن التحويل اكتمل بالكامل.
