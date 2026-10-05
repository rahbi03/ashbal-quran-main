# fix_points_tables.py
from main import app, db, StudentPoints, PointsLog

def recreate_points_tables():
    with app.app_context():
        # حذف الجداول القديمة (إذا وجدت)
        PointsLog.__table__.drop(db.engine, checkfirst=True)
        StudentPoints.__table__.drop(db.engine, checkfirst=True)
        # إعادة إنشاء الجداول
        db.create_all()
        print("✅ تم إعادة إنشاء جداول النقاط")

if __name__ == "__main__":
    recreate_points_tables()