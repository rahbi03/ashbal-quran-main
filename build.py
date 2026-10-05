# -*- coding: utf-8 -*-
"""
build.py — أمر البناء على Vercel.

ماذا يفعل؟
----------
ينسخ مجلد static/ إلى public/static/ وقت البناء، فيقدّمه Vercel عبر شبكته
(CDN) بسرعة ويخفّف الحمل عن دالة Flask. القوالب تشير إلى /static/... عبر
url_for، فيطابق المسار ما ينشره CDN تمامًا.

لماذا وقت البناء لا نسخة ثابتة في المستودع؟
------------------------------------------
حتى لا تتكرر الملفات في Git وتتباعد النسختان عند أي تعديل مستقبلي.
"""
import shutil
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "static"
DST = ROOT / "public" / "static"


def main() -> None:
    if not SRC.exists():
        print("[build] لا يوجد مجلد static/ — تخطّي النسخ")
        return

    if DST.exists():
        shutil.rmtree(DST)
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DST)
    count = sum(1 for _ in DST.rglob("*") if _.is_file())
    print(f"[build] نُسخ {count} ملفًا من static/ إلى public/static/")


if __name__ == "__main__":
    main()
