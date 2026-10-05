# -*- coding: utf-8 -*-
"""app.py — ملف دخول تشخيصي مؤقت (بلا أي استيراد خارجي).

الهدف: كشف سبب FUNCTION_INVOCATION_FAILED. لأن استيراد flask في الأعلى يُسقط
الدالة قبل أن نطبع أي شيء، نستخدم هنا WSGI خامًا (stdlib فقط) ثم نحاول تحميل
التطبيق الحقيقي داخل try/except ونعرض الخطأ في الاستجابة.

⚠️ مؤقت للتشخيص — يُحذف بعد تحديد السبب.
"""
import os
import sys
import traceback

_DIAG = None
_APP = None

try:
    import main as _main           # يجرّب استيراد التطبيق الحقيقي
    _APP = _main.app
    _DIAG = "OK: imported main.app successfully"
except Exception:
    _DIAG = traceback.format_exc()

_ENV = (
    f"python: {sys.version}\n"
    f"executable: {sys.executable}\n"
    f"cwd: {os.getcwd()}\n"
    f"VERCEL: {os.environ.get('VERCEL')}\n"
    f"DATABASE_URL set: {bool(os.environ.get('DATABASE_URL'))}\n"
    f"SESSION_SECRET set: {bool(os.environ.get('SESSION_SECRET'))}\n"
    f"sys.path[:5]: {sys.path[:5]}\n"
)

if _APP is not None:
    # نجح الاستيراد: خدمة عادية عبر التطبيق الحقيقي
    app = _APP
else:
    def app(environ, start_response):
        body = f"=== DIAG (import failed) ===\n{_ENV}\n=== TRACEBACK ===\n{_DIAG}\n".encode("utf-8")
        start_response("500 Internal Server Error", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]
