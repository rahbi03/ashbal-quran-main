"""نقطة دخول WSGI للتشغيل المحلي/الخادم الدائم (gunicorn).

على Vercel لا تحتاج هذا الملف: المنصّة تكتشف Flask من كائن ``app`` في
``main.py`` مباشرة وتشغّله كدالة Serverless.

عند التشغيل بـ gunicorn (خادم دائم بعامل واحد + عدة خيوط) نُشغّل تهيئة
قاعدة البيانات مرة واحدة. على Vercel تُتجاهل لأن main.py يتكفّل بتهيئة
مؤجّلة عند أول طلب (انظر ``_serverless_ensure_db``).
"""

import os

from werkzeug.middleware.proxy_fix import ProxyFix

from main import app, db, IS_SERVERLESS

# عندما ينهي الوكيل العكسي TLS، نعلّم Flask أن الأصل https ليولّد روابط صحيحة.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _init_database() -> None:
    from main import create_default_admin, update_database_schema

    with app.app_context():
        try:
            db.create_all()
            update_database_schema()
            create_default_admin()
            print("✅ تهيئة قاعدة البيانات اكتملت", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ خطأ في تهيئة قاعدة البيانات: {exc}", flush=True)


if not IS_SERVERLESS and not os.environ.get("SKIP_STARTUP_TASKS"):
    # خيط خلفي: لا نؤخّر إقلاع gunicorn انتظارًا للتهيئة.
    import threading

    threading.Thread(target=_init_database, name="startup-init", daemon=True).start()


# gunicorn يشير إلى wsgi:app
__all__ = ["app"]
