## v5.8.7.2 - Treasury Transfer Edit & Delete Controls
- Added edit and delete buttons for financial supplies and treasury transfers.
- Editing and deletion are restricted to DRAFT records to protect posted balances.
- Added branch, permission, account, and available-balance validation.


## v5.8.7.1 - Production Route & Inventory Hotfix
- Restored missing inventory and stock-transfer endpoints referenced by templates.
- Added transactional stock send/receive protections and validation.
- Added inventory permissions to the permission catalog.
# Changelog

## v5.7.3.1 — Dashboard Pro & System Health (2026-07-22)
- Added administration and financial dashboard cards.
- Added permission-aware quick actions, attention center, recent audit activity, and health summary.
- Added read-only System Health page with database, service, table, backup, and disk checks.
- No destructive database migration.

## 5.7.1-dev — 2026-07-21

- تطوير مركز الإدارة بلوحة مؤشرات للاعتمادات والمهام والتنبيهات وآخر نشاطات التدقيق.
- إضافة نسخ الأدوار مع جميع الصلاحيات.
- ربط الأقسام بالفروع عبر ترحيل إضافي آمن.
- إضافة مراجع department_id وjob_id للموظفين مع ترحيل تلقائي من الحقول النصية القديمة.
- إضافة is_main للفروع دون حذف أو تغيير البيانات الحالية.
- لم يتم حذف أي جدول أو مسار قديم.

## 5.7.0-dev — 2026-07-21

- تحويل نقطة الدخول بعد تسجيل الدخول إلى لوحة التحكم الإدارية.
- إخفاء وحدات التشغيل اليومي (العملاء، المبيعات، تحويلات الفواتير) من القائمة دون حذف الكود أو الجداول.
- بدء مسار Administration & Accounting Core بطريقة غير هادمة.
- تحديث الإصدار واختبارات الاستقرار.

## 5.6.3-dev — 2026-07-21
- Sales POS UI v2 with F1 smart product lookup, keyboard workflow, Delete clear and F8 logout.

## 5.6.2-dev — 2026-07-21

- Added full-screen sales workspace.
- Added F12 navigation drawer toggle and overlay.
- Preserved standard layout on all non-sales screens.

## 5.6.1-dev — 2026-07-21

- جعل واجهة المبيعات الصفحة الأولى بعد تسجيل الدخول.
- الإبقاء على لوحة التحكم متاحة من القائمة الرئيسية.
- إضافة اختبار لمسار التوجيه بعد تسجيل الدخول.

## 5.6.0-dev — 2026-07-21

- Added customer master data and automatic numbering.
- Added sales invoice drafts and item calculations.
- Integrated sales events with Event Bus, Audit and Activity Timeline.
- Added customer, invoice list and invoice detail interfaces.
- Database version upgraded to 5.6.

## 5.5.0-dev — 2026-07-21
- Added Event Bus, Event Explorer, Activity Timeline, Audit Center Pro and Error Center.

# v3.6 — الديون الخارجية
- وحدة ديون لنا وعلينا، أنواع الجهات، السداد والحالات التلقائية.
- ربط بدفتر الحركة والبحث والتقارير وسلة المحذوفات.

# v3.5.1 - Stable Ledger Sync Fix
- إصلاح مزامنة تعديل الإيرادات مع دفتر الحركة المالية.
- إصلاح مزامنة تعديل المصروفات وسدادات الموردين.
- دعم تعديل تقسيم العملية على عدة حسابات مالية مع التحقق من المجموع.
- إضافة اختبار آلي يمنع رجوع مشكلة اختلاف الأرصدة.
- تنظيف الحزمة من البيئات الافتراضية وملفات التخزين المؤقت.

# v3.4 - البحث الذكي العام
- إضافة مربع بحث ثابت في أعلى الواجهة.
- البحث في الموردين والمستخدمين والمواقع وتحويلات الفواتير والحركات المالية.
- احترام صلاحيات المستخدم والموقع التابع له عند عرض النتائج.
- فتح النتيجة مباشرة من صفحة النتائج.

# v3.3 - اختيار المستخدم في تسجيل الدخول
- استبدال كتابة اسم المستخدم بقائمة تلقائية للحسابات المفعلة.
- عرض الاسم الكامل واسم الدخول معًا.
- عدم إظهار الحسابات الموقوفة.

# v3.1 - محرك التقارير
- تصدير تقارير الإيرادات والمصروفات والسدادات والحسابات المالية.
- دعم PDF وWord وExcel.
- فلترة حسب الفترة والموقع.
- إضافة اسم الشركة والمستخدم وتاريخ الإصدار داخل التقرير.

# v2.7 - إدارة المواقع
- إضافة وتعديل أسماء الفروع والمخازن وأكوادها.
- تفعيل وإيقاف المواقع مع حفظ البيانات القديمة.
- عرض أعداد المستخدمين والحسابات وحسابات الموردين والحركات لكل موقع.

# سجل الإصدارات

## v2.2.0 — Treasury
- إضافة دفتر خزينة يومي لكل فرع.
- إضافة رصيد افتتاحي مستقل حسب الفرع والتاريخ.
- دمج الإيرادات والمصروفات وسدادات الموردين في كشف حركة واحد.
- حساب الرصيد المتحرك ورصيد الإغلاق المتوقع تلقائيًا.
- إضافة فلترة الخزينة بالفرع والتاريخ.
- إضافة صلاحية مستقلة لإدارة رصيد افتتاح الخزينة.
- إضافة اختبارات دخان لتسجيل الدخول ولوحة التحكم والخزينة والصلاحيات.

## v2.1.0 — Control Center
- مركز تحكم وفلاتر زمنية ومقارنة الفروع.

## v2.0.0 — Foundation
- إعادة تنظيم أساس المشروع وفصل الملفات الرئيسية.


## v2.3 — مركز القيادة وإقفال اليوم
- تغيير اسم لوحة التحكم إلى مركز القيادة.
- عرض حالة كل فرع لليوم وتنبيهات الفروع التي لم تسجل الإيراد.
- إقفال اليوم وإعادة فتحه بصلاحية مستقلة.
- منع إضافة أو تعديل أو حذف الحركات في الأيام المقفلة.

## v2.4.0 — Financial Accounts Foundation
- إضافة الحسابات المالية لكل فرع: نقدي، مصرفي، محفظة وبطاقة.
- إضافة دفتر الحركة المالية الموحد.
- ربط الإيرادات والمصروفات وسدادات الموردين بحساب مالي.
- ترحيل الحركات القديمة تلقائيًا إلى الخزينة النقدية الافتراضية.
- تقسيم صفحة الخزينة حسب الحسابات والأرصدة.
- إيقاف وتفعيل الحسابات مع بقاء الحركات القديمة محفوظة.

> هذه المرحلة تدعم حسابًا واحدًا لكل عملية. تقسيم العملية الواحدة على عدة حسابات مقرر للمرحلة التالية.

## v2.5 — تقسيم العمليات على عدة حسابات
- تقسيم الإيراد الواحد على أكثر من حساب مالي.
- تقسيم المصروف وسداد المورد بالطريقة نفسها.
- التحقق من أن مجموع التقسيم يساوي قيمة العملية قبل الحفظ.
- إنشاء قيد مستقل لكل حساب داخل دفتر الحركة مع مرجع موحد للعملية.
- ترقية آمنة لجدول دفتر الحركة القديم دون فقد البيانات.

## v2.6 - Supplier Accounts by Location
- Added locations support with a main warehouse.
- Added independent supplier accounts for each branch or warehouse.
- Supplier payments now post to the selected supplier-location account.
- Existing supplier balances and payments migrate safely to the main warehouse account.

## v2.8 - Inventory Transfers
- Added products and per-location inventory balances.
- Added stock transfer workflow: draft, sent, received, cancelled.
- Added quantity reconciliation on receipt.


## v2.9 - Invoice Transfers
- استبدال الأصناف بتحويل الفواتير كاملة بين المواقع.
- دورة مسودة، إرسال، استلام، وإلغاء.

## v3.2 - صلاحيات المستخدمين والتعديل
- فصل صلاحيات عرض المستخدمين وإضافة المستخدمين وتعديل المستخدمين.
- إضافة تعديل الاسم الكامل واسم المستخدم وكلمة المرور والموقع والحالة.
- إضافة تعديل صلاحيات المستخدم من شاشة مستقلة.
- حماية حسابات المدير من التعديل بواسطة المستخدمين العاديين.

## v3.5 - Recycle Bin
- سلة محذوفات موحدة للإيرادات والمصروفات والسدادات والموردين.
- استرجاع القيود المالية المرتبطة والحذف النهائي للمدير فقط.

## v3.7 — ملخص الأرصدة وكشوف الحساب
- ملخص الرصيد الحالي لكل حساب مالي ولكل موقع.
- إجمالي منفصل للنقدي والمصارف والبطاقات والمحافظ والإجمالي العام.
- صفحة تفاصيل خزينة كل فرع أو مخزن.
- كشف حساب بفترة زمنية مع الرصيد السابق والوارد والصادر والختامي.
- تصدير كشف الحساب إلى PDF وWord وExcel.

## v3.8 — الخزينة الرئيسية وتوريدات الفروع
- اعتماد المخزن كمركز مالي وخزينة رئيسية.
- عرض خزائن الفروع مستقلة مع إجمالي الخزينة الرئيسية وإجمالي أموال الشركة.
- إضافة توريدات وتحويلات مالية بين حسابات الفروع والمخزن.
- دورة مسودة، إرسال، انتظار استلام، استلام، وإلغاء.
- نقل الرصيد فقط عند تأكيد الاستلام، بدون احتسابه إيرادًا أو مصروفًا.
- إشعارات ومهام تلقائية للموقع المستلم وإشعار تأكيد للمرسل.
- صلاحيات مستقلة للعرض والإنشاء والاستلام والتحويل من الخزينة الرئيسية والتصدير.
- تقارير PDF وWord وExcel للتوريدات المالية.


## v3.9 - التصنيف المالي للمصروفات
- إضافة تصنيف مالي للمصروف: تشغيلي، أصل، أو التزام/خصم.
- إضافة نوع الأصل عند اختيار تصنيف أصل.
- اعتبار المصروفات القديمة تشغيلية تلقائيًا.
- إظهار التصنيف المالي في شاشة المصروفات والتقارير وصادرات PDF وWord وExcel.

## v4.0.0 — Financial Classification Foundation
- إضافة جدول `financial_classifications` كدليل مركزي للتصنيفات المحاسبية.
- ربط المصروفات بالتصنيف عبر `classification_id` مع الحفاظ على التوافق مع البيانات القديمة.
- إضافة أقسام القوائم المالية وطبيعة الرصيد وحالة التفعيل وترتيب العرض.
- ترحيل المصروفات القديمة تلقائيًا إلى التصنيف المناسب.

## v4.1 - Financial Reports
- إضافة قسم التقارير المالية: المركز المالي، قائمة الدخل، التدفق النقدي، والتعديلات اليدوية.
- دعم PDF وWord وExcel للتقارير المالية.
- فصل الأرقام المحسوبة من النظام عن التعديلات اليدوية مع سجل إلغاء محفوظ.
- إضافة صلاحيات مستقلة لعرض وإدارة وتصدير التقارير المالية.

## 5.0.0-dev — Enterprise Foundation
- إضافة معلومات الإصدار وقاعدة البيانات داخل النظام.
- إضافة الأساس العام لمحرك سير العمل: التعريفات، الحالات، الانتقالات، النسخ الجارية، والسجل التاريخي.
- إضافة دورة افتراضية لتحويلات الخزينة.
- إضافة مركز سير العمل داخل القائمة الجانبية وإدارة النظام.


## 5.1.0-dev — Unified System Settings
- إضافة مركز إعدادات نظام موحّد.
- إضافة أقسام بيانات الشركة، المالية، الضرائب، الإشعارات، الأمان، المظهر، والنسخ الاحتياطي.
- ربط الفروع ومولد الأكواد ومعلومات الإصدار بالمركز.
- تسجيل كل تعديل على الإعدادات في سجل النشاط.
- تقييد المركز بصلاحية إدارة الإعدادات.

## v5.2.0-dev — Business Rules Engine
- إضافة جداول سياسات النظام وسجل تغييراتها.
- إضافة خدمة مركزية لقراءة وتعديل وإعادة ضبط السياسات.
- إضافة شاشة سياسات النظام مع البحث والتصفية وسجل التغييرات.
- إضافة صلاحية مستقلة لإدارة السياسات وربط التعديلات بسجل النشاط.
- إضافة اختبارات آلية لمحرك السياسات والواجهة.

## 5.3.0-dev — 2026-07-21
- Added the Approval Engine and Approval Center.
- Added approval definitions, request decisions, and history.
- Connected treasury transfer submission to approvals for non-admin users.

## 5.4.0-dev — 2026-07-21

- إضافة Notification Service وTask Service.
- منع التكرار ودعم الروابط المباشرة والاستحقاق.
- ربط دورة الاعتماد بالإشعارات والمهام تلقائيًا.
- إضافة مؤشرات العمل العاجل إلى لوحة القيادة.
- تحديث مخطط قاعدة البيانات إلى 5.4.

## v5.8.3 — Supplier Financial Profile & Due Alerts
- Added supplier statements, direct payments, due-date alerts, supplier statistics, rating, category, grace days, and per-location account statements.

## v5.8.7 - Revenue Invoice Count & Employee Performance Allocation
- Added daily invoice count to revenue entries.
- Added employee revenue and invoice allocation with strict totals validation.
- Added attendance-hours snapshot for later performance analysis.
- Added invoice count and average invoice value to the revenue list.
