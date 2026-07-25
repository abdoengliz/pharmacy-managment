# دليل النشر النهائي على Vercel + Supabase

## إعداد المشروع

1. ارفع **محتويات مجلد المشروع نفسه** إلى مستودع Git؛ يجب أن يكون `app.py` و`requirements.txt` في جذر المستودع.
2. استورد المستودع في Vercel. لا تضبط Build Command أو Output Directory؛ اكتشاف Flask تلقائي.
3. أضف متغيرات البيئة التالية لكل من Production وPreview عند الحاجة:

```text
APP_ENV=production
DATABASE_URL=رابط اتصال Supabase PostgreSQL مع sslmode=require
SECRET_KEY=قيمة عشوائية ثابتة لا تقل عن 32 حرفاً
ADMIN_INITIAL_PASSWORD=كلمة مرور أولية قوية (مطلوبة فقط عند إنشاء قاعدة جديدة)
SESSION_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
```

## ملاحظات مهمة

- لا تستخدم قاعدة SQLite المرفقة على Vercel؛ نظام الملفات ليس مخزناً دائماً. يجب ضبط `DATABASE_URL`.
- لا تضع الأسرار داخل Git أو داخل ملف ZIP المنشور.
- التطبيق يسجل الأحداث إلى Vercel Logs/Observability تلقائياً عند وجود متغير `VERCEL`.
- النسخة تحدد Python 3.13 عبر `.python-version`.
- بعد النشر افتح الصفحة الرئيسية، سجل الدخول، واختبر لوحة التحكم وفلاتر الإيرادات على Preview قبل الترقية إلى Production.

## نشر عبر Vercel CLI

```bash
npm i -g vercel
vercel
vercel --prod
```
