# أشبال القرآن

منصة عربية (RTL) لإدارة تحفيظ القرآن الكريم: طلاب، معلمون، مشرفون، مقررات
أسبوعية (حفظ/سرد/قصائد/تقييم مرحلة)، تقارير يومية، إنذارات، ونقاط ومكافآت.
التطبيق يعمل كـ **PWA** يدعم العمل دون اتصال والمزامنة لاحقاً.

## التقنيات

| الطبقة | التقنية |
|---|---|
| الواجهة | Flask 3 + Jinja2 + Bootstrap 5 RTL |
| البيانات | SQLAlchemy 2 + PostgreSQL (Supabase) أو SQLite محلياً |
| المصادقة | Flask-Login + Werkzeug |
| دون اتصال | Service Worker + IndexedDB + Background Sync |
| الاستضافة | **Vercel** (دوال Serverless على Fluid Compute) |

## التشغيل محلياً

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env     # ثم ضع DATABASE_URL و SESSION_SECRET
python main.py            # http://localhost:5000
```

عند أول تشغيل تُنشأ الجداول وحساب مشرف افتراضي (`admin` / `1`).
**غيّر كلمة المرور فوراً بعد أول دخول.**

## النشر

راجع **[VERCEL_DEPLOY.md](VERCEL_DEPLOY.md)** — دليل خطوة بخطوة للنشر على Vercel
مع شرح إعداد Supabase وترويسات الذاكرة المؤقتة وتسريع الفتح.

## هيكل المشروع

```
main.py              التطبيق الكامل (نماذج + مسارات + منطق)
wsgi.py              نقطة دخول خادم دائم محلي (gunicorn)
api_routes.py        نسخة مرجعية لمسارات المزامنة (غير مستخدمة؛ مدمجة في main.py)
build.py             نسخ static/ إلى public/static وقت بناء Vercel
vercel.json          إعدادات دالة Vercel والرؤوس
supabase_schema.sql  مخطط قاعدة البيانات (نفّذه مرة واحدة على Supabase)
templates/           39 قالب Jinja
static/              PWA: manifest + service-worker + IndexedDB + المزامنة
VERCEL_DEPLOY.md     دليل النشر
```

## ملاحظات أمنية

- لا ترفع `.env` إلى Git أبداً (مستثنى في `.gitignore`).
- لا تضع `DATABASE_URL` أو `SESSION_SECRET` في المستودع — استخدم متغيّرات بيئة المنصة.
- غيّر كلمة مرور المشرف الافتراضية مباشرة بعد النشر.
- مسارات المزامنة `/api/*` محمية بتسجيل الدخول وملكية البيانات.
