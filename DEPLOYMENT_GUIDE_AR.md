# دليل النشر المختصر

1. ثبّت Python والحزم: `pip install -r requirements.txt`.
2. انسخ `.env.example` إلى إعدادات الاستضافة واضبط القيم، ولا ترفع الأسرار إلى Git.
3. عيّن `APP_ENV=production` و`SECRET_KEY` ثابتًا قويًا و`SESSION_COOKIE_SECURE=1`.
4. في أول قاعدة جديدة عيّن `ADMIN_INITIAL_PASSWORD` قويًا؛ سيطلب النظام تغييره عند أول دخول.
5. شغّل Windows عبر `run_production_windows.bat` أو Linux عبر `run_production_linux.sh`.
6. ضع Nginx/Apache أمام التطبيق مع HTTPS، ووجّه الطلبات إلى 127.0.0.1:8000.
7. خزّن قاعدة البيانات ومجلد logs خارج مجلد النشر وخذ نسخة احتياطية يومية.
8. لا تستخدم `flask run` أو `app.py` كخادم إنترنت مباشر.
