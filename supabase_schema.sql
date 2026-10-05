-- =====================================================================
--  supabase_schema.sql — PWA أشبال القرآن
--  PostgreSQL / Supabase — مطابق 100% لنماذج SQLAlchemy في main.py
--  Run in: Supabase Dashboard → SQL Editor → New query → Run
--  المخطط مُولّد من `db.metadata` مباشرة (SQLAlchemy 2.0 / postgresql dialect)
--  ثم أُضيفت: قيم DEFAULT + ON DELETE + الفهارس
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0) تنظيف — يمرّر حتى لو كانت الجداول غير موجودة (استخدم لمشروع Supabase جديد).
--    ⚠️ يحذف أي بيانات موجودة. لا تشغّله على قاعدة فيها بيانات حقيقية.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS points_log, daily_report, evaluation, weekly_plan,
    reward_calculation, reward_rule_detail, reward_rule, phase_evaluation,
    student_period_pages, student_pages, student_points, warning,
    teacher_student, poem, student, teacher, admin,
    system_setting, system_settings CASCADE;

-- =====================================================================
-- 1) teacher — المعلمون
-- =====================================================================
CREATE TABLE teacher (
    id                      SERIAL PRIMARY KEY,
    full_name               VARCHAR(100) NOT NULL,
    username                VARCHAR(50)  NOT NULL UNIQUE,
    password                VARCHAR(255) NOT NULL,
    can_view_missing_reports BOOLEAN     DEFAULT FALSE,
    can_add_plan            BOOLEAN      DEFAULT FALSE,
    can_add_poem_plan       BOOLEAN      DEFAULT TRUE  NOT NULL,
    is_active               BOOLEAN      DEFAULT TRUE
);

-- =====================================================================
-- 2) student — الطلاب
-- =====================================================================
CREATE TABLE student (
    id                  SERIAL PRIMARY KEY,
    full_name           VARCHAR(100)  NOT NULL,
    username            VARCHAR(50)   NOT NULL UNIQUE,
    password            VARCHAR(255)  NOT NULL,
    phone               VARCHAR(15),
    memorization_level  VARCHAR(50),
    is_active           BOOLEAN       DEFAULT TRUE,
    is_suspended        BOOLEAN       DEFAULT FALSE,
    warning_count       INTEGER       DEFAULT 0
);

-- =====================================================================
-- 3) admin — المشرفون
-- =====================================================================
CREATE TABLE admin (
    id                  SERIAL PRIMARY KEY,
    username            VARCHAR(50)  NOT NULL UNIQUE,
    password            VARCHAR(255) NOT NULL,
    can_access_students BOOLEAN      DEFAULT TRUE,
    can_edit_students   BOOLEAN      DEFAULT TRUE,
    can_access_teachers BOOLEAN      DEFAULT TRUE,
    can_edit_teachers   BOOLEAN      DEFAULT TRUE,
    can_access_reports  BOOLEAN      DEFAULT TRUE,
    can_access_warnings BOOLEAN      DEFAULT TRUE,
    can_access_backup   BOOLEAN      DEFAULT TRUE,
    can_restore_backup  BOOLEAN      DEFAULT TRUE,
    is_super_admin      BOOLEAN      DEFAULT FALSE
);

-- =====================================================================
-- 4) teacher_student — ربط المعلم بالطالب (Many-to-Many)
-- =====================================================================
CREATE TABLE teacher_student (
    teacher_id  INTEGER NOT NULL REFERENCES teacher(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    PRIMARY KEY (teacher_id, student_id)
);

-- =====================================================================
-- 5) poem — قصائد مقررات الحفظ
-- =====================================================================
CREATE TABLE poem (
    id           SERIAL PRIMARY KEY,
    poem_number  INTEGER,
    poem_title   VARCHAR(200),
    poet         VARCHAR(200),
    verse_count  INTEGER
);

-- =====================================================================
-- 6) weekly_plan — المقررات الأسبوعية
-- =====================================================================
CREATE TABLE weekly_plan (
    id                 SERIAL PRIMARY KEY,
    student_id         INTEGER     NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    teacher_id         INTEGER     NOT NULL REFERENCES teacher(id) ON DELETE CASCADE,
    plan_type          VARCHAR(10),                        -- 'حفظ' | 'سرد' | 'قصيدة'
    sard_subtype       VARCHAR(20),                        -- 'كامل' | 'مراجعة'
    poem_id            INTEGER     REFERENCES poem(id) ON DELETE SET NULL,
    verse_start        INTEGER,
    verse_end          INTEGER,
    start_date         DATE        NOT NULL,
    new_memorization   TEXT,
    recent_review      TEXT,
    previous_review    TEXT,
    revision_text      TEXT,
    revision_notes     TEXT,
    notes              TEXT,
    -- الترحيل التلقائي: شارة دائمة تظهر في صفحات الخطة عند نسخها للأسبوع التالي
    rolled_over        BOOLEAN     DEFAULT FALSE,
    rolled_from_id     INTEGER     REFERENCES weekly_plan(id) ON DELETE SET NULL,
    roll_reason        VARCHAR(20),                        -- 'new' | 'old' | 'both' | 'poem'
    roll_notes         TEXT
);

-- =====================================================================
-- 7) evaluation — تقييمات المقررات
-- =====================================================================
CREATE TABLE evaluation (
    id                 SERIAL PRIMARY KEY,
    plan_id            INTEGER NOT NULL REFERENCES weekly_plan(id) ON DELETE CASCADE,
    teacher_id         INTEGER NOT NULL REFERENCES teacher(id) ON DELETE CASCADE,
    evaluation_date    DATE    NOT NULL,
    new_score          INTEGER,
    new_pass           BOOLEAN,
    recent_score       INTEGER,
    previous_score     INTEGER,
    previous_pass      BOOLEAN,
    revision_score     INTEGER,
    revision_pass      BOOLEAN,
    evaluation_notes   TEXT
);

-- =====================================================================
-- 8) daily_report — التقارير اليومية
-- =====================================================================
CREATE TABLE daily_report (
    id                     SERIAL PRIMARY KEY,
    plan_id                INTEGER NOT NULL REFERENCES weekly_plan(id) ON DELETE CASCADE,
    report_date            DATE    NOT NULL,
    day_name               VARCHAR(20),
    listening              BOOLEAN DEFAULT FALSE,
    recitation_mastery     BOOLEAN DEFAULT FALSE,
    listened_new           BOOLEAN DEFAULT FALSE,
    repeated_new           BOOLEAN DEFAULT FALSE,
    reviewed_week          BOOLEAN DEFAULT FALSE,
    phase_memorization     TEXT,
    previous_memorization  TEXT,
    recitation_done        BOOLEAN DEFAULT FALSE,
    recitation_text        TEXT,
    recitation_notes       TEXT
);

-- =====================================================================
-- 9) warning — الإنذارات
-- =====================================================================
CREATE TABLE warning (
    id              SERIAL PRIMARY KEY,
    student_id      INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    date            TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    warning_number  INTEGER,          -- 1..5 أو NULL
    action          VARCHAR(20),      -- 'warning' | 'suspend' | 'reactivate'
    notes           TEXT,
    admin_id        INTEGER REFERENCES admin(id) ON DELETE SET NULL
);

-- =====================================================================
-- 10) student_pages — إجمالي صفحات الحفظ لكل طالب
-- =====================================================================
CREATE TABLE student_pages (
    id           SERIAL PRIMARY KEY,
    student_id   INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    total_pages  INTEGER DEFAULT 0,
    last_updated TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    notes        TEXT
);

-- =====================================================================
-- 11) student_period_pages — صفحات الطالب حسب الفترة
-- =====================================================================
CREATE TABLE student_period_pages (
    id           SERIAL PRIMARY KEY,
    student_id   INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    period_start DATE    NOT NULL,
    period_end   DATE    NOT NULL,
    pages        INTEGER DEFAULT 0,
    notes        TEXT,
    created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    CONSTRAINT uq_student_period_pages UNIQUE (student_id, period_start, period_end)
);

-- =====================================================================
-- 12) reward_rule — قواعد المكافآت
-- =====================================================================
CREATE TABLE reward_rule (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    period_months  INTEGER      NOT NULL,
    is_active      BOOLEAN      DEFAULT TRUE,
    created_at     TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

-- =====================================================================
-- 13) reward_rule_detail — شرائح كل قاعدة
-- =====================================================================
CREATE TABLE reward_rule_detail (
    id               SERIAL PRIMARY KEY,
    rule_id          INTEGER NOT NULL REFERENCES reward_rule(id) ON DELETE CASCADE,
    min_score        FLOAT   NOT NULL,
    max_score        FLOAT   NOT NULL,
    reward_per_page  FLOAT   NOT NULL
);

-- =====================================================================
-- 14) phase_evaluation — تقييمات المراحل
-- =====================================================================
CREATE TABLE phase_evaluation (
    id               SERIAL PRIMARY KEY,
    student_id       INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    teacher_id       INTEGER NOT NULL REFERENCES teacher(id) ON DELETE CASCADE,
    evaluation_date  DATE    NOT NULL,
    phase_number     INTEGER,
    phase_name       VARCHAR(100),
    score            INTEGER NOT NULL,        -- من 80
    notes            TEXT
);

-- =====================================================================
-- 15) reward_calculation — حسابات المكافآت
-- =====================================================================
CREATE TABLE reward_calculation (
    id                      SERIAL PRIMARY KEY,
    student_id              INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    rule_id                 INTEGER NOT NULL REFERENCES reward_rule(id) ON DELETE CASCADE,
    calculation_date        TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    period_start            DATE    NOT NULL,
    period_end              DATE    NOT NULL,
    total_pages             INTEGER DEFAULT 0,
    avg_memorization_score  FLOAT   DEFAULT 0,
    phase_score             INTEGER DEFAULT 0,
    final_score             FLOAT   DEFAULT 0,
    reward_per_page         FLOAT   DEFAULT 0,
    total_reward            FLOAT   DEFAULT 0,
    is_paid                 BOOLEAN DEFAULT FALSE,
    paid_date               DATE,
    notes                   TEXT
);

-- =====================================================================
-- 16) student_points — أرصدة النقاط
-- =====================================================================
CREATE TABLE student_points (
    id                    SERIAL PRIMARY KEY,
    student_id            INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    total_points          INTEGER DEFAULT 0,
    star_level            INTEGER DEFAULT 0,
    current_cycle_start   DATE    DEFAULT CURRENT_DATE,
    cycle_end_date        DATE,
    current_cycle_points  INTEGER DEFAULT 0,
    archived              BOOLEAN DEFAULT FALSE,
    last_updated          TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

-- =====================================================================
-- 17) points_log — سجل حركة النقاط
-- =====================================================================
CREATE TABLE points_log (
    id                SERIAL PRIMARY KEY,
    student_id        INTEGER NOT NULL REFERENCES student(id)    ON DELETE CASCADE,
    evaluation_id     INTEGER NOT NULL REFERENCES evaluation(id) ON DELETE CASCADE,
    plan_id           INTEGER NOT NULL REFERENCES weekly_plan(id) ON DELETE CASCADE,
    points_earned     INTEGER DEFAULT 0,
    stars_earned      INTEGER DEFAULT 0,
    evaluation_date   DATE    NOT NULL,
    created_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
    cycle_start_date  DATE    NOT NULL
);

-- =====================================================================
-- 18) system_settings — إعدادات النظام (نصية)
-- =====================================================================
CREATE TABLE system_settings (
    id             SERIAL PRIMARY KEY,
    setting_key    VARCHAR(100)  NOT NULL UNIQUE,
    setting_value  VARCHAR(255)  NOT NULL,
    description    VARCHAR(255),
    updated_at     TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

-- =====================================================================
-- 19) system_setting — إعدادات النظام (منطقية/نصية)
-- =====================================================================
CREATE TABLE system_setting (
    id           SERIAL PRIMARY KEY,
    key          VARCHAR(100)  NOT NULL UNIQUE,
    value_text   VARCHAR(200),
    value_bool   BOOLEAN       DEFAULT FALSE,
    updated_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
);

COMMIT;


-- =====================================================================
--  الفهارس (Indexes) — إلزامية لأداء الشاشات على Supabase
--  بدونها ستُبطأ صفحات: لوحة الطالب، لوحة المعلم، لوحة الأدمن،
--  كشف المتخلفين، احتساب المكافآت، ومزامنة PWA
-- =====================================================================

-- ربط المعلم بالطالب (يستخدم في كل تسجيل دخول معلم وفي كل شاشة طلاب)
CREATE INDEX idx_teacher_student_student ON teacher_student (student_id);
CREATE INDEX idx_teacher_student_teacher ON teacher_student (teacher_id);

-- المقررات (أكثر جدول استعلاماً في الموقع)
CREATE INDEX idx_weekly_plan_student      ON weekly_plan (student_id);
CREATE INDEX idx_weekly_plan_teacher      ON weekly_plan (teacher_id);
CREATE INDEX idx_weekly_plan_student_date ON weekly_plan (student_id, start_date DESC);
CREATE INDEX idx_weekly_plan_type         ON weekly_plan (plan_type);
CREATE INDEX idx_weekly_plan_poem         ON weekly_plan (poem_id);

-- التقييمات
CREATE INDEX idx_evaluation_plan         ON evaluation (plan_id);
CREATE INDEX idx_evaluation_teacher      ON evaluation (teacher_id);
CREATE INDEX idx_evaluation_plan_date    ON evaluation (plan_id, evaluation_date);

-- التقارير اليومية
CREATE INDEX idx_daily_report_plan       ON daily_report (plan_id);
CREATE INDEX idx_daily_report_plan_date  ON daily_report (plan_id, report_date DESC);
CREATE INDEX idx_daily_report_date       ON daily_report (report_date DESC);

-- الإنذارات
CREATE INDEX idx_warning_student         ON warning (student_id);
CREATE INDEX idx_warning_student_date    ON warning (student_id, date DESC);
CREATE INDEX idx_warning_admin           ON warning (admin_id);

-- الصفحات
CREATE INDEX idx_student_pages_student            ON student_pages (student_id);
CREATE INDEX idx_student_period_pages_student     ON student_period_pages (student_id);
CREATE INDEX idx_student_period_pages_range       ON student_period_pages (period_start, period_end);

-- النقاط
CREATE INDEX idx_student_points_student     ON student_points (student_id);
CREATE INDEX idx_student_points_archived    ON student_points (archived);
CREATE INDEX idx_student_points_cycle       ON student_points (archived, current_cycle_start);
CREATE INDEX idx_points_log_student         ON points_log (student_id);
CREATE INDEX idx_points_log_cycle           ON points_log (student_id, cycle_start_date);
CREATE INDEX idx_points_log_evaluation      ON points_log (evaluation_id);
CREATE INDEX idx_points_log_plan            ON points_log (plan_id);

-- المكافآت
CREATE INDEX idx_reward_rule_detail_rule            ON reward_rule_detail (rule_id);
CREATE INDEX idx_reward_calculation_student         ON reward_calculation (student_id);
CREATE INDEX idx_reward_calculation_rule            ON reward_calculation (rule_id);
CREATE INDEX idx_reward_calculation_period          ON reward_calculation (period_start, period_end);
CREATE INDEX idx_reward_calculation_paid            ON reward_calculation (is_paid);

-- تقييمات المرحلة
CREATE INDEX idx_phase_evaluation_student   ON phase_evaluation (student_id);
CREATE INDEX idx_phase_evaluation_teacher   ON phase_evaluation (teacher_id);
CREATE INDEX idx_phase_evaluation_stu_date  ON phase_evaluation (student_id, evaluation_date DESC);

-- القصائد
CREATE INDEX idx_poem_number ON poem (poem_number);


-- =====================================================================
--  تهيئة ranks/schemas
-- =====================================================================

-- ضمان أن كل شيء في public (الافتراضي لـ DATABASE_URL)
ALTER DATABASE postgres SET search_path TO public;

-- التطبيق يتصل كـ postgres عبر SQLAlchemy (Server-Side) — لا RLS مطلوب.
-- لكن نمنعها صراحةً احتياطاً لو استُخدم مفتاح anon من الواجهة someday.
DO $$
DECLARE t text;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

-- صلاحيات دور Supabase القياسية
GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON SEQUENCES TO postgres, anon, authenticated, service_role;


-- =====================================================================
--  التحقق (Verification) — يجب أن يُرجع 19 صفاً
-- =====================================================================
-- SELECT table_name FROM information_schema.tables
--  WHERE table_schema = 'public' ORDER BY table_name;

-- مطابقة أعمدة الجداول مع نماذج SQLAlchemy (يجب ألا يُرجع شيئاً):
-- SELECT table_name, column_name, data_type FROM information_schema.columns
--  WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;
