import os
import csv
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, date
from io import StringIO
from time import sleep
import pytz
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, session, jsonify, Blueprint, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import inspect, exc, func, and_, or_
from sqlalchemy.orm import joinedload, lazyload, selectinload
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, DisconnectionError
import uuid as uuid_module
from itertools import combinations

# ------------------ إصلاح ترميز المخرجات على Windows ------------------
# الطرفية الافتراضية تستخدم cp1256 ولا تستطيع طباعة الرموز التعبيرية،
# فيفشل print داخل مهام البدء ويتوقف إنشاء حساب الأدمن وقاعدة البيانات.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

# ------------------ تحميل القيم من ملف .env (محلياً) ------------------
# على Replit/Supabase تُمرَّر نفس المتغيرات من إعدادات Secrets، والملف يُتجاهل عندها.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ------------------ إعداد التطبيق ------------------
# IS_SERVERLESS: true على Vercel (وقابل للتفعيل يدويًا عبر SERVERLESS=1).
# يتحكّم في: تجمّع الاتصالات، ومهام التهيئة، وكوكيز الأمان.
IS_SERVERLESS = bool(os.environ.get('VERCEL') or os.environ.get('SERVERLESS'))

app = Flask(__name__)

# Supabase/Render قد يمرّران بادئة postgres:// القديمة التي لا يفهمها SQLAlchemy 2.
_DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///quran.db')
if _DATABASE_URL.startswith('postgres://'):
    _DATABASE_URL = _DATABASE_URL.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.secret_key = (
    os.environ.get('SESSION_SECRET')
    or os.environ.get('SECRET_KEY')
)
if not app.secret_key:
    if IS_SERVERLESS:
        # لا نُسقط الاستيراد وقت البناء؛ المفتاح يُضبط من Environment Variables.
        import secrets as _secrets
        app.secret_key = _secrets.token_hex(32)
        print('⚠️ SESSION_SECRET غير مضبوط — استُخدم مفتاح مؤقت. اضبطه في بيئة النشر.', flush=True)
    else:
        raise RuntimeError('SESSION_SECRET is required to protect login sessions')

# ------------------ إعدادات الجلسة لمنع انتهائها المبكر ------------------
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True                      # تجديد الصلاحية مع كل طلب
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=365)
app.config['REMEMBER_COOKIE_REFRESH_EACH_REQUEST'] = True              # تجديدها مع كل طلب
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# على HTTPS (Vercel) نُجبر الكوكيز على السفر عبر HTTPS فقط.
if IS_SERVERLESS or os.environ.get('VERCEL_ENV'):
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['REMEMBER_COOKIE_SECURE'] = True
    app.config['PREFERRED_URL_SCHEME'] = 'https'

# ------------------ إعدادات اتصال قاعدة البيانات ------------------

# أضف هذه الدالة في أي مكان بعد تعريف db
_schema_updated = False  # علامة لضمان التشغيل مرة واحدة فقط

def update_database_schema():
    """تحديث هيكل قاعدة البيانات — تعمل مرة واحدة فقط"""
    global _schema_updated
    if _schema_updated:
        return
    _schema_updated = True
    try:
        with app.app_context():
            inspector = db.inspect(db.engine)

            # ---- جدول weekly_plan ----
            columns = [col['name'] for col in inspector.get_columns('weekly_plan')]
            if 'sard_subtype' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN sard_subtype VARCHAR(20)'))
                print("✅ تم إضافة عمود sard_subtype")
            if 'revision_notes' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN revision_notes TEXT'))
                print("✅ تم إضافة عمود revision_notes")
            if 'poem_id' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN poem_id INTEGER'))
                print("✅ تم إضافة عمود poem_id")
            if 'verse_start' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN verse_start INTEGER'))
                print("✅ تم إضافة عمود verse_start")
            if 'verse_end' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN verse_end INTEGER'))
                print("✅ تم إضافة عمود verse_end")
            # ---- أعمدة الترحيل التلقائي (2026) ----
            if 'rolled_over' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN rolled_over BOOLEAN DEFAULT FALSE'))
                print("✅ تم إضافة عمود rolled_over")
            if 'rolled_from_id' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN rolled_from_id INTEGER REFERENCES weekly_plan(id) ON DELETE SET NULL'))
                print("✅ تم إضافة عمود rolled_from_id")
            if 'roll_reason' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN roll_reason VARCHAR(20)'))
                print("✅ تم إضافة عمود roll_reason")
            if 'roll_notes' not in columns:
                db.session.execute(text('ALTER TABLE weekly_plan ADD COLUMN roll_notes TEXT'))
                print("✅ تم إضافة عمود roll_notes")

            # ---- جدول teacher ----
            teacher_columns = [col['name'] for col in inspector.get_columns('teacher')]
            if 'can_add_plan' not in teacher_columns:
                db.session.execute(text('ALTER TABLE teacher ADD COLUMN can_add_plan BOOLEAN DEFAULT FALSE'))
                print("✅ تم إضافة عمود can_add_plan")
            if 'can_add_poem_plan' not in teacher_columns:
                db.session.execute(text('ALTER TABLE teacher ADD COLUMN can_add_poem_plan BOOLEAN DEFAULT TRUE'))
                print("✅ تم إضافة صلاحية إضافة مقررات القصائد")
            if 'is_active' not in teacher_columns:
                db.session.execute(text('ALTER TABLE teacher ADD COLUMN is_active BOOLEAN DEFAULT TRUE'))
                print("✅ تم إضافة عمود is_active")

            # ---- جدول student_points ----
            sp_columns = [col['name'] for col in inspector.get_columns('student_points')]
            if 'cycle_end_date' not in sp_columns:
                db.session.execute(text('ALTER TABLE student_points ADD COLUMN cycle_end_date DATE'))
                print("✅ تم إضافة عمود cycle_end_date")

            db.session.commit()
            print("🎉 تم تحديث قاعدة البيانات بنجاح")

            # ---- إصلاح تزامن الـ sequences (يمنع أخطاء duplicate key) ----
            # نتخطّاه في بيئة serverless: عشرات الاستعلامات تُبطئ أول طلب،
            # والمزامنة الدورية معطّلة هناك، والاستعادة تستدعيه صراحةً.
            if not IS_SERVERLESS:
                fix_all_sequences()

    except Exception as e:
        print(f"⚠️ خطأ في تحديث قاعدة البيانات: {e}")
        db.session.rollback()


def fix_all_sequences():
    """
    إعادة مزامنة sequences الخاصة بأعمدة id في PostgreSQL مع أعلى قيمة فعلية
    موجودة في كل جدول. يمنع هذا خطأ:
    UniqueViolation: duplicate key value violates unique constraint "<table>_pkey"
    والذي يحدث غالباً بعد استعادة نسخة احتياطية (backup/restore) لا تُحدّث الـ sequence.
    تعمل فقط مع PostgreSQL، ويتم تجاهلها تلقائياً مع SQLite.
    """
    dburi = os.environ.get('DATABASE_URL', 'sqlite:///quran.db')
    if not dburi or 'sqlite' in dburi:
        return
    try:
        with app.app_context():
            inspector = db.inspect(db.engine)
            for table_name in inspector.get_table_names():
                try:
                    columns = [c['name'] for c in inspector.get_columns(table_name)]
                    if 'id' not in columns:
                        continue
                    db.session.execute(text(f"""
                        SELECT setval(
                            pg_get_serial_sequence('"{table_name}"', 'id'),
                            COALESCE((SELECT MAX(id) FROM "{table_name}"), 0) + 1,
                            false
                        )
                    """))
                except Exception as inner_e:
                    db.session.rollback()
                    print(f"⚠️ تعذر إصلاح sequence للجدول {table_name}: {inner_e}")
                    continue
            db.session.commit()
            print("🎉 تم فحص وإصلاح جميع sequences بنجاح")
    except Exception as e:
        print(f"⚠️ خطأ عام في إصلاح sequences: {e}")
        db.session.rollback()


# PostgreSQL-specific connection args (only for non-SQLite databases)
_connect_args = {}
if _DATABASE_URL and 'sqlite' not in _DATABASE_URL:
    _connect_args = {
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5
    }

app.config['TEMPLATES_AUTO_RELOAD'] = not IS_SERVERLESS

# إعدادات المحرك:
# - على Vercel/serverless: NullPool إلزامي. كل نسخة دالة قصيرة العمر، وتجمّع
#   اتصالات دائم فيها يستنزف حد اتصالات Supabase بسرعة ويعيد أخطاء "too many clients".
#   Supabase Transaction pooler (المنفذ 6543) يصمد أمام هذا النمط.
# - محليًا/على خادم دائم: تجمّع عادي مع pool_pre_ping وrecycle.
if IS_SERVERLESS:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'poolclass': __import__('sqlalchemy.pool', fromlist=['NullPool']).NullPool,
        'connect_args': _connect_args,
    }
else:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
        'pool_size': 10,
        'max_overflow': 20,
        'connect_args': _connect_args
    }

db = SQLAlchemy(app)

# ─── إصلاح تلقائي دوري للـ sequences (يمنع أخطاء duplicate key) ────────────
# ملاحظة: هذا الخيط غير مجدٍ في بيئة serverless (يُجمَّد بعد انتهاء الطلب)
# لذا نُعطّله هناك؛ الاستعادة (restore) تستدعي fix_all_sequences() مباشرة.
_seqs_last_check = [0.0]
_seqs_lock = threading.Lock()

@app.before_request
def periodic_sequence_sync():
    """كل 120 ثانية يعيد مزامنة sequences الجداول مع أعلى قيمة id حقيقية.
    يمنع خطأ UniqueViolation بعد استعادة نسخة احتياطية دون إعادة تشغيل الخادم.

    يُنفَّذ في خيط خلفي عمداً: fix_all_sequences() ترسل عشرات الاستعلامات إلى
    Supabase فتقضي قرابة 10 ثوان. يُعطَّل بالكامل في بيئة serverless.
    """
    if IS_SERVERLESS:
        return
    now = time.time()
    if now - _seqs_last_check[0] < 120:
        return
    # نضع العلامة قبل إطلاق الخيط لا بعده، وإلا أطلقت طلبات متزامنة
    # عدة خيوط مزامنة، وكل واحد منها يعدّل 19 جدولاً.
    if not _seqs_lock.acquire(blocking=False):
        return
    _seqs_last_check[0] = now
    try:
        threading.Thread(target=_run_sequence_sync, daemon=True).start()
    finally:
        _seqs_lock.release()


def _run_sequence_sync():
    try:
        fix_all_sequences()
    except Exception:
        pass

# ------------------ إعداد Flask-Login ------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.session_protection = None          # تقليل الفحوصات الأمنية لتحسين الأداء
login_manager.login_view = 'home'                 # إذا احتاج تسجيل دخول يُوجه للصفحة الرئيسية

@login_manager.unauthorized_handler
def login_required_redirect():
    return redirect(url_for('home'))

@app.after_request
def prevent_private_page_cache(response):
    # الصفحات العامة (بلا جلسة) قد تُخزَّن مؤقتًا لتسريع الفتح، أما الصفحات
    # الخاصة التي تحمل جلسة دخول فتبقى private, no-store كما كان.
    if response.mimetype == 'text/html':
        if request.endpoint in ('home', 'offline_page'):
            response.headers['Cache-Control'] = 'public, max-age=300'
        else:
            response.headers['Cache-Control'] = 'private, no-store'
    return response

# ------------------ سياق القوالب ------------------

# ------------------ إعدادات النظام العامة ------------------

# ─── كاش بسيط للإعدادات في الذاكرة ───────────────────────────
# يمنع استعلام DB على كل طلب HTTP (السبب الرئيسي لبطء صفحة البداية)
_settings_cache: dict = {}
_CACHE_TTL = 60  # ثانية — يُحدَّث كل دقيقة فقط

def get_setting_bool(key: str, default: bool = False) -> bool:
    """إرجاع قيمة منطقية مع كاش في الذاكرة لتجنب استعلام DB على كل طلب"""
    import time
    now_ts = time.time()
    cache_key = f'bool_{key}'
    cached = _settings_cache.get(cache_key)
    if cached and now_ts - cached['ts'] < _CACHE_TTL:
        return cached['val']
    try:
        s = SystemSetting.query.filter_by(key=key).first()
        val = bool(s.value_bool) if s is not None else default
    except Exception:
        val = default
    _settings_cache[cache_key] = {'val': val, 'ts': now_ts}
    return val


def set_setting_bool(key: str, value: bool) -> None:
    """تعيين قيمة منطقية وتحديث الكاش فوراً"""
    import time
    s = SystemSetting.query.filter_by(key=key).first()
    if not s:
        s = SystemSetting(key=key, value_bool=bool(value))
        db.session.add(s)
    else:
        s.value_bool = bool(value)
    db.session.commit()
    # تحديث الكاش مباشرة بدون انتظار انتهاء TTL
    _settings_cache[f'bool_{key}'] = {'val': bool(value), 'ts': time.time()}

@app.context_processor
def utility_processor():
    # phase_plan_enabled: يُقرأ من الكاش (لا استعلام DB إلا كل دقيقة)
    return dict(
        now=datetime.now(),
        timedelta=timedelta,
        phase_plan_enabled=get_setting_bool('enable_phase_plan', False)
    )

# ------------------ تحميل المستخدم ------------------
@login_manager.user_loader
def load_user(user_id):
    try:
        kind, raw_id = user_id.split('_', 1)
        model = {'student': Student, 'teacher': Teacher, 'admin': Admin}.get(kind)
        user = db.session.get(model, int(raw_id)) if model else None
        if isinstance(user, Student) and (not user.is_active or user.is_suspended):
            return None
        if isinstance(user, Teacher) and not user.is_active:
            return None
        return user
    except (ValueError, TypeError):
        return None

# إعداد التسجيل للأخطاء
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# دالة لإعادة محاولة الاستعلام عند فشل الاتصال
def retry_on_db_error(func, max_retries=3, delay=1):
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except exc.OperationalError as e:
                if "SSL connection has been closed" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"فشل اتصال SSL، إعادة محاولة {attempt + 1}/{max_retries}")
                    db.session.rollback()
                    sleep(delay * (attempt + 1))
                else:
                    raise
    return wrapper

# معالج للأخطاء على مستوى التطبيق
@app.errorhandler(exc.OperationalError)
def handle_db_error(error):
    if "SSL connection has been closed" in str(error):
        db.session.rollback()
        flash('انتهت الجلسة. يرجى إعادة تسجيل الدخول.', 'warning')
        return redirect(url_for('logout'))
    return str(error), 500

def find_duplicate_plans():
    """
    تُعيد قائمة من القواميس:
    [
      {
        'student': <Student>,
        'plans':   [<plan_info>, ...]   ← مقررات متداخلة في نفس الأسبوع
      },
      ...
    ]
    كل plan_info قاموس يحتوي:
      id, plan_type, start_date, end_date,
      new_memorization, revision_text,
      is_evaluated, report_count
    """


    students = Student.query.all()
    groups = []

    for student in students:
        plans = WeeklyPlan.query.filter_by(student_id=student.id).all()
        if len(plans) < 2:
            continue

        # ابحث عن كل زوج متداخل داخل المجموعة نفسها:
        # مقررات القرآن (حفظ/سرد/تقييم مرحلة) تُقارن ببعضها،
        # ومقررات القصائد تُقارن ببعضها، ولا تُقارن المجموعتان معاً.
        duplicate_ids = set()
        for a, b in combinations(plans, 2):
            a_category = 'قصائد' if a.plan_type == 'قصيدة' else 'قرآن'
            b_category = 'قصائد' if b.plan_type == 'قصيدة' else 'قرآن'
            if a_category != b_category:
                continue

            diff = abs((a.start_date - b.start_date).days)
            if diff < 7:
                duplicate_ids.add(a.id)
                duplicate_ids.add(b.id)

        if not duplicate_ids:
            continue

        # بنِ معلومات كل مقرر
        plan_infos = []
        for plan in plans:
            if plan.id not in duplicate_ids:
                continue
            plan_infos.append({
                'id':               plan.id,
                'plan_type':        plan.plan_type,
                'plan_category':    'قصائد' if plan.plan_type == 'قصيدة' else 'قرآن',
                'start_date':       plan.start_date,
                'end_date':         plan.start_date + timedelta(days=6),
                'new_memorization': plan.new_memorization,
                'revision_text':    plan.revision_text,
                'is_evaluated':     len(plan.evaluations) > 0,
                'report_count':     len(plan.daily_reports),
            })

        plan_infos.sort(key=lambda p: p['start_date'])
        groups.append({'student': student, 'plans': plan_infos})

    return groups


# ------------------ نماذج قاعدة البيانات ------------------
class SystemSettings(db.Model):
        """نموذج إعدادات النظام"""
        id = db.Column(db.Integer, primary_key=True)
        setting_key = db.Column(db.String(100), unique=True, nullable=False)
        setting_value = db.Column(db.String(255), nullable=False)
        description = db.Column(db.String(255))
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        def __repr__(self):
            return f'<SystemSettings {self.setting_key}={self.setting_value}>'

class Student(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(15))
    memorization_level = db.Column(db.String(50))
    teachers = db.relationship('Teacher', secondary='teacher_student', backref='students')
    is_active = db.Column(db.Boolean, default=True)
    is_suspended = db.Column(db.Boolean, default=False)
    warning_count = db.Column(db.Integer, default=0)
    warnings = db.relationship('Warning', backref='student', lazy=True, order_by='Warning.date.desc()')


    def get_id(self):
        return f'student_{self.id}'

class Teacher(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    can_view_missing_reports = db.Column(db.Boolean, default=False)
    can_add_plan = db.Column(db.Boolean, default=False)  # صلاحية إضافة المقررات
    can_add_poem_plan = db.Column(db.Boolean, default=True, nullable=False)  # مفعلة افتراضياً
    is_active = db.Column(db.Boolean, default=True)  # للحذف الناعم

    def get_id(self):
        return f'teacher_{self.id}'

class Admin(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    # صلاحيات
    can_access_students = db.Column(db.Boolean, default=True)
    can_edit_students = db.Column(db.Boolean, default=True)
    can_access_teachers = db.Column(db.Boolean, default=True)
    can_edit_teachers = db.Column(db.Boolean, default=True)
    can_access_reports = db.Column(db.Boolean, default=True)   # تقارير المتخلفين والتقارير التفصيلية
    can_access_warnings = db.Column(db.Boolean, default=True)  # شاشة الإنذارات
    can_access_backup = db.Column(db.Boolean, default=True)    # النسخ الاحتياطي (قراءة وتنزيل)
    can_restore_backup = db.Column(db.Boolean, default=True)   # استعادة النسخ (تعديل)
    is_super_admin = db.Column(db.Boolean, default=False)      # صلاحية كاملة (يتجاوز كل القيود)

    def get_id(self):
        return f'admin_{self.id}'

class Warning(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    warning_number = db.Column(db.Integer)  # 1,2,3,4,5 (أو None إذا كان إجراء آخر)
    action = db.Column(db.String(20))       # 'warning', 'suspend', 'reactivate'
    notes = db.Column(db.Text)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'))
    admin = db.relationship('Admin')


class SystemSetting(db.Model):
    """إعدادات عامة للنظام (قيم منطقية/نصية)"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value_text = db.Column(db.String(200))
    value_bool = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SystemSetting {self.key}>'

# جدول وسيط للعلاقة بين المعلم والطالب (Many-to-Many)
teacher_student = db.Table('teacher_student',
    db.Column('teacher_id', db.Integer, db.ForeignKey('teacher.id'), primary_key=True),
    db.Column('student_id', db.Integer, db.ForeignKey('student.id'), primary_key=True)
)
class Poem(db.Model):
    """قصيدة من مقررات الحفظ"""
    id = db.Column(db.Integer, primary_key=True)
    poem_number = db.Column(db.Integer)          # رقم القصيدة
    poem_title = db.Column(db.String(200))       # اسم القصيدة
    poet = db.Column(db.String(200))             # الشاعر
    verse_count = db.Column(db.Integer)          # عدد الأبيات

    def __repr__(self):
        return f'<Poem {self.poem_number}. {self.poem_title}>'

class WeeklyPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    plan_type = db.Column(db.String(10))  # 'حفظ' أو 'سرد' أو 'قصيدة'
    sard_subtype = db.Column(db.String(20))  # 'كامل' أو 'مراجعة' (جديد)
    poem_id = db.Column(db.Integer, db.ForeignKey('poem.id'), nullable=True)  # للقصائد
    verse_start = db.Column(db.Integer, nullable=True)  # أول بيت في المقرر
    verse_end = db.Column(db.Integer, nullable=True)    # آخر بيت في المقرر
    start_date = db.Column(db.Date, nullable=False)
    new_memorization = db.Column(db.Text)
    recent_review = db.Column(db.Text)
    previous_review = db.Column(db.Text)
    revision_text = db.Column(db.Text)
    revision_notes = db.Column(db.Text)  # ملاحظات السرد (جديد)
    notes = db.Column(db.Text)
    # ---- الترحيل التلقائي للمقرر عند الرسب ----
    # when_fail: سبب الترحيل ('new' أو 'old' أو 'poem') — يجعل الشارة تشرح نفسها
    # rolled_from: رقم المقرر الأصلي الذي رُسخ منه هذا المقرر (للأسبوع التالي)
    rolled_over = db.Column(db.Boolean, default=False)
    rolled_from_id = db.Column(db.Integer, db.ForeignKey('weekly_plan.id', ondelete='SET NULL'), nullable=True)
    roll_reason = db.Column(db.String(20))
    roll_notes = db.Column(db.Text)

    student = db.relationship('Student', backref='weekly_plans')
    teacher = db.relationship('Teacher', backref='weekly_plans')
    poem = db.relationship('Poem', backref='weekly_plans')
    evaluations = db.relationship(
        'Evaluation',
        backref='plan',
        lazy=True,
        order_by='Evaluation.id.asc()'
    )
    daily_reports = db.relationship('DailyReport', backref='plan', lazy=True)

class Evaluation(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        plan_id = db.Column(db.Integer, db.ForeignKey('weekly_plan.id'), nullable=False)
        teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
        evaluation_date = db.Column(db.Date, nullable=False)
        new_score = db.Column(db.Integer)
        new_pass = db.Column(db.Boolean)
        recent_score = db.Column(db.Integer)
        previous_score = db.Column(db.Integer)
        previous_pass = db.Column(db.Boolean)
        revision_score = db.Column(db.Integer)
        revision_pass = db.Column(db.Boolean)
        evaluation_notes = db.Column(db.Text)

        # العلاقة مع المعلم فقط (العلاقة مع الخطة تأتي من backref في WeeklyPlan)
        teacher = db.relationship('Teacher', backref='evaluations')
class DailyReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('weekly_plan.id'), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    day_name = db.Column(db.String(20))
    listening = db.Column(db.Boolean, default=False)
    recitation_mastery = db.Column(db.Boolean, default=False)
    listened_new = db.Column(db.Boolean, default=False)
    repeated_new = db.Column(db.Boolean, default=False)
    reviewed_week = db.Column(db.Boolean, default=False)
    phase_memorization = db.Column(db.Text)
    previous_memorization = db.Column(db.Text)
    recitation_done = db.Column(db.Boolean, default=False)
    recitation_text = db.Column(db.Text)
    recitation_notes = db.Column(db.Text)

# المكافآت

class StudentPages(db.Model):
    """نموذج صفحات الحفظ لكل طالب"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    total_pages = db.Column(db.Integer, default=0)  # إجمالي عدد الصفحات المحفوظة
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)

    student = db.relationship('Student', backref='pages_info', uselist=False)

    def __repr__(self):
        return f'<StudentPages {self.student_id}: {self.total_pages}>'

class StudentPeriodPages(db.Model):
    """صفحات الطالب حسب الفترة المحددة (من/إلى)"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    pages = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref='period_pages')

    __table_args__ = (
        db.UniqueConstraint('student_id', 'period_start', 'period_end', name='uq_student_period_pages'),
    )



class RewardRule(db.Model):
    """نموذج قواعد المكافآت"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # اسم القاعدة (مثلاً: "قاعدة 3 أشهر", "قاعدة 6 أشهر")
    period_months = db.Column(db.Integer, nullable=False)  # الفترة بالأشهر (3 أو 6)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # العلاقة مع تفاصيل القاعدة
    details = db.relationship('RewardRuleDetail', backref='rule', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<RewardRule {self.name}>'


class RewardRuleDetail(db.Model):
    """نموذج تفاصيل قواعد المكافآت (الشرائح)"""
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('reward_rule.id'), nullable=False)
    min_score = db.Column(db.Float, nullable=False)  # الحد الأدنى للدرجة
    max_score = db.Column(db.Float, nullable=False)  # الحد الأقصى للدرجة
    reward_per_page = db.Column(db.Float, nullable=False)  # المكافأة لكل صفحة

    def __repr__(self):
        return f'<RewardRuleDetail {self.min_score}-{self.max_score}: {self.reward_per_page}>'


class PhaseEvaluation(db.Model):
    """نموذج تقييم المرحلة (نوع جديد من المقررات)"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    evaluation_date = db.Column(db.Date, nullable=False)
    phase_number = db.Column(db.Integer)  # رقم المرحلة
    phase_name = db.Column(db.String(100))  # اسم المرحلة
    score = db.Column(db.Integer, nullable=False)  # الدرجة من 80
    notes = db.Column(db.Text)

    student = db.relationship('Student', backref='phase_evaluations')
    teacher = db.relationship('Teacher', backref='phase_evaluations')

    def __repr__(self):
        return f'<PhaseEvaluation {self.student_id}: {self.score}/80>'


class RewardCalculation(db.Model):
    """نموذج لحسابات المكافآت لكل فترة"""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    rule_id = db.Column(db.Integer, db.ForeignKey('reward_rule.id'), nullable=False)
    calculation_date = db.Column(db.DateTime, default=datetime.utcnow)
    period_start = db.Column(db.Date, nullable=False)  # بداية الفترة
    period_end = db.Column(db.Date, nullable=False)    # نهاية الفترة

    # بيانات الحساب
    total_pages = db.Column(db.Integer, default=0)  # إجمالي الصفحات في هذه الفترة
    avg_memorization_score = db.Column(db.Float, default=0)  # متوسط درجات الحفظ
    phase_score = db.Column(db.Integer, default=0)  # درجة تقييم المرحلة
    final_score = db.Column(db.Float, default=0)    # الدرجة النهائية (phase_score + 20% من avg_memorization)

    # المكافآت
    reward_per_page = db.Column(db.Float, default=0)  # المكافأة لكل صفحة حسب القاعدة
    total_reward = db.Column(db.Float, default=0)     # إجمالي المكافأة

    is_paid = db.Column(db.Boolean, default=False)    # هل تم صرف المكافأة
    paid_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text)

    student = db.relationship('Student', backref='reward_calculations')
    rule = db.relationship('RewardRule')

    def __repr__(self):
        return f'<RewardCalculation {self.student_id}: {self.total_reward} ريال>'


class StudentPoints(db.Model):
    __tablename__ = 'student_points'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    total_points = db.Column(db.Integer, default=0)
    star_level = db.Column(db.Integer, default=0)
    current_cycle_start = db.Column(db.Date, default=date.today)
    cycle_end_date = db.Column(db.Date, nullable=True)      # يُعيَّن عند الأرشفة
    current_cycle_points = db.Column(db.Integer, default=0)
    archived = db.Column(db.Boolean, default=False)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref='points_records', lazy=True)

class PointsLog(db.Model):
    __tablename__ = 'points_log'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    evaluation_id = db.Column(db.Integer, db.ForeignKey('evaluation.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('weekly_plan.id'), nullable=False)
    points_earned = db.Column(db.Integer, default=0)
    stars_earned = db.Column(db.Integer, default=0)
    evaluation_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cycle_start_date = db.Column(db.Date, nullable=False)

    student = db.relationship('Student', backref='points_logs', lazy=True)
    evaluation = db.relationship('Evaluation', backref='points_log', lazy=True)
    plan = db.relationship('WeeklyPlan', backref='points_log', lazy=True)



# ------------------ دوال مساعدة محسنة للأداء ------------------
def get_student_average_optimized(student):
    """حساب متوسط درجات الطالب - نسخة محسنة باستخدام استعلام واحد"""
    # استعلام واحد يجلب جميع التقييمات دفعة واحدة
    evaluations = db.session.query(
        Evaluation, WeeklyPlan.plan_type
    ).join(
        WeeklyPlan, Evaluation.plan_id == WeeklyPlan.id
    ).filter(
        WeeklyPlan.student_id == student.id
    ).all()

    if not evaluations:
        return 0

    total_score = 0
    count = 0

    for evaluation, plan_type in evaluations:
        if plan_type == 'حفظ':
            score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
        else:  # سرد أو تقييم مرحلة
            score = evaluation.revision_score or evaluation.new_score or 0
        total_score += score
        count += 1

    return round(total_score / count, 2) if count > 0 else 0


def categorize_plans(plans, today_date):
    """تصنيف المقررات إلى حالي وسابق ومستقبل"""
    current_plan = None
    previous_plans = []
    future_plans = []

    for plan in plans:
        end_date = plan.start_date + timedelta(days=6)
        if plan.start_date <= today_date <= end_date:
            current_plan = plan
        elif end_date < today_date:
            previous_plans.append(plan)
        else:
            future_plans.append(plan)

    return current_plan, previous_plans, future_plans


def get_student_points_optimized(student_id):
    """جلب بيانات نقاط الطالب مع إنشاء تلقائي إذا لم توجد"""
    student_points = StudentPoints.query.filter_by(
        student_id=student_id, archived=False
    ).first()

    if not student_points:
        student_points = StudentPoints(student_id=student_id)
        db.session.add(student_points)
        db.session.commit()

    return student_points


def calculate_points_from_evaluation(evaluation, plan):
    """
    حساب النقاط والنجوم بناءً على نوع التقييم.
    - حفظ : مجموع (جديد + حديث + سابق) / 10  → نقاط لكل مقرر
    - سرد  : درجة_السرد / 10
    - تقييم مرحلة: الدرجة / 8  (من 80)
    النجوم تعكس جودة التقييم الفردي، وليست تراكمية.
    """
    points = 0
    stars  = 0

    if plan.plan_type == 'حفظ':
        total_score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
        points = total_score // 10
        if points >= 9:   stars = 3
        elif points >= 6: stars = 2
        elif points >= 3: stars = 1
        else:             stars = 0

    elif plan.plan_type == 'سرد':
        total_score = evaluation.revision_score or 0
        points = total_score // 10
        if points >= 9:   stars = 3
        elif points >= 6: stars = 2
        elif points >= 3: stars = 1
        else:             stars = 0

    elif plan.plan_type == 'تقييم مرحلة':
        total_score = evaluation.new_score or 0
        points = total_score // 8
        if points >= 9:   stars = 3
        elif points >= 6: stars = 2
        elif points >= 3: stars = 1
        else:             stars = 0

    elif plan.plan_type == 'قصيدة':
        # مقررات القصائد لا تحسب عليها نقاط أو نجوم
        points = 0
        stars = 0

    return points, stars


def recalculate_star_level(current_cycle_points):
    """
    حساب مستوى النجوم بناءً على نقاط الدورة الحالية.
    كل 30 نقطة = نجمة واحدة (حد أقصى 3 نجوم = 90 نقطة)
    """
    return min(3, current_cycle_points // 30)
# تعديل نموذج WeeklyPlan لإضافة النوع الجديد
# أضف هذا السطر داخل كلاس WeeklyPlan بعد السطر plan_type = db.Column(db.String(10))
# مع ملاحظة: ستحتاج إلى تعديل قاعدة البيانات يدوياً أو حذفها وإعادة إنشائها


# ------------------ دوال مساعدة للمكافآت (حسب الفترة) ------------------

def get_cutoff_hour():
        """الحصول على الساعة التي يتم بعدها منع إرسال تقرير اليوم السابق"""
        setting = SystemSettings.query.filter_by(setting_key='report_cutoff_hour').first()
        if setting:
            try:
                return int(setting.setting_value)
            except:
                return 8  # القيمة الافتراضية 8 صباحاً
        return 8  # القيمة الافتراضية 8 صباحاً

def set_cutoff_hour(hour):
        """تعيين الساعة التي يتم بعدها منع إرسال تقرير اليوم السابق"""
        setting = SystemSettings.query.filter_by(setting_key='report_cutoff_hour').first()
        if setting:
            setting.setting_value = str(hour)
            setting.updated_at = datetime.utcnow()
        else:
            setting = SystemSettings(
                setting_key='report_cutoff_hour',
                setting_value=str(hour),
                description='الساعة التي يتم بعدها منع إرسال تقرير اليوم السابق (0-23)'
            )
            db.session.add(setting)
        db.session.commit()

def get_period_pages(student_id, period_start, period_end):
    rec = StudentPeriodPages.query.filter_by(
        student_id=student_id,
        period_start=period_start,
        period_end=period_end
    ).first()
    return rec.pages if rec else 0


def get_phase_score_80(student_id, period_start, period_end):
    """
    جلب درجة تقييم المرحلة (من 80) من جدول التقييمات Evaluation.
    حسب نظامك: درجة المرحلة تُسجل داخل Evaluation.new_score لمقرر نوعه 'تقييم مرحلة'.
    """
    ev = Evaluation.query.join(WeeklyPlan).filter(
        WeeklyPlan.student_id == student_id,
        WeeklyPlan.plan_type == 'تقييم مرحلة',
        Evaluation.evaluation_date.between(period_start, period_end)
    ).order_by(Evaluation.evaluation_date.desc()).first()

    return int(ev.new_score) if ev and ev.new_score is not None else None


def calculate_memorization_20_from_newscore(student_id, period_start, period_end):
    """تحويل متوسط درجات الحفظ الأسبوعي (new_score من 40) إلى 20 درجة"""
    evaluations = Evaluation.query.join(WeeklyPlan).filter(
        WeeklyPlan.student_id == student_id,
        WeeklyPlan.plan_type == 'حفظ',
        Evaluation.evaluation_date.between(period_start, period_end)
    ).all()

    if not evaluations:
        return 0.0, 0.0  # (memo20, avg40)

    total = sum(float(ev.new_score or 0) for ev in evaluations)
    avg40 = total / len(evaluations)
    memo20 = (avg40 / 40.0) * 20.0

    return round(memo20, 2), round(avg40, 2)

# ------------------ دوال مساعدة للمكافآت ------------------

def calculate_student_final_score(student_id, phase_score, period_start, period_end):
    """
    حساب الدرجة النهائية للطالب:
    phase_score (من 80) + 20% من متوسط درجات الحفظ
    """
    # الحصول على جميع تقييمات الحفظ في الفترة
    evaluations = Evaluation.query.join(WeeklyPlan).filter(
        WeeklyPlan.student_id == student_id,
        WeeklyPlan.plan_type == 'حفظ',
        Evaluation.evaluation_date.between(period_start, period_end)
    ).all()

    if not evaluations:
        avg_memo_score = 0
    else:
        total_score = 0
        for eval in evaluations:
            # مجموع درجات الحفظ (new + recent + previous)
            eval_score = (eval.new_score or 0) + (eval.recent_score or 0) + (eval.previous_score or 0)
            total_score += eval_score
        avg_memo_score = total_score / len(evaluations)

    # الدرجة النهائية = phase_score + 20% من متوسط الحفظ
    final_score = phase_score + (avg_memo_score * 0.2)
    return round(final_score, 2), avg_memo_score


def calculate_reward_for_student(student_id, rule_id, period_start, period_end):
    """حساب المكافأة لطالب معين في فترة محددة وفق المعادلة:
    - الصفحات = صفحات الفترة المحددة
    - الحفظ = متوسط new_score (من 40) محوّل إلى 20
    - المرحلة = PhaseEvaluation.score (من 80)
    - النهائي = 80 + 20 = 100
    """

    student = Student.query.get(student_id)
    rule = RewardRule.query.get(rule_id)
    if not student or not rule:
        return None

    total_pages = get_period_pages(student_id, period_start, period_end)

    phase_score = get_phase_score_80(student_id, period_start, period_end)
    if phase_score is None:
        return None

    memo20, avg40 = calculate_memorization_20_from_newscore(student_id, period_start, period_end)
    final_score = round(float(phase_score) + float(memo20), 2)

    reward_per_page = 0.0
    for detail in rule.details:
        if detail.min_score <= final_score <= detail.max_score:
            reward_per_page = float(detail.reward_per_page)
            break

    total_reward = round(total_pages * reward_per_page, 3)

    calculation = RewardCalculation(
        student_id=student_id,
        rule_id=rule_id,
        period_start=period_start,
        period_end=period_end,
        total_pages=total_pages,
        avg_memorization_score=avg40,  # متوسط الحفظ من 40
        phase_score=int(phase_score),
        final_score=final_score,
        reward_per_page=reward_per_page,
        total_reward=total_reward
    )

    return calculation



def calculate_average_memorization_score(student_id, period_start, period_end):
        """
        حساب متوسط درجات الحفظ للطالب في فترة محددة
        """
        # البحث عن جميع تقييمات الحفظ في الفترة
        evaluations = Evaluation.query.join(WeeklyPlan).filter(
            WeeklyPlan.student_id == student_id,
            WeeklyPlan.plan_type == 'حفظ',
            Evaluation.evaluation_date.between(period_start, period_end)
        ).all()

        if not evaluations:
            return 0

        total_score = 0
        count = 0

        for eval in evaluations:
            # مجموع درجات الحفظ (new + recent + previous)
            # كل واحدة من 10 درجات كحد أقصى حسب التصميم القديم
            eval_score = (eval.new_score or 0) + (eval.recent_score or 0) + (eval.previous_score or 0)
            total_score += eval_score
            count += 1

        if count == 0:
            return 0

        avg_score = total_score / count
        return round(avg_score, 2)
# ------------------ دوال مساعدة ------------------
def get_student_average(student):
    total_score = 0
    total_evaluations = 0
    for plan in student.weekly_plans:
        for eval in plan.evaluations:
            if plan.plan_type == 'حفظ':
                score = (eval.new_score or 0) + (eval.recent_score or 0) + (eval.previous_score or 0)
            else:
                score = eval.revision_score or 0
            total_score += score
            total_evaluations += 1
    return round(total_score / total_evaluations, 2) if total_evaluations else 0

def check_admin_permission(permission, require_edit=False):
    """دالة للتحقق من صلاحيات المستخدم الإداري"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))
    if current_user.is_super_admin:
        return None  # مسموح
    if permission == 'students':
        if not current_user.can_access_students:
            flash('غير مصرح: لا تملك صلاحية الوصول للطلاب', 'error')
            return redirect(url_for('admin', tab='dashboard'))
        if require_edit and not current_user.can_edit_students:
            flash('غير مصرح: لا تملك صلاحية تعديل الطلاب', 'error')
            return redirect(url_for('admin', tab='students'))
    elif permission == 'teachers':
        if not current_user.can_access_teachers:
            flash('غير مصرح: لا تملك صلاحية الوصول للمعلمين', 'error')
            return redirect(url_for('admin', tab='dashboard'))
        if require_edit and not current_user.can_edit_teachers:
            flash('غير مصرح: لا تملك صلاحية تعديل المعلمين', 'error')
            return redirect(url_for('admin', tab='teachers'))
    elif permission == 'reports':
        if not current_user.can_access_reports:
            flash('غير مصرح: لا تملك صلاحية الوصول للتقارير', 'error')
            return redirect(url_for('admin', tab='dashboard'))
    elif permission == 'warnings':
        if not current_user.can_access_warnings:
            flash('غير مصرح: لا تملك صلاحية الوصول للإنذارات', 'error')
            return redirect(url_for('admin', tab='dashboard'))
    elif permission == 'backup':
        if not current_user.can_access_backup:
            flash('غير مصرح: لا تملك صلاحية الوصول للنسخ الاحتياطي', 'error')
            return redirect(url_for('admin', tab='dashboard'))
        if require_edit and not current_user.can_restore_backup:
            flash('غير مصرح: لا تملك صلاحية استعادة النسخ الاحتياطي', 'error')
            return redirect(url_for('admin', tab='data'))
    return None

def model_to_dict(obj):
    """تحويل كائن SQLAlchemy إلى قاموس مع معالجة التواريخ"""
    data = {}
    for col in inspect(obj).mapper.column_attrs:
        value = getattr(obj, col.key)
        if isinstance(value, (date, datetime)):
            data[col.key] = value.isoformat()
        else:
            data[col.key] = value
    return data


def get_cumulative_report_until_date(target_date, students):
    """
    تُنشئ تقريرًا تراكميًا من الأحد حتى target_date فقط
    target_date: object من نوع date
    students: قائمة الطلاب (من معلم معين أو الكل)
    """
    WEEKDAYS_AR = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة']

    # حساب بداية الأسبوع (الأحد الذي يسبق أو يساوي target_date)
    days_to_sunday = (target_date.weekday() - 6) % 7  # الأحد = 6 في weekday()
    start_of_week = target_date - timedelta(days=days_to_sunday)

    # الأيام المطلوبة حتى target_date
    target_index = target_date.weekday()  # 0=الاثنين, 6=الأحد -> نحتاج تعديل
    # تحويل weekday (0=الاثنين) إلى مؤشر في WEEKDAYS_AR (0=الأحد)
    weekday_map = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
    target_day_name = target_date.strftime('%A')  # بالانجليزية
    target_idx = weekday_map[target_day_name]

    days_covered = WEEKDAYS_AR[:target_idx + 1]  # الأحد حتى اليوم المطلوب
    total_days = len(days_covered)

    cumulative_data = []

    for student in students:
        daily_status = []
        sent_count = 0

        # نحصل على المقرر الأسبوعي لهذا الطالب في هذا الأسبوع
        weekly_plan = WeeklyPlan.query.filter_by(
            student_id=student.id,
            start_date=start_of_week
        ).first()

        for i, day_name in enumerate(days_covered):
            current_date = start_of_week + timedelta(days=i)
            status = 'not_sent'

            if weekly_plan:
                # البحث عن تقرير في هذا اليوم
                report = DailyReport.query.filter_by(
                    plan_id=weekly_plan.id,
                    report_date=current_date
                ).first()
                if report:
                    status = 'sent'
                    sent_count += 1

            daily_status.append({
                'day': day_name,
                'status': status,
                'date': current_date
            })

        not_sent_count = total_days - sent_count
        percentage = round((sent_count / total_days) * 100, 1) if total_days > 0 else 0

        cumulative_data.append({
            'student': student,
            'daily_status': daily_status,
            'sent_count': sent_count,
            'not_sent_count': not_sent_count,
            'percentage': percentage,
            'days_covered': days_covered
        })

    return cumulative_data, start_of_week, days_covered

# ------------------ دالة مساعدة للفلاش Toast ------------------
def flash_toast(message, category='success'):
    """
    دالة لإضافة رسالة فلاش مع تحديد نوعها لاستخدامها في Toast
    الأنواع: success, error, warning, info
    """
    flash(message, category)


# ------------------ دوال تصدير واستيراد خاصة بجدول الربط ------------------
def export_teacher_student():
    result = db.session.execute(teacher_student.select()).fetchall()
    columns = ['teacher_id', 'student_id']
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(columns)
    for row in result:
        cw.writerow([row.teacher_id, row.student_id])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=teacher_student.csv"
    output.headers["Content-type"] = "text/csv"
    return output

def import_teacher_student(file):
    stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    header = next(csv_input)
    if header != ['teacher_id', 'student_id']:
        return False, "ملف teacher_student.csv غير متوافق"
    db.session.execute(teacher_student.delete())
    imported = 0
    for row in csv_input:
        if len(row) < 2:
            continue
        teacher_id, student_id = int(row[0]), int(row[1])
        db.session.execute(teacher_student.insert().values(teacher_id=teacher_id, student_id=student_id))
        imported += 1
    db.session.commit()
    return True, f"تم استيراد {imported} علاقة بنجاح"

# ------------------ الصفحات العامة ------------------
@app.route('/')
def home():
    if current_user.is_authenticated:
        if isinstance(current_user, Student):
            return redirect(url_for('student_dashboard'))
        if isinstance(current_user, Teacher):
            return redirect(url_for('teacher_dashboard'))
        if isinstance(current_user, Admin):
            return redirect(url_for('admin'))
    error = request.args.get('error')
    return render_template('home.html', error=error)
@app.route('/login', methods=['POST'])
def do_login():
    username = request.form['username']
    password = request.form['password']

    user = Student.query.filter_by(username=username).first()
    if user and check_password_hash(user.password, password):
        if not user.is_active or user.is_suspended:
            flash('تم تعطيل هذا الحساب. يرجى التواصل مع الإدارة.', 'error')
            return redirect(url_for('home'))
        login_user(user, remember=True)
        session.permanent = True
        return redirect(url_for('student_dashboard'))

    user = Teacher.query.filter_by(username=username, is_active=True).first()
    if user and check_password_hash(user.password, password):
        login_user(user, remember=True)
        session.permanent = True
        return redirect(url_for('teacher_dashboard'))

    user = Admin.query.filter_by(username=username).first()
    if user and check_password_hash(user.password, password):
        login_user(user, remember=True)
        session.permanent = True
        return redirect(url_for('admin'))

    return redirect(url_for('home', error='بيانات الدخول غير صحيحة'))
@app.route('/logout')
def logout():
    session.clear()
    logout_user()
    return redirect(url_for('home'))

# ------------------ لوحة الطالب ------------------

    # ------------------ لوحة الطالب (نسخة محسنة) ------------------
@app.route('/student')
@login_required
def student_dashboard():
        if not isinstance(current_user, Student):
            return redirect(url_for('home'))

        # استعلام واحد مع تحميل جميع العلاقات المرتبطة دفعة واحدة
        plans = WeeklyPlan.query.filter_by(
            student_id=current_user.id
        ).options(
            joinedload(WeeklyPlan.evaluations).joinedload(Evaluation.teacher),
            joinedload(WeeklyPlan.daily_reports)
        ).order_by(WeeklyPlan.start_date.desc()).all()

        # حساب end_date للخطة الواحدة (يستخدم في التصنيف)
        for plan in plans:
            plan.end_date = plan.start_date + timedelta(days=6)

        # تصنيف المقررات في الباك اند (بدون تحميل زائد في القالب)
        oman_tz   = pytz.timezone('Asia/Muscat')
        now_oman  = datetime.now(oman_tz)
        today     = now_oman.date()
        yesterday = today - timedelta(days=1)

        # إحصاءات وتصنيف لوحة الطالب تخص مقررات القرآن فقط.
        # مقررات القصائد لها قسم مستقل ولا تدخل في عداد المقررات.
        quran_plans = [plan for plan in plans if plan.plan_type != 'قصيدة']
        total_plans = len(quran_plans)

        # متوسط الدرجات
        total_score = 0
        eval_count = 0
        for plan in quran_plans:
            evaluation = plan.evaluations[0] if plan.evaluations else None
            if not evaluation:
                continue
            if plan.plan_type == 'حفظ':
                score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
            else:
                score = evaluation.revision_score or evaluation.new_score or 0
            total_score += score
            eval_count += 1
        avg_score = round(total_score / eval_count, 2) if eval_count > 0 else 0

        # أيام الغياب لآخر 30 يوم (حسب تاريخ المقرر)
        thirty_days_ago = today - timedelta(days=30)
        absent_days_30 = 0
        for plan in quran_plans:
            plan_end = plan.start_date + timedelta(days=6)
            if plan.start_date <= today and plan_end >= thirty_days_ago:
                start = max(plan.start_date, thirty_days_ago)
                end = min(plan_end, today)
                current = start
                while current <= end:
                    if current.weekday() != 5:
                        report = DailyReport.query.filter_by(
                            plan_id=plan.id,
                            report_date=current
                        ).first()
                        if not report:
                            absent_days_30 += 1
                    current += timedelta(days=1)

        # جلب آخر إنذار
        last_warning = current_user.warnings[0] if current_user.warnings else None

        # جلب بيانات النقاط
        student_points = get_student_points_optimized(current_user.id)

        current_plan, previous_plans, future_plans = categorize_plans(quran_plans, today)
        poem_state = poem_study_data(current_user.id)

        # ── منطق تقرير أمس ──────────────────────────────────────────
        cutoff_hour = get_cutoff_hour()
        within_cutoff = now_oman.hour < cutoff_hour

        yesterday_in_plan = False
        yesterday_report_sent = False
        if current_plan:
            plan_end = current_plan.start_date + timedelta(days=5)
            yesterday_in_plan = current_plan.start_date <= yesterday <= plan_end
            sent_dates = [r.report_date for r in current_plan.daily_reports]
            yesterday_report_sent = yesterday in sent_dates

        show_yesterday_alert = (
            yesterday_in_plan
            and not yesterday_report_sent
            and within_cutoff
        )

        return render_template('student_dashboard.html',
                              current_plan=current_plan,
                              previous_plans=previous_plans,
                              future_plans=future_plans,
                              total_plans=total_plans,
                              avg_score=avg_score,
                              absent_days_30=absent_days_30,
                              now_date=today,
                              now_hour=now_oman.hour,
                              cutoff_hour=cutoff_hour,
                              yesterday_date=yesterday,
                              yesterday_report_sent=yesterday_report_sent,
                              show_yesterday_alert=show_yesterday_alert,
                              last_warning=last_warning,
                              student_points=student_points,
                              poem_state=poem_state)


    # ------------------ لوحة المعلم ------------------



# ------------------ لوحة المعلم (نسخة محسنة) ------------------
@app.route('/teacher')
@login_required
def teacher_dashboard():
    if not isinstance(current_user, Teacher):
        return redirect(url_for('home'))

    # استعلام واحد مع تحميل الطلاب والمقررات والتقييمات دفعة واحدة
    students = Student.query.join(
        teacher_student, Student.id == teacher_student.c.student_id
    ).filter(
        teacher_student.c.teacher_id == current_user.id,
        Student.is_suspended == False
    ).options(
        joinedload(Student.weekly_plans).joinedload(WeeklyPlan.evaluations)
    ).order_by(Student.full_name).all()

    student_data = []
    for student in students:
        # حساب المتوسط من البيانات المحملة مسبقاً (بدون استعلامات إضافية)
        total_score = 0
        total_evaluations = 0

        for plan in student.weekly_plans:
            # كل مقرر له تقييم واحد فقط. استخدام أقدم تقييم يمنع تضخيم
            # المتوسط بسبب بيانات قديمة مكررة.
            evaluation = plan.evaluations[0] if plan.evaluations else None
            if not evaluation:
                continue

            if plan.plan_type == 'حفظ':
                score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
            elif plan.plan_type == 'سرد':
                score = evaluation.revision_score or 0
            elif plan.plan_type == 'تقييم مرحلة':
                score = evaluation.new_score or 0
            else:
                # تقييم القصيدة (يجاز/لا يجاز) لا يدخل في المتوسط الرقمي.
                continue

            total_score += score
            total_evaluations += 1

        avg_score = round(total_score / total_evaluations, 2) if total_evaluations > 0 else 0

        student_data.append({
            'student': student,
            'avg_score': avg_score
        })

    return render_template('teacher_dashboard.html', 
                          teacher=current_user, 
                          student_data=student_data)

# ------------------ واجهة الإدارة ------------------
@app.route('/admin')
@login_required
def admin():
    if not isinstance(current_user, Admin):
        return redirect(url_for('home'))

    students = Student.query.options(
        selectinload(Student.points_logs).selectinload(PointsLog.plan),
        selectinload(Student.points_records),
    ).all()
    teachers = Teacher.query.filter_by(is_active=True).options(
        selectinload(Teacher.students),
    ).all()
    admins = Admin.query.all()
    active_tab = request.args.get('tab', 'students')
    error = request.args.get('error', '')
    form_data = None
    form_type = None

    # التحقق من صلاحية الوصول لكل تبويب
    if active_tab == 'students':
        redirect_resp = check_admin_permission('students')
        if redirect_resp:
            return redirect_resp
    elif active_tab == 'teachers':
        redirect_resp = check_admin_permission('teachers')
        if redirect_resp:
            return redirect_resp
    elif active_tab == 'reports':
        redirect_resp = check_admin_permission('reports')
        if redirect_resp:
            return redirect_resp
    elif active_tab == 'warnings':
        redirect_resp = check_admin_permission('warnings')
        if redirect_resp:
            return redirect_resp
    elif active_tab == 'data':
        redirect_resp = check_admin_permission('backup')
        if redirect_resp:
            return redirect_resp
    elif active_tab == 'admins':
        if not current_user.is_super_admin:
            flash('غير مصرح: هذه الصفحة مخصصة للمشرفين الكبار فقط', 'error')
            return redirect(url_for('admin', tab='dashboard'))

    # معالجة التقارير
    report_data = None
    start_date = None
    end_date = None
    # ========== بيانات النقاط ==========
    students_with_points_data = []
    total_points_all = 0
    students_with_points = 0
    current_cycle_start = None

    for student in students:
        # سجل النقاط النشط للطالب — من الذاكرة (points_records محمّل مسبقاً
        # مع students) لتفادي استعلام لكل طالب على قاعدة بعيدة.
        student_points = next(
            (sp for sp in student.points_records if not sp.archived),
            None,
        )

        if student_points:
            students_with_points_data.append({
                'id': student.id,
                'full_name': student.full_name,
                'total_points': student_points.total_points,
                'star_level': student_points.star_level,
                'current_cycle_points': student_points.current_cycle_points,
                'current_cycle_start': student_points.current_cycle_start,
                'points_logs': sorted(
                    student.points_logs,
                    key=lambda l: l.evaluation_date or date.min,
                    reverse=True,
                )
            })
            total_points_all += student_points.total_points
            students_with_points += 1
            if not current_cycle_start:
                current_cycle_start = student_points.current_cycle_start
        else:
            # طالب بدون نقاط (لم يتم تقييمه بعد)
            students_with_points_data.append({
                'id': student.id,
                'full_name': student.full_name,
                'total_points': 0,
                'star_level': 0,
                'current_cycle_points': 0,
                'current_cycle_start': None,
                'points_logs': []
            })

    # ترتيب حسب النقاط تنازلياً
    students_with_points_data.sort(key=lambda x: x['total_points'], reverse=True)

    if active_tab == 'reports':
        today = date.today()
        end_date = today
        start_date = today - timedelta(days=30)

        if request.args.get('start_date') and request.args.get('end_date'):
            try:
                start_date = datetime.strptime(request.args['start_date'], '%Y-%m-%d').date()
                end_date = datetime.strptime(request.args['end_date'], '%Y-%m-%d').date()
            except:
                pass

        report_data = []
        for student in students:
            total_plans = 0
            evaluated_plans = 0
            unevaluated_plans = 0
            total_score = 0
            total_evaluated = 0
            absent_days = 0

            plans = WeeklyPlan.query.filter(
                WeeklyPlan.student_id == student.id,
                WeeklyPlan.start_date <= end_date
            ).all()

            relevant_plans = []
            for plan in plans:
                plan_end = plan.start_date + timedelta(days=6)
                if plan_end >= start_date:
                    relevant_plans.append(plan)

            total_plans = len(relevant_plans)

            for plan in relevant_plans:
                if plan.evaluations:
                    evaluated_plans += 1
                    for eval in plan.evaluations:
                        if plan.plan_type == 'حفظ':
                            score = (eval.new_score or 0) + (eval.recent_score or 0) + (eval.previous_score or 0)
                        else:
                            score = eval.revision_score or 0
                        total_score += score
                        total_evaluated += 1
                else:
                    unevaluated_plans += 1

                plan_start = max(plan.start_date, start_date)
                plan_end = min(plan.start_date + timedelta(days=6), end_date)

                current_day = plan_start
                while current_day <= plan_end:
                    if current_day.weekday() != 5:  # السبت مستثنى
                        report = DailyReport.query.filter_by(
                            plan_id=plan.id,
                            report_date=current_day
                        ).first()
                        if not report:
                            absent_days += 1
                    current_day += timedelta(days=1)

            avg_score = round(total_score / total_evaluated, 2) if total_evaluated > 0 else 0

            report_data.append({
                'student': student,
                'total_plans': total_plans,
                'evaluated_plans': evaluated_plans,
                'unevaluated_plans': unevaluated_plans,
                'absent_days': absent_days,
                'avg_score': avg_score
            })

    # ── تقرير تقييمات المعلمين ──
    teacher_report_data = None
    tr_start = None
    tr_end = None
    if active_tab == 'reports' and request.args.get('tr_start') and request.args.get('tr_end'):
        try:
            tr_start = datetime.strptime(request.args['tr_start'], '%Y-%m-%d').date()
            tr_end   = datetime.strptime(request.args['tr_end'],   '%Y-%m-%d').date()
            teacher_report_data = []
            for teacher in teachers:
                evals = Evaluation.query.filter(
                    Evaluation.teacher_id == teacher.id,
                    Evaluation.evaluation_date >= tr_start,
                    Evaluation.evaluation_date <= tr_end
                ).order_by(Evaluation.evaluation_date.desc()).all()
                if evals:
                    ev_list = []
                    for ev in evals:
                        plan    = ev.plan
                        stud    = plan.student if plan else None
                        plan_start = plan.start_date if plan else None
                        ev_list.append({
                            'student_name':    stud.full_name if stud else '—',
                            'plan_type':       plan.plan_type if plan else '—',
                            'plan_start':      plan_start.isoformat() if plan_start else '—',
                            'evaluation_date': ev.evaluation_date.isoformat(),
                        })
                    teacher_report_data.append({
                        'teacher_name': teacher.full_name,
                        'evaluations':  ev_list,
                    })
            teacher_report_data.sort(key=lambda x: len(x['evaluations']), reverse=True)
        except Exception:
            teacher_report_data = []

    return render_template('admin.html',
                          students=students,
                          teachers=teachers,
                          admins=admins,
                          active_tab=active_tab,
                          error=error,
                          form_data=form_data,
                          form_type=form_type,
                          report_data=report_data,
                          start_date=start_date,
                          end_date=end_date,
                          teacher_report_data=teacher_report_data,
                          tr_start=tr_start,
                          tr_end=tr_end,
                          students_with_points_data=students_with_points_data,
                          total_points_all=total_points_all,
                          students_with_points=students_with_points,
                          current_cycle_start=current_cycle_start)

# ------------------ مسارات الطلاب (إدارة) ------------------
@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    """صفحة إعدادات النظام"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    if request.method == 'POST':
        cutoff_hour = request.form.get('cutoff_hour', type=int)
        if cutoff_hour is not None and 0 <= cutoff_hour <= 23:
            set_cutoff_hour(cutoff_hour)
            flash(f'تم تحديث وقت القطع إلى الساعة {cutoff_hour}:00', 'success')
        else:
            flash('يرجى إدخال ساعة صحيحة بين 0 و 23', 'error')
        return redirect(url_for('admin_settings'))

    current_cutoff_hour = get_cutoff_hour()
    return render_template('admin_settings.html', 
                         cutoff_hour=current_cutoff_hour,
                         active_tab='settings')

@app.route('/admin/toggle_phase_plan', methods=['POST'])
@login_required
def toggle_phase_plan():
    """تفعيل/تعطيل مقرر (تقييم مرحلة) على مستوى النظام (متاح لأي Admin)"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    enabled = request.form.get('phase_plan_enabled') == 'on'
    set_setting_bool('enable_phase_plan', enabled)
    flash('تم تفعيل تقييم المرحلة' if enabled else 'تم تعطيل تقييم المرحلة', 'success')
    return redirect(url_for('admin', tab=request.form.get('tab', 'students')))




@app.route('/add_student', methods=['POST'])
@login_required
def add_student():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('students', require_edit=True)
    if redirect_resp:
        return redirect_resp

    full_name = request.form.get('full_name', '')
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    phone = request.form.get('phone', '')
    level = request.form.get('level', '')
    tab = request.form.get('tab', 'students')  # استلام قيمة التبويب من النموذج

    existing_student = Student.query.filter_by(username=username).first()
    existing_teacher = Teacher.query.filter_by(username=username).first()
    existing_admin = Admin.query.filter_by(username=username).first()

    if existing_student or existing_teacher or existing_admin:
        students = Student.query.all()
        teachers = Teacher.query.filter_by(is_active=True).all()
        error = f"اسم المستخدم '{username}' موجود بالفعل."
        return render_template('admin.html', students=students, teachers=teachers, active_tab=tab, error=error, form_data=request.form, form_type='student')

    new_student = Student(
        full_name=full_name,
        username=username,
        password=generate_password_hash(password),
        phone=phone,
        memorization_level=level
    )
    db.session.add(new_student)
    db.session.commit()
    flash('تم إضافة الطالب بنجاح', 'success')
    return redirect(url_for('admin', tab=tab))


@app.route('/delete_student/<int:id>', methods=['GET', 'POST'])
@login_required
def delete_student(id):

    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    redirect_resp = check_admin_permission('students', require_edit=True)
    if redirect_resp:
        return redirect_resp

    student = Student.query.get_or_404(id)

    has_plans = WeeklyPlan.query.filter_by(student_id=id).count() > 0
    has_evaluations = Evaluation.query.join(WeeklyPlan).filter(
        WeeklyPlan.student_id == id
    ).count() > 0
    has_reports = DailyReport.query.join(WeeklyPlan).filter(
        WeeklyPlan.student_id == id
    ).count() > 0

    if request.method == 'POST':
        try:
            # ✅ التقارير اليومية
            DailyReport.query.filter(
                DailyReport.plan_id.in_(
                    db.session.query(WeeklyPlan.id).filter_by(student_id=id)
                )
            ).delete(synchronize_session=False)

            # ✅ حذف سجل النقاط (يجب أن يسبق حذف التقييمات والطالب لأنه يرتبط بهما)
            PointsLog.query.filter_by(student_id=id).delete()

            # ✅ حذف رصيد النقاط
            StudentPoints.query.filter_by(student_id=id).delete()

            # ✅ التقييمات
            Evaluation.query.filter(
                Evaluation.plan_id.in_(
                    db.session.query(WeeklyPlan.id).filter_by(student_id=id)
                )
            ).delete(synchronize_session=False)

            # ✅ حذف تقييمات المرحلة
            PhaseEvaluation.query.filter_by(student_id=id).delete()

            # ✅ حذف المقررات
            WeeklyPlan.query.filter_by(student_id=id).delete()

            # ✅ حذف صفحات الحفظ
            StudentPages.query.filter_by(student_id=id).delete()
            StudentPeriodPages.query.filter_by(student_id=id).delete()

            # ✅ حذف حسابات المكافآت
            RewardCalculation.query.filter_by(student_id=id).delete()

            # ✅ حذف الإنذارات
            Warning.query.filter_by(student_id=id).delete()

            # ✅ حذف الربط مع المعلمين
            db.session.execute(
                teacher_student.delete().where(
                    teacher_student.c.student_id == id
                )
            )

            # ✅ أخيراً حذف الطالب
            db.session.delete(student)
            db.session.commit()

            flash('✅ تم حذف الطالب وجميع بياناته المرتبطة نهائيًا', 'success')
            return redirect(url_for('admin', tab='students'))

        except Exception as e:
            db.session.rollback()
            print("DELETE STUDENT ERROR:", e)  # ← لمراجعة السجل
            flash('❌ فشل حذف هذا الطالب – راجع السجلات', 'error')
            return redirect(url_for('admin', tab='students'))

    return render_template(
        'confirm_delete_student.html',
        student=student,
        has_plans=has_plans,
        has_evaluations=has_evaluations,
        has_reports=has_reports
    )

@app.route('/edit_student/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('students', require_edit=True)
    if redirect_resp:
        return redirect_resp
    student = Student.query.get_or_404(id)
    if request.method == 'POST':
        student.full_name = request.form['full_name']
        student.username = request.form['username']
        if request.form['password']:
            student.password = generate_password_hash(request.form['password'])
        student.phone = request.form['phone']
        student.memorization_level = request.form['level']
        db.session.commit()
        flash('تم تعديل بيانات الطالب بنجاح', 'success')
        return redirect(url_for('admin', tab='students'))
    return render_template('edit_student.html', student=student)

# ------------------ مسارات المعلمين (إدارة) ------------------
@app.route('/add_teacher', methods=['POST'])
@login_required
def add_teacher():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('teachers', require_edit=True)
    if redirect_resp:
        return redirect_resp

    full_name = request.form.get('full_name', '')
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    tab = request.form.get('tab', 'teachers')  # استلام قيمة التبويب من النموذج

    existing_student = Student.query.filter_by(username=username).first()
    existing_teacher = Teacher.query.filter_by(username=username).first()
    existing_admin = Admin.query.filter_by(username=username).first()

    if existing_student or existing_teacher or existing_admin:
        students = Student.query.all()
        teachers = Teacher.query.filter_by(is_active=True).all()
        error = f"اسم المستخدم '{username}' موجود بالفعل."
        return render_template('admin.html', students=students, teachers=teachers, active_tab=tab, error=error, form_data=request.form, form_type='teacher')

    new_teacher = Teacher(
        full_name=full_name,
        username=username,
        password=generate_password_hash(password)
    )
    db.session.add(new_teacher)
    db.session.commit()
    flash('تم إضافة المعلم بنجاح', 'success')
    return redirect(url_for('admin', tab=tab))


@app.route('/delete_teacher/<int:id>')
@login_required
def delete_teacher(id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('teachers', require_edit=True)
    if redirect_resp:
        return redirect_resp
    teacher = Teacher.query.get_or_404(id)
    teacher.is_active = False
    db.session.commit()
    flash('تم إخفاء المعلم بنجاح', 'success')
    return redirect(url_for('admin', tab=request.args.get('tab', 'teachers')))


@app.route('/edit_teacher/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_teacher(id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('teachers', require_edit=True)
    if redirect_resp:
        return redirect_resp
    teacher = Teacher.query.get_or_404(id)
    if request.method == 'POST':
        teacher.full_name = request.form['full_name']
        teacher.username = request.form['username']
        if request.form['password']:
            teacher.password = generate_password_hash(request.form['password'])
        db.session.commit()
        flash('تم تعديل بيانات المعلم بنجاح', 'success')
        return redirect(url_for('admin', tab='teachers'))
    return render_template('edit_teacher.html', teacher=teacher)


@app.route('/toggle_teacher_report_permission/<int:teacher_id>')
@login_required
def toggle_teacher_report_permission(teacher_id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    teacher = Teacher.query.get_or_404(teacher_id)
    teacher.can_view_missing_reports = not teacher.can_view_missing_reports
    db.session.commit()
    flash(f'تم تغيير صلاحية المعلم {teacher.full_name} بنجاح', 'success')
    return redirect(url_for('admin', tab=request.args.get('tab', 'teachers')))


@app.route('/toggle_teacher_add_plan_permission/<int:teacher_id>')
@login_required
def toggle_teacher_add_plan_permission(teacher_id):
    """تبديل صلاحية إضافة المقررات للمعلم"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    if not current_user.is_super_admin:
        flash('هذه الصلاحية للمشرفين الكبار فقط', 'error')
        return redirect(url_for('admin', tab='teachers'))
    teacher = Teacher.query.get_or_404(teacher_id)
    teacher.can_add_plan = not teacher.can_add_plan
    db.session.commit()
    status = 'مُفعَّلة' if teacher.can_add_plan else 'مُعطَّلة'
    flash(f'صلاحية إضافة المقررات للمعلم {teacher.full_name} أصبحت {status}', 'success')
    return redirect(url_for('admin', tab=request.args.get('tab', 'teachers')))


@app.route('/toggle_teacher_add_poem_plan_permission/<int:teacher_id>')
@login_required
def toggle_teacher_add_poem_plan_permission(teacher_id):
    """تبديل صلاحية إضافة مقررات القصائد للمعلم."""
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('هذه الصلاحية للمشرفين الكبار فقط', 'error')
        return redirect(url_for('admin', tab='teachers'))

    teacher = Teacher.query.get_or_404(teacher_id)
    teacher.can_add_poem_plan = not teacher.can_add_poem_plan
    db.session.commit()
    status = 'مُفعَّلة' if teacher.can_add_poem_plan else 'مُعطَّلة'
    flash(f'صلاحية إضافة مقرر القصائد للمعلم {teacher.full_name} أصبحت {status}', 'success')
    return redirect(url_for('admin', tab=request.args.get('tab', 'teachers')))

# ------------------ مسارات الربط ------------------
@app.route('/assign_teacher_students', methods=['POST'])
@login_required
def assign_teacher_students():
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))
        tab = request.form.get('tab', 'assign')  # استلام قيمة التبويب من النموذج
        teacher_id = request.form.get('teacher_id', '').strip()
        student_ids = request.form.getlist('student_ids')
        student_ids = [sid for sid in student_ids if str(sid).strip()]

        try:
            teacher_id = int(teacher_id)
        except (TypeError, ValueError):
            teacher_id = 0

        teacher = Teacher.query.filter_by(id=teacher_id, is_active=True).first()
        if not teacher:
            flash('لم يتم اختيار معلم صحيح. اختر معلماً ثم احفظ.', 'error')
            return redirect(url_for('admin', tab=tab))

        try:
            teacher.students = []
            for sid in student_ids:
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    continue
                student = Student.query.get(sid)
                if student and student not in teacher.students:
                    teacher.students.append(student)
            db.session.commit()
            flash('تم حفظ الارتباطات بنجاح', 'success')
        except Exception as e:
            db.session.rollback()
            print("ASSIGN TEACHER STUDENTS ERROR:", e)
            flash('فشل حفظ الارتباطات – راجع السجلات', 'error')
        return redirect(url_for('admin', tab=tab))


@app.route('/delete_assignment/<int:teacher_id>')
@login_required
def delete_assignment(teacher_id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    teacher = Teacher.query.filter_by(id=teacher_id, is_active=True).first()
    if not teacher:
        flash('المعلم غير موجود', 'error')
        return redirect(url_for('admin', tab=request.args.get('tab', 'assign')))
    teacher.students = []
    db.session.commit()
    flash('تم حذف جميع ارتباطات هذا المعلم', 'success')
    return redirect(url_for('admin', tab=request.args.get('tab', 'assign')))


@app.route('/get_teacher_students/<int:teacher_id>')
@login_required
def get_teacher_students(teacher_id):
    if not isinstance(current_user, Admin):
        return {'error': 'Unauthorized'}, 403
    teacher = Teacher.query.get_or_404(teacher_id)
    student_ids = [s.id for s in teacher.students]
    return {'student_ids': student_ids}

def poem_study_data(student_id):
    """يعيد بيانات مقررات القصائد للطالب:
    poem_plans   : كل مقررات القصائد (الأحدث أولاً)
    current_poem : القصيدة التي يدرسها الطالب الآن (أو None)
    current_verse: آخر بيت مُجاز في القصيدة الحالية
    achievements : سجل مقررات القصائد المُقيّمة
    """
    plans = WeeklyPlan.query.filter_by(
        student_id=student_id, plan_type='قصيدة'
    ).order_by(WeeklyPlan.id.asc()).all()

    achievements = []
    for p in plans:
        ev = p.evaluations[0] if p.evaluations else None
        if ev:
            achievements.append({'plan': p, 'evaluation': ev, 'poem': p.poem})

    current_poem = None
    current_verse = 0
    if plans:
        last = plans[-1]
        ev = last.evaluations[0] if last.evaluations else None
        current_poem = last.poem
        if ev and ev.new_pass:
            current_verse = last.verse_end or 0
        else:
            current_verse = max(0, (last.verse_start or 1) - 1)

    return {
        'poem_plans': list(reversed(plans)),
        'current_poem': current_poem,
        'current_verse': current_verse,
        'achievements': achievements
    }


def resolve_next_poem_verse(student_id):
    """تُرجع (القصيدة، أول بيت) للمقرر القادم للطالب.
    يعتمد على آخر بيت مُجاز: إذا انتهت القصيدة انتقل إلى القصيدة التالية.
    """
    plans = WeeklyPlan.query.filter_by(
        student_id=student_id, plan_type='قصيدة'
    ).order_by(WeeklyPlan.id.asc()).all()
    poems = Poem.query.order_by(Poem.poem_number.asc(), Poem.id.asc()).all()
    if not poems:
        return None, 1

    if plans:
        last = plans[-1]
        ev = last.evaluations[0] if last.evaluations else None
        if ev and ev.new_pass and last.verse_end:
            poem_obj = last.poem or poems[0]
            if last.verse_end >= (poem_obj.verse_count or 1):
                nxt = next(
                    (x for x in poems if (x.poem_number or 0) > (poem_obj.poem_number or 0)),
                    None
                )
                if nxt is None:
                    return None, 0  # انتهت كل القصائد
                return nxt, 1
            return poem_obj, last.verse_end + 1
        # لم يُجز المقرر السابق → نعرض نفس القصيدة (يُمنع الإضافة في المسار)
        return last.poem if last else poems[0], max(1, (last.verse_start or 1) if last else 1)

    return poems[0], 1


# ------------------ مسارات المقررات الأسبوعية ------------------






@app.route('/add_plan/<int:student_id>', methods=['GET', 'POST'])
@login_required
def add_plan(student_id):
    if not isinstance(current_user, Teacher):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    # فحص صلاحية إضافة المقررات
    if not current_user.can_add_plan:
        flash('ليس لديك صلاحية إضافة المقررات. يرجى التواصل مع الإدارة.', 'error')
        return redirect(url_for('teacher_dashboard'))

    student = Student.query.get_or_404(student_id)

    if student not in current_user.students:
        flash('هذا الطالب ليس من طلابك', 'error')
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'POST':
        try:
            plan_type = request.form['plan_type']
            start_date_str = request.form['start_date']
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()

            if start_date.weekday() != 6:
                flash('تاريخ البدء يجب أن يكون الأحد', 'error')
                return redirect(url_for('add_plan', student_id=student.id))

            # يسمح لكل طالب بمقررين في الأسبوع:
            # مقرر قرآن واحد (حفظ/سرد/تقييم مرحلة) ومقرر قصيدة واحد.
            existing_plan = WeeklyPlan.query.filter(
                WeeklyPlan.student_id == student.id,
                WeeklyPlan.start_date == start_date,
                WeeklyPlan.plan_type != 'قصيدة'
            ).first()
            if existing_plan:
                flash(f'يوجد مقرر قرآن مسجّل مسبقاً لهذا الطالب في نفس الفترة (بداية: {start_date.strftime("%Y-%m-%d")}). يمكن إضافة مقرر قصيدة مستقل.', 'error')
                return redirect(url_for('add_plan', student_id=student.id))

            notes = request.form.get('notes', '')

            plan = WeeklyPlan(
                student_id=student.id,
                teacher_id=current_user.id,
                plan_type=plan_type,
                start_date=start_date,
                notes=notes
            )

            if plan_type == 'حفظ':
                plan.new_memorization = request.form.get('new_memorization', '')
                plan.recent_review = request.form.get('recent_review', '')
                plan.previous_review = request.form.get('previous_review', '')
            else:  # سرد
                plan.sard_subtype = request.form.get('sard_subtype', 'full')
                plan.revision_text = request.form.get('revision_text', '')
                plan.revision_notes = request.form.get('revision_notes', '')

            db.session.add(plan)
            db.session.commit()
            flash('تم إضافة المقرر بنجاح', 'success')
            return redirect(url_for('view_student_plans', student_id=student.id))

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {str(e)}', 'error')
            return redirect(url_for('add_plan', student_id=student.id))

    today = date.today()
    if today.weekday() != 6:
        days_until_sunday = (6 - today.weekday()) % 7
        default_date = today + timedelta(days=days_until_sunday)
    else:
        default_date = today
    default_date_str = default_date.strftime('%Y-%m-%d')

    return render_template('add_plan.html', student=student, default_date=default_date_str)


@app.route('/add_poem_plan/<int:student_id>', methods=['GET', 'POST'])
@login_required
def add_poem_plan(student_id):
    if not isinstance(current_user, Teacher):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    # فحص صلاحية إضافة المقررات
    if not current_user.can_add_plan:
        flash('ليس لديك صلاحية إضافة المقررات. يرجى التواصل مع الإدارة.', 'error')
        return redirect(url_for('teacher_dashboard'))

    if not current_user.can_add_poem_plan:
        flash('تم تعطيل صلاحية إضافة مقررات القصائد من قبل الإدارة.', 'error')
        return redirect(url_for('view_student_plans', student_id=student_id))

    student = Student.query.get_or_404(student_id)

    if student not in current_user.students:
        flash('هذا الطالب ليس من طلابك', 'error')
        return redirect(url_for('teacher_dashboard'))

    state = poem_study_data(student.id)
    poem_plans = state['poem_plans']
    last_plan = poem_plans[0] if poem_plans else None

    if request.method == 'POST':
        try:
            start_date_str = request.form['start_date']
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()

            if start_date.weekday() != 6:
                flash('تاريخ البدء يجب أن يكون الأحد', 'error')
                return redirect(url_for('add_poem_plan', student_id=student.id))

            existing_plan = WeeklyPlan.query.filter_by(
                student_id=student.id,
                plan_type='قصيدة',
                start_date=start_date
            ).first()
            if existing_plan:
                flash('يوجد مقرر قصيدة مسجّل لهذا الطالب في نفس الفترة', 'error')
                return redirect(url_for('add_poem_plan', student_id=student.id))

            # لا يمكن إضافة مقرر جديد قبل إجازة المقرر السابق
            if last_plan:
                ev = last_plan.evaluations[0] if last_plan.evaluations else None
                if not ev or not ev.new_pass:
                    flash('لا يمكن إضافة مقرر قصيدة جديد قبل إجازة المقرر السابق.', 'error')
                    return redirect(url_for('add_poem_plan', student_id=student.id))

            poem, verse_start = resolve_next_poem_verse(student.id)
            if poem is None:
                flash('انتهت جميع القصائد المتاحة، لا يمكن إضافة مقرر جديد.', 'error')
                return redirect(url_for('add_poem_plan', student_id=student.id))

            verse_count = int(request.form.get('verse_count') or 0)
            if verse_count <= 0:
                flash('حدد عدد الأبيات', 'error')
                return redirect(url_for('add_poem_plan', student_id=student.id))

            verse_end = min(verse_start + verse_count - 1, poem.verse_count or (verse_start + verse_count - 1))

            plan = WeeklyPlan(
                student_id=student.id,
                teacher_id=current_user.id,
                plan_type='قصيدة',
                poem_id=poem.id,
                verse_start=verse_start,
                verse_end=verse_end,
                start_date=start_date,
                notes=request.form.get('notes', '')
            )
            db.session.add(plan)
            db.session.commit()
            flash('تم إضافة مقرر القصيدة بنجاح', 'success')
            return redirect(url_for('view_student_plans', student_id=student.id))

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {str(e)}', 'error')
            return redirect(url_for('add_poem_plan', student_id=student.id))

    # ── عرض: تحديد حالة الحظر تلقائياً ──
    blocked = None
    poem = None
    verse_start = 1
    if last_plan:
        ev = last_plan.evaluations[0] if last_plan.evaluations else None
        if not ev:
            blocked = 'pending'  # بانتظار التقييم
            poem = last_plan.poem
        elif not ev.new_pass:
            blocked = 'fail'  # لم يُجز المقرر السابق
            poem = last_plan.poem

    if not blocked:
        poem, verse_start = resolve_next_poem_verse(student.id)
        if poem is None:
            blocked = 'nomore'

    remaining = 0
    if poem and blocked is None:
        total = poem.verse_count or (verse_start + 0)
        remaining = max(0, total - verse_start + 1)

    today = date.today()
    if today.weekday() != 6:
        days_until_sunday = (6 - today.weekday()) % 7
        default_date = today + timedelta(days=days_until_sunday)
    else:
        default_date = today
    default_date_str = default_date.strftime('%Y-%m-%d')

    return render_template('add_poem_plan.html',
                          student=student,
                          poem=poem,
                          verse_start=verse_start,
                          remaining=remaining,
                          blocked=blocked,
                          last_plan=last_plan,
                          default_date=default_date_str,
                          state=state)


@app.route('/edit_plan/<int:plan_id>', methods=['GET', 'POST'])
@login_required
def edit_plan(plan_id):
    plan = WeeklyPlan.query.get_or_404(plan_id)
    if not isinstance(current_user, Teacher):
        flash('غير مصرح: فقط المعلمون يمكنهم تعديل المقررات', 'error')
        return redirect(url_for('view_student_plans', student_id=plan.student_id))
    if plan.student not in current_user.students:
        flash('هذا الطالب ليس من طلابك', 'error')
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'POST':
        if plan.plan_type == 'قصيدة':
            # قسم القصائد: يُعدَّل التاريخ والملاحظات وعدد الأبيات (لتصحيح خطأ الإدخال)
            start_date_str = request.form['start_date']
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            if start_date.weekday() != 6:
                flash('تاريخ البدء يجب أن يكون الأحد', 'error')
                return redirect(url_for('edit_plan', plan_id=plan.id))

            duplicate_poem = WeeklyPlan.query.filter(
                WeeklyPlan.student_id == plan.student_id,
                WeeklyPlan.start_date == start_date,
                WeeklyPlan.plan_type == 'قصيدة',
                WeeklyPlan.id != plan.id
            ).first()
            if duplicate_poem:
                flash('يوجد مقرر قصيدة آخر لهذا الطالب في نفس الأسبوع.', 'error')
                return redirect(url_for('edit_plan', plan_id=plan.id))

            verse_count = int(request.form.get('verse_count') or 0)
            verse_start = plan.verse_start or 1
            poem = plan.poem
            poem_total = (poem.verse_count or (verse_start + verse_count - 1)) if poem else (verse_start + verse_count - 1)
            if verse_count <= 0:
                flash('حدد عدد الأبيات', 'error')
                return redirect(url_for('edit_plan', plan_id=plan.id))
            new_end = verse_start + verse_count - 1
            if new_end > poem_total:
                flash(f'عدد الأبيات يتجاوز أبيات القصيدة (الحد الأقصى: {poem_total})', 'error')
                return redirect(url_for('edit_plan', plan_id=plan.id))

            plan.start_date = start_date
            plan.verse_end = new_end
            plan.notes = request.form.get('notes', '')
            db.session.commit()
            flash('تم تعديل المقرر بنجاح', 'success')
            return redirect(url_for('view_student_plans', student_id=plan.student_id))

        new_plan_type = request.form['plan_type']
        start_date_str = request.form['start_date']
        new_start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        if new_start_date.weekday() != 6:
            flash('تاريخ البدء يجب أن يكون الأحد', 'error')
            return redirect(url_for('edit_plan', plan_id=plan.id))

        duplicate_quran_plan = WeeklyPlan.query.filter(
            WeeklyPlan.student_id == plan.student_id,
            WeeklyPlan.start_date == new_start_date,
            WeeklyPlan.plan_type != 'قصيدة',
            WeeklyPlan.id != plan.id
        ).first()
        if duplicate_quran_plan:
            flash('يوجد مقرر قرآن آخر لهذا الطالب في نفس الأسبوع.', 'error')
            return redirect(url_for('edit_plan', plan_id=plan.id))

        plan.plan_type = new_plan_type
        plan.start_date = new_start_date
        plan.notes = request.form.get('notes', '')

        if plan.plan_type == 'حفظ':
            plan.new_memorization = request.form.get('new_memorization', '')
            plan.recent_review = request.form.get('recent_review', '')
            plan.previous_review = request.form.get('previous_review', '')
            plan.revision_text = None
            plan.revision_notes = None
            plan.sard_subtype = None
        else:  # سرد
            plan.sard_subtype = request.form.get('sard_subtype', 'full')
            plan.revision_text = request.form.get('revision_text', '')
            plan.revision_notes = request.form.get('revision_notes', '')
            plan.new_memorization = plan.recent_review = plan.previous_review = None

        db.session.commit()
        flash('تم تعديل المقرر بنجاح', 'success')
        return redirect(url_for('view_student_plans', student_id=plan.student_id))

    return render_template('edit_plan.html', plan=plan)





@app.route('/delete_plan/<int:plan_id>')
@login_required
def delete_plan(plan_id):
    plan = WeeklyPlan.query.get_or_404(plan_id)
    if isinstance(current_user, Teacher):
        if plan.student not in current_user.students:
            flash('هذا الطالب ليس من طلابك', 'error')
            return redirect(url_for('teacher_dashboard'))
        if current_user.id != plan.teacher_id:
            flash('غير مصرح: لا يمكنك حذف هذا المقرر', 'error')
            return redirect(url_for('view_student_plans', student_id=plan.student_id))
    elif not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    if plan.daily_reports or plan.evaluations:
        flash('لا يمكن حذف مقرر له تقارير أو تقييمات', 'error')
        return redirect(url_for('view_student_plans', student_id=plan.student_id))

    db.session.delete(plan)
    db.session.commit()
    flash('تم حذف المقرر بنجاح', 'success')
    return redirect(url_for('view_student_plans', student_id=plan.student_id))


@app.route('/admin/missing-plans', methods=['GET', 'POST'])
@login_required
def missing_plans():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    teachers = Teacher.query.filter_by(is_active=True).all()

    # حساب تاريخ الأحد القادم
    today = date.today()
    days_until_sunday = (6 - today.weekday()) % 7
    next_sunday = today + timedelta(days=days_until_sunday)

    selected_date = None
    selected_teacher_id = None
    students_without_plan = []
    students_with_plan = []

    if request.method == 'POST':
        date_str = request.form.get('report_date')
        selected_teacher_id = request.form.get('teacher_id', '')

        if date_str:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            # التحقق من أن التاريخ هو الأحد
            if selected_date.weekday() != 6:
                flash('التاريخ المحدد يجب أن يكون يوم الأحد', 'warning')
                selected_date = None
            else:
                # جلب جميع المقررات في هذا التاريخ
                plans_in_week = WeeklyPlan.query.filter_by(start_date=selected_date).all()

                # إنشاء قائمة بمعرفات الطلاب الذين لديهم مقرر
                students_with_plan_ids = set()
                plans_info = {}

                for plan in plans_in_week:
                    students_with_plan_ids.add(plan.student_id)
                    plans_info[plan.student_id] = {
                        'teacher_name': plan.teacher.full_name,
                        'plan_type': plan.plan_type
                    }

                # جلب جميع الطلاب (حسب المعلم إذا تم اختياره)
                if selected_teacher_id and selected_teacher_id != '':
                    teacher = Teacher.query.get(int(selected_teacher_id))
                    if teacher:
                        all_students = teacher.students
                    else:
                        all_students = []
                else:
                    all_students = Student.query.filter_by(is_suspended=False).all()

                # تصنيف الطلاب
                for student in all_students:
                    if student.id in students_with_plan_ids:
                        # الطالب لديه مقرر
                        info = plans_info[student.id]
                        students_with_plan.append({
                            'student': student,
                            'teacher_name': info['teacher_name'],
                            'plan_type': info['plan_type']
                        })
                    else:
                        # الطالب ليس لديه مقرر
                        students_without_plan.append(student)

                # ترتيب القوائم أبجدياً
                students_without_plan.sort(key=lambda x: x.full_name)
                students_with_plan.sort(key=lambda x: x['student'].full_name)

                # طباعة للتأكد
                print(f"=== تقرير الأسبوع: {selected_date} ===")
                print(f"المقررات الموجودة: {len(plans_in_week)}")
                print(f"طلاب بدون مقرر: {len(students_without_plan)}")
                print(f"طلاب مع مقرر: {len(students_with_plan)}")
                for item in students_with_plan:
                    print(f"  - {item['student'].full_name}: {item['plan_type']} (معلم: {item['teacher_name']})")

    return render_template('missing_plans.html',
                          teachers=teachers,
                          next_sunday=next_sunday,
                          selected_date=selected_date,
                          selected_teacher_id=selected_teacher_id,
                          students_without_plan=students_without_plan,
                          students_with_plan=students_with_plan)

@app.route('/view_student_plans/<int:student_id>')
@login_required
def view_student_plans(student_id):
    student = Student.query.get_or_404(student_id)
    error = request.args.get('error', '')

    if isinstance(current_user, Teacher):
        if student not in current_user.students:
            flash('هذا الطالب ليس من طلابك', 'error')
            return redirect(url_for('teacher_dashboard'))
        is_teacher = True
        is_student = False
        is_admin = False
    elif isinstance(current_user, Student):
        if current_user.id != student.id:
            flash('غير مصرح', 'error')
            return redirect(url_for('student_dashboard'))
        is_teacher = False
        is_student = True
        is_admin = False
    elif isinstance(current_user, Admin):
        is_teacher = False
        is_student = False
        is_admin = True
    else:
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    plans = WeeklyPlan.query.filter_by(student_id=student.id).order_by(WeeklyPlan.start_date.desc()).all()
    for plan in plans:
        plan.end_date = plan.start_date + timedelta(days=6)

    # إضافة بيانات النقاط للطالب - مع معالجة حالة عدم وجود بيانات
    try:
        student_points = StudentPoints.query.filter_by(student_id=student.id, archived=False).first()
    except:
        student_points = None

    poem_state = poem_study_data(student.id)
    quran_plan_count = sum(1 for plan in plans if plan.plan_type != 'قصيدة')

    return render_template('view_student_plans.html',
                          student=student,
                          plans=plans,
                          is_teacher=is_teacher,
                          is_student=is_student,
                          is_admin=is_admin,
                          error=error,
                          now=datetime.now(),
                          student_points=student_points,
                          poem_state=poem_state,
                          quran_plan_count=quran_plan_count)

def find_existing_rollover_plan(plan, target_start):
    """يبحث عن مقرر مرحَّل أصلاً في نفس تاريخ البدء لتفادي النسخ المزدوج.

    الترحيل يبحث في نفس نوع المقرر فقط: مقرر حفظ لا يقابل مقرر سرد.
    """
    return WeeklyPlan.query.filter(
        WeeklyPlan.student_id == plan.student_id,
        WeeklyPlan.start_date == target_start,
        WeeklyPlan.plan_type == plan.plan_type,
        WeeklyPlan.rolled_from_id == plan.id,
    ).first()


def next_rollover_start(plan, today=None):
    """يحسب تاريخ بدء المقرر المرحَّل.

    الأساس أسبوع بعد المقرر الأصلي (+7). لكن إن كان ذلك التاريخ في الماضي
    — مثل تقييم مقرر متأخر — نقدّمه إلى أقرب يوم أحد قادم حتى يظهر المقرر
    ضمن «المقررات القادمة» لا ضمن「Mقررات سابقة».
    """
    today = today or date.today()
    target = plan.start_date + timedelta(days=7)
    if target <= today:
        days_ahead = (6 - today.weekday()) % 7  # الأحد = 6
        target = today + timedelta(days=days_ahead or 7)
    return target


def build_rollover_plan(plan, reason, next_start):
    """ينسخ المقرر إلى مقرر جديد يبدأ في next_start ويحمل شارة الترحيل.

    لا يحفظ هنا — الاتصال يتولى ذلك. يعيد (plan, created) حيث created=False
    إذا وُجد مقرر مرحَّل مسبقاً في نفس التاريخ (يمنع التكرار عند تكرار التقييم).
    """
    existing = find_existing_rollover_plan(plan, next_start)
    if existing:
        return existing, False
    # ننسخ الحقول التعليمية فقط. لا ننسخ التقييم ولا التقارير ولا النقاط:
    # المقرر الجديد يبدأ نظيفاً ليُقيَّم من جديد.
    clone = WeeklyPlan(
        student_id=plan.student_id,
        teacher_id=plan.teacher_id,
        plan_type=plan.plan_type,
        sard_subtype=plan.sard_subtype,
        poem_id=plan.poem_id,
        verse_start=plan.verse_start,
        verse_end=plan.verse_end,
        start_date=next_start,
        new_memorization=plan.new_memorization,
        recent_review=plan.recent_review,
        previous_review=plan.previous_review,
        revision_text=plan.revision_text,
        revision_notes=plan.revision_notes,
        notes=plan.notes,
        rolled_over=True,
        rolled_from_id=plan.id,
        roll_reason=reason,
        roll_notes='مقرر مُرحَّل من أسبوع %s بسبب الرسب' % plan.start_date.strftime('%Y/%m/%d'),
    )
    db.session.add(clone)
    return clone, True


ROLL_REASON_TEXT = {
    'new':  'الحفظ الجديد',
    'old':  'الحفظ القديم',
    'both': 'الحفظ الجديد والقديم',
    'sard': 'السرد',
    'poem': 'القصيدة',
}


def roll_reason_for(new_pass, old_pass):
    """سبب الترحيل من نتيجة الإجازة، أو None إذا أجاز المعلم كل Parts."""
    if not new_pass and not old_pass:
        return 'both'
    if not new_pass:
        return 'new'
    if not old_pass:
        return 'old'
    return None


@app.route('/evaluate_plan/<int:plan_id>', methods=['GET', 'POST'])
@login_required
def evaluate_plan(plan_id):
    plan = WeeklyPlan.query.get_or_404(plan_id)

    if not isinstance(current_user, Teacher):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))
    if plan.student not in current_user.students:
        flash('هذا الطالب ليس من طلابك', 'error')
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'GET':
        existing_evaluation = Evaluation.query.filter_by(
            plan_id=plan.id
        ).order_by(Evaluation.id.asc()).first()
        if existing_evaluation:
            flash('هذا المقرر مقيّم مسبقاً. يمكنك تعديل التقييم الحالي.', 'info')
            return redirect(url_for('edit_evaluation', eval_id=existing_evaluation.id))

    if request.method == 'POST':
        try:
            # قفل سجل المقرر حتى نهاية المعاملة. إذا وصل طلبان في الوقت
            # نفسه ينتظر الثاني، ثم يرى التقييم الذي أنشأه الطلب الأول.
            plan = WeeklyPlan.query.filter_by(id=plan.id).with_for_update().one()
            existing_evaluation = Evaluation.query.filter_by(
                plan_id=plan.id
            ).order_by(Evaluation.id.asc()).first()
            if existing_evaluation:
                db.session.rollback()
                flash('هذا المقرر مقيّم مسبقاً. يمكنك تعديل التقييم الحالي.', 'info')
                return redirect(url_for('edit_evaluation', eval_id=existing_evaluation.id))

            eval_date = datetime.strptime(request.form['evaluation_date'], '%Y-%m-%d').date()
            notes = request.form.get('notes', '')

            if plan.plan_type == 'قصيدة':
                poem_pass_value = request.form.get('poem_pass')
                if poem_pass_value not in ('true', 'false'):
                    flash('اختر نتيجة تقييم القصيدة: يجاز أو لا يجاز.', 'error')
                    return redirect(url_for('evaluate_plan', plan_id=plan.id))
                poem_pass = poem_pass_value == 'true'
                evaluation = Evaluation(
                    plan_id=plan.id,
                    teacher_id=current_user.id,
                    evaluation_date=eval_date,
                    new_pass=poem_pass,
                    evaluation_notes=notes
                )
                db.session.add(evaluation)

                # القصيدة: الترحيل يُفعَّل افتراضياً عند الرسب، ويمكن إلغاؤه.
                roll_created = False
                if not poem_pass and request.form.get('rollover') == '1':
                    next_start = next_rollover_start(plan)
                    _, roll_created = build_rollover_plan(plan, 'poem', next_start)
                db.session.commit()

                if poem_pass:
                    flash('تم تقييم مقرر القصيدة: أُجيز المقرر بنجاح ✅', 'success')
                elif roll_created:
                    flash('لم يُجز ❌ — تم نسخ مقرر القصيدة إلى الأسبوع القادم', 'error')
                else:
                    flash('لم يُجز ❌', 'error')
                return redirect(url_for('view_student_plans', student_id=plan.student_id))

            if plan.plan_type == 'حفظ':
                new_score = int(request.form['new_score'])
                new_pass = request.form.get('new_pass') == 'true'
                recent_score = int(request.form['recent_score'])
                old_score = int(request.form['old_score'])
                old_pass = request.form.get('old_pass') == 'true'

                evaluation = Evaluation(
                    plan_id=plan.id,
                    teacher_id=current_user.id,
                    evaluation_date=eval_date,
                    new_score=new_score,
                    new_pass=new_pass,
                    recent_score=recent_score,
                    previous_score=old_score,
                    previous_pass=old_pass,
                    evaluation_notes=notes
                )
                total_score = new_score + recent_score + old_score
                points = total_score // 10

                if points >= 9: stars = 3
                elif points >= 6: stars = 2
                elif points >= 3: stars = 1
                else: stars = 0

            elif plan.plan_type == 'سرد':
                revision_score = int(request.form['revision_score'])
                revision_pass = request.form.get('revision_pass') == 'true'

                evaluation = Evaluation(
                    plan_id=plan.id,
                    teacher_id=current_user.id,
                    evaluation_date=eval_date,
                    revision_score=revision_score,
                    revision_pass=revision_pass,
                    evaluation_notes=notes
                )
                points = revision_score // 10
                if points >= 9: stars = 3
                elif points >= 6: stars = 2
                elif points >= 3: stars = 1
                else: stars = 0

            elif plan.plan_type == 'تقييم مرحلة':
                phase_score = int(request.form['phase_score'])
                phase_pass = request.form.get('phase_pass') == 'true'

                evaluation = Evaluation(
                    plan_id=plan.id,
                    teacher_id=current_user.id,
                    evaluation_date=eval_date,
                    new_score=phase_score,
                    new_pass=phase_pass,
                    evaluation_notes=notes
                )
                points = phase_score // 8
                stars = points // 3
                if stars > 3: stars = 3

            db.session.add(evaluation)
            db.session.flush()  # للحصول على ID التقييم

            # حساب النقاط من الدالة المركزية
            points, stars = calculate_points_from_evaluation(evaluation, plan)

            # حفظ النقاط للطالب
            student_points = StudentPoints.query.filter_by(student_id=plan.student_id, archived=False).first()
            if not student_points:
                student_points = StudentPoints(
                    student_id=plan.student_id,
                    total_points=0,
                    current_cycle_points=0,
                    star_level=0,
                    current_cycle_start=date.today()
                )
                db.session.add(student_points)
                db.session.flush()

            # ضمان أن current_cycle_start ليس None
            if not student_points.current_cycle_start:
                student_points.current_cycle_start = date.today()

            # تحديث النقاط (star_level يُحسب من نقاط الدورة الحالية)
            student_points.total_points += points
            student_points.current_cycle_points += points
            student_points.star_level = recalculate_star_level(student_points.current_cycle_points)
            student_points.last_updated = datetime.utcnow()

            # تسجيل سجل النقاط
            cycle_start = student_points.current_cycle_start or date.today()
            points_log = PointsLog(
                student_id=plan.student_id,
                evaluation_id=evaluation.id,
                plan_id=plan.id,
                points_earned=points,
                stars_earned=stars,
                evaluation_date=eval_date,
                cycle_start_date=cycle_start
            )
            db.session.add(points_log)

            # ---- الترحيل التلقائي عند الرسب ----
            # رسب في الحفظ الجديد أو القديم، أو في السرد (كامل/مراجعة)،
            # أو في القصيدة ← تُنسخ الخطة كاملة للأسبوع القادم.
            # الخيار يصل من القالب مفعّلاً افتراضياً عند اختيار «لا يجاز»،
            # ويمكن للمعلم إلغاؤه.
            roll_created = False
            roll_reason = None
            if plan.plan_type == 'حفظ':
                roll_reason = roll_reason_for(new_pass, old_pass)
            elif plan.plan_type == 'سرد':
                roll_reason = None if revision_pass else 'sard'
            elif plan.plan_type == 'قصيدة':
                roll_reason = None if poem_pass else 'poem'
            if roll_reason and request.form.get('rollover') == '1':
                next_start = next_rollover_start(plan)
                _, roll_created = build_rollover_plan(plan, roll_reason, next_start)

            db.session.commit()

            if roll_created:
                flash('✅ تم تقييم المقرر. تم نسخ المقرر إلى الأسبوع القادم بسبب الرسب في %s.'
                      % ROLL_REASON_TEXT.get(roll_reason, 'المقرر'), 'success')
            else:
                flash(f'✅ تم تقييم المقرر بنجاح! تم إضافة {points} نقطة للطالب.', 'success')

        except Exception as e:
            db.session.rollback()
            print(f"❌ خطأ في التقييم: {str(e)}")
            flash(f'حدث خطأ أثناء التقييم: {str(e)}', 'error')

        return redirect(url_for('view_student_plans', student_id=plan.student_id))

    default_eval_date = (plan.start_date + timedelta(days=6)).strftime('%Y-%m-%d')
    return render_template('evaluate_plan.html', plan=plan, default_eval_date=default_eval_date)



@app.route('/edit_evaluation/<int:eval_id>', methods=['GET', 'POST'])
@login_required
def edit_evaluation(eval_id):
    evaluation = Evaluation.query.get_or_404(eval_id)
    plan = evaluation.plan

    if not isinstance(current_user, Teacher):
        flash('غير مصرح: فقط المعلمون يمكنهم تعديل التقييمات', 'error')
        return redirect(url_for('view_student_plans', student_id=plan.student_id))
    if plan.student not in current_user.students:
        flash('هذا الطالب ليس من طلابك', 'error')
        return redirect(url_for('teacher_dashboard'))

    if request.method == 'POST':
        try:
            # حساب النقاط القديمة من التقييم الحالي (قبل التعديل)
            old_points, _ = calculate_points_from_evaluation(evaluation, plan)

            # تحديث التقييم بالقيم الجديدة
            eval_date = datetime.strptime(request.form['evaluation_date'], '%Y-%m-%d').date()
            evaluation.evaluation_date = eval_date
            evaluation.evaluation_notes = request.form.get('notes', '')

            if plan.plan_type == 'حفظ':
                evaluation.new_score    = int(request.form['new_score'])
                evaluation.new_pass     = request.form.get('new_pass') == 'true'
                evaluation.recent_score = int(request.form['recent_score'])
                evaluation.previous_score = int(request.form['old_score'])
                evaluation.previous_pass  = request.form.get('old_pass') == 'true'

            elif plan.plan_type == 'سرد':
                evaluation.revision_score = int(request.form['revision_score'])
                evaluation.revision_pass  = request.form.get('revision_pass') == 'true'

            elif plan.plan_type == 'تقييم مرحلة':
                evaluation.new_score = int(request.form['phase_score'])
                evaluation.new_pass  = request.form.get('phase_pass') == 'true'

            elif plan.plan_type == 'قصيدة':
                poem_pass_value = request.form.get('poem_pass')
                if poem_pass_value not in ('true', 'false'):
                    flash('اختر نتيجة تقييم القصيدة: يجاز أو لا يجاز.', 'error')
                    return redirect(url_for('edit_evaluation', eval_id=evaluation.id))
                evaluation.new_pass = poem_pass_value == 'true'

            # حساب النقاط الجديدة بعد التعديل
            new_points, new_stars = calculate_points_from_evaluation(evaluation, plan)
            points_diff = new_points - old_points

            # ترحيل المقرر: يحدث أيضاً عند تعديل تقييم ناجح إلى «لا يجاز»،
            # أو عند تقييم قصيدة لا تُجاز. الخيار يصل من القالب مفعّلاً.
            roll_created = False
            roll_reason = None
            if plan.plan_type == 'حفظ':
                roll_reason = roll_reason_for(evaluation.new_pass, evaluation.previous_pass)
            elif plan.plan_type == 'سرد':
                roll_reason = None if evaluation.revision_pass else 'sard'
            elif plan.plan_type == 'قصيدة':
                roll_reason = None if evaluation.new_pass else 'poem'
            if roll_reason and request.form.get('rollover') == '1':
                next_start = next_rollover_start(plan)
                _, roll_created = build_rollover_plan(plan, roll_reason, next_start)

            db.session.commit()

            # تحديث النقاط في جدول StudentPoints
            student_points = StudentPoints.query.filter_by(student_id=plan.student_id, archived=False).first()

            if student_points:
                if points_diff != 0:
                    new_total_points = max(0, student_points.total_points + points_diff)
                    new_cycle_points = max(0, student_points.current_cycle_points + points_diff)

                    student_points.total_points         = new_total_points
                    student_points.current_cycle_points = new_cycle_points
                    # النجوم تُحسب دائماً من نقاط الدورة الحالية
                    student_points.star_level   = recalculate_star_level(new_cycle_points)
                    student_points.last_updated = datetime.utcnow()

                    # تحديث أو إنشاء سجل النقاط
                    points_log = PointsLog.query.filter_by(evaluation_id=evaluation.id).first()
                    if points_log:
                        points_log.points_earned = new_points
                        points_log.stars_earned  = new_stars
                    else:
                        cycle_start = student_points.current_cycle_start or date.today()
                        points_log = PointsLog(
                            student_id=plan.student_id,
                            evaluation_id=evaluation.id,
                            plan_id=plan.id,
                            points_earned=new_points,
                            stars_earned=new_stars,
                            evaluation_date=eval_date,
                            cycle_start_date=cycle_start
                        )
                        db.session.add(points_log)

                    db.session.commit()

                    if points_diff > 0:
                        flash(f'✅ تم تعديل التقييم بنجاح! تم إضافة {points_diff} نقطة للطالب.', 'success')
                    else:
                        flash(f'✅ تم تعديل التقييم بنجاح! تم خصم {abs(points_diff)} نقطة من رصيد الطالب.', 'warning')
                else:
                    flash('✅ تم تعديل التقييم بنجاح (النقاط لم تتغير).', 'success')
            else:
                flash('✅ تم تعديل التقييم بنجاح.', 'success')

            if roll_created:
                flash('✅ تم نسخ المقرر إلى الأسبوع القادم بسبب الرسب في %s.'
                      % ROLL_REASON_TEXT.get(roll_reason, 'المقرر'), 'success')

        except Exception as e:
            db.session.rollback()
            print(f"❌ خطأ في تعديل التقييم: {str(e)}")
            flash(f'حدث خطأ أثناء تعديل التقييم: {str(e)}', 'error')

        return redirect(url_for('view_student_plans', student_id=plan.student_id))

    return render_template('edit_evaluation.html', evaluation=evaluation, plan=plan)


    # ------------------ مسارات صفحات الحفظ ------------------
@app.route('/admin/student_pages')
@login_required
def admin_student_pages():
        """عرض وإدارة صفحات الحفظ للطلاب"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        students = Student.query.all()
        student_pages_list = []

        for student in students:
            pages_info = StudentPages.query.filter_by(student_id=student.id).first()
            student_pages_list.append({
                'student': student,
                'pages': pages_info.total_pages if pages_info else 0,
                'last_updated': pages_info.last_updated if pages_info else None,
                'notes': pages_info.notes if pages_info else ''
            })

        return render_template('admin_student_pages.html', 
                              student_pages=student_pages_list,
                              active_tab='pages')
@app.route('/admin/update_student_pages/<int:student_id>', methods=['POST'])
@login_required
def update_student_pages(student_id):
        """تحديث عدد صفحات الطالب"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        student = Student.query.get_or_404(student_id)
        pages = request.form.get('total_pages', type=int)
        notes = request.form.get('notes', '')

        student_pages = StudentPages.query.filter_by(student_id=student_id).first()
        if student_pages:
            student_pages.total_pages = pages
            student_pages.notes = notes
        else:
            student_pages = StudentPages(
                student_id=student_id,
                total_pages=pages,
                notes=notes
            )
            db.session.add(student_pages)

        db.session.commit()
        flash(f'تم تحديث عدد صفحات الطالب {student.full_name} بنجاح', 'success')
        return redirect(url_for('admin_student_pages'))


    # ------------------ مسارات قواعد المكافآت ------------------
@app.route('/admin/reward_rules')
@login_required
def admin_reward_rules():
        """عرض وإدارة قواعد المكافآت"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        rules = RewardRule.query.all()
        return render_template('admin_reward_rules.html', 
                              rules=rules,
                              active_tab='rewards')
@app.route('/admin/add_reward_rule', methods=['POST'])
@login_required
def add_reward_rule():
        """إضافة قاعدة مكافآت جديدة"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        name = request.form.get('name')
        period_months = request.form.get('period_months', type=int)

        if not name or not period_months:
            flash('جميع الحقول مطلوبة', 'error')
            return redirect(url_for('admin_reward_rules'))

        rule = RewardRule(
            name=name,
            period_months=period_months
        )
        db.session.add(rule)
        db.session.flush()  # للحصول على id القاعدة

        # إضافة تفاصيل القاعدة (الشرائح)
        min_scores = request.form.getlist('min_score[]')
        max_scores = request.form.getlist('max_score[]')
        rewards = request.form.getlist('reward_per_page[]')

        for i in range(len(min_scores)):
            if min_scores[i] and max_scores[i] and rewards[i]:
                detail = RewardRuleDetail(
                    rule_id=rule.id,
                    min_score=float(min_scores[i]),
                    max_score=float(max_scores[i]),
                    reward_per_page=float(rewards[i])
                )
                db.session.add(detail)

        db.session.commit()
        flash('تم إضافة قاعدة المكافآت بنجاح', 'success')
        return redirect(url_for('admin_reward_rules'))
@app.route('/admin/edit_reward_rule/<int:rule_id>', methods=['POST'])
@login_required
def edit_reward_rule(rule_id):
        """تعديل قاعدة مكافآت"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        rule = RewardRule.query.get_or_404(rule_id)
        rule.name = request.form.get('name')
        rule.period_months = request.form.get('period_months', type=int)

        # حذف التفاصيل القديمة وإضافة الجديدة
        RewardRuleDetail.query.filter_by(rule_id=rule_id).delete()

        min_scores = request.form.getlist('min_score[]')
        max_scores = request.form.getlist('max_score[]')
        rewards = request.form.getlist('reward_per_page[]')

        for i in range(len(min_scores)):
            if min_scores[i] and max_scores[i] and rewards[i]:
                detail = RewardRuleDetail(
                    rule_id=rule_id,
                    min_score=float(min_scores[i]),
                    max_score=float(max_scores[i]),
                    reward_per_page=float(rewards[i])
                )
                db.session.add(detail)

        db.session.commit()
        flash('تم تعديل قاعدة المكافآت بنجاح', 'success')
        return redirect(url_for('admin_reward_rules'))
@app.route('/admin/toggle_reward_rule/<int:rule_id>')
@login_required
def toggle_reward_rule(rule_id):
        """تفعيل/تعطيل قاعدة مكافآت"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        rule = RewardRule.query.get_or_404(rule_id)
        rule.is_active = not rule.is_active
        db.session.commit()

        status = 'مفعلة' if rule.is_active else 'معطلة'
        flash(f'تم {status} قاعدة المكافآت بنجاح', 'success')
        return redirect(url_for('admin_reward_rules'))
@app.route('/admin/delete_reward_rule/<int:rule_id>')
@login_required
def delete_reward_rule(rule_id):
        """حذف قاعدة مكافآت"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        rule = RewardRule.query.get_or_404(rule_id)
        db.session.delete(rule)
        db.session.commit()

        flash('تم حذف قاعدة المكافآت بنجاح', 'success')
        return redirect(url_for('admin_reward_rules'))


    # ------------------ مسارات تقييم المرحلة ------------------
@app.route('/add_phase_evaluation/<int:student_id>', methods=['GET', 'POST'])
@login_required
def add_phase_evaluation(student_id):
        """إضافة تقييم مرحلة لطالب"""
        if not isinstance(current_user, Teacher):
            flash('غير مصرح', 'error')
            return redirect(url_for('home'))

        student = Student.query.get_or_404(student_id)

        # تأكد من أن الطالب من طلابك
        if student not in current_user.students:
            flash('هذا الطالب ليس من طلابك', 'error')
            return redirect(url_for('teacher_dashboard'))

        if request.method == 'POST':
            try:
                eval_date = datetime.strptime(request.form['evaluation_date'], '%Y-%m-%d').date()
                phase_number = request.form.get('phase_number', type=int)
                phase_name = request.form.get('phase_name', '')
                score = request.form.get('score', type=int)
                notes = request.form.get('notes', '')

                if score < 0 or score > 80:
                    flash('الدرجة يجب أن تكون بين 0 و 80', 'error')
                    return redirect(url_for('add_phase_evaluation', student_id=student.id))

                evaluation = PhaseEvaluation(
                    student_id=student.id,
                    teacher_id=current_user.id,
                    evaluation_date=eval_date,
                    phase_number=phase_number,
                    phase_name=phase_name,
                    score=score,
                    notes=notes
                )

                db.session.add(evaluation)
                db.session.commit()
                flash('تم إضافة تقييم المرحلة بنجاح', 'success')
                return redirect(url_for('view_student_plans', student_id=student.id))

            except Exception as e:
                db.session.rollback()
                flash(f'حدث خطأ: {str(e)}', 'error')
                return redirect(url_for('add_phase_evaluation', student_id=student.id))

        return render_template('add_phase_evaluation.html', student=student)
@app.route('/edit_phase_evaluation/<int:eval_id>', methods=['GET', 'POST'])
@login_required
def edit_phase_evaluation(eval_id):
        """تعديل تقييم مرحلة"""
        evaluation = PhaseEvaluation.query.get_or_404(eval_id)

        if not isinstance(current_user, Teacher):
            flash('غير مصرح', 'error')
            return redirect(url_for('view_student_plans', student_id=evaluation.student_id))
        if not any(student.id == evaluation.student_id for student in current_user.students):
            flash('هذا الطالب ليس من طلابك', 'error')
            return redirect(url_for('teacher_dashboard'))

        if request.method == 'POST':
            try:
                evaluation.evaluation_date = datetime.strptime(request.form['evaluation_date'], '%Y-%m-%d').date()
                evaluation.phase_number = request.form.get('phase_number', type=int)
                evaluation.phase_name = request.form.get('phase_name', '')
                evaluation.score = request.form.get('score', type=int)
                evaluation.notes = request.form.get('notes', '')

                if evaluation.score < 0 or evaluation.score > 80:
                    flash('الدرجة يجب أن تكون بين 0 و 80', 'error')
                    return redirect(url_for('edit_phase_evaluation', eval_id=evaluation.id))

                db.session.commit()
                flash('تم تعديل تقييم المرحلة بنجاح', 'success')
                return redirect(url_for('view_student_plans', student_id=evaluation.student_id))

            except Exception as e:
                db.session.rollback()
                flash(f'حدث خطأ: {str(e)}', 'error')

        return render_template('edit_phase_evaluation.html', evaluation=evaluation)



    # ------------------ مسارات حساب المكافآت ------------------
@app.route('/admin/calculate_rewards', methods=['GET', 'POST'])
@login_required
def calculate_rewards():
    """حساب المكافآت داخل نفس صفحة الحساب مع إدخال صفحات الفترة"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    rules = RewardRule.query.filter_by(is_active=True).all()
    students = Student.query.order_by(Student.full_name).all()

    today = date.today()
    default_start = today.replace(day=1)
    if default_start.month > 3:
        default_start = default_start.replace(month=default_start.month - 3)
    else:
        default_start = default_start.replace(year=default_start.year - 1, month=default_start.month + 9)

    context = dict(
        rules=rules,
        students=students,
        selected_rule=None,
        period_start=None,
        period_end=None,
        existing_pages={},
        default_start=default_start.strftime('%Y-%m-%d'),
        default_end=today.strftime('%Y-%m-%d')
    )

    if request.method == 'POST':
        action = request.form.get('action', 'load')  # load | calculate

        rule_id = request.form.get('rule_id', type=int)
        period_start = datetime.strptime(request.form['period_start'], '%Y-%m-%d').date()
        period_end = datetime.strptime(request.form['period_end'], '%Y-%m-%d').date()

        rule = RewardRule.query.get(rule_id)
        if not rule:
            flash('القاعدة غير موجودة', 'error')
            return redirect(url_for('calculate_rewards'))

        rows = StudentPeriodPages.query.filter_by(period_start=period_start, period_end=period_end).all()
        existing_pages = {r.student_id: r.pages for r in rows}

        context.update(
            selected_rule=rule,
            period_start=period_start,
            period_end=period_end,
            existing_pages=existing_pages,
            default_start=period_start.strftime('%Y-%m-%d'),
            default_end=period_end.strftime('%Y-%m-%d')
        )

        if action == 'calculate':
            # حفظ صفحات الفترة
            for s in students:
                pages_val = request.form.get(f'pages_{s.id}', type=int)
                if pages_val is None:
                    pages_val = 0

                rec = StudentPeriodPages.query.filter_by(
                    student_id=s.id,
                    period_start=period_start,
                    period_end=period_end
                ).first()

                if rec:
                    rec.pages = pages_val
                else:
                    db.session.add(StudentPeriodPages(
                        student_id=s.id,
                        period_start=period_start,
                        period_end=period_end,
                        pages=pages_val
                    ))

            db.session.commit()

            # حساب النتائج
            calculations = []
            for s in students:
                calc = calculate_reward_for_student(s.id, rule_id, period_start, period_end)
                if calc:
                    calculations.append(calc)

            if not calculations:
                flash('لا توجد نتائج للحساب. تأكد من وجود تقييم مرحلة خلال الفترة.', 'warning')
                return render_template('admin_calculate_rewards.html', **context)

            student_names = {s.id: s.full_name for s in students}

            return render_template('admin_reward_results.html',
                                   calculations=calculations,
                                   rule=rule,
                                   period_start=period_start,
                                   period_end=period_end,
                                   student_names=student_names)

        return render_template('admin_calculate_rewards.html', **context)

    return render_template('admin_calculate_rewards.html', **context)


@app.route('/admin/save_reward_calculations', methods=['POST'])
@login_required
def save_reward_calculations():
        """حفظ حسابات المكافآت في قاعدة البيانات"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        try:
            calculations_data = request.form.get('calculations_data', '[]')

            if not calculations_data or calculations_data == '[]':
                flash('لا توجد بيانات للحفظ', 'warning')
                return redirect(url_for('calculate_rewards'))

            data = json.loads(calculations_data)

            if not data:
                flash('لا توجد بيانات للحفظ', 'warning')
                return redirect(url_for('calculate_rewards'))

            saved_count = 0
            for item in data:
                # التحقق من وجود السجل مسبقاً
                existing = RewardCalculation.query.filter_by(
                    student_id=item['student_id'],
                    rule_id=item['rule_id'],
                    period_start=datetime.strptime(item['period_start'], '%Y-%m-%d').date(),
                    period_end=datetime.strptime(item['period_end'], '%Y-%m-%d').date()
                ).first()

                if existing:
                    # تحديث السجل الموجود
                    existing.total_pages = item['total_pages']
                    existing.avg_memorization_score = item['avg_memorization_score']
                    existing.phase_score = item['phase_score']
                    existing.final_score = item['final_score']
                    existing.reward_per_page = item['reward_per_page']
                    existing.total_reward = item['total_reward']
                    existing.calculation_date = datetime.utcnow()
                else:
                    # إنشاء سجل جديد
                    calculation = RewardCalculation(
                        student_id=item['student_id'],
                        rule_id=item['rule_id'],
                        period_start=datetime.strptime(item['period_start'], '%Y-%m-%d').date(),
                        period_end=datetime.strptime(item['period_end'], '%Y-%m-%d').date(),
                        total_pages=item['total_pages'],
                        avg_memorization_score=item['avg_memorization_score'],
                        phase_score=item['phase_score'],
                        final_score=item['final_score'],
                        reward_per_page=item['reward_per_page'],
                        total_reward=item['total_reward']
                    )
                    db.session.add(calculation)

                saved_count += 1

            db.session.commit()
            flash(f'تم حفظ {saved_count} مكافأة بنجاح', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ أثناء حفظ الحسابات: {str(e)}', 'error')

        return redirect(url_for('reward_history'))
@app.route('/admin/reward_history')
@login_required
def reward_history():
        """عرض تاريخ المكافآت"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        calculations = RewardCalculation.query.order_by(
            RewardCalculation.calculation_date.desc()
        ).all()

        return render_template('admin_reward_history.html',
                              calculations=calculations,
                              active_tab='rewards_history')
@app.route('/admin/mark_reward_paid/<int:calc_id>', methods=['POST'])
@login_required
def mark_reward_paid(calc_id):
        """تحديد مكافأة كمدفوعة"""
        if not isinstance(current_user, Admin):
            flash('غير مصرح', 'error')
            return redirect(url_for('admin'))

        calculation = RewardCalculation.query.get_or_404(calc_id)
        calculation.is_paid = True
        calculation.paid_date = date.today()
        calculation.notes = request.form.get('notes', calculation.notes)

        db.session.commit()
        flash('تم تحديث حالة المكافأة إلى مدفوعة', 'success')

        return redirect(url_for('reward_history'))
@app.route('/admin/print_rewards/<int:rule_id>/<path:period_start>/<path:period_end>')
@login_required
def print_rewards(rule_id, period_start, period_end):
    """طباعة كشف المكافآت لفترة محددة"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    # تحويل التواريخ من النص
    start_date = datetime.strptime(period_start, '%Y-%m-%d').date()
    end_date = datetime.strptime(period_end, '%Y-%m-%d').date()

    # جلب القاعدة
    rule = RewardRule.query.get_or_404(rule_id)

    # جلب حسابات المكافآت لهذه الفترة
    calculations = RewardCalculation.query.filter_by(
        rule_id=rule_id,
        period_start=start_date,
        period_end=end_date
    ).order_by(RewardCalculation.student_id).all()

    # حساب الإجماليات
    total_rewards = sum(calc.total_reward for calc in calculations)
    total_pages = sum(calc.total_pages for calc in calculations)
    paid_count = sum(1 for calc in calculations if calc.is_paid)
    unpaid_count = len(calculations) - paid_count

    return render_template('print_rewards.html',
                          calculations=calculations,
                          rule=rule,
                          period_start=start_date,
                          period_end=end_date,
                          total_rewards=total_rewards,
                          total_pages=total_pages,
                          paid_count=paid_count,
                          unpaid_count=unpaid_count,
                          now=datetime.now())
@app.route('/admin/get_reward_rule/<int:rule_id>')
@login_required
def get_reward_rule(rule_id):
    """جلب بيانات قاعدة مكافآت (للتعديل)"""
    if not isinstance(current_user, Admin):
        return {'error': 'Unauthorized'}, 403

    rule = RewardRule.query.get_or_404(rule_id)
    data = {
        'id': rule.id,
        'name': rule.name,
        'period_months': rule.period_months,
        'details': [{
            'id': d.id,
            'min_score': d.min_score,
            'max_score': d.max_score,
            'reward_per_page': d.reward_per_page
        } for d in rule.details]
    }
    return jsonify(data)
@app.route('/admin/export_rewards_excel/<int:rule_id>/<path:period_start>/<path:period_end>')
@login_required
def export_rewards_excel(rule_id, period_start, period_end):
    """تصدير نتائج المكافآت (من النتائج المحفوظة) إلى Excel"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        flash('يرجى تثبيت مكتبة openpyxl أولاً: pip install openpyxl', 'error')
        return redirect(url_for('reward_history'))

    # تحويل التواريخ
    start_date = datetime.strptime(period_start, '%Y-%m-%d').date()
    end_date = datetime.strptime(period_end, '%Y-%m-%d').date()

    # جلب القاعدة
    rule = RewardRule.query.get_or_404(rule_id)

    # ✅ جلب النتائج المحفوظة لهذه الفترة
    calculations = RewardCalculation.query.options(joinedload(RewardCalculation.student)).filter_by(
        rule_id=rule_id,
        period_start=start_date,
        period_end=end_date
    ).order_by(RewardCalculation.student_id).all()

    if not calculations:
        flash('لا توجد نتائج محفوظة لهذه الفترة للتصدير. قم بحفظ النتائج أولاً.', 'warning')
        return redirect(url_for('reward_history'))

    # إنشاء ملف Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'كشف المكافآت'

    header_font = Font(name='Arial', size=12, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='28A745', end_color='28A745', fill_type='solid')
    center = Alignment(horizontal='center', vertical='center')
    border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    ws.merge_cells('A1:I1')
    ws['A1'].value = f"كشف مكافآت الحفظ - {rule.name}"
    ws['A1'].font = Font(size=16, bold=True)
    ws['A1'].alignment = center

    ws.merge_cells('A2:I2')
    ws['A2'].value = f"الفترة: {start_date} إلى {end_date}"
    ws['A2'].alignment = center

    headers = ['م', 'الطالب', 'الصفحات', 'متوسط الحفظ (من 40)', 'درجة المرحلة', 'الدرجة النهائية', 'المكافأة/صفحة', 'الإجمالي', 'الحالة']
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = border

    for i, calc in enumerate(calculations, start=1):
        row = 4 + i

        # اسم الطالب (آمن)
        if calc.student is not None:
            student_name = calc.student.full_name
        else:
            s = Student.query.get(calc.student_id)
            student_name = s.full_name if s else f"طالب غير موجود (ID: {calc.student_id})"

        status = 'مدفوعة' if calc.is_paid else 'غير مدفوعة'

        values = [
            i,
            student_name,
            calc.total_pages,
            round(calc.avg_memorization_score or 0, 2),
            f"{calc.phase_score}/80",
            calc.final_score,
            f"{calc.reward_per_page} ريال",
            calc.total_reward,
            status
        ]

        for col, v in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=v)
            cell.alignment = center
            cell.border = border
            if col == 2:
                cell.alignment = Alignment(horizontal='right', vertical='center')

    total_row = 5 + len(calculations)
    ws.cell(row=total_row, column=7, value='الإجمالي:').font = Font(bold=True)
    ws.cell(row=total_row, column=8, value=sum(float(c.total_reward or 0) for c in calculations)).font = Font(bold=True)

    widths = [5, 30, 10, 18, 12, 12, 14, 12, 12]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    from io import BytesIO
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"mokafaat_{rule.name}_{start_date}_to_{end_date}.xlsx".replace(' ', '_')
    response = make_response(bio.getvalue())
    response.headers['Content-Disposition'] = f"attachment; filename={filename}"
    response.headers['Content-type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return response

@app.route('/admin/export_all_rewards_excel')
@login_required
def export_all_rewards_excel():
    """تصدير جميع سجلات المكافآت إلى Excel"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        flash('يرجى تثبيت مكتبة openpyxl أولاً: pip install openpyxl', 'error')
        return redirect(url_for('reward_history'))

    # جلب جميع الحسابات
    calculations = RewardCalculation.query.order_by(
        RewardCalculation.calculation_date.desc(),
        RewardCalculation.student_id
    ).all()

    if not calculations:
        flash('لا توجد بيانات للتصدير', 'warning')
        return redirect(url_for('reward_history'))

    # إنشاء ملف Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "سجل المكافآت الكامل"

    # تعريف التنسيقات
    title_font = Font(name='Arial', size=16, bold=True, color='FFFFFF')
    header_font = Font(name='Arial', size=12, bold=True, color='FFFFFF')
    normal_font = Font(name='Arial', size=11)

    header_fill = PatternFill(start_color='007BFF', end_color='007BFF', fill_type='solid')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # عنوان التقرير
    ws.merge_cells('A1:K1')
    title_cell = ws['A1']
    title_cell.value = "سجل المكافآت الكامل - أشبال القرآن"
    title_cell.font = title_font
    title_cell.fill = header_fill
    title_cell.alignment = Alignment(horizontal='center')

    # تاريخ التقرير
    ws.merge_cells('A2:K2')
    date_cell = ws['A2']
    date_cell.value = f"تاريخ التقرير: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    date_cell.font = normal_font
    date_cell.alignment = Alignment(horizontal='center')

    # رأس الجدول
    headers = ['م', 'تاريخ الحساب', 'الطالب', 'الفترة من', 'الفترة إلى', 
               'القاعدة', 'الصفحات', 'متوسط الحفظ', 'درجة المرحلة',
               'الدرجة النهائية', 'المكافأة', 'الحالة']

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = border

    # بيانات الجدول
    for row, calc in enumerate(calculations, 5):
        # الرقم
        ws.cell(row=row, column=1, value=row-4).alignment = Alignment(horizontal='center')

        # تاريخ الحساب
        ws.cell(row=row, column=2, value=calc.calculation_date.strftime('%Y-%m-%d')).alignment = Alignment(horizontal='center')

        # اسم الطالب
        student_name = calc.student.full_name if calc.student is not None else (Student.query.get(calc.student_id).full_name if Student.query.get(calc.student_id) else f'طالب غير موجود (ID: {calc.student_id})')
        ws.cell(row=row, column=3, value=student_name)

        # الفترة من
        ws.cell(row=row, column=4, value=calc.period_start.strftime('%Y-%m-%d')).alignment = Alignment(horizontal='center')

        # الفترة إلى
        ws.cell(row=row, column=5, value=calc.period_end.strftime('%Y-%m-%d')).alignment = Alignment(horizontal='center')

        # القاعدة
        ws.cell(row=row, column=6, value=calc.rule.name if calc.rule else 'محذوفة')

        # الصفحات
        ws.cell(row=row, column=7, value=calc.total_pages).alignment = Alignment(horizontal='center')

        # متوسط الحفظ
        ws.cell(row=row, column=8, value=round(calc.avg_memorization_score, 2)).alignment = Alignment(horizontal='center')

        # درجة المرحلة
        ws.cell(row=row, column=9, value=f"{calc.phase_score}/80").alignment = Alignment(horizontal='center')

        # الدرجة النهائية
        ws.cell(row=row, column=10, value=calc.final_score).alignment = Alignment(horizontal='center')

        # المكافأة
        reward_cell = ws.cell(row=row, column=11, value=calc.total_reward)
        reward_cell.alignment = Alignment(horizontal='center')
        reward_cell.font = Font(bold=True)

        # الحالة
        status = 'مدفوعة' if calc.is_paid else 'غير مدفوعة'
        status_cell = ws.cell(row=row, column=12, value=status)
        status_cell.alignment = Alignment(horizontal='center')
        if calc.is_paid:
            status_cell.font = Font(color='28A745', bold=True)
        else:
            status_cell.font = Font(color='DC3545', bold=True)

        # إضافة الحدود
        for col in range(1, 13):
            ws.cell(row=row, column=col).border = border

    # إجمالي المكافآت
    total_row = len(calculations) + 5
    ws.merge_cells(f'A{total_row}:J{total_row}')
    total_label = ws.cell(row=total_row, column=1, value="الإجمالي الكلي:")
    total_label.font = Font(bold=True)

    total_value = ws.cell(row=total_row, column=11, value=sum(c.total_reward for c in calculations))
    total_value.font = Font(bold=True, size=12)
    total_value.alignment = Alignment(horizontal='center')

    # ضبط عرض الأعمدة
    column_widths = [5, 12, 25, 12, 12, 15, 8, 12, 12, 12, 12, 10]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # حفظ الملف
    from io import BytesIO
    excel_file = BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)

    filename = f"kashf_mokafaat_kamel_{datetime.now().strftime('%Y-%m-%d')}.xlsx"

    response = make_response(excel_file.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return response


# ------------------ مسارات الطالب (التقارير اليومية) ------------------
@app.route('/student/plan/<int:plan_id>')
@login_required
def view_plan_details(plan_id):
    plan = WeeklyPlan.query.get_or_404(plan_id)
    if isinstance(current_user, Student):
        if plan.student_id != current_user.id:
            flash('غير مصرح', 'error')
            return redirect(url_for('student_dashboard'))
        user_type = 'student'
    elif isinstance(current_user, Teacher):
        user_type = 'teacher'
    elif isinstance(current_user, Admin):
        user_type = 'admin'
    else:
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    reports = DailyReport.query.filter_by(plan_id=plan.id).order_by(DailyReport.report_date).all()
    evaluations = plan.evaluations
    return render_template('view_plan_details.html',
                          plan=plan,
                          reports=reports,
                          evaluations=evaluations,
                          user_type=user_type)
# تعريف التوقيت المحلي لعمان (UTC+4)
OMAN_TZ = pytz.timezone('Asia/Muscat')

@app.route('/student/plan/<int:plan_id>/add_report', methods=['GET', 'POST'])
@login_required
def add_daily_report(plan_id):
    if not isinstance(current_user, Student):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))
    plan = WeeklyPlan.query.get_or_404(plan_id)
    if plan.student_id != current_user.id:
        flash('غير مصرح', 'error')
        return redirect(url_for('student_dashboard'))

    if plan.plan_type == 'قصيدة':
        flash('مقررات القصائد لا تحتاج تقارير يومية', 'error')
        return redirect(url_for('view_plan_details', plan_id=plan.id))

    # استخدام توقيت سلطنة عمان (GMT+4)
    oman_tz = pytz.timezone('Asia/Muscat')
    now_oman = datetime.now(oman_tz)
    today_oman = now_oman.date()

    allowed_dates = [today_oman]
    yesterday_oman = today_oman - timedelta(days=1)

    # الحصول على وقت القطع من الإعدادات
    cutoff_hour = get_cutoff_hour()

    # إذا كانت الساعة الحالية أقل من وقت القطع، يمكن إرسال تقرير الأمس
    if now_oman.hour < cutoff_hour:
        allowed_dates.append(yesterday_oman)

    if request.method == 'POST':
        report_date_str = request.form['report_date']
        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
        if report_date not in allowed_dates:
            flash('التاريخ غير مسموح به', 'error')
            return redirect(url_for('add_daily_report', plan_id=plan.id))

        existing = DailyReport.query.filter_by(plan_id=plan.id, report_date=report_date).first()
        if existing:
            return redirect(url_for('edit_daily_report', report_id=existing.id))

        weekdays = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']
        day_name = weekdays[report_date.weekday()]

        # التحقق من نوع اليوم (خميس/جمعة أم لا)
        is_weekend = report_date.weekday() in [3, 4]  # 3=خميس, 4=جمعة

        if plan.plan_type == 'حفظ':
            if is_weekend:
                # أيام الخميس والجمعة: استخدام الحقول النصية فقط
                phase_memo = request.form.get('phase_memorization', '')
                prev_memo = request.form.get('previous_memorization', '')

                report = DailyReport(
                    plan_id=plan.id,
                    report_date=report_date,
                    day_name=day_name,
                    listening=False,
                    recitation_mastery=False,
                    listened_new=False,
                    repeated_new=False,
                    reviewed_week=False,
                    phase_memorization=phase_memo,
                    previous_memorization=prev_memo,
                    recitation_done=False,
                    recitation_text='',
                    recitation_notes=''
                )
            else:
                # الأيام العادية
                listening = 'listening' in request.form
                recitation_mastery = 'recitation_mastery' in request.form
                listened_new = 'listened_new' in request.form
                repeated_new = 'repeated_new' in request.form
                reviewed_week = 'reviewed_week' in request.form
                phase_memo = request.form.get('phase_memorization', '')
                prev_memo = request.form.get('previous_memorization', '')

                report = DailyReport(
                    plan_id=plan.id,
                    report_date=report_date,
                    day_name=day_name,
                    listening=listening,
                    recitation_mastery=recitation_mastery,
                    listened_new=listened_new,
                    repeated_new=repeated_new,
                    reviewed_week=reviewed_week,
                    phase_memorization=phase_memo,
                    previous_memorization=prev_memo,
                    recitation_done=False,
                    recitation_text='',
                    recitation_notes=''
                )

        elif plan.plan_type == 'سرد':
            recitation_done = 'recitation_done' in request.form
            recitation_text = request.form.get('recitation_text', '')
            recitation_notes = request.form.get('recitation_notes', '')

            report = DailyReport(
                plan_id=plan.id,
                report_date=report_date,
                day_name=day_name,
                listening=False,
                recitation_mastery=False,
                listened_new=False,
                repeated_new=False,
                reviewed_week=False,
                phase_memorization='',
                previous_memorization='',
                recitation_done=recitation_done,
                recitation_text=recitation_text,
                recitation_notes=recitation_notes
            )
        else:
            # أنواع أخرى من المقررات
            report = DailyReport(
                plan_id=plan.id,
                report_date=report_date,
                day_name=day_name,
                listening=False,
                recitation_mastery=False,
                listened_new=False,
                repeated_new=False,
                reviewed_week=False,
                phase_memorization='',
                previous_memorization='',
                recitation_done=False,
                recitation_text='',
                recitation_notes=''
            )

        try:
            db.session.add(report)
            db.session.commit()
            flash('تم إضافة التقرير اليومي بنجاح', 'success')
            return redirect(url_for('student_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ أثناء حفظ التقرير: {str(e)}', 'error')
            return redirect(url_for('add_daily_report', plan_id=plan.id))

    return render_template('add_daily_report.html', plan=plan, allowed_dates=allowed_dates, now=datetime.now(pytz.timezone('Asia/Muscat')))



@app.route('/student/report/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_daily_report(report_id):
    report = DailyReport.query.get_or_404(report_id)
    plan = report.plan
    if not isinstance(current_user, Student) or plan.student_id != current_user.id:
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    if plan.plan_type == 'قصيدة':
        flash('مقررات القصائد لا تحتاج تقارير يومية', 'error')
        return redirect(url_for('view_plan_details', plan_id=plan.id))

    # استخدام توقيت سلطنة عمان (GMT+4)
    oman_tz = pytz.timezone('Asia/Muscat')
    now_oman = datetime.now(oman_tz)
    today_oman = now_oman.date()

    allowed_dates = [today_oman]
    yesterday_oman = today_oman - timedelta(days=1)

    # الحصول على وقت القطع من الإعدادات
    cutoff_hour = get_cutoff_hour()

    if now_oman.hour < cutoff_hour:
        allowed_dates.append(yesterday_oman)

    if report.report_date not in allowed_dates:
        flash('لا يمكن تعديل تقرير ليوم سابق أو لاحق', 'error')
        return redirect(url_for('student_dashboard', plan_id=plan.id))

    if request.method == 'POST':
        is_weekend = report.report_date.weekday() in [3, 4]

        if plan.plan_type == 'حفظ':
            if is_weekend:
                # أيام الخميس والجمعة
                report.listening = False
                report.recitation_mastery = False
                report.listened_new = False
                report.repeated_new = False
                report.reviewed_week = False
                report.phase_memorization = request.form.get('phase_memorization', '')
                report.previous_memorization = request.form.get('previous_memorization', '')
            else:
                # الأيام العادية
                report.listening = 'listening' in request.form
                report.recitation_mastery = 'recitation_mastery' in request.form
                report.listened_new = 'listened_new' in request.form
                report.repeated_new = 'repeated_new' in request.form
                report.reviewed_week = 'reviewed_week' in request.form
                report.phase_memorization = request.form.get('phase_memorization', '')
                report.previous_memorization = request.form.get('previous_memorization', '')

        elif plan.plan_type == 'سرد':
            report.recitation_done = 'recitation_done' in request.form
            report.recitation_text = request.form.get('recitation_text', '')
            report.recitation_notes = request.form.get('recitation_notes', '')

        db.session.commit()
        flash('تم تعديل التقرير بنجاح', 'success')
        return redirect(url_for('student_dashboard', plan_id=plan.id))

    return render_template('edit_daily_report.html', report=report)


@app.route('/teacher/view_report/<int:report_id>')
@login_required
def teacher_view_report(report_id):
    """عرض تفاصيل التقرير اليومي للمعلم"""
    report = DailyReport.query.get_or_404(report_id)
    plan = report.plan

    # التحقق من الصلاحية
    if isinstance(current_user, Teacher):
        if plan.student not in current_user.students:
            flash('غير مصرح: هذا الطالب ليس من طلابك', 'error')
            return redirect(url_for('teacher_dashboard'))
        user_type = 'teacher'
    elif isinstance(current_user, Student):
        if plan.student_id != current_user.id:
            flash('غير مصرح', 'error')
            return redirect(url_for('student_dashboard'))
        user_type = 'student'
    elif isinstance(current_user, Admin):
        user_type = 'admin'
    else:
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    return render_template('view_daily_report.html', report=report, plan=plan,
                           reports=[report], user_type=user_type, now=datetime.now())

# ------------------ مسارات تغيير كلمة المرور ------------------
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
        if request.method == 'POST':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')

            # التحقق من وجود البيانات
            if not current_password or not new_password or not confirm_password:
                flash('جميع الحقول مطلوبة', 'error')
                return redirect(url_for('change_password'))

            # التحقق من تطابق كلمة المرور الجديدة وتأكيدها
            if new_password != confirm_password:
                flash('كلمة المرور الجديدة وتأكيدها غير متطابقين', 'error')
                return redirect(url_for('change_password'))

            # التحقق من صحة كلمة المرور الحالية
            if not check_password_hash(current_user.password, current_password):
                flash('كلمة المرور الحالية غير صحيحة', 'error')
                return redirect(url_for('change_password'))

            # تحديث كلمة المرور
            current_user.password = generate_password_hash(new_password)
            db.session.commit()

            flash('تم تغيير كلمة المرور بنجاح', 'success')

            # التوجيه حسب نوع المستخدم
            if isinstance(current_user, Student):
                return redirect(url_for('student_dashboard'))
            elif isinstance(current_user, Teacher):
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('admin'))

        # عرض صفحة تغيير كلمة المرور (GET request)
        return render_template('change_password.html')
# ------------------ مسارات تقارير المتخلفين (للمسؤول فقط) ------------------




@app.route('/admin/missing-reports', methods=['GET', 'POST'])
@login_required
def missing_reports():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('reports')
    if redirect_resp:
        return redirect_resp

    teachers = Teacher.query.filter_by(is_active=True).all()
    # ترتيب الأيام حسب نظام Python weekday():
    # 0=الاثنين, 1=الثلاثاء, 2=الأربعاء, 3=الخميس, 4=الجمعة, 5=السبت, 6=الأحد
    WEEKDAYS_AR = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']

    if request.method == 'POST':
        report_date_str = request.form['report_date']
        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
        teacher_id = request.form.get('teacher_id')
        report_type = request.form.get('report_type', 'absence')

        # تحديد الطلاب
        if teacher_id:
            teacher = Teacher.query.get(teacher_id)
            students = teacher.students if teacher else []
        else:
            students = Student.query.all()

        # حساب بداية الأسبوع (أقرب يوم أحد سابق - الأحد هو 6 في نظام Python)
        days_to_sunday = (report_date.weekday() - 6) % 7
        start_of_week = report_date - timedelta(days=days_to_sunday)
        week_end = start_of_week + timedelta(days=6)

        # عرض التاريخ بشكل صحيح
        day_name = WEEKDAYS_AR[report_date.weekday()]
        week_end_day = WEEKDAYS_AR[week_end.weekday()]

        # ==================== التقرير التراكمي ====================
        if report_type == 'cumulative':
            # تحديد الأيام المراد عرضها (من الأحد إلى الجمعة فقط، 6 أيام)
            days_to_show = []
            current = start_of_week

            # الحد الأقصى للأيام المعروضة هو الجمعة (الأحد + 5 أيام = الجمعة)
            max_date = start_of_week + timedelta(days=5)  # الجمعة

            # إذا كان التاريخ المختار هو السبت أو بعد الجمعة، نعرض حتى الجمعة فقط
            if report_date > max_date:
                end_date = max_date
            else:
                end_date = report_date

            while current <= end_date:
                days_to_show.append({
                    'name': WEEKDAYS_AR[current.weekday()],
                    'date': current
                })
                current += timedelta(days=1)

            days_names = [day['name'] for day in days_to_show]
            days_dates = [day['date'] for day in days_to_show]

            cumulative_data = []

            # جلب بيانات التقييمات إذا كان اليوم المختار هو السبت
            student_evaluations = {}
            show_evaluation_column = (report_date.weekday() == 5)  # 5 = السبت

            if show_evaluation_column:
                # البحث عن المقررات الأسبوعية لهذا الأسبوع
                weekly_plans = WeeklyPlan.query.filter(
                    WeeklyPlan.start_date == start_of_week
                ).all()

                for plan in weekly_plans:
                    evaluation = Evaluation.query.filter_by(plan_id=plan.id).first()
                    if evaluation:
                        if plan.plan_type == 'حفظ':
                            max_score = 30
                            score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
                        elif plan.plan_type == 'سرد':
                            max_score = 10
                            score = evaluation.revision_score or 0
                        else:  # تقييم مرحلة
                            max_score = 80
                            score = evaluation.new_score or 0

                        student_evaluations[plan.student_id] = {
                            'evaluated': True,
                            'score': score,
                            'max_score': max_score
                        }
                    else:
                        student_evaluations[plan.student_id] = {
                            'evaluated': False,
                            'score': None,
                            'max_score': None
                        }

            # بناء بيانات كل طالب
            for student in students:
                daily_status = []
                sent_count = 0

                for day_date in days_dates:
                    # البحث عن تقرير الطالب في هذا اليوم
                    report = DailyReport.query.join(WeeklyPlan).filter(
                        WeeklyPlan.student_id == student.id,
                        DailyReport.report_date == day_date
                    ).first()

                    if report:
                        daily_status.append({'status': 'sent', 'date': day_date})
                        sent_count += 1
                    else:
                        daily_status.append({'status': 'not_sent', 'date': day_date})

                total_days = len(days_dates)
                not_sent_count = total_days - sent_count
                percentage = round((sent_count / total_days * 100), 1) if total_days > 0 else 0

                cumulative_data.append({
                    'student': student,
                    'daily_status': daily_status,
                    'days_covered': days_names,
                    'sent_count': sent_count,
                    'not_sent_count': not_sent_count,
                    'percentage': percentage
                })

            # ترتيب البيانات حسب النسبة تنازلياً (الأعلى أولاً)
            cumulative_data.sort(key=lambda x: x['percentage'], reverse=True)

            return render_template('missing_reports.html',
                                   report_type='cumulative',
                                   cumulative_data=cumulative_data,
                                   date=report_date,
                                   start_of_week=start_of_week,
                                   week_end=week_end,
                                   day_name=WEEKDAYS_AR[start_of_week.weekday()],
                                   week_end_day=week_end_day,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   is_teacher=False,
                                   show_evaluation_column=show_evaluation_column,
                                   student_evaluations=student_evaluations,
                                   now=datetime.now())

        # ==================== تقرير الغياب ====================
        elif report_type == 'absence':
            reports = DailyReport.query.filter_by(report_date=report_date).all()
            students_with_report = {r.plan.student_id for r in reports if r.plan}
            missing_students = [s for s in students if s.id not in students_with_report]
            title = "الطلاب المتخلفون"
            display_date = report_date
            display_day = day_name

            return render_template('missing_reports.html',
                                   date=display_date,
                                   week_end=week_end,
                                   day_name=display_day,
                                   week_end_day=week_end_day,
                                   missing_students=missing_students,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   report_type=report_type,
                                   title=title,
                                   is_teacher=False,
                                   now=datetime.now())

        # ==================== تقرير عدم التقييم ====================
        elif report_type == 'no_evaluation':
            # البحث عن المقررات التي تبدأ في بداية الأسبوع
            plans = WeeklyPlan.query.filter_by(start_date=start_of_week).all()
            students_without_evaluation = set()
            for plan in plans:
                if not plan.evaluations:
                    student = plan.student
                    if student in students:
                        students_without_evaluation.add(student)
            missing_students = list(students_without_evaluation)
            title = "الطلاب غير المقيمين"
            display_date = start_of_week
            display_day = WEEKDAYS_AR[start_of_week.weekday()]

            return render_template('missing_reports.html',
                                   date=display_date,
                                   week_end=week_end,
                                   day_name=display_day,
                                   week_end_day=week_end_day,
                                   missing_students=missing_students,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   report_type=report_type,
                                   title=title,
                                   is_teacher=False,
                                   now=datetime.now())

        # ==================== تقرير المقررات الأسبوعية (طلاب بدون مقرر) ====================
        elif report_type == 'missing_plans':
            # التحقق من أن التاريخ المحدد هو يوم الأحد
            if report_date.weekday() != 6:  # 6 = الأحد
                flash('تقرير المقررات الأسبوعية يتطلب اختيار يوم الأحد فقط', 'warning')
                return redirect(url_for('missing_reports'))

            # البحث عن المقررات التي تبدأ في هذا التاريخ (بداية الأسبوع)
            existing_plans = WeeklyPlan.query.filter_by(start_date=start_of_week).all()
            students_with_plan = {plan.student_id for plan in existing_plans}

            # الطلاب الذين ليس لديهم مقرر
            students_without_plan = [s for s in students if s.id not in students_with_plan]

            # ترتيب حسب الاسم
            students_without_plan.sort(key=lambda x: x.full_name)

            return render_template('missing_plans.html',
                                   students_without_plan=students_without_plan,
                                   teachers=teachers,
                                   selected_date=start_of_week,
                                   selected_teacher_id=teacher_id,
                                   is_teacher=False,
                                   now=datetime.now())

    # عرض نموذج الإدخال (GET request)
    today_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    days_until_sunday = (6 - datetime.now().weekday()) % 7
    next_sunday_str = (datetime.now() + timedelta(days=days_until_sunday)).strftime('%Y-%m-%d')
    return render_template('missing_reports_form.html', teachers=teachers, today=today_str, next_sunday=next_sunday_str, is_teacher=False)




@app.route('/teacher/missing-reports', methods=['GET', 'POST'])
@login_required
def teacher_missing_reports():
    if not isinstance(current_user, Teacher) or not current_user.can_view_missing_reports:
        flash('غير مصرح: لا تملك صلاحية الوصول إلى هذه الصفحة', 'error')
        return redirect(url_for('teacher_dashboard'))

    teachers = Teacher.query.filter_by(is_active=True).all()
    WEEKDAYS_AR = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']

    if request.method == 'POST':
        report_date_str = request.form['report_date']
        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
        teacher_id = request.form.get('teacher_id')
        report_type = request.form.get('report_type', 'absence')

        # تحديد الطلاب
        if teacher_id:
            selected_teacher = Teacher.query.get(teacher_id)
            students = selected_teacher.students if selected_teacher else []
        else:
            students = current_user.students

        # حساب بداية الأسبوع
        days_to_sunday = (report_date.weekday() - 6) % 7
        start_of_week = report_date - timedelta(days=days_to_sunday)
        week_end = start_of_week + timedelta(days=6)
        day_name = WEEKDAYS_AR[report_date.weekday()]
        week_end_day = WEEKDAYS_AR[week_end.weekday()]

        # ==================== التقرير التراكمي ====================
        if report_type == 'cumulative':
            # ... الكود الموجود للتقرير التراكمي ...
            days_to_show = []
            current = start_of_week
            max_date = start_of_week + timedelta(days=5)

            if report_date > max_date:
                end_date = max_date
            else:
                end_date = report_date

            while current <= end_date:
                days_to_show.append({
                    'name': WEEKDAYS_AR[current.weekday()],
                    'date': current
                })
                current += timedelta(days=1)

            days_names = [day['name'] for day in days_to_show]
            days_dates = [day['date'] for day in days_to_show]

            cumulative_data = []
            student_evaluations = {}
            show_evaluation_column = (report_date.weekday() == 5)

            if show_evaluation_column:
                weekly_plans = WeeklyPlan.query.filter(
                    WeeklyPlan.start_date == start_of_week
                ).all()

                for plan in weekly_plans:
                    evaluation = Evaluation.query.filter_by(plan_id=plan.id).first()
                    if evaluation:
                        if plan.plan_type == 'حفظ':
                            max_score = 30
                            score = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
                        elif plan.plan_type == 'سرد':
                            max_score = 10
                            score = evaluation.revision_score or 0
                        else:
                            max_score = 80
                            score = evaluation.new_score or 0

                        student_evaluations[plan.student_id] = {
                            'evaluated': True,
                            'score': score,
                            'max_score': max_score
                        }
                    else:
                        student_evaluations[plan.student_id] = {
                            'evaluated': False,
                            'score': None,
                            'max_score': None
                        }

            for student in students:
                daily_status = []
                sent_count = 0

                for day_date in days_dates:
                    report = DailyReport.query.join(WeeklyPlan).filter(
                        WeeklyPlan.student_id == student.id,
                        DailyReport.report_date == day_date
                    ).first()

                    if report:
                        daily_status.append({'status': 'sent', 'date': day_date})
                        sent_count += 1
                    else:
                        daily_status.append({'status': 'not_sent', 'date': day_date})

                total_days = len(days_dates)
                not_sent_count = total_days - sent_count
                percentage = round((sent_count / total_days * 100), 1) if total_days > 0 else 0

                cumulative_data.append({
                    'student': student,
                    'daily_status': daily_status,
                    'days_covered': days_names,
                    'sent_count': sent_count,
                    'not_sent_count': not_sent_count,
                    'percentage': percentage
                })

            cumulative_data.sort(key=lambda x: x['percentage'], reverse=True)

            return render_template('missing_reports.html',
                                   report_type='cumulative',
                                   cumulative_data=cumulative_data,
                                   date=report_date,
                                   start_of_week=start_of_week,
                                   week_end=week_end,
                                   day_name=WEEKDAYS_AR[start_of_week.weekday()],
                                   week_end_day=week_end_day,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   is_teacher=True,
                                   show_evaluation_column=show_evaluation_column,
                                   student_evaluations=student_evaluations,
                                   now=datetime.now())

        # ==================== تقرير الغياب ====================
        elif report_type == 'absence':
            reports = DailyReport.query.filter_by(report_date=report_date).all()
            students_with_report = {r.plan.student_id for r in reports if r.plan}
            missing_students = [s for s in students if s.id not in students_with_report]
            title = "الطلاب المتخلفون"
            display_date = report_date
            display_day = day_name

            return render_template('missing_reports.html',
                                   date=display_date,
                                   week_end=week_end,
                                   day_name=display_day,
                                   week_end_day=week_end_day,
                                   missing_students=missing_students,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   report_type=report_type,
                                   title=title,
                                   is_teacher=True,
                                   now=datetime.now())

        # ==================== تقرير عدم التقييم ====================
        elif report_type == 'no_evaluation':
            plans = WeeklyPlan.query.filter_by(start_date=start_of_week).all()
            students_without_evaluation = set()
            for plan in plans:
                if not plan.evaluations:
                    student = plan.student
                    if student in students:
                        students_without_evaluation.add(student)
            missing_students = list(students_without_evaluation)
            title = "الطلاب غير المقيمين"
            display_date = start_of_week
            display_day = WEEKDAYS_AR[start_of_week.weekday()]

            return render_template('missing_reports.html',
                                   date=display_date,
                                   week_end=week_end,
                                   day_name=display_day,
                                   week_end_day=week_end_day,
                                   missing_students=missing_students,
                                   teachers=teachers,
                                   selected_teacher=teacher_id,
                                   report_type=report_type,
                                   title=title,
                                   is_teacher=True,
                                   now=datetime.now())

        # ==================== تقرير المقررات الأسبوعية (طلاب بدون مقرر) ====================
        elif report_type == 'missing_plans':
            # التحقق من أن التاريخ المحدد هو يوم الأحد
            if report_date.weekday() != 6:  # 6 = الأحد
                flash('تقرير المقررات الأسبوعية يتطلب اختيار يوم الأحد فقط', 'warning')
                return redirect(url_for('teacher_missing_reports'))

            # البحث عن المقررات التي تبدأ في هذا التاريخ
            existing_plans = WeeklyPlan.query.filter_by(start_date=start_of_week).all()
            students_with_plan = {plan.student_id for plan in existing_plans}

            # الطلاب الذين ليس لديهم مقرر
            students_without_plan = [s for s in students if s.id not in students_with_plan]
            students_without_plan.sort(key=lambda x: x.full_name)

            # طباعة للتأكد (للتجربة)
            print(f"[Teacher] عدد الطلاب بدون مقرر: {len(students_without_plan)}")
            print(f"[Teacher] التاريخ المختار: {start_of_week}")

            return render_template('missing_plans_report.html',
                                   students_without_plan=students_without_plan,
                                   teachers=teachers,
                                   selected_date=start_of_week,
                                   selected_teacher_id=teacher_id,
                                   is_teacher=True,
                                   now=datetime.now())

    # عرض نموذج الإدخال (GET request)
    today_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    days_until_sunday = (6 - datetime.now().weekday()) % 7
    next_sunday_str = (datetime.now() + timedelta(days=days_until_sunday)).strftime('%Y-%m-%d')
    return render_template('missing_reports_form.html', teachers=teachers, today=today_str, next_sunday=next_sunday_str, is_teacher=True)




# ------------------ مسارات الإنذارات ------------------
@app.route('/admin/warnings')
@login_required
def admin_warnings_simple():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('warnings')
    if redirect_resp:
        return redirect_resp
    students = Student.query.all()
    return render_template('admin_warnings_simple.html', students=students)
@app.route('/admin/update_warning/<int:student_id>', methods=['POST'])
@login_required
def update_warning(student_id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('warnings')
    if redirect_resp:
        return redirect_resp
    student = Student.query.get_or_404(student_id)
    action = request.form.get('action')

    if action == 'increment':
        student.warning_count += 1
        if student.warning_count >= 3:
            student.is_suspended = True
            flash(f'تم إصدار إنذار للطالب {student.full_name}. الحساب معطل الآن.', 'warning')
        else:
            flash(f'تم إصدار إنذار للطالب {student.full_name} (الإنذار {student.warning_count}/3)', 'success')
    elif action == 'reset':
        student.warning_count = 0
        student.is_suspended = False
        flash(f'تم إعادة تعيين الإنذارات للطالب {student.full_name}', 'success')
    elif action == 'activate':
        student.is_suspended = False
        student.warning_count = 0
        flash(f'تم تنشيط حساب الطالب {student.full_name}', 'success')
    elif action == 'suspend':
        student.is_suspended = True
        flash(f'تم تعطيل حساب الطالب {student.full_name}', 'warning')

    db.session.commit()
    return redirect(url_for('admin_warnings_simple'))

# ------------------ مسارات إدارة المستخدمين الإداريين ------------------
@app.route('/admin/add_admin', methods=['POST'])
@login_required
def add_admin():
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    username = request.form['username']
    password = request.form['password']
    existing = Admin.query.filter_by(username=username).first()
    if existing:
        flash('اسم المستخدم موجود بالفعل', 'error')
        return redirect(url_for('admin', tab='admins'))

    can_access_students = 'can_access_students' in request.form
    can_edit_students = 'can_edit_students' in request.form
    can_access_teachers = 'can_access_teachers' in request.form
    can_edit_teachers = 'can_edit_teachers' in request.form
    can_access_reports = 'can_access_reports' in request.form
    can_access_warnings = 'can_access_warnings' in request.form
    can_access_backup = 'can_access_backup' in request.form
    can_restore_backup = 'can_restore_backup' in request.form
    is_super_admin = 'is_super_admin' in request.form and current_user.is_super_admin

    new_admin = Admin(
        username=username,
        password=generate_password_hash(password),
        can_access_students=can_access_students,
        can_edit_students=can_edit_students,
        can_access_teachers=can_access_teachers,
        can_edit_teachers=can_edit_teachers,
        can_access_reports=can_access_reports,
        can_access_warnings=can_access_warnings,
        can_access_backup=can_access_backup,
        can_restore_backup=can_restore_backup,
        is_super_admin=is_super_admin
    )
    db.session.add(new_admin)
    db.session.commit()
    flash('تم إضافة المستخدم بنجاح', 'success')
    return redirect(url_for('admin', tab='admins'))
@app.route('/admin/edit_admin/<int:id>', methods=['POST'])
@login_required
def edit_admin(id):
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    admin = Admin.query.get_or_404(id)
    if admin.is_super_admin and current_user.id != admin.id:
        flash('لا يمكن تعديل مستخدم مشرف كامل', 'error')
        return redirect(url_for('admin', tab='admins'))

    admin.username = request.form['username']
    if request.form.get('password'):
        admin.password = generate_password_hash(request.form['password'])

    admin.can_access_students = 'can_access_students' in request.form
    admin.can_edit_students = 'can_edit_students' in request.form
    admin.can_access_teachers = 'can_access_teachers' in request.form
    admin.can_edit_teachers = 'can_edit_teachers' in request.form
    admin.can_access_reports = 'can_access_reports' in request.form
    admin.can_access_warnings = 'can_access_warnings' in request.form
    admin.can_access_backup = 'can_access_backup' in request.form
    admin.can_restore_backup = 'can_restore_backup' in request.form
    if 'is_super_admin' in request.form and current_user.is_super_admin:
        admin.is_super_admin = True
    else:
        admin.is_super_admin = False

    db.session.commit()
    flash('تم تعديل المستخدم بنجاح', 'success')
    return redirect(url_for('admin', tab='admins'))
@app.route('/admin/delete_admin/<int:id>')
@login_required
def delete_admin(id):
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    admin = Admin.query.get_or_404(id)
    if admin.id == current_user.id:
        flash('لا يمكن حذف المستخدم الحالي', 'error')
        return redirect(url_for('admin', tab='admins'))
    if admin.is_super_admin:
        flash('لا يمكن حذف مستخدم مشرف كامل', 'error')
        return redirect(url_for('admin', tab='admins'))
    db.session.delete(admin)
    db.session.commit()
    flash('تم حذف المستخدم بنجاح', 'success')
    return redirect(url_for('admin', tab='admins'))

# ------------------ إدارة القصائد (مقررات الحفظ) ------------------
@app.route('/admin/poems', methods=['GET', 'POST'])
@login_required
def admin_poems():
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    if request.method == 'POST':
        try:
            poem_number = int(request.form.get('poem_number') or 0)
            poem_title = request.form.get('poem_title', '').strip()
            poet = request.form.get('poet', '').strip()
            verse_count = int(request.form.get('verse_count') or 0)
            if verse_count <= 0:
                flash('عدد الأبيات يجب أن يكون أكبر من صفر', 'error')
                return redirect(url_for('admin_poems'))
            db.session.add(Poem(
                poem_number=poem_number,
                poem_title=poem_title,
                poet=poet,
                verse_count=verse_count
            ))
            db.session.commit()
            flash('تم إضافة القصيدة بنجاح', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {str(e)}', 'error')
        return redirect(url_for('admin_poems'))

    poems = Poem.query.order_by(Poem.poem_number.asc(), Poem.id.asc()).all()
    # عدد المقررات المرتبطة بكل قصيدة
    usages = {}
    for p in poems:
        usages[p.id] = WeeklyPlan.query.filter_by(poem_id=p.id).count()
    return render_template('admin_poems.html', poems=poems, usages=usages)


@app.route('/admin/poems/edit/<int:poem_id>', methods=['POST'])
@login_required
def edit_admin_poem(poem_id):
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    poem = Poem.query.get_or_404(poem_id)
    try:
        poem.poem_number = int(request.form.get('poem_number') or 0)
        poem.poem_title = request.form.get('poem_title', '').strip()
        poem.poet = request.form.get('poet', '').strip()
        poem.verse_count = int(request.form.get('verse_count') or 0)
        if poem.verse_count <= 0:
            flash('عدد الأبيات يجب أن يكون أكبر من صفر', 'error')
            return redirect(url_for('admin_poems'))
        db.session.commit()
        flash('تم تعديل القصيدة بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ: {str(e)}', 'error')
    return redirect(url_for('admin_poems'))


@app.route('/admin/poems/delete/<int:poem_id>', methods=['POST'])
@login_required
def delete_admin_poem(poem_id):
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    poem = Poem.query.get_or_404(poem_id)
    usage = WeeklyPlan.query.filter_by(poem_id=poem.id).count()
    if usage:
        flash(f'لا يمكن حذف القصيدة لأنها مرتبطة بـ {usage} مقرر', 'error')
        return redirect(url_for('admin_poems'))
    db.session.delete(poem)
    db.session.commit()
    flash('تم حذف القصيدة بنجاح', 'success')
    return redirect(url_for('admin_poems'))

# ------------------ النسخ الاحتياطي ------------------
# ترتيب الجداول: من الآباء نحو الأبناء — إلزامي لأن إدراج العلاقات
# يفشل إن لم يكن الأب موجوداً. المفاتيح القديمة (poems/plans/...) محفوظة
# كما هي حتى تبقى ملفات النسخ القديمة قابلة للاستعادة.
BACKUP_MODELS = (
    # (المفتاح في ملف JSON, النموذج, أعمدة التاريخ التي تحتاج تحويل)
    ('teachers',             Teacher,             ()),
    ('students',             Student,             ()),
    ('admins',               Admin,               ()),
    ('poems',                Poem,                ()),
    ('reward_rules',         RewardRule,          ('created_at',)),
    ('system_settings',      SystemSettings,      ('updated_at',)),
    ('system_setting',       SystemSetting,       ('updated_at',)),
    ('teacher_student',      None,                ()),   # جدول وسيط يُدرَج بالتتابع
    ('plans',                WeeklyPlan,          ('start_date',)),
    ('evaluations',          Evaluation,          ('evaluation_date',)),
    ('reports',              DailyReport,         ('report_date',)),
    ('warnings',             Warning,             ('date',)),
    ('phase_evaluations',    PhaseEvaluation,     ('evaluation_date',)),
    ('reward_rule_details',  RewardRuleDetail,    ()),
    ('student_pages',        StudentPages,        ('last_updated',)),
    ('student_period_pages', StudentPeriodPages,  ('period_start', 'period_end', 'created_at')),
    ('student_points',       StudentPoints,       ('current_cycle_start', 'cycle_end_date', 'last_updated')),
    ('reward_calculations',  RewardCalculation,   ('calculation_date', 'period_start', 'period_end', 'paid_date')),
    ('points_log',           PointsLog,           ('evaluation_date', 'created_at', 'cycle_start_date')),
)

# ترتيب الحذف: من الأبناء نحو الآباء لتفادي مخالفات المفاتيح الأجنبية
BACKUP_DELETE_ORDER = (
    PointsLog, DailyReport, Evaluation, RewardRuleDetail, RewardCalculation,
    StudentPeriodPages, StudentPages, StudentPoints, PhaseEvaluation,
    WeeklyPlan, Warning, RewardRule, Poem, Teacher, Student, Admin,
    SystemSetting, SystemSettings,
)


def _coerce_backup_dates(record, cols):
    """تحويل نصوص ISO إلى كائنات date/datetime قبل إدخالها في SQLAlchemy"""
    import datetime as dtmod
    for col in cols:
        if col in record and isinstance(record[col], str):
            try:
                record[col] = dtmod.date.fromisoformat(record[col])
            except (ValueError, TypeError):
                try:
                    record[col] = dtmod.datetime.fromisoformat(record[col])
                except (ValueError, TypeError):
                    pass


def build_full_backup():
    """نسخة احتياطية شاملة لكل جداول النظام — تُستخدم في /backup ولترحيل supabase_schema.sql"""
    backup_data = {
        key: [model_to_dict(obj) for obj in model.query.all()]
        for key, model, _cols in BACKUP_MODELS if model is not None
    }
    rows = db.session.execute(teacher_student.select()).fetchall()
    backup_data['teacher_student'] = [
        {'teacher_id': r.teacher_id, 'student_id': r.student_id} for r in rows
    ]
    backup_data['_meta'] = {
        'version': 2,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'tables': sorted(k for k in backup_data if k != '_meta')
    }
    return backup_data


@app.route('/backup', methods=['POST'])
@login_required
def backup():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('backup', require_edit=False)
    if redirect_resp:
        return redirect_resp

    backup_data = build_full_backup()

    current_time = datetime.now().strftime('%Y-%m-%d')
    filename = f"quran_backup_{current_time}.json"

    output = make_response(json.dumps(backup_data, ensure_ascii=False, indent=2))
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "application/json"
    return output
@app.route('/restore', methods=['POST'])
@login_required
def restore():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('backup', require_edit=True)
    if redirect_resp:
        return redirect_resp

    file = request.files.get('backup_file')
    if not file:
        flash('لم يتم رفع ملف', 'error')
        return redirect(url_for('admin', tab='data'))

    try:
        data = json.load(file)
        if not isinstance(data, dict) or not data:
            raise ValueError('ملف النسخة الاحتياطية فارغ أو غير صالح')

        # حماية حساب المشرف: النسخ القديمة (v1) لا تحتوي جدول المشرفين إطلاقاً،
        # فلولا هذا السطر لحوّلت الاستعادة كل المشرفين إلى جدول فارغ وأقفلت لوحة الأدمن.
        admin_rows = list(data.get('admins') or [])
        backup_admin_ids = {r.get('id') for r in admin_rows}
        if isinstance(current_user, Admin) and current_user.id not in backup_admin_ids:
            admin_rows.append(model_to_dict(current_user))
        data['admins'] = admin_rows

        # 1) تفريغ كل الجداول — الأبناء أولاً.
        #    ON DELETE CASCADE في قاعدة البيانات (PostgreSQL/Supabase) يتولّى
        #    حذف أي جدول تابع لم يُذكر هنا.
        db.session.execute(teacher_student.delete())
        for model in BACKUP_DELETE_ORDER:
            model.query.delete()
        db.session.commit()
        # تفريغ خريطة الهوية وإلا تعارضت السجلات الجديدة مع كائنات الجلسة القديمة
        db.session.expunge_all()

        # 2) إدراج البيانات — الآباء أولاً (ترتيب BACKUP_MODELS)
        for key, model, date_cols in BACKUP_MODELS:
            if model is None:
                links = data.get(key) or []
                if links:
                    db.session.execute(teacher_student.insert(), links)
                continue
            records = data.get(key) or []
            # weekly_plan يحوي مرجعاً ذاتياً (rolled_from_id). الرئيسية تُنشأ
            # دائماً بعد المصدر لأنها نسخة لاحقة، فترتيبها تصاعدياً حسب id
            # يضمن وجود الأصل قبل النسخة. بدون ذلك قد تفشل FK عند الاستعادة.
            if key == 'plans':
                records = sorted(records, key=lambda r: (r.get('id') or 0))
            for record in records:
                _coerce_backup_dates(record, date_cols)
                db.session.add(model(**record))
            db.session.flush()

        db.session.commit()
        fix_all_sequences()
        # شبكة أمان: لا يبقى النظام بلا مشرف كامل مهما كان محتوى النسخة
        if not Admin.query.filter_by(is_super_admin=True).first():
            create_default_admin()
        flash('تم استعادة النسخة الاحتياطية بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        import traceback
        tb = traceback.format_exc()
        print(f"=== RESTORE ERROR ===")
        print(f"Error: {str(e)}")
        print(f"Traceback: {tb}")
        flash(f'حدث خطأ أثناء الاستعادة: {str(e)}', 'error')

    return redirect(url_for('admin', tab='data'))
@app.route('/reset_database', methods=['POST'])
@login_required
def reset_database():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('backup', require_edit=True)
    if redirect_resp:
        return redirect_resp

    try:
        db.session.execute(teacher_student.delete())
        for model in BACKUP_DELETE_ORDER:
            model.query.delete()
        db.session.commit()
        fix_all_sequences()
        flash('تم إعادة تهيئة قاعدة البيانات بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ: {str(e)}', 'error')

    return redirect(url_for('admin', tab='data'))

# ------------------ دوال التصدير والاستيراد القديمة (للتوافق) ------------------
@app.route('/export/<string:table>')
@login_required
def export_table(table):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('backup')
    if redirect_resp:
        return redirect_resp
    if table == 'teacher_student':
        return export_teacher_student()
    models = {
        'students': Student,
        'teachers': Teacher,
        'plans': WeeklyPlan,
        'evaluations': Evaluation,
        'reports': DailyReport,
        'poems': Poem
    }
    if table not in models:
        flash('الجدول غير موجود', 'error')
        return redirect(url_for('admin'))
    model = models[table]
    data = model.query.all()
    si = StringIO()
    cw = csv.writer(si)
    columns = [col.name for col in model.__table__.columns]
    cw.writerow(columns)
    for row in data:
        cw.writerow([getattr(row, col) for col in columns])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename={table}.csv"
    output.headers["Content-type"] = "text/csv"
    return output
@app.route('/import/<string:table>', methods=['POST'])
@login_required
def import_table(table):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))
    redirect_resp = check_admin_permission('backup', require_edit=True)
    if redirect_resp:
        return redirect_resp
    if table == 'teacher_student':
        file = request.files.get('file')
        if not file:
            flash('لم يتم رفع ملف', 'error')
            return redirect(url_for('admin', tab='data'))
        try:
            success, message = import_teacher_student(file)
            if success:
                flash(message, 'success')
            else:
                flash(message, 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ أثناء استيراد العلاقات: {str(e)}', 'error')
        return redirect(url_for('admin', tab='data'))
    models = {
        'students': Student,
        'teachers': Teacher,
        'plans': WeeklyPlan,
        'evaluations': Evaluation,
        'reports': DailyReport,
        'poems': Poem
    }
    if table not in models:
        flash('الجدول غير موجود', 'error')
        return redirect(url_for('admin'))
    model = models[table]
    file = request.files.get('file')
    if not file:
        flash('لم يتم رفع ملف', 'error')
        return redirect(url_for('admin', tab='data'))
    try:
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        header = next(csv_input)
        imported = 0
        for row in csv_input:
            data = dict(zip(header, row))
            for key, value in data.items():
                if value == '':
                    data[key] = None
            bool_fields = ['new_pass', 'previous_pass', 'revision_pass', 'listening', 'recitation_mastery',
                           'listened_new', 'repeated_new', 'reviewed_week', 'recitation_done',
                           'is_active', 'is_suspended', 'can_view_missing_reports']
            for field in bool_fields:
                if field in data and data[field] is not None:
                    data[field] = data[field].lower() in ['1', 'true', 'yes', 'on']
            if 'warning_count' in data and data['warning_count'] is not None:
                try:
                    data['warning_count'] = int(data['warning_count'])
                except:
                    pass
            date_fields = ['start_date', 'evaluation_date', 'report_date']
            for field in date_fields:
                if field in data and data[field]:
                    try:
                        data[field] = datetime.strptime(data[field], '%Y-%m-%d').date()
                    except:
                        pass
            numeric_fields = ['new_score', 'recent_score', 'previous_score', 'revision_score']
            for field in numeric_fields:
                if field in data and data[field] is not None:
                    try:
                        data[field] = int(data[field])
                    except:
                        data[field] = 0
            # لا نحذف id
            new_row = model(**data)
            db.session.add(new_row)
            imported += 1
        db.session.commit()
        fix_all_sequences()
        flash(f'تم استيراد {imported} سجل بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء الاستيراد: {str(e)}', 'error')
    return redirect(url_for('admin', tab='data'))

# ------------------ فلتر تحويل رقم الهاتف إلى رقم واتساب ------------------
@app.template_filter('phone_to_whatsapp')
def phone_to_whatsapp(phone):
    if not phone:
        return None
    # إزالة كل ما ليس رقماً
    cleaned = ''.join(filter(str.isdigit, phone))
    # إذا كان الرقم يبدأ بصفر، نستبدله برمز عمان 968
    if cleaned.startswith('0'):
        cleaned = '968' + cleaned[1:]
    elif not cleaned.startswith('968'):
        # إذا لم يكن به رمز البلد، نفترض أنه عمان ونضيف 968
        cleaned = '968' + cleaned
    return cleaned

# ------------------ دالة إنشاء مستخدم إدارة افتراضي ------------------
def add_missing_columns():
    """إضافة الأعمدة المفقودة إلى جدول weekly_plan"""
    try:
        # قائمة الأعمدة المراد إضافتها
        columns_to_add = [
            ("sard_subtype", "VARCHAR(20)"),
            ("revision_notes", "TEXT")
        ]

        for col_name, col_type in columns_to_add:
            try:
                # التحقق من وجود العمود وإضافته إذا لم يكن موجوداً
                check_sql = text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='weekly_plan' AND column_name=:col_name
                """)
                result = db.session.execute(check_sql, {"col_name": col_name}).fetchone()

                if not result:
                    # إضافة العمود
                    alter_sql = text(f"ALTER TABLE weekly_plan ADD COLUMN {col_name} {col_type}")
                    db.session.execute(alter_sql)
                    print(f"✅ تم إضافة عمود {col_name}")
                else:
                    print(f"ℹ️ عمود {col_name} موجود بالفعل")
            except Exception as e:
                print(f"⚠️ خطأ في إضافة عمود {col_name}: {e}")

        db.session.commit()
        print("✅ تم الانتهاء من تحديث قاعدة البيانات")
    except Exception as e:
        print(f"❌ خطأ عام: {e}")
        db.session.rollback()


def create_default_admin():
    admin = Admin.query.filter_by(username='admin').first()
    if not admin:
        admin = Admin(
            username='admin',
            password=generate_password_hash('1'),
            is_super_admin=True,
            can_access_students=True,
            can_edit_students=True,
            can_access_teachers=True,
            can_edit_teachers=True,
            can_access_reports=True,
            can_access_warnings=True,
            can_access_backup=True,
            can_restore_backup=True
        )
        db.session.add(admin)
        db.session.commit()
    else:
        if not admin.is_super_admin:
            admin.is_super_admin = True
            db.session.commit()


@app.route('/admin/recalculate_all_points', methods=['POST'])
@login_required
def recalculate_all_points():
    """إعادة حساب نقاط جميع الطلاب بالمنطق الجديد"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    try:
        students = Student.query.all()
        updated = 0

        for student in students:
            # جلب جميع تقييمات الطالب
            plans = WeeklyPlan.query.filter_by(student_id=student.id).options(
                joinedload(WeeklyPlan.evaluations)
            ).all()

            total_points = 0
            cycle_points = 0

            for plan in plans:
                if plan.evaluations:
                    ev = plan.evaluations[0]
                    # حساب نقاط المقرر (100 درجة كحد أقصى)
                    plan_pts = 0
                    if plan.plan_type == 'حفظ':
                        if ev.new_score is not None:
                            plan_pts += ev.new_score
                        if ev.recent_score is not None:
                            plan_pts += ev.recent_score
                        if ev.previous_score is not None:
                            plan_pts += ev.previous_score
                    elif plan.plan_type == 'سرد':
                        if ev.revision_score is not None:
                            plan_pts += ev.revision_score

                    plan_pts = min(int(plan_pts), 100)
                    total_points += plan_pts
                    cycle_points += plan_pts

            # حساب النجوم: كل 120 نقطة = نجمة (حد أقصى 5)
            star_level = min(cycle_points // 120, 5)

            # تحديث أو إنشاء سجل النقاط
            pts_record = StudentPoints.query.filter_by(student_id=student.id).first()
            if pts_record:
                pts_record.total_points = total_points
                pts_record.current_cycle_points = cycle_points
                pts_record.star_level = star_level
            else:
                pts_record = StudentPoints(
                    student_id=student.id,
                    total_points=total_points,
                    current_cycle_points=cycle_points,
                    star_level=star_level
                )
                db.session.add(pts_record)

            updated += 1

        db.session.commit()
        flash(f'✅ تم إعادة حساب نقاط {updated} طالب بنجاح', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ خطأ في إعادة الحساب: {str(e)}', 'error')

    return redirect(url_for('admin', tab='points'))


@app.route('/admin/archive_points_cycle', methods=['POST'])
@login_required
def archive_points_cycle():
    """أرشفة الدورة الحالية وبدء دورة جديدة"""
    if not isinstance(current_user, Admin) or not current_user.is_super_admin:
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    try:
        today = date.today()
        active_cycles = StudentPoints.query.filter_by(archived=False).all()

        for cycle in active_cycles:
            # أرشفة الدورة الحالية وتسجيل تاريخ انتهائها
            cycle.archived = True
            cycle.cycle_end_date = today  # سيُضاف للـ model

            # إنشاء دورة جديدة بنقاط صفر ونجوم صفر
            new_cycle = StudentPoints(
                student_id=cycle.student_id,
                total_points=cycle.total_points,    # الاحتفاظ بالنقاط التراكمية الإجمالية
                current_cycle_points=0,             # نقاط الدورة تبدأ من صفر
                star_level=0,                       # النجوم تبدأ من صفر
                current_cycle_start=today,
                archived=False
            )
            db.session.add(new_cycle)

        db.session.commit()
        flash('تم أرشفة الدورات الحالية وبدء دورة جديدة بنجاح', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ: {str(e)}', 'error')

    return redirect(url_for('admin', tab='points'))


@app.route('/admin/points_cycle_history')
@login_required
def points_cycle_history():
    """عرض الدورات السابقة المؤرشفة"""
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('admin'))

    # جلب جميع الدورات المؤرشفة مرتبة بالتاريخ
    archived_cycles = StudentPoints.query.filter_by(archived=True)\
        .order_by(StudentPoints.current_cycle_start.desc()).all()

    # تجميع الدورات حسب تاريخ البداية
    cycles_by_date = {}
    for cycle in archived_cycles:
        key = str(cycle.current_cycle_start)
        if key not in cycles_by_date:
            cycles_by_date[key] = {
                'cycle_start': cycle.current_cycle_start,
                'students': []
            }
        student = Student.query.get(cycle.student_id)
        if student:
            # جلب سجلات النقاط لهذه الدورة تحديداً
            logs = PointsLog.query.filter_by(
                student_id=cycle.student_id,
                cycle_start_date=cycle.current_cycle_start
            ).order_by(PointsLog.evaluation_date.desc()).all()

            cycles_by_date[key]['students'].append({
                'student': student,
                'total_cycle_points': cycle.current_cycle_points,
                'star_level': cycle.star_level,
                'logs': logs
            })

    # ترتيب الطلاب داخل كل دورة حسب النقاط تنازلياً
    for key in cycles_by_date:
        cycles_by_date[key]['students'].sort(
            key=lambda x: x['total_cycle_points'], reverse=True
        )

    cycles_list = sorted(cycles_by_date.values(),
                         key=lambda x: x['cycle_start'], reverse=True)

    return render_template('points_cycle_history.html', cycles_list=cycles_list)


def fix_points_logs():
    """إعادة حساب النقاط لجميع التقييمات الموجودة"""
    with app.app_context():
        evaluations = Evaluation.query.all()
        for evaluation in evaluations:
            plan = evaluation.plan
            if not plan:
                continue

            # حساب النقاط الصحيحة لكل تقييم
            points = 0
            stars = 0

            if plan.plan_type == 'حفظ':
                total = (evaluation.new_score or 0) + (evaluation.recent_score or 0) + (evaluation.previous_score or 0)
                points = total // 10
                if points >= 9: stars = 3
                elif points >= 6: stars = 2
                elif points >= 3: stars = 1
            elif plan.plan_type == 'سرد':
                points = (evaluation.revision_score or 0) // 10
                if points >= 9: stars = 3
                elif points >= 6: stars = 2
                elif points >= 3: stars = 1
            elif plan.plan_type == 'تقييم مرحلة':
                points = (evaluation.new_score or 0) // 8
                stars = min(3, points // 3)

            # تحديث أو إنشاء سجل النقاط
            points_log = PointsLog.query.filter_by(evaluation_id=evaluation.id).first()
            if points_log:
                points_log.points_earned = points
                points_log.stars_earned = stars
            else:
                student_points = StudentPoints.query.filter_by(student_id=plan.student_id, archived=False).first()
                if student_points:
                    points_log = PointsLog(
                        student_id=plan.student_id,
                        evaluation_id=evaluation.id,
                        plan_id=plan.id,
                        points_earned=points,
                        stars_earned=stars,
                        evaluation_date=evaluation.evaluation_date,
                        cycle_start_date=student_points.current_cycle_start
                    )
                    db.session.add(points_log)

        db.session.commit()
        print("✅ تم إعادة حساب سجلات النقاط")





def rebuild_all_student_points():
    """
    إعادة بناء جدول StudentPoints وPointsLog من الصفر بناءً على التقييمات الموجودة.
    تُستدعى عند التشغيل لإصلاح أي بيانات مفقودة.
    """
    with app.app_context():
        try:
            students = Student.query.all()
            for student in students:
                # جمع كل التقييمات لهذا الطالب مرتبة بالتاريخ
                plans = WeeklyPlan.query.filter_by(student_id=student.id).all()
                evaluations_with_plan = []
                for plan in plans:
                    for ev in plan.evaluations:
                        evaluations_with_plan.append((ev, plan))

                if not evaluations_with_plan:
                    continue

                # ترتيب حسب التاريخ
                evaluations_with_plan.sort(key=lambda x: x[0].evaluation_date)

                # جلب أو إنشاء سجل النقاط النشط
                student_points = StudentPoints.query.filter_by(
                    student_id=student.id, archived=False
                ).first()

                if not student_points:
                    student_points = StudentPoints(
                        student_id=student.id,
                        total_points=0,
                        current_cycle_points=0,
                        star_level=0,
                        current_cycle_start=evaluations_with_plan[0][0].evaluation_date
                    )
                    db.session.add(student_points)
                    db.session.flush()

                if not student_points.current_cycle_start:
                    student_points.current_cycle_start = evaluations_with_plan[0][0].evaluation_date

                # إعادة حساب النقاط الإجمالية من التقييمات
                total = 0
                cycle_total = 0
                for ev, plan in evaluations_with_plan:
                    points, stars = calculate_points_from_evaluation(ev, plan)
                    total += points
                    cycle_total += points

                    # إنشاء PointsLog إن لم يكن موجوداً
                    existing_log = PointsLog.query.filter_by(evaluation_id=ev.id).first()
                    if not existing_log:
                        cycle_start = student_points.current_cycle_start or date.today()
                        log = PointsLog(
                            student_id=student.id,
                            evaluation_id=ev.id,
                            plan_id=plan.id,
                            points_earned=points,
                            stars_earned=stars,
                            evaluation_date=ev.evaluation_date,
                            cycle_start_date=cycle_start
                        )
                        db.session.add(log)

                # تحديث الإجماليات فقط إذا كانت صفر (تجنب الكتابة فوق بيانات صحيحة)
                if student_points.total_points == 0 and total > 0:
                    student_points.total_points = total
                    student_points.current_cycle_points = cycle_total
                    student_points.star_level = recalculate_star_level(cycle_total)

            db.session.commit()
            print("✅ تم إعادة بناء بيانات النقاط بنجاح")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ خطأ في إعادة بناء النقاط: {e}")


    return None  # /api moved to module scope (PWA)
# ─────────────────────────────────────────────────────────────
# 1. عرض صفحة المقررات المكررة
# ─────────────────────────────────────────────────────────────
@app.route('/admin/duplicates')
@login_required
def admin_duplicates():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    groups = find_duplicate_plans()

    # عدد كل المقررات المكررة
    total_duplicate_plans = sum(len(g['plans']) for g in groups)

    return render_template(
        'admin_duplicates.html',
        groups=groups,
        total_duplicate_plans=total_duplicate_plans,
        total_students=Student.query.count(),
    )


# ─────────────────────────────────────────────────────────────
# 2. حذف مقرر مكرر
# ─────────────────────────────────────────────────────────────
@app.route('/admin/duplicates/delete/<int:plan_id>')
@login_required
def admin_delete_duplicate_plan(plan_id):
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    plan = WeeklyPlan.query.get_or_404(plan_id)

    # منع حذف المقرر المقيّم
    if plan.evaluations:
        flash('⚠️ لا يمكن حذف مقرر مُقيَّم. قم بحذف التقييم أولاً.', 'error')
        return redirect(url_for('admin_duplicates'))

    try:
        # حذف التقارير اليومية المرتبطة أولاً
        DailyReport.query.filter_by(plan_id=plan.id).delete()
        db.session.delete(plan)
        db.session.commit()
        flash('✅ تم حذف المقرر بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء الحذف: {str(e)}', 'error')

    return redirect(url_for('admin_duplicates'))


# ─────────────────────────────────────────────────────────────
# 3. نقل التقارير اليومية من مقرر إلى آخر
# ─────────────────────────────────────────────────────────────
@app.route('/admin/duplicates/move_reports', methods=['POST'])
@login_required
def admin_move_reports():
    if not isinstance(current_user, Admin):
        flash('غير مصرح', 'error')
        return redirect(url_for('home'))

    from_id = request.form.get('from_plan_id', type=int)
    to_id   = request.form.get('to_plan_id',   type=int)

    if not from_id or not to_id or from_id == to_id:
        flash('⚠️ بيانات غير صحيحة', 'error')
        return redirect(url_for('admin_duplicates'))

    from_plan = WeeklyPlan.query.get_or_404(from_id)
    to_plan   = WeeklyPlan.query.get_or_404(to_id)

    reports = DailyReport.query.filter_by(plan_id=from_plan.id).all()
    if not reports:
        flash('⚠️ لا توجد تقارير لنقلها في هذا المقرر', 'warning')
        return redirect(url_for('admin_duplicates'))

    try:
        for report in reports:
            report.plan_id = to_plan.id
        db.session.commit()
        flash(f'✅ تم نقل {len(reports)} تقرير بنجاح إلى المقرر المحدد', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء النقل: {str(e)}', 'error')

    return redirect(url_for('admin_duplicates'))


# ------------------ تشغيل التطبيق ------------------
def run_startup_tasks():
    """مهام بدء التشغيل في خيط خلفي — لا تبطئ تحميل الصفحة"""
    import time
    time.sleep(2)  # انتظر حتى يبدأ Flask أولاً
    with app.app_context():
        try:
            db.create_all()
            update_database_schema()
            create_default_admin()
            # rebuild_all_student_points فقط عند الحاجة (من لوحة الأدمن)
        except Exception as e:
            print(f"⚠️ خطأ في مهام البدء: {e}")

if __name__ == '__main__':
    import threading
    t = threading.Thread(target=run_startup_tasks, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)


# =====================================================================
# PWA/API routes - were nested inside rebuild_all_student_points() and never registered
# =====================================================================
# ─── /offline page ────────────────────────────────────────────────────
@app.route('/offline')
def offline_page():
    return render_template('offline.html')


# ─── Service Worker على الجذر ليملك نطاق الموقع كاملًا ───────────────────────
@app.route('/service-worker.js')
def serve_service_worker():
    resp = make_response(send_from_directory(
        app.static_folder, 'service-worker.js', mimetype='application/javascript'))
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ─── /healthz — فحص صحة لـ Vercel ومراقبة الجاهزية ─────────────────────────
@app.route('/healthz')
def healthz():
    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:
        return {'status': 'error', 'database': str(exc)}, 503
    return {'status': 'ok', 'database': 'ok'}, 200


# ─── /api/student/dashboard — بيانات الطالب المخزّنة محليًا للعرض Offline ──
@app.route('/api/student/dashboard')
@login_required
def api_student_dashboard():
    if not isinstance(current_user, Student):
        return jsonify({'success': False, 'error': 'غير مصرح'}), 403

    plans = WeeklyPlan.query.filter_by(
        student_id=current_user.id
    ).order_by(WeeklyPlan.start_date.desc()).all()

    def plan_dict(p):
        return {
            'id': p.id,
            'plan_type': p.plan_type,
            'start_date': p.start_date.isoformat() if p.start_date else None,
            'new_memorization': p.new_memorization,
            'recent_review': p.recent_review,
            'previous_review': p.previous_review,
            'revision_text': p.revision_text,
        }

    return jsonify({'success': True, 'plans': [plan_dict(p) for p in plans]})


# ─── /api/ping ────────────────────────────────────────────────────────
@app.route('/api/ping')
def api_ping():
    return jsonify({'success': True, 'server_time': datetime.utcnow().isoformat()})


# ─── /api/sync/status ─────────────────────────────────────────────────
@app.route('/api/sync/status')
@login_required
def api_sync_status():
    return jsonify({
        'success': True,
        'user_id': current_user.get_id(),
        'server_time': datetime.utcnow().isoformat(),
        'online': True
    })


# ─── /api/sync/reports ────────────────────────────────────────────────
@app.route('/api/sync/reports', methods=['POST'])
@login_required
def api_sync_reports():
    if not isinstance(current_user, Student):
        return jsonify({'success': False, 'error': 'غير مصرح'}), 403

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    plan_id_str     = str(data.get('plan_id', '') or data.get('plan-id', ''))
    report_date_str = str(data.get('report_date', ''))

    if not plan_id_str or not report_date_str:
        return jsonify({'success': False, 'error': 'plan_id و report_date مطلوبان'}), 400

    try:
        plan_id     = int(plan_id_str)
        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
    except Exception as e:
        return jsonify({'success': False, 'error': f'تنسيق خاطئ: {e}'}), 400

    plan = WeeklyPlan.query.get(plan_id)
    if not plan or plan.student_id != current_user.id:
        return jsonify({'success': False, 'error': 'المقرر غير موجود أو غير مصرح'}), 403

    # قبول التواريخ حتى 3 أيام ماضية في طلبات المزامنة
    is_sync = request.headers.get('X-Sync-Request') == 'true'
    oman_tz = pytz.timezone('Asia/Muscat')
    now_oman = datetime.now(oman_tz)
    today_oman = now_oman.date()

    if is_sync:
        oldest_allowed = today_oman - timedelta(days=3)
        if report_date < oldest_allowed:
            return jsonify({'success': False, 'error': 'التاريخ قديم جداً'}), 400
    else:
        allowed_dates = [today_oman]
        cutoff_hour   = get_cutoff_hour()
        if now_oman.hour < cutoff_hour:
            allowed_dates.append(today_oman - timedelta(days=1))
        if report_date not in allowed_dates:
            return jsonify({'success': False, 'error': 'التاريخ غير مسموح به'}), 400

    # فحص التكرار
    existing = DailyReport.query.filter_by(plan_id=plan.id, report_date=report_date).first()
    if existing:
        return jsonify({
            'success': True,
            'duplicate': True,
            'report_id': existing.id,
            'message': 'التقرير موجود مسبقاً'
        })

    def get_bool(key):
        val = data.get(key)
        if isinstance(val, bool): return val
        return str(val).lower() in ('on', 'true', '1', 'yes')

    weekdays  = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']
    day_name  = weekdays[report_date.weekday()]
    is_weekend = report_date.weekday() in [3, 4]

    if plan.plan_type == 'حفظ':
        if is_weekend:
            report = DailyReport(
                plan_id=plan.id, report_date=report_date, day_name=day_name,
                listening=False, recitation_mastery=False,
                listened_new=False, repeated_new=False, reviewed_week=False,
                phase_memorization=data.get('phase_memorization', ''),
                previous_memorization=data.get('previous_memorization', ''),
                recitation_done=False, recitation_text='', recitation_notes=''
            )
        else:
            report = DailyReport(
                plan_id=plan.id, report_date=report_date, day_name=day_name,
                listening=get_bool('listening'),
                recitation_mastery=get_bool('recitation_mastery'),
                listened_new=get_bool('listened_new'),
                repeated_new=get_bool('repeated_new'),
                reviewed_week=get_bool('reviewed_week'),
                phase_memorization=data.get('phase_memorization', ''),
                previous_memorization=data.get('previous_memorization', ''),
                recitation_done=False, recitation_text='', recitation_notes=''
            )
    elif plan.plan_type == 'سرد':
        report = DailyReport(
            plan_id=plan.id, report_date=report_date, day_name=day_name,
            listening=False, recitation_mastery=False,
            listened_new=False, repeated_new=False, reviewed_week=False,
            phase_memorization='', previous_memorization='',
            recitation_done=get_bool('recitation_done'),
            recitation_text=data.get('recitation_text', ''),
            recitation_notes=data.get('recitation_notes', '')
        )
    else:
        report = DailyReport(
            plan_id=plan.id, report_date=report_date, day_name=day_name,
            listening=False, recitation_mastery=False,
            listened_new=False, repeated_new=False, reviewed_week=False,
            phase_memorization='', previous_memorization='',
            recitation_done=False, recitation_text='', recitation_notes=''
        )

    try:
        db.session.add(report)
        db.session.commit()
        return jsonify({
            'success': True,
            'report_id': report.id,
            'sync_id': sync_id,
            'message': 'تم حفظ التقرير بنجاح'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── /api/sync/plans ──────────────────────────────────────────────────
@app.route('/api/sync/plans', methods=['POST'])
@login_required
def api_sync_plans():
    if not isinstance(current_user, Teacher):
        return jsonify({'success': False, 'error': 'غير مصرح'}), 403
    if not current_user.can_add_plan:
        return jsonify({'success': False, 'error': 'لا صلاحية لإضافة المقررات'}), 403

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    try:
        student_id = int(data.get('student_id', 0))
        plan_type  = data.get('plan_type', '')
        start_date = datetime.strptime(data.get('start_date', ''), '%Y-%m-%d').date()
    except Exception as e:
        return jsonify({'success': False, 'error': f'تنسيق خاطئ: {e}'}), 400

    student = Student.query.get(student_id)
    if not student or student not in current_user.students:
        return jsonify({'success': False, 'error': 'الطالب غير موجود أو غير مصرح'}), 403

    if start_date.weekday() != 6:
        return jsonify({'success': False, 'error': 'تاريخ البدء يجب أن يكون الأحد'}), 400

    existing = WeeklyPlan.query.filter_by(student_id=student_id, start_date=start_date).first()
    if existing:
        return jsonify({'success': True, 'duplicate': True, 'plan_id': existing.id})

    plan = WeeklyPlan(
        student_id=student_id, teacher_id=current_user.id,
        plan_type=plan_type, start_date=start_date,
        notes=data.get('notes', '')
    )
    if plan_type == 'حفظ':
        plan.new_memorization = data.get('new_memorization', '')
        plan.recent_review    = data.get('recent_review', '')
        plan.previous_review  = data.get('previous_review', '')
    else:
        plan.sard_subtype   = data.get('sard_subtype', 'full')
        plan.revision_text  = data.get('revision_text', '')
        plan.revision_notes = data.get('revision_notes', '')

    try:
        db.session.add(plan)
        db.session.commit()
        return jsonify({'success': True, 'plan_id': plan.id, 'sync_id': sync_id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── /api/sync/evaluations ────────────────────────────────────────────
@app.route('/api/sync/evaluations', methods=['POST'])
@login_required
def api_sync_evaluations():
    if not isinstance(current_user, Teacher):
        return jsonify({'success': False, 'error': 'غير مصرح'}), 403

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    try:
        plan_id   = int(data.get('plan_id', 0))
        eval_date = datetime.strptime(data.get('evaluation_date', ''), '%Y-%m-%d').date()
    except Exception as e:
        return jsonify({'success': False, 'error': f'تنسيق خاطئ: {e}'}), 400

    plan = WeeklyPlan.query.get(plan_id)
    if not plan or plan.student not in current_user.students:
        return jsonify({'success': False, 'error': 'المقرر غير موجود أو غير مصرح'}), 403

    existing = Evaluation.query.filter_by(plan_id=plan_id).first()
    if existing:
        return jsonify({'success': True, 'duplicate': True, 'evaluation_id': existing.id})

    def to_int(key):
        try: return int(data.get(key, 0) or 0)
        except: return 0

    def to_bool(key):
        val = data.get(key)
        return str(val).lower() in ('true', '1', 'on', 'yes') if val else False

    try:
        if plan.plan_type == 'حفظ':
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id, evaluation_date=eval_date,
                new_score=to_int('new_score'), new_pass=to_bool('new_pass'),
                recent_score=to_int('recent_score'),
                previous_score=to_int('old_score'), previous_pass=to_bool('old_pass'),
                evaluation_notes=data.get('notes', '')
            )
        elif plan.plan_type == 'سرد':
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id, evaluation_date=eval_date,
                revision_score=to_int('revision_score'), revision_pass=to_bool('revision_pass'),
                evaluation_notes=data.get('notes', '')
            )
        else:
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id, evaluation_date=eval_date,
                new_score=to_int('phase_score'), new_pass=to_bool('phase_pass'),
                evaluation_notes=data.get('notes', '')
            )

        db.session.add(evaluation)
        db.session.flush()

        points, stars = calculate_points_from_evaluation(evaluation, plan)

        student_points = StudentPoints.query.filter_by(
            student_id=plan.student_id, archived=False
        ).first()
        if not student_points:
            student_points = StudentPoints(
                student_id=plan.student_id, total_points=0,
                current_cycle_points=0, star_level=0,
                current_cycle_start=date.today()
            )
            db.session.add(student_points)
            db.session.flush()

        if not student_points.current_cycle_start:
            student_points.current_cycle_start = date.today()

        student_points.total_points         += points
        student_points.current_cycle_points += points
        student_points.star_level = recalculate_star_level(student_points.current_cycle_points)
        student_points.last_updated = datetime.utcnow()

        points_log = PointsLog(
            student_id=plan.student_id, evaluation_id=evaluation.id,
            plan_id=plan.id, points_earned=points, stars_earned=stars,
            evaluation_date=eval_date,
            cycle_start_date=student_points.current_cycle_start or date.today()
        )
        db.session.add(points_log)
        db.session.commit()

        return jsonify({
            'success': True,
            'evaluation_id': evaluation.id,
            'points': points,
            'sync_id': sync_id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# =====================================================================
# تهيئة قاعدة البيانات — تُنفَّذ مرة واحدة عند أول طلب في بيئة serverless.
# لا نستخدم خيوطًا ولا sleep: Vercel يستورد الوحدة لكل نسخة دالة، والعلامة
# _serverless_ready تمنع تكرار العمل داخل النسخة نفسها. قاعدة Supabase
# موجودة مسبقًا فالعملية تقتصر على تحديث المخطط وإنشاء الأدمن الافتراضي.
# =====================================================================
_serverless_ready = False
_serverless_init_lock = threading.Lock()


@app.before_request
def _serverless_ensure_db():
    global _serverless_ready
    if not IS_SERVERLESS or _serverless_ready:
        return
    with _serverless_init_lock:
        if _serverless_ready:
            return
        _serverless_ready = True  # نمنع التكرار حتى لو فشل جزء من التهيئة
        try:
            db.create_all()
            update_database_schema()
            create_default_admin()
            print('✅ تهيئة قاعدة البيانات (serverless) اكتملت', flush=True)
        except Exception as e:
            print(f'⚠️ خطأ في تهيئة قاعدة البيانات (serverless): {e}', flush=True)
            db.session.rollback()


__all__ = ['app', 'db']
