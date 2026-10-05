# نشر «أشبال القرآن» على Vercel (Serverless)

> **لماذا Vercel وليس Netlify؟**
> Netlify Functions لا تدعم Python للتشغيل — الوظائف فيها JavaScript/TypeScript فقط.
> Vercel يدعم Python أصلاً، ويكتشف Flask تلقائيًا، ويشغّله كدالة Serverless على
> Fluid Compute. لا خدمة «تنام» كما في خطط الاستضافة المجانية الأخرى، والفتح سريع.

---

## 1. المتطلبات

- حساب على [vercel.com](https://vercel.com) (الخطة المجانية كافية).
- قاعدة بيانات **Supabase** جاهزة (نفّذ `supabase_schema.sql` مرة واحدة).
- مفاتيح البيئة: `DATABASE_URL` و`SESSION_SECRET`.

## 2. إعداد قاعدة البيانات (مهم للسرعة)

على Vercel كل نسخة دالة قصيرة العمر. لذلك:

1. من لوحة Supabase → **Connect** → اختر **Transaction pooler** (المنفذ **6543**).
2. الصق الرابط في متغيّر `DATABASE_URL` وأضف `?sslmode=require`.
3. استخدم **Session pooler (5432)** فقط إذا كنت تشغّل الموقع محليًا بخادم دائم.

مثال:
```
postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

## 3. متغيّرات البيئة على Vercel

Project Settings → **Environment Variables**:

| المفتاح | القيمة | ملاحظة |
|---|---|---|
| `DATABASE_URL` | رابط Transaction pooler | إلزامي |
| `SESSION_SECRET` | سر عشوائي 64 حرفًا | إلزامي |

لتوليد السر:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 4. النشر

### الطريقة الأولى: من Git (موصى بها)
1. ارفع المشروع إلى GitHub/GitLab/Bitbucket.
2. من [vercel.com/new](https://vercel.com/new) استورد المستودع.
3. لا تغيّر أي إعداد — Vercel يكتشف Flask من `main.py` تلقائيًا.
4. **Deploy**.

### الطريقة الثانية: Vercel CLI
```bash
npm i -g vercel
vercel login          # سجّل الدخول بحسابك (مرة واحدة)
cd ashbal-quran-main
vercel                # معاينة
vercel --prod         # الإنتاج
```
> لا تستخدم `vercel deploy --temporary`: غير متاح للحسابات العادية.
> يجب تسجيل الدخول أولًا، ثم النشر يتطلب وجود متغيّرات البيئة في مشروع Vercel.

## 4.1 التحقق قبل النشر (Pre-flight)

يمكن تشغيل هذه الفحوص محليًا قبل الرفع لتجنّب فشل البناء:

```bash
# 1) تثبيت التبعيات في بيئة نظيفة (كما يفعل Vercel)
python -m venv .venv
.venv\Scripts\activate          # ويندوز
pip install -r requirements.txt

# 2) تشغيل أمر البناء (ينسخ static/ إلى public/static/)
python build.py

# 3) التحقق من استيراد نقطة الدخول
set VERCEL=1
set SESSION_SECRET=test
set DATABASE_URL=sqlite:///./_test.db
python -c "import main; print(main.app); print(len(list(main.app.url_map.iter_rules())), 'routes')"

# 4) اختبار أول طلب
python -c "import main; c=main.app.test_client(); print(c.get('/healthz').status_code)"
```

المتوقّع: `81 routes`، و`/healthz` يعيد `200`.

### المصدر المعتمد للتبعيات
`requirements.txt` هو المصدر المعتمد، وقد وُحّدت إصداراته مع `pyproject.toml`.
حُذف `uv.lock` القديم لأنه كان لا يضمّ `flask-wtf` وكان سيفشل البناء إن اختاره Vercel.

## 5. أول تشغيل

- عند أول طلب تُنشئ الدالة الجداول الناقصة وحساب الأدمن الافتراضي تلقائيًا.
- **غيّر كلمة مرور الأدمن فورًا** (الافتراضية `admin` / `1`).
- تحقّق من الجاهزية عبر: `https://<موقعك>/healthz` (يجب أن يعيد `{"status":"ok"}`).
- تابع سجلات الدالة من: Vercel → مشروعك → **Logs**. أول طلب قد يستغرق ثوانٍ
  (تهيئة القاعدة)، ثم تصبح الاستجابات سريعة.

## 6. ما تغيّر ليتوافق مع Vercel

- **إزالة النسخة المكرّرة** من `main.py` (كان الملف يعرّف التطبيق مرتين).
- **تفعيل مسارات PWA/API** التي كانت معرّفة داخل دالة ولا تُسجَّل أبدًا.
- **`NullPool`** لمحرك قاعدة البيانات + دعم بادئة `postgres://` القديمة.
- **تهيئة مؤجّلة** عند أول طلب بدل مهام الإقلاع والخيوط.
- **تعطيل** المزامنة الدورية للـ sequences (غير مجدية في serverless).
- **كوكيز آمنة** (`Secure`) على HTTPS.
- **عرض `/service-worker.js` من الجذر** بنطاق كامل.
- **كاش CDN للملفات الثابتة** عبر `public/static` (ينسخها `build.py` وقت البناء).

## 7. التحصينات الأمنية المضافة

- **حماية CSRF شاملة** عبر Flask-WTF:
  - رمز يُحقن تلقائيًا في كل صفحة HTML (وسم `meta`) وفي كل نموذج، عبر سكربت
    `static/js/csrf.js` يُضاف من الخادم — بلا الحاجة لتعديل 39 قالبًا.
  - ترقيع `fetch` و`XMLHttpRequest` لإرسال `X-CSRFToken` تلقائيًا.
- **تحويل كل العمليات المدمِّرة إلى POST** (كانت GET قابلة للاستغلال بوسم
  واحد): حذف طالب/معلم/مشرف/مقرر/قاعدة مكافآت، والتبديل بين الصلاحيات.
  وروابط هذه العمليات تُرسَل الآن كطلبات POST مع الرمز عبر `__aqGo`.
- **حماية مسارات JSON** (`/api/sync/*`) بشرط ترويسة `X-Sync-Request` لمنع
  الطلبات المزوّرة المبنية على الكوكيز (لأن Service Worker لا يحمل رمز CSRF).
- **تحديد محاولات تسجيل الدخول**: 8 محاولات لكل (IP + اسم مستخدم) في 15 دقيقة.
- **`session_protection='basic'`** لكشف سرقة الجلسة.
- **إصلاح ترتيب تسجيل الخروج**: `logout_user` قبل `session.clear` حتى يُمحى
  كوكي «تذكّرني» فعلاً.
- **عدم تسريب نصوص الاستثناءات** للعميل؛ تُسجَّل في الخادم فقط.
- **منع تخزين الصفحات في كاش مشترك** لأنها تحمل رمز جلسة (الحقن صار خاصًا).

## 8. هيكل ملفات النشر

```
vercel.json     ← إعدادات الدالة والرؤوس
pyproject.toml  ← نقطة الدخول (main:app) وأمر البناء
build.py        ← نسخ static/ إلى public/static وقت البناء
.vercelignore   ← ملفات لا تُرفع مع الدالة
static/js/csrf.js ← جسر الحماية CSRF في كل الصفحات
```

## 9. استكشاف الأخطاء

| العرض | السبب المحتمل | الحل |
|---|---|---|
| `500` عند أول طلب | `DATABASE_URL` خاطئ أو منفذ 5432 | استخدم 6543 + `sslmode=require` |
| `too many clients` | تجمّع اتصالات غير NullPool | تأكّد أن المتغيّر `VERCEL` موجود (يضبطه Vercel تلقائيًا) |
| الصفحات لا تُحدَّث | Service Worker قديم | ارفع `SW_VERSION` في `service-worker.js` |
| المزامنة لا تعمل | مسارات `/api` | تحقّق من `/api/ping` عبر المتصفح |

## 10. ملاحظة اختيارية: الـ Domain

إذا ربطت نطاقًا مخصّصًا، أبقِ `SESSION_COOKIE_SECURE` مفعّلًا (مضبوط تلقائيًا
عند وجود `VERCEL`). الكوكيز على `netlify.app`/`vercel.app` لا تعمل عبر
النطاقات الفرعية بسبب قائمة Public Suffix، لذا استخدم نطاقًا مخصّصًا عند الحاجة.
