# Pharma ERP v6.1 — Supabase Edition (المرحلة الأولى)

هذه نسخة انتقالية تدعم اختيار قاعدة البيانات من متغير البيئة `DATABASE_URL`:

- بدون `DATABASE_URL`: يعمل النظام على SQLite كما في النسخة الأصلية.
- مع رابط PostgreSQL: يتصل النظام بقاعدة Supabase عبر `psycopg`.

## التشغيل التجريبي على Windows

1. ثبّت المتطلبات:

```powershell
python -m pip install -r requirements.txt
```

2. انسخ `.env.supabase.example` إلى ملف إعداد محلي، أو عرّف المتغير داخل PowerShell:

```powershell
$env:DATABASE_URL="postgresql://postgres.udczeoltolukwxxmkkpa:YOUR_PASSWORD@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
```

3. لا تستخدم قاعدة الإنتاج قبل إكمال اختبارات التوافق والترحيل.

## حالة هذه المرحلة

تمت إضافة طبقة توافق أولية لتحويل placeholders من `?` إلى `%s`، ودعم صفوف النتائج بالاسم أو الرقم، وأوامر SQLite الشائعة مثل `PRAGMA table_info` و`INSERT OR IGNORE` و`BEGIN IMMEDIATE`.

لم يتم بعد اعتماد النسخة للإنتاج. يلزم اختبار إنشاء المخطط على Supabase، ثم إصلاح أي استعلامات PostgreSQL متبقية، ثم نقل البيانات والتحقق منها.
