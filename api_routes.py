# =====================================================================
# api_routes.py — endpoints المزامنة لـ PWA
# =====================================================================
# أضف هذا الملف بجانب main.py
# ثم في main.py أضف: from api_routes import api_bp, register_api
#                     register_api(app)
# =====================================================================

from flask import Blueprint, request, jsonify, session
from flask_login import current_user, login_required
from datetime import datetime, date, timedelta
from werkzeug.security import check_password_hash
import pytz
import json

# استيراد النماذج من main.py
# (إذا كان main.py به كل شيء، انسخ هذا الكود في نهاية main.py مباشرة)
from main import (
    db, app, Student, Teacher, Admin, WeeklyPlan, DailyReport,
    Evaluation, StudentPoints, get_cutoff_hour, calculate_points_from_evaluation,
    recalculate_star_level, PointsLog
)

api_bp = Blueprint('api', __name__, url_prefix='/api')


# ─────────────────────────────────────────────────────────────────────
# مساعدات
# ─────────────────────────────────────────────────────────────────────

def api_error(message, code=400):
    return jsonify({'success': False, 'error': message}), code

def api_ok(data=None, message='تمت العملية بنجاح'):
    resp = {'success': True, 'message': message}
    if data is not None:
        resp['data'] = data
    return jsonify(resp)

def get_oman_now():
    oman_tz = pytz.timezone('Asia/Muscat')
    return datetime.now(oman_tz)

def is_sync_request():
    return request.headers.get('X-Sync-Request') == 'true'


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /api/ping — فحص حالة الاتصال
# ─────────────────────────────────────────────────────────────────────
@api_bp.route('/ping')
def ping():
    return api_ok({'server_time': datetime.utcnow().isoformat()})


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /api/sync/reports — مزامنة التقارير اليومية
# ─────────────────────────────────────────────────────────────────────
@api_bp.route('/sync/reports', methods=['POST'])
@login_required
def sync_reports():
    """
    يستقبل تقرير يومي محفوظ محلياً ويحفظه في قاعدة البيانات.
    يتعامل مع التكرار: إذا وُجد تقرير لنفس اليوم والمقرر يتجاهله.
    """
    if not isinstance(current_user, Student):
        return api_error('غير مصرح', 403)

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    # التحقق من الحقول المطلوبة
    plan_id_str    = data.get('plan_id') or data.get('plan-id')
    report_date_str = data.get('report_date')

    if not plan_id_str or not report_date_str:
        return api_error('حقول مطلوبة ناقصة: plan_id, report_date')

    try:
        plan_id     = int(plan_id_str)
        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError) as e:
        return api_error(f'تنسيق بيانات خاطئ: {e}')

    # التحقق من ملكية المقرر
    plan = WeeklyPlan.query.get(plan_id)
    if not plan or plan.student_id != current_user.id:
        return api_error('المقرر غير موجود أو غير مصرح', 403)

    # التحقق من التاريخ المسموح به
    now_oman    = get_oman_now()
    today_oman  = now_oman.date()
    yesterday   = today_oman - timedelta(days=1)
    cutoff_hour = get_cutoff_hour()

    if is_sync_request():
        # طلبات المزامنة تقبل تواريخ أقدم (حتى 3 أيام)
        oldest_allowed = today_oman - timedelta(days=3)
        if report_date < oldest_allowed:
            return api_error(f'التاريخ قديم جداً للمزامنة: {report_date_str}')
    else:
        allowed = [today_oman]
        if now_oman.hour < cutoff_hour:
            allowed.append(yesterday)
        if report_date not in allowed:
            return api_error('التاريخ غير مسموح به')

    # فحص التكرار
    existing = DailyReport.query.filter_by(
        plan_id=plan.id,
        report_date=report_date
    ).first()

    if existing:
        return api_ok(
            {'report_id': existing.id, 'duplicate': True},
            'تقرير هذا اليوم موجود مسبقاً'
        )

    # بناء التقرير
    weekdays = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']
    day_name  = weekdays[report_date.weekday()]
    is_weekend = report_date.weekday() in [3, 4]  # خميس/جمعة

    def get_bool(key):
        val = data.get(key)
        if isinstance(val, bool): return val
        if isinstance(val, str):  return val.lower() in ('on', 'true', '1', 'yes')
        return bool(val)

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
        return api_ok(
            {'report_id': report.id, 'sync_id': sync_id},
            'تم حفظ التقرير اليومي بنجاح'
        )
    except Exception as e:
        db.session.rollback()
        return api_error(f'خطأ في قاعدة البيانات: {str(e)}', 500)


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /api/sync/plans — مزامنة المقررات الأسبوعية
# ─────────────────────────────────────────────────────────────────────
@api_bp.route('/sync/plans', methods=['POST'])
@login_required
def sync_plans():
    """مزامنة مقرر أسبوعي أنشأه المعلم وهو Offline"""
    if not isinstance(current_user, Teacher):
        return api_error('غير مصرح — يجب أن تكون معلماً', 403)

    if not current_user.can_add_plan:
        return api_error('ليس لديك صلاحية إضافة المقررات', 403)

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    student_id_str  = data.get('student_id')
    plan_type       = data.get('plan_type')
    start_date_str  = data.get('start_date')

    if not all([student_id_str, plan_type, start_date_str]):
        return api_error('حقول مطلوبة ناقصة: student_id, plan_type, start_date')

    try:
        student_id = int(student_id_str)
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError) as e:
        return api_error(f'تنسيق بيانات خاطئ: {e}')

    # التحقق أن الطالب من طلاب هذا المعلم
    student = Student.query.get(student_id)
    if not student or student not in current_user.students:
        return api_error('الطالب غير موجود أو ليس من طلابك', 403)

    # يجب أن يبدأ الأحد
    if start_date.weekday() != 6:
        return api_error('تاريخ البدء يجب أن يكون الأحد')

    # فحص التكرار
    existing = WeeklyPlan.query.filter_by(
        student_id=student_id,
        start_date=start_date
    ).first()

    if existing:
        return api_ok(
            {'plan_id': existing.id, 'duplicate': True},
            'يوجد مقرر لهذا الأسبوع مسبقاً'
        )

    # بناء المقرر
    plan = WeeklyPlan(
        student_id  = student_id,
        teacher_id  = current_user.id,
        plan_type   = plan_type,
        start_date  = start_date,
        notes       = data.get('notes', '')
    )

    if plan_type == 'حفظ':
        plan.new_memorization = data.get('new_memorization', '')
        plan.recent_review    = data.get('recent_review', '')
        plan.previous_review  = data.get('previous_review', '')
    else:  # سرد
        plan.sard_subtype   = data.get('sard_subtype', 'full')
        plan.revision_text  = data.get('revision_text', '')
        plan.revision_notes = data.get('revision_notes', '')

    try:
        db.session.add(plan)
        db.session.commit()
        return api_ok(
            {'plan_id': plan.id, 'sync_id': sync_id},
            'تم حفظ المقرر بنجاح'
        )
    except Exception as e:
        db.session.rollback()
        return api_error(f'خطأ في قاعدة البيانات: {str(e)}', 500)


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /api/sync/evaluations — مزامنة التقييمات
# ─────────────────────────────────────────────────────────────────────
@api_bp.route('/sync/evaluations', methods=['POST'])
@login_required
def sync_evaluations():
    """مزامنة تقييم مقرر أجراه المعلم وهو Offline"""
    if not isinstance(current_user, Teacher):
        return api_error('غير مصرح — يجب أن تكون معلماً', 403)

    payload = request.get_json(force=True, silent=True) or {}
    sync_id = payload.get('sync_id', '')
    data    = payload.get('data', {})

    plan_id_str    = data.get('plan_id')
    eval_date_str  = data.get('evaluation_date')

    if not plan_id_str or not eval_date_str:
        return api_error('حقول مطلوبة ناقصة: plan_id, evaluation_date')

    try:
        plan_id   = int(plan_id_str)
        eval_date = datetime.strptime(eval_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError) as e:
        return api_error(f'تنسيق بيانات خاطئ: {e}')

    plan = WeeklyPlan.query.get(plan_id)
    if not plan or plan.student not in current_user.students:
        return api_error('المقرر غير موجود أو غير مصرح', 403)

    # فحص التكرار
    existing = Evaluation.query.filter_by(plan_id=plan_id).first()
    if existing:
        return api_ok(
            {'evaluation_id': existing.id, 'duplicate': True},
            'تم تقييم هذا المقرر مسبقاً'
        )

    def to_int(key, default=0):
        try: return int(data.get(key, default) or default)
        except: return default

    def to_bool(key):
        val = data.get(key)
        if isinstance(val, bool): return val
        return str(val).lower() in ('true', '1', 'on', 'yes')

    try:
        if plan.plan_type == 'حفظ':
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id,
                evaluation_date=eval_date,
                new_score=to_int('new_score'),
                new_pass=to_bool('new_pass'),
                recent_score=to_int('recent_score'),
                previous_score=to_int('old_score'),
                previous_pass=to_bool('old_pass'),
                evaluation_notes=data.get('notes', '')
            )
        elif plan.plan_type == 'سرد':
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id,
                evaluation_date=eval_date,
                revision_score=to_int('revision_score'),
                revision_pass=to_bool('revision_pass'),
                evaluation_notes=data.get('notes', '')
            )
        else:  # تقييم مرحلة
            evaluation = Evaluation(
                plan_id=plan.id, teacher_id=current_user.id,
                evaluation_date=eval_date,
                new_score=to_int('phase_score'),
                new_pass=to_bool('phase_pass'),
                evaluation_notes=data.get('notes', '')
            )

        db.session.add(evaluation)
        db.session.flush()

        # حساب النقاط
        points, stars = calculate_points_from_evaluation(evaluation, plan)

        student_points = StudentPoints.query.filter_by(
            student_id=plan.student_id, archived=False
        ).first()
        if not student_points:
            student_points = StudentPoints(
                student_id=plan.student_id,
                total_points=0, current_cycle_points=0,
                star_level=0, current_cycle_start=date.today()
            )
            db.session.add(student_points)
            db.session.flush()

        if not student_points.current_cycle_start:
            student_points.current_cycle_start = date.today()

        student_points.total_points         += points
        student_points.current_cycle_points += points
        student_points.star_level = recalculate_star_level(student_points.current_cycle_points)
        student_points.last_updated         = datetime.utcnow()

        points_log = PointsLog(
            student_id=plan.student_id,
            evaluation_id=evaluation.id,
            plan_id=plan.id,
            points_earned=points,
            stars_earned=stars,
            evaluation_date=eval_date,
            cycle_start_date=student_points.current_cycle_start or date.today()
        )
        db.session.add(points_log)
        db.session.commit()

        return api_ok(
            {'evaluation_id': evaluation.id, 'points': points, 'sync_id': sync_id},
            f'تم حفظ التقييم — {points} نقطة مضافة'
        )

    except Exception as e:
        db.session.rollback()
        return api_error(f'خطأ في قاعدة البيانات: {str(e)}', 500)


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /api/sync/status — حالة المزامنة
# ─────────────────────────────────────────────────────────────────────
@api_bp.route('/sync/status')
@login_required
def sync_status():
    """إحصاءات سريعة لحالة البيانات"""
    now = datetime.utcnow()
    return api_ok({
        'user_id'    : current_user.get_id(),
        'server_time': now.isoformat(),
        'online'     : True
    })


# ─────────────────────────────────────────────────────────────────────
# ROUTE: /offline — صفحة offline الاحتياطية
# ─────────────────────────────────────────────────────────────────────
# أضف هذا في main.py مباشرة:
"""
@app.route('/offline')
def offline_page():
    return render_template('offline.html'), 200
"""


# ─────────────────────────────────────────────────────────────────────
# تسجيل Blueprint
# ─────────────────────────────────────────────────────────────────────
def register_api(flask_app):
    flask_app.register_blueprint(api_bp)
    print('[API] تم تسجيل endpoints المزامنة')
