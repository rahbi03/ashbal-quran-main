# سجل التغييرات — نسخة Vercel المعتمدة

هذه النسخة بديلة معتمدة لتفعيل التطبيق على Vercel (Serverless) وتسريعه.
فيما يلي الفرق الكامل عن نسخة GitHub الأصلية.

## ملفات مُعدّلة (استبدلها)

| الملف | أبرز التغيير |
|---|---|
| `main.py` | حذف النسخة المكرّرة (11,513 → 5,880 سطراً)، تفعيل مسارات `/api/*` و`/offline`، `NullPool`، تهيئة مؤجّلة، تعطيل مزامنة الـ sequences الدورية، مسار `/service-worker.js` و`/healthz`، نقطة `/api/student/dashboard`، كوكيز `Secure`، كاش الصفحات العامة |
| `wsgi.py` | إزالة تعارض `/healthz`، تهيئة خلفية آمنة، دعم `IS_SERVERLESS` |
| `pyproject.toml` | نقطة الدخول `main:app` + أمر البناء `python build.py` |
| `requirements.txt` | إضافة `setuptools` (مطلوب لـ Flask 3) |
| `README.md` | تحديث كامل لوثائق المشروع |
| `.env.example` | توضيح Transaction pooler (6543) و`sslmode=require` |
| `.gitignore` | تجاهل `public/` (ناتج البناء) |
| `static/pwa-sync.js` | منع فقدان البيانات الصامت عند الانقطاع |
| `static/service-worker.js` | رفع الإصدار إلى `v6` وإضافة ملفات الأمان للكاش |
| `pyproject.toml` | إضافة `flask-wtf` |
| `requirements.txt` | إضافة `flask-wtf` |
| `templates/add_phase_evaluation.html` | إصلاح `now()` التي كانت تسبب خطأ 500 |
| `templates/admin_duplicates.html` | تحويل الحذف إلى POST عبر `__aqGo` |
| `templates/view_student_plans.html` | تحويل الحذف إلى POST عبر `__aqGo` |
| `static/js/confirm-modal.js` | نسخة واحدة نظيفة تدعم POST |

## ملفات جديدة (أضفها)

| الملف | الوظيفة |
|---|---|
| `vercel.json` | إعدادات دالة Vercel والرؤوس وحدود الحزمة |
| `build.py` | نسخ `static/` إلى `public/static/` وقت البناء (كاش CDN) |
| `.vercelignore` | ملفات لا تُرفع مع الدالة |
| `static/js/csrf.js` | جسر CSRF يُحقن في كل الصفحات (نماذج + fetch + روابط الحذف) |
| `VERCEL_DEPLOY.md` | دليل النشر خطوة بخطوة |
| `push_to_github.ps1` | سكربت رفع آمن إلى GitHub |
| `CHANGES.md` | هذا الملف |

## تفكيك القوالب المكرّرة

كانت 13 قالباً تحتوي تكراراً كاملاً (المستند نفسه مكرّر مرتين إلى أربع مرات):

| النوع | الملفات | الوصف | الإجراء |
|---|---|---|---|
| كود ميت | `admin.html`, `admin_duplicates.html`, `edit_evaluation.html`, `evaluate_plan.html`, `student_dashboard.html`, `view_plan_details.html`, `view_student_plans.html` | نسخة ثانية داخل `{% if false %}` (لا تُعرض أبدًا) | حُذفت |
| مستندات ملتصقة | `login.html`, `add_weekly_plan.html`, `admin_calculate_rewards.html`, `admin_reward_results.html`, `admin_reward_rules.html`, `missing_plans.html` | 2–4 مستندات HTML كاملة ملتصقة، تُرسل للمتصفح معًا | أُبقيت نسخة واحدة |

النتيجة: **916 ك.ب → 430 ك.ب** (توفير 53% في هذه الملفات)، وصفحات نظيفة
بمستند HTML واحد (`<!DOCTYPE>` واحد) بدل أربعة.

## ملفات محذوفة

| الملف | السبب |
|---|---|
| `render.yaml` | لم يعد المشروع على Render |

## ملفات لم تتغيّر

`api_routes.py` (نسخة مرجعية، مدمجة فعلياً في `main.py`)، `fix_points_tables.py`،
`supabase_schema.sql`، `uv.lock`، `.python-version`، `.replit`، `INSTALL.md`،
`templates/**`، `static/` (بقية الملفات)، `static/images/**`، `static/js/**`،
`.agents/**`، `screenshots/**`، الأيقونات.

---

## طريقة التطبيق على GitHub

### الخيار الأسهل: استبدال الملفات
انسخ كامل محتوى هذه الحزمة فوق مستودعك (استبدل المتشابه وأضف الجديد واحذف
`render.yaml`)، ثم:
```bash
git add -A
git commit -m "نشر على Vercel: إصلاح البنية وتفعيل المزامنة وتحسين السرعة"
git push
```

### الخيار الأسرع: سكربت جاهز
```powershell
powershell -ExecutionPolicy Bypass -File .\push_to_github.ps1 -RepoUrl "https://github.com/USER/REPO.git"
```
أضف `-Force` فقط إذا أردت أن تحل هذه النسخة محل كل تاريخ المستودع
(`--force-with-lease`).
