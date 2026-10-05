# -*- coding: utf-8 -*-
"""app.py — ملف دخول تشخيصي مؤقت.

Vercel يكتشف ملفات الدخول بالترتيب: app.py قبل main.py. هذا الملف يحاول
استيراد التطبيق الحقيقي من main.py، وإن فشل يعرض نص الخطأ الكامل في الاستجابة
حتى نرى السبب الحقيقي (لأن الخطأ في بيئة Vercel لا يظهر لنا محليًا).

⚠️ ملف مؤقت للتشخيص فقط — يُحذف بعد معرفة السبب.
"""
import os
import sys
import traceback

from flask import Flask

# اطبع معلومات البيئة في سجلات Vercel
print("=== DIAG app.py ===", flush=True)
print("python:", sys.version, flush=True)
print("cwd:", os.getcwd(), flush=True)
print("VERCEL:", os.environ.get("VERCEL"), flush=True)
print("DATABASE_URL set:", bool(os.environ.get("DATABASE_URL")), flush=True)
print("SESSION_SECRET set:", bool(os.environ.get("SESSION_SECRET")), flush=True)

try:
    import main as _main  # noqa: F401
    from main import app  # noqa: F401
    print("=== DIAG import main OK ===", flush=True)
except Exception:
    _tb = traceback.format_exc()
    print("=== DIAG import main FAILED ===", flush=True)
    print(_tb, flush=True)

    app = Flask(__name__)
    app.secret_key = "diag"

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def _diag(path):
        import platform
        info = (
            f"python: {sys.version}\n"
            f"cwd: {os.getcwd()}\n"
            f"VERCEL: {os.environ.get('VERCEL')}\n"
            f"DATABASE_URL set: {bool(os.environ.get('DATABASE_URL'))}\n"
            f"SESSION_SECRET set: {bool(os.environ.get('SESSION_SECRET'))}\n\n"
            f"=== TRACEBACK ===\n{_tb}"
        )
        return f"<pre>{info}</pre>", 500
