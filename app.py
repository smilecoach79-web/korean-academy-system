import html
import altair as alt
import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

# [수정 7] 서버(Streamlit Cloud)는 영국 기준 시간(UTC)으로 돌아가므로 한국 시간으로 고정
KST = timezone(timedelta(hours=9))


def today_kst():
    return datetime.now(KST).date()
from sqlalchemy import create_engine, text

# ----------------- DB 연결 설정 (Supabase PostgreSQL) -----------------
st.set_page_config(page_title="국어 전문 학생 종합 관리 시스템", layout="wide")

try:
    DB_URL = st.secrets["database"]["url"]
    ADMIN_PW = st.secrets["passwords"]["admin"]
except Exception:
    st.error("보안 설정(secrets.toml)을 찾을 수 없습니다. .streamlit/secrets.toml 파일을 확인해 주세요.")
    st.stop()


# [수정 5] 화면을 누를 때마다 DB 연결을 새로 만들지 않도록, 연결은 한 번만 만들어 재사용
@st.cache_resource
def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


engine = get_engine()


# [수정 5] 테이블 확인도 앱이 처음 켜질 때 한 번만 실행
@st.cache_resource
def init_db():
    with engine.begin() as conn:
        # 1. 학생 테이블
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                group_name TEXT,
                contact TEXT,
                notes TEXT,
                status TEXT DEFAULT '재원'
            );
        """))
        # [수정 2] 이미 만들어진 테이블에도 재원/퇴원(status) 칸이 없으면 추가
        conn.execute(text("ALTER TABLE members ADD COLUMN IF NOT EXISTS status TEXT DEFAULT '재원';"))

        # 2. 출결 테이블
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members (id) ON DELETE CASCADE,
                event_date TEXT,
                status TEXT
            );
        """))
        # 3. 국어 성적 테이블 (통합 공통국어 체제)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scores (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members (id) ON DELETE CASCADE,
                record_date TEXT,
                category TEXT,
                score REAL,
                feedback TEXT,
                reading_score REAL DEFAULT 0,
                literature_score REAL DEFAULT 0,
                grammar_score REAL DEFAULT 0,
                wrong_questions TEXT DEFAULT ''
            );
        """))
        # 4. 상담 일지 테이블
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members (id) ON DELETE CASCADE,
                log_date TEXT,
                topic TEXT,
                details TEXT
            );
        """))
        # [수정 12] 반 / 수업 시간 / 반 학생 테이블
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS classes (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                grade TEXT,
                notes TEXT,
                active BOOLEAN DEFAULT TRUE
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS class_schedules (
                id SERIAL PRIMARY KEY,
                class_id INTEGER REFERENCES classes (id) ON DELETE CASCADE,
                weekday INTEGER NOT NULL,      -- 0=월 ... 6=일
                start_time TEXT NOT NULL,      -- 'HH:MM'
                end_time TEXT NOT NULL
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS class_members (
                class_id INTEGER REFERENCES classes (id) ON DELETE CASCADE,
                member_id INTEGER REFERENCES members (id) ON DELETE CASCADE,
                PRIMARY KEY (class_id, member_id)
            );
        """))
        # 출결이 어느 반 수업인지 기록 (반을 지워도 출결 기록은 남도록 SET NULL)
        conn.execute(text("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS class_id INTEGER REFERENCES classes (id) ON DELETE SET NULL;"))
        # [수정 21] 시험 종류별 성적: 기존 성적은 자동으로 '모의고사'로 분류됨
        for col_sql in [
            "exam_type TEXT DEFAULT '모의고사'", "max_score REAL DEFAULT 100", "grade TEXT DEFAULT ''",
            "percentile REAL", "exam_scope TEXT DEFAULT ''", "school_term TEXT DEFAULT ''",
            "objective_score REAL", "essay_score REAL", "school_avg REAL",
            "class_id INTEGER REFERENCES classes (id) ON DELETE SET NULL", "quiz_format TEXT DEFAULT ''",
        ]:
            conn.execute(text(f"ALTER TABLE scores ADD COLUMN IF NOT EXISTS {col_sql};"))
        # [수정 19] 수업 일지: 반 + 날짜마다 수업 내용, 과제, 제출일
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS lessons (
                id SERIAL PRIMARY KEY,
                class_id INTEGER REFERENCES classes (id) ON DELETE CASCADE,
                lesson_date TEXT NOT NULL,
                content TEXT,
                homework TEXT,
                due_date TEXT,
                notes TEXT,
                UNIQUE (class_id, lesson_date)
            );
        """))
    return True


init_db()

WEEKDAYS = "월화수목금토일"

# [수정 21] 시험 종류
EXAM_TYPES = ["모의고사", "학교 내신", "수업 테스트", "기타"]
EXAM_ICONS = {"모의고사": "📊", "학교 내신": "🏫", "수업 테스트": "✏️", "기타": "📁"}
EXAM_HELP = {
    "모의고사": "독서·문학·문법(선택과목) 점수와 등급·백분위를 기록해요.",
    "학교 내신": "중간·기말고사 점수와 시험 범위, 객관식·서술형, 등급·성취도를 기록해요.",
    "수업 테스트": "OX·빈칸·단답 테스트를 반 학생 전체 표로 한 번에 입력해요. 맞은 개수만 적으면 정답률이 자동 계산돼요.",
    "기타": "사설 모의고사, 단원평가 등 점수와 만점만 기록해요.",
}
SCHOOL_TERMS = ["1학기 중간", "1학기 기말", "2학기 중간", "2학기 기말"]
QUIZ_FORMATS = ["OX", "빈칸", "단답", "혼합"]
SCORE_DEFAULTS = dict(feedback="", reading_score=None, literature_score=None, grammar_score=None,
                      wrong_questions="", max_score=100.0, grade="", percentile=None, exam_scope="",
                      school_term="", objective_score=None, essay_score=None, school_avg=None,
                      class_id=None, quiz_format="")


def trend_chart(d, ycols, y_title, y_max=None):
    """성적 추이 그래프: 가로 날짜 라벨 + 점마다 점수 표시 [수정 21]"""
    d = d.copy()
    d["x"] = [f"{r[5:].replace('-', '/')}" for r in d["record_date"]]
    # 같은 날 여러 시험이 있으면 라벨이 겹치지 않게 번호를 붙임
    seen = {}
    xs = []
    for x in d["x"]:
        seen[x] = seen.get(x, 0) + 1
        xs.append(x if seen[x] == 1 else f"{x}({seen[x]})")
    d["x"] = xs
    long = d.melt(id_vars=["x", "category"], value_vars=ycols, var_name="구분", value_name="값").dropna(subset=["값"])
    x_enc = alt.X("x:N", sort=list(d["x"]), title=None,
                  axis=alt.Axis(labelAngle=0, labelFontSize=11, labelLimit=80))
    y_scale = alt.Scale(domain=[0, y_max]) if y_max else alt.Scale(zero=True)
    y_enc = alt.Y("값:Q", title=y_title, scale=y_scale)
    color = alt.Color("구분:N", legend=alt.Legend(orient="top", title=None) if len(ycols) > 1 else None,
                      scale=alt.Scale(range=["#e63c3c", "#8b93a7"]))
    tip = [alt.Tooltip("x:N", title="날짜"), alt.Tooltip("category:N", title="시험"), alt.Tooltip("값:Q", title=y_title)]
    base = alt.Chart(long).encode(x=x_enc, y=y_enc, color=color, tooltip=tip)
    chart = (base.mark_line(point=alt.OverlayMarkDef(size=60), strokeWidth=2.5)
             + base.mark_text(dy=-12, fontSize=11, fontWeight="bold").encode(text=alt.Text("값:Q", format=".0f")))
    st.altair_chart(chart.properties(height=260), width="stretch")


def fmt_score(score, max_score):
    """84 / 100 처럼 점수 표시"""
    if score is None or pd.isna(score):
        return "-"
    mx = 100 if max_score is None or pd.isna(max_score) else max_score
    return f"{float(score):g} / {float(mx):g}"
STATUS_OPTIONS = ["출석", "지각", "결석", "보강완료"]


def schedule_text(rows):
    """수업 시간 여러 줄을 '토 20:30~22:00 · 일 12:30~14:00' 형태로"""
    return " · ".join(f"{WEEKDAYS[int(r.weekday)]} {r.start_time}~{r.end_time}" for r in rows.itertuples())


def md(text_):
    """화면 글자(마크다운)에서 '~' 두 개가 취소선으로 바뀌지 않게 막기"""
    return text_.replace("~", "\\~")

# [수정 4] 입력 화면(출결·성적·상담)에서 쓸 '재원생만' 조건
ACTIVE_ONLY = "WHERE status = '재원' OR status IS NULL"

# ----------------- 관리자 로그인 보안 -----------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    # [수정 17] 로그인 화면: 가운데 카드 형태로 정돈
    st.markdown("""
    <style>
    /* 로그인 화면에서는 사이드바 숨기고 배경을 은은하게 */
    section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none; }
    [data-testid="stApp"] { background: linear-gradient(160deg, #fff5f5 0%, #f4f6fb 45%, #eef1f8 100%); }
    /* 가운데 카드 (신버전/구버전 Streamlit 모두) */
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .login-head),
    [data-testid="stVerticalBlockBorderWrapper"]:has(.login-head) {
        background: #ffffff;
        border-radius: 20px !important;
        border: 1px solid #e3e6ee !important;
        box-shadow: 0 18px 40px rgba(30, 40, 70, 0.10), 0 2px 6px rgba(30, 40, 70, 0.05);
        padding: 28px 24px 20px 24px !important;
    }
    .login-head { text-align: center; margin: 8px 0 4px 0; }
    .login-logo {
        width: 72px; height: 72px; margin: 0 auto 14px auto; border-radius: 20px;
        display: flex; align-items: center; justify-content: center; font-size: 38px;
        background: linear-gradient(180deg, #ff6b6b 0%, #e63c3c 100%);
        box-shadow: 0 4px 0 #a82424, 0 8px 16px rgba(230, 60, 60, 0.30);
    }
    .login-title { font-size: 1.55rem; font-weight: 800; color: #262730; letter-spacing: -0.02em; }
    .login-sub { font-size: 0.92rem; color: #808495; margin-top: 4px; }
    .login-date {
        display: inline-block; margin-top: 14px; padding: 5px 14px; border-radius: 999px;
        background: #f1f3f8; color: #5f6475; font-size: 0.85rem;
    }
    .login-foot { text-align: center; color: #a3a8b8; font-size: 0.78rem; margin: 4px 0 2px 0; }
    /* 로그인 버튼도 메뉴처럼 입체감 있게 */
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .login-head) [data-testid="stFormSubmitButton"] button,
    [data-testid="stVerticalBlockBorderWrapper"]:has(.login-head) [data-testid="stFormSubmitButton"] button {
        background: linear-gradient(180deg, #ff6b6b 0%, #e63c3c 100%);
        border: 1px solid #c92f2f; color: #ffffff; font-weight: 700;
        padding: 10px 0; border-radius: 12px;
        box-shadow: 0 4px 0 #a82424, 0 6px 12px rgba(230, 60, 60, 0.25);
        transition: transform 0.08s, box-shadow 0.08s;
    }
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .login-head) [data-testid="stFormSubmitButton"] button:active,
    [data-testid="stVerticalBlockBorderWrapper"]:has(.login-head) [data-testid="stFormSubmitButton"] button:active {
        transform: translateY(3px); box-shadow: 0 1px 0 #a82424;
    }
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .login-head) [data-testid="stForm"],
    [data-testid="stVerticalBlockBorderWrapper"]:has(.login-head) [data-testid="stForm"] { border: none; padding: 0; }
    </style>
    """, unsafe_allow_html=True)

    t = today_kst()
    st.write("")
    st.write("")
    _, mid, _ = st.columns([1, 1.15, 1])
    with mid:
        with st.container(border=True):
            st.markdown(f"""
            <div class="login-head">
                <div class="login-logo">📚</div>
                <div class="login-title">국어 관리 시스템</div>
                <div class="login-sub">학생 · 출결 · 성적 · 상담을 한곳에서</div>
                <div class="login-date">{t.strftime('%Y년 %m월 %d일')} ({WEEKDAYS[t.weekday()]})</div>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            # 양식(form)으로 감싸서, 비밀번호 입력 후 Enter 키만 눌러도 로그인
            with st.form("login_form", border=False):
                input_pw = st.text_input("비밀번호", type="password", placeholder="관리자 비밀번호",
                                         label_visibility="collapsed")
                login_ok = st.form_submit_button("로그인", width="stretch")
            if login_ok:
                if input_pw == ADMIN_PW:
                    st.session_state.logged_in = True
                    st.rerun()
                else:
                    st.error("비밀번호가 올바르지 않습니다.")
            st.markdown("<div class='login-foot'>🔒 관리자 전용 · 학생 개인정보 보호</div>", unsafe_allow_html=True)
    st.stop()

# ----------------- 본 프로그램 화면 -----------------

# [수정 1] 메뉴 이름을 상수로 두고, 아래 조건문에서도 같은 이름을 쓰도록 통일
MENU_TODAY = "🏠  오늘"
MENU_MEMBERS = "👥  구성원 등록/목록"
MENU_ATT = "📅  출결·수업 기록"
MENU_SCORE = "📝  성적 및 국어 평가"
MENU_LOG = "💬  상담/소통 일지"
MENU_PORTFOLIO = "👤  개인별 종합 조회"
MENU_TIMETABLE = "🗓️  내 시간표"
MENU_CLASS = "🏫  반·시간표 관리"
MENU_BACKUP = "💾  데이터 백업"

# ----------------- 사이드바 꾸미기 -----------------
# 라디오 버튼의 동그라미를 숨기고, 메뉴 하나하나를 버튼처럼 보이게 함
st.markdown("""
<style>
/* 메뉴 항목 하나하나: 볼록 튀어나온 버튼
   (신버전 Streamlit: label[data-testid="stRadioOption"], 구버전: label[data-baseweb="radio"]) */
/* [수정 9] 메뉴 묶음이 사이드바 폭을 꽉 채우도록 */
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has([data-testid="stRadio"]),
section[data-testid="stSidebar"] [data-testid="stRadio"],
section[data-testid="stSidebar"] [data-testid="stRadio"] > div,
section[data-testid="stSidebar"] [role="radiogroup"],
section[data-testid="stSidebar"] [role="radiogroup"] > div { width: 100% !important; }
section[data-testid="stSidebar"] [role="radiogroup"] { align-items: stretch !important; }
section[data-testid="stSidebar"] label[data-testid="stRadioOption"],
section[data-testid="stSidebar"] label[data-baseweb="radio"] {
    display: flex;
    width: 100%;
    box-sizing: border-box;
    padding: 12px 16px;
    margin: 0 0 10px 0;
    border-radius: 12px;
    background: linear-gradient(180deg, #ffffff 0%, #eef0f4 100%);
    border: 1px solid #d5d9e0;
    box-shadow: 0 4px 0 #c3c8d1, 0 6px 10px rgba(0, 0, 0, 0.10);
    transition: transform 0.08s, box-shadow 0.08s;
    cursor: pointer;
}
/* 라디오 동그라미 숨기기 */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child:not([data-testid]),
section[data-testid="stSidebar"] label[data-baseweb="radio"] > div:first-child {
    display: none;
}
/* 마우스를 올렸을 때: 살짝 떠오름 */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover,
section[data-testid="stSidebar"] label[data-baseweb="radio"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 5px 0 #c3c8d1, 0 8px 14px rgba(0, 0, 0, 0.14);
}
/* 누르는 순간: 쑥 눌림 */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:active,
section[data-testid="stSidebar"] label[data-baseweb="radio"]:active {
    transform: translateY(3px);
    box-shadow: 0 1px 0 #c3c8d1, 0 2px 4px rgba(0, 0, 0, 0.10);
}
/* 지금 선택된 메뉴: 눌린 채로 색이 들어간 버튼 */
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"],
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked),
section[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {
    background: linear-gradient(180deg, #ff6b6b 0%, #e63c3c 100%);
    border-color: #c92f2f;
    transform: translateY(3px);
    box-shadow: 0 1px 0 #a82424, inset 0 2px 4px rgba(0, 0, 0, 0.25);
}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] p,
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) p,
section[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) p {
    color: #ffffff;
    font-weight: 700;
}
/* [수정 19] 한글이 단어 중간에서 줄바꿈되지 않도록 (예: '풀 / 어오기' 방지) */
[data-testid="stMarkdownContainer"] p,
[data-testid="stCaptionContainer"] { word-break: keep-all; overflow-wrap: anywhere; }
/* 메뉴 글자 크기 */
section[data-testid="stSidebar"] [role="radiogroup"] p {
    font-size: 1.02rem;
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    # [수정 22] 사이드바 제목과 날짜 글씨 키우기
    _t = today_kst()
    st.markdown(f"""
    <div style="margin: 4px 0 6px 0;">
        <div style="font-size: 1.85rem; font-weight: 800; color: #262730; letter-spacing: -0.04em; line-height: 1.3; white-space: nowrap;">
            📚 국어 관리 시스템
        </div>
        <div style="font-size: 1.15rem; color: #5f6475; margin-top: 8px;">
            오늘 {_t.strftime('%Y년 %m월 %d일')} ({WEEKDAYS[_t.weekday()]})
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()
    menu = st.radio("메뉴 선택", [
        MENU_TODAY, MENU_TIMETABLE, MENU_ATT, MENU_SCORE, MENU_LOG, MENU_PORTFOLIO,
        MENU_MEMBERS, MENU_CLASS, MENU_BACKUP
    ], label_visibility="collapsed", key="menu")
    st.divider()
    if st.button("🔒 로그아웃", width="stretch"):
        st.session_state.logged_in = False
        st.rerun()

# 저장 후 화면을 새로 그려도 완료 메시지가 보이도록 (오른쪽 아래 알림) [수정 16]
if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))


# 오늘 수업 카드의 '출결 입력' 버튼: 출결 메뉴로 이동하면서 그 반을 미리 선택 [수정 13]
def go_class_attendance(class_name):
    st.session_state["menu"] = MENU_ATT
    st.session_state["bulk_class"] = class_name
    st.session_state["bulk_date"] = today_kst()


# [수정 23] 어디서든 학생을 누르면 그 학생의 개인별 종합 조회로 이동
def go_student(member_id):
    st.session_state["menu"] = MENU_PORTFOLIO
    st.session_state["pf_target_id"] = int(member_id)


def student_button(member_id, name, key):
    st.button(f"👤 {name} 학생 종합 조회 →", key=key, on_click=go_student, args=(member_id,), type="primary")


# 0. 오늘 (첫 화면) [수정 10, 13]
if menu == MENU_TODAY:
    t = today_kst()
    t_str = str(t)
    week_ago = str(t - timedelta(days=7))
    st.header(f"🏠 오늘 · {t.strftime('%m월 %d일')} ({WEEKDAYS[t.weekday()]})")

    with engine.connect() as conn:
        active = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY group_name, name", conn)
        today_classes = pd.read_sql_query(text("""
            SELECT c.id, c.name, s.start_time, s.end_time
            FROM class_schedules s JOIN classes c ON c.id = s.class_id
            WHERE c.active AND s.weekday = :wd
            ORDER BY s.start_time, c.name
        """), conn, params={"wd": t.weekday()})
        class_roster = pd.read_sql_query(f"""
            SELECT cm.class_id, cm.member_id
            FROM class_members cm JOIN members m ON m.id = cm.member_id
            {ACTIVE_ONLY.replace('status', 'm.status')}
        """, conn)
        att_today = pd.read_sql_query(text("""
            SELECT a.member_id, a.class_id, m.name AS 이름, m.group_name AS 소속,
                   COALESCE(c.name, '') AS 반, a.status AS 상태
            FROM attendance a JOIN members m ON a.member_id = m.id
            LEFT JOIN classes c ON c.id = a.class_id
            WHERE a.event_date = :d ORDER BY c.name, m.name
        """), conn, params={"d": t_str})
        log_week = conn.execute(text("SELECT COUNT(*) FROM logs WHERE log_date >= :d"), {"d": week_ago}).scalar()
        recent_logs = pd.read_sql_query("""
            SELECT l.log_date AS 일자, m.name AS 이름, m.group_name AS 소속, l.topic AS 주제,
                   l.details, l.member_id
            FROM logs l JOIN members m ON l.member_id = m.id
            ORDER BY l.log_date DESC, l.id DESC LIMIT 5
        """, conn)
        recent_scores = pd.read_sql_query("""
            SELECT s.record_date AS 일자, COALESCE(s.exam_type, '모의고사') AS 종류, m.name AS 이름,
                   s.category AS 시험, s.score, s.max_score, s.member_id, m.group_name,
                   s.grade, s.exam_scope, s.quiz_format, s.wrong_questions, s.feedback,
                   s.reading_score, s.literature_score, s.grammar_score, s.school_avg
            FROM scores s JOIN members m ON s.member_id = m.id
            ORDER BY s.record_date DESC, s.id DESC LIMIT 5
        """, conn)
        if not recent_scores.empty:
            recent_scores["점수"] = [fmt_score(a, b) for a, b in zip(recent_scores["score"], recent_scores["max_score"])]

    # 오늘 출석해야 하는 학생: 오늘 수업이 있으면 그 반 학생들, 없으면 재원생 전체
    if not today_classes.empty:
        expected_ids = set(class_roster[class_roster["class_id"].isin(today_classes["id"])]["member_id"])
    else:
        expected_ids = set(active["id"])
    entered_ids = set(att_today["member_id"]) if not att_today.empty else set()
    absent_late = att_today[att_today["상태"].isin(["결석", "지각"])] if not att_today.empty else att_today

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("오늘 수업", f"{today_classes['id'].nunique()}개")
    c2.metric("오늘 출결 입력", f"{len(entered_ids & expected_ids)} / {len(expected_ids)}명")
    c3.metric("오늘 결석·지각", f"{len(absent_late)}명")
    c4.metric("최근 7일 상담", f"{log_week}건")

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("📚 오늘 수업")
        if today_classes.empty:
            st.info("오늘은 등록된 수업이 없습니다. '반·시간표 관리'에서 수업 시간을 등록하면 여기에 나타나요.")
        else:
            # [수정 19] 반별 수업 기록: 오늘 기록 여부 + 지난 시간 과제
            with engine.connect() as conn:
                lesson_hist = pd.read_sql_query(text("""
                    SELECT class_id, lesson_date, content, homework, due_date, notes FROM lessons
                    WHERE lesson_date <= :d ORDER BY lesson_date DESC
                """), conn, params={"d": t_str})
            for r in today_classes.itertuples():
                roster_ids = set(class_roster[class_roster["class_id"] == r.id]["member_id"])
                my_ls = lesson_hist[lesson_hist["class_id"] == r.id]
                logged_today = (my_ls["lesson_date"] == t_str).any()
                prev_hw = my_ls[(my_ls["lesson_date"] < t_str) & (my_ls["homework"].fillna("").str.strip() != "")]
                done = len(set(att_today[att_today["class_id"] == r.id]["member_id"])) if not att_today.empty else 0
                with st.container(border=True):
                    a, b = st.columns([3, 1.3])
                    a.markdown(md(f"**{r.start_time}~{r.end_time}**　{r.name}"))
                    if done == 0:
                        a.caption(f"학생 {len(roster_ids)}명 · 출결 미입력")
                    elif done >= len(roster_ids):
                        a.caption(f"학생 {len(roster_ids)}명 · ✅ 출결 완료")
                    else:
                        a.caption(f"학생 {len(roster_ids)}명 · 출결 {done}/{len(roster_ids)}명 입력")
                    if not prev_hw.empty:
                        h = prev_hw.iloc[0]
                        a.markdown(md(f"📝 **지난 과제** ({h['lesson_date'][5:].replace('-', '/')} 수업): {h['homework']}"))
                    b.button("출결·수업 기록 →", key=f"go_att_{r.id}_{r.start_time}",
                             on_click=go_class_attendance, args=(r.name,), width="stretch")
                    # [수정 23] 오늘 수업 기록을 그 자리에서 펼쳐 보기
                    if logged_today:
                        lt = my_ls[my_ls["lesson_date"] == t_str].iloc[0]
                        with a.popover("📖 오늘 수업 기록 ✅ 보기"):
                            st.markdown(f"**{r.name}** · {t.strftime('%m/%d')} {r.start_time}~{r.end_time}")
                            st.markdown("**수업 내용**")
                            st.write(md(lt["content"] or "-"))
                            st.markdown("**과제**")
                            hw_txt = lt["homework"] or "-"
                            if lt["homework"] and lt["due_date"]:
                                hw_txt += f"  \n(제출: {lt['due_date'][5:].replace('-', '/')})"
                            st.write(md(hw_txt))
                            if lt["notes"]:
                                st.caption(md(f"메모: {lt['notes']}"))
                            my_att = att_today[att_today["class_id"] == r.id] if not att_today.empty else att_today
                            if not my_att.empty:
                                st.markdown("**오늘 출결**")
                                for stt in STATUS_OPTIONS:
                                    who = my_att[my_att["상태"] == stt]["이름"].tolist()
                                    if who:
                                        st.write(f"{stt} {len(who)}명: " + ", ".join(who))
                    else:
                        a.caption("📖 오늘 수업 기록 아직 없음")

        st.subheader("📅 오늘 출결")
        if att_today.empty:
            st.info("오늘 입력된 출결이 아직 없습니다.")
        else:
            st.caption("👆 맨 왼쪽 네모 칸을 누르면 그 학생의 종합 조회로 갈 수 있어요.")
            ev_att = st.dataframe(att_today.drop(columns=["member_id", "class_id"]), hide_index=True, width="stretch",
                                  on_select="rerun", selection_mode="single-row", key="today_att_tbl")
            if ev_att.selection.rows:
                ra = att_today.iloc[ev_att.selection.rows[0]]
                student_button(ra["member_id"], ra["이름"], "go_stu_att")

    with col_r:
        st.subheader("💬 최근 상담")
        if recent_logs.empty:
            st.info("상담 기록이 없습니다.")
        else:
            st.caption("👆 맨 왼쪽 네모 칸을 누르면 상담 내용 전체가 보여요.")
            ev_lg = st.dataframe(recent_logs[["일자", "이름", "소속", "주제"]], hide_index=True, width="stretch",
                                 on_select="rerun", selection_mode="single-row", key="today_log_tbl")
            if ev_lg.selection.rows:
                rl = recent_logs.iloc[ev_lg.selection.rows[0]]
                with st.container(border=True):
                    st.markdown(f"**{rl['일자']} · {rl['이름']}** ({rl['소속'] or '소속 없음'})")
                    st.markdown(f"**주제:** {rl['주제']}")
                    st.write(md(rl["details"] or "(상세 내용 없음)"))
                    student_button(rl["member_id"], rl["이름"], "go_stu_log")

        st.subheader("📝 최근 시험 결과")
        if recent_scores.empty:
            st.info("성적 기록이 없습니다.")
        else:
            st.caption("👆 맨 왼쪽 네모 칸을 누르면 범위·틀린 문항·피드백이 보여요.")
            ev_sc = st.dataframe(recent_scores[["일자", "종류", "이름", "시험", "점수"]], hide_index=True, width="stretch",
                                 on_select="rerun", selection_mode="single-row", key="today_score_tbl")
            if ev_sc.selection.rows:
                rs = recent_scores.iloc[ev_sc.selection.rows[0]]

                def _v(x):
                    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)

                with st.container(border=True):
                    st.markdown(f"**{EXAM_ICONS.get(rs['종류'], '')} {rs['시험']}** · {rs['일자']}")
                    st.markdown(f"**{rs['이름']}** ({_v(rs['group_name']) or '소속 없음'}) · 점수 **{rs['점수']}**"
                                + (f" · {rs['grade']}" if _v(rs['grade']) else ""))
                    if rs["종류"] == "수업 테스트" and rs["max_score"]:
                        st.caption(f"정답률 {float(rs['score']) / float(rs['max_score']) * 100:.0f}%"
                                   + (f" · 유형 {rs['quiz_format']}" if _v(rs['quiz_format']) else ""))
                    if rs["종류"] == "모의고사" and _v(rs["reading_score"]):
                        st.caption(f"독서 {float(rs['reading_score']):g} · 문학 {float(rs['literature_score'] or 0):g} · "
                                   f"문법·화법 {float(rs['grammar_score'] or 0):g}")
                    if _v(rs["school_avg"]):
                        st.caption(f"학교 평균 {float(rs['school_avg']):g}")
                    if _v(rs["exam_scope"]):
                        st.write(md(f"**범위:** {rs['exam_scope']}"))
                    if _v(rs["wrong_questions"]):
                        st.write(md(f"**틀린 문항:** {rs['wrong_questions']}"))
                    if _v(rs["feedback"]):
                        st.write(md(f"**피드백:** {rs['feedback']}"))
                    student_button(rs["member_id"], rs["이름"], "go_stu_score")

# 1. 구성원 등록 및 목록
elif menu == MENU_MEMBERS:
    st.header("👥 구성원 등록 및 목록")
    col_add, col_edit, col_del = st.columns([1, 1, 1])

    # [수정 18] 등록 버튼을 누르면 실행: 같은 이름이 있으면 먼저 확인, 저장 후 입력칸 비우기
    def add_member_cb():
        nm = st.session_state.get("new_name", "").strip()
        if not nm:
            st.session_state["add_msg"] = ("warning", "학생 이름을 입력해 주세요.")
            return
        with engine.connect() as conn:
            dup = pd.read_sql_query(text("SELECT id, name, group_name, status FROM members WHERE name = :n ORDER BY id"),
                                    conn, params={"n": nm})
        if not dup.empty and not st.session_state.get("new_dup_ok", False):
            who = ", ".join(f"{r['name']}({r['group_name'] or '소속 없음'}, {r['status'] or '재원'}) · ID {r['id']}"
                            for _, r in dup.iterrows())
            st.session_state["add_msg"] = ("warning",
                f"이미 같은 이름이 등록되어 있어요 → {who}\n\n"
                "동명이인이 맞다면 위의 '동명이인이 맞습니다'를 체크하고 다시 등록하세요.")
            return
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO members (name, group_name, contact, notes, status)
                VALUES (:name, :group_name, :contact, :notes, '재원')
            """), {"name": nm, "group_name": st.session_state.get("new_group", "").strip(),
                   "contact": st.session_state.get("new_contact", "").strip(),
                   "notes": st.session_state.get("new_notes", "").strip()})
        for k in ["new_name", "new_group", "new_contact", "new_notes"]:
            st.session_state[k] = ""
        st.session_state["new_dup_ok"] = False
        st.session_state["flash"] = f"✅ '{nm}' 학생을 등록했어요."

    with col_add:
        with st.expander("➕ 새 학생 등록", expanded=False):
            with st.form("add_member_form"):
                st.text_input("학생 이름 *", key="new_name")
                st.text_input("학교 및 학년/반 (예: 비전고1, 한광고2)", key="new_group")
                st.text_input("연락처", key="new_contact")
                st.text_input("기본 메모 (취약 영역, 목표 등급/대학)", key="new_notes")
                st.checkbox("동명이인이 맞습니다 (같은 이름의 다른 학생)", key="new_dup_ok")
                st.form_submit_button("등록하기", on_click=add_member_cb)
            if "add_msg" in st.session_state:
                kind, msg = st.session_state.pop("add_msg")
                st.warning(msg)

    with col_edit:
        with st.expander("✏️ 학생 정보 수정", expanded=False):
            with engine.connect() as conn:
                m_list_edit = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY id ASC", conn)

            if m_list_edit.empty:
                st.info("등록된 학생이 없습니다.")
            else:
                edit_options = {f"{row['name']} ({row['group_name']}) · ID {row['id']}": row['id'] for _, row in m_list_edit.iterrows()}
                target_edit_label = st.selectbox("수정할 대상 선택", list(edit_options.keys()), key="edit_selector")
                target_edit_id = edit_options[target_edit_label]

                with engine.connect() as conn:
                    curr_info = pd.read_sql_query(text("SELECT * FROM members WHERE id = :id"), conn, params={"id": target_edit_id}).iloc[0]

                with st.form("edit_member_form"):
                    edit_name = st.text_input("학생 이름 *", value=curr_info['name'])
                    edit_group = st.text_input("소속 / 반", value=curr_info['group_name'] if curr_info['group_name'] else "")
                    edit_contact = st.text_input("연락처", value=curr_info['contact'] if curr_info['contact'] else "")
                    edit_notes = st.text_input("기본 메모", value=curr_info['notes'] if curr_info['notes'] else "")
                    submit_edit = st.form_submit_button("수정사항 저장")

                    if submit_edit and edit_name:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                UPDATE members
                                SET name = :name, group_name = :group_name, contact = :contact, notes = :notes
                                WHERE id = :id
                            """), {"name": edit_name, "group_name": edit_group, "contact": edit_contact, "notes": edit_notes, "id": target_edit_id})
                        st.success(f"'{edit_name}' 학생 정보가 수정되었습니다.")
                        st.rerun()

    with col_del:
        with st.expander("⚙️ 퇴원 처리 · 삭제", expanded=False):
            with engine.connect() as conn:
                m_list = pd.read_sql_query("SELECT id, name, group_name, status FROM members ORDER BY id ASC", conn)

            if m_list.empty:
                st.info("등록된 인원이 없습니다.")
            else:
                # 동명이인 구분을 위해 ID를 함께 표시
                del_options = {
                    f"[{row['status'] or '재원'}] {row['name']} ({row['group_name']}) · ID {row['id']}": row['id']
                    for _, row in m_list.iterrows()
                }
                target_del_label = st.selectbox("대상 원생 선택", list(del_options.keys()), key="del_selector")
                target_id = del_options[target_del_label]

                st.caption("※ 퇴원 처리해도 과거 성적, 출결, 상담 기록은 안전하게 보존됩니다.")
                col_b1, col_b2 = st.columns(2)

                with col_b1:
                    if st.button("🚫 '퇴원' 처리", width="stretch"):
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE members SET status = '퇴원' WHERE id = :id"), {"id": target_id})
                        st.session_state["flash"] = "🚫 퇴원 처리했어요. 명단과 입력 화면에서 숨겨집니다."
                        st.rerun()

                with col_b2:
                    if st.button("🔄 '재원' 복원", width="stretch"):
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE members SET status = '재원' WHERE id = :id"), {"id": target_id})
                        st.session_state["flash"] = "🔄 재원생으로 복원했어요."
                        st.rerun()

                # [수정 18] 잘못 등록한 학생 완전 삭제
                st.divider()
                st.markdown("**🗑️ 잘못 등록한 학생 완전 삭제**")
                with engine.connect() as conn:
                    linked = conn.execute(text("""
                        SELECT (SELECT COUNT(*) FROM attendance WHERE member_id = :id),
                               (SELECT COUNT(*) FROM scores WHERE member_id = :id),
                               (SELECT COUNT(*) FROM logs WHERE member_id = :id),
                               (SELECT COUNT(*) FROM class_members WHERE member_id = :id)
                    """), {"id": int(target_id)}).fetchone()
                n_att, n_sc, n_lg, n_cls = [int(x) for x in linked]
                if n_att + n_sc + n_lg == 0:
                    st.caption("이 학생에게 연결된 출결·성적·상담 기록이 없어 안전하게 지울 수 있어요.")
                else:
                    st.warning(f"이 학생의 출결 {n_att}건, 성적 {n_sc}건, 상담 {n_lg}건도 함께 영구 삭제됩니다. "
                               "그만둔 학생이라면 삭제 대신 '퇴원' 처리를 권해요.")
                confirm_m_del = st.checkbox("위 학생을 완전히 삭제합니다 (되돌릴 수 없음)", key=f"confirm_mdel_{target_id}")
                if st.button("완전 삭제", type="primary", width="stretch", key="member_hard_delete"):
                    if not confirm_m_del:
                        st.warning("삭제하려면 위의 확인 칸을 먼저 체크해 주세요.")
                    else:
                        del_name = m_list[m_list["id"] == target_id].iloc[0]["name"]
                        with engine.begin() as conn:
                            # 출결·성적·상담·반 배정은 ON DELETE CASCADE로 함께 지워짐
                            conn.execute(text("DELETE FROM members WHERE id = :id"), {"id": int(target_id)})
                        st.session_state.pop("del_selector", None)
                        st.session_state["flash"] = f"🗑️ '{del_name}' (ID {target_id}) 학생을 삭제했어요."
                        st.rerun()

    st.divider()
    st.subheader("📋 전체 학생 명단 검색 및 조회")

    with engine.connect() as conn:
        df_members_raw = pd.read_sql_query(f"""
            SELECT m.id AS "ID", m.name AS 이름, m.group_name AS 소속_학교,
                   (SELECT string_agg(c.name, ', ' ORDER BY c.name)
                      FROM class_members cm JOIN classes c ON c.id = cm.class_id
                     WHERE cm.member_id = m.id) AS 수강반,
                   m.contact AS 연락처, m.notes AS 기본메모
            FROM members m {ACTIVE_ONLY} ORDER BY m.id ASC
        """, conn)

    if df_members_raw.empty:
        st.info("등록된 학생이 없습니다. 새 학생을 등록해 보세요.")
    else:
        f_col1, f_col2 = st.columns([1, 2])
        all_groups = ["전체"] + sorted([g for g in df_members_raw['소속_학교'].dropna().unique() if g])
        selected_grp = f_col1.selectbox("🏫 학교/그룹별 필터", all_groups, key="filter_group_list")
        search_kw = f_col2.text_input("🔍 이름·반·메모 검색", placeholder="학생 이름, 반 이름, 메모 키워드를 입력하세요", key="search_kw_list")

        filtered_df = df_members_raw.copy()
        if selected_grp != "전체":
            filtered_df = filtered_df[filtered_df['소속_학교'] == selected_grp]
        if search_kw.strip():
            kw = search_kw.strip()
            filtered_df = filtered_df[
                filtered_df['이름'].str.contains(kw, na=False) |
                filtered_df['기본메모'].str.contains(kw, na=False) |
                filtered_df['수강반'].str.contains(kw, na=False)
            ]

        st.caption(f"검색 결과: 총 **{len(filtered_df)}명** / 전체 {len(df_members_raw)}명")
        # [수정 11] 왼쪽 0,1,2 번호 칸 숨기고, 빈칸의 None은 비워서 표시
        st.dataframe(filtered_df.fillna(""), hide_index=True, width="stretch")

# 2. 출결 체크
elif menu == MENU_ATT:
    st.header("2. 출석 및 수업 참여 관리")
    with engine.connect() as conn:
        # [수정 4] 퇴원생은 입력 목록에서 제외
        members = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        status_options = STATUS_OPTIONS

        # ----- [수정 14] 반 단위 출결 한 번에 입력 -----
        st.subheader("🏫 반 단위 출결")
        with engine.connect() as conn:
            classes_df = pd.read_sql_query("SELECT id, name FROM classes WHERE active ORDER BY name", conn)
            sched_df = pd.read_sql_query("SELECT class_id, weekday, start_time, end_time FROM class_schedules ORDER BY weekday, start_time", conn)

        if classes_df.empty:
            st.info("'반·시간표 관리'에서 반을 만들고 학생을 배정하면, 여기서 반 학생 전체 출결을 한 번에 입력할 수 있어요.")
        else:
            cls_opts = {r["name"]: int(r["id"]) for _, r in classes_df.iterrows()}
            names = list(cls_opts.keys())
            # 처음 들어왔을 때는 오늘 수업이 있는 반을 먼저 골라 둠
            if st.session_state.get("bulk_class") not in names:
                today_ids = set(sched_df[sched_df["weekday"] == today_kst().weekday()]["class_id"])
                today_names = [n for n in names if cls_opts[n] in today_ids]
                st.session_state["bulk_class"] = today_names[0] if today_names else names[0]
            if "bulk_date" not in st.session_state:
                st.session_state["bulk_date"] = today_kst()

            b1, b2 = st.columns([2, 1])
            cname = b1.selectbox("반 선택", names, key="bulk_class")
            bdate = b2.date_input("수업 날짜", key="bulk_date")
            cid = cls_opts[cname]

            my_sched = sched_df[sched_df["class_id"] == cid]
            if my_sched.empty:
                st.caption("수업 시간이 아직 등록되지 않은 반입니다.")
            else:
                st.caption(md(f"수업 시간: {schedule_text(my_sched)}"))
                if bdate.weekday() not in set(my_sched["weekday"]):
                    st.warning(f"{bdate.strftime('%m월 %d일')}({WEEKDAYS[bdate.weekday()]})은 이 반의 정규 수업 요일이 아닙니다. 보강이라면 그대로 저장해도 됩니다.")

            with engine.connect() as conn:
                roster = pd.read_sql_query(text("""
                    SELECT m.id, m.name, m.group_name
                    FROM class_members cm JOIN members m ON m.id = cm.member_id
                    WHERE cm.class_id = :c AND (m.status = '재원' OR m.status IS NULL)
                    ORDER BY m.name
                """), conn, params={"c": cid})
                existing = pd.read_sql_query(text(
                    "SELECT member_id, status FROM attendance WHERE class_id = :c AND event_date = :d"
                ), conn, params={"c": cid, "d": str(bdate)})

            if roster.empty:
                st.info("이 반에 배정된 학생이 없습니다. '반·시간표 관리 → ③ 학생 배정'에서 추가해 주세요.")
            else:
                prev = dict(zip(existing["member_id"], existing["status"]))
                edit_df = pd.DataFrame({
                    "학생ID": roster["id"],
                    "이름": roster["name"],
                    "소속": roster["group_name"].fillna(""),
                    "출결": [prev.get(i, "출석") for i in roster["id"]],
                })
                if prev:
                    st.caption("✅ 이 날짜에 저장된 출결을 불러왔어요. 고친 뒤 다시 저장하면 덮어씁니다.")
                else:
                    st.caption("모두 '출석'으로 채워 두었어요. 지각·결석인 학생만 바꾸고 저장하세요.")

                edited = st.data_editor(
                    edit_df,
                    column_config={
                        "학생ID": None,
                        "출결": st.column_config.SelectboxColumn("출결", options=STATUS_OPTIONS, required=True),
                    },
                    disabled=["이름", "소속"],
                    hide_index=True,
                    width="stretch",
                    key=f"bulk_editor_{cid}_{bdate}",
                )
                if st.button(f"💾 {cname} 출결 {len(edited)}명 한 번에 저장", type="primary"):
                    with engine.begin() as conn:
                        for _, r in edited.iterrows():
                            conn.execute(text(
                                "DELETE FROM attendance WHERE member_id = :m AND event_date = :d AND class_id = :c"
                            ), {"m": int(r["학생ID"]), "d": str(bdate), "c": cid})
                            conn.execute(text("""
                                INSERT INTO attendance (member_id, event_date, status, class_id)
                                VALUES (:m, :d, :s, :c)
                            """), {"m": int(r["학생ID"]), "d": str(bdate), "s": r["출결"], "c": cid})
                    counts = edited["출결"].value_counts()
                    st.success(f"{cname} {bdate} 출결 저장 완료 · " + ", ".join(f"{k} {v}명" for k, v in counts.items()))

            # ----- [수정 19] 수업 내용 · 과제 -----
            st.markdown(f"#### 📖 {bdate.strftime('%m월 %d일')}({WEEKDAYS[bdate.weekday()]}) 수업 내용 · 과제")
            with engine.connect() as conn:
                cur_ls = pd.read_sql_query(text(
                    "SELECT content, homework, due_date, notes FROM lessons WHERE class_id = :c AND lesson_date = :d"
                ), conn, params={"c": cid, "d": str(bdate)})
                past_ls = pd.read_sql_query(text("""
                    SELECT lesson_date AS 날짜, content AS 수업내용, homework AS 과제, due_date AS 제출일, notes AS 메모
                    FROM lessons WHERE class_id = :c ORDER BY lesson_date DESC LIMIT 30
                """), conn, params={"c": cid})

            # 과제 제출일 기본값: 이 반의 다음 수업 날짜
            next_class = None
            reg_days = set(int(w) for w in my_sched["weekday"]) if not my_sched.empty else set()
            for k in range(1, 15):
                d2 = bdate + timedelta(days=k)
                if d2.weekday() in reg_days:
                    next_class = d2
                    break

            has_ls = not cur_ls.empty
            c_val = cur_ls.iloc[0] if has_ls else None
            if has_ls:
                st.caption("✅ 이 날짜에 저장된 수업 기록을 불러왔어요. 고친 뒤 저장하면 덮어씁니다.")
            due_default = None
            if has_ls and c_val["due_date"]:
                try:
                    due_default = datetime.strptime(c_val["due_date"], "%Y-%m-%d").date()
                except ValueError:
                    due_default = None
            elif not has_ls:
                due_default = next_class

            with st.form(f"lesson_form_{cid}_{bdate}"):
                l_content = st.text_area("수업 내용", value=(c_val["content"] or "") if has_ls else "",
                                         placeholder="고전시가 – 관동별곡(정철) 서사·본사1 해석, 화자의 여정과 정서 변화", height=90)
                l_hw = st.text_area("과제", value=(c_val["homework"] or "") if has_ls else "",
                                    placeholder="관동별곡 기출 문제 풀어오기 (교재 p.45~52)", height=70)
                f1, f2 = st.columns([1, 2])
                l_due = f1.date_input("과제 제출일", value=due_default,
                                      help="기본값은 이 반의 다음 수업 날짜예요. 과제가 없으면 비워 두세요.")
                l_notes = f2.text_input("메모 (선택)", value=(c_val["notes"] or "") if has_ls else "",
                                        placeholder="진도 덜 나감, 다음 시간 본사2부터")
                g1, g2 = st.columns([1, 1])
                save_ls = g1.form_submit_button("📖 수업 기록 저장", type="primary", width="stretch")
                del_ls = False
                if has_ls:
                    confirm_ls_del = g2.checkbox("이 날짜 수업 기록 삭제")
                    del_ls = g2.form_submit_button("기록 삭제")
            if save_ls:
                if not l_content.strip() and not l_hw.strip():
                    st.warning("수업 내용이나 과제 중 하나는 입력해 주세요.")
                else:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO lessons (class_id, lesson_date, content, homework, due_date, notes)
                            VALUES (:c, :d, :ct, :hw, :due, :nt)
                            ON CONFLICT (class_id, lesson_date)
                            DO UPDATE SET content = EXCLUDED.content, homework = EXCLUDED.homework,
                                          due_date = EXCLUDED.due_date, notes = EXCLUDED.notes
                        """), {"c": cid, "d": str(bdate), "ct": l_content.strip(), "hw": l_hw.strip(),
                               "due": str(l_due) if (l_due and l_hw.strip()) else None, "nt": l_notes.strip()})
                    st.session_state["flash"] = f"📖 {cname} {bdate.strftime('%m/%d')} 수업 기록을 저장했어요."
                    st.rerun()
            if del_ls:
                if not confirm_ls_del:
                    st.warning("삭제하려면 '이 날짜 수업 기록 삭제'를 먼저 체크해 주세요.")
                else:
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM lessons WHERE class_id = :c AND lesson_date = :d"),
                                     {"c": cid, "d": str(bdate)})
                    st.session_state["flash"] = "🗑️ 수업 기록을 삭제했어요."
                    st.rerun()

            with st.expander(f"📚 {cname} 지난 수업 기록 ({len(past_ls)}회)"):
                if past_ls.empty:
                    st.caption("아직 저장된 수업 기록이 없습니다.")
                else:
                    st.dataframe(past_ls.fillna(""), hide_index=True, width="stretch")

        st.divider()
        col_att_add, col_att_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}

        with col_att_add:
            with st.expander("➕ 학생 한 명씩 입력 (보강·개별 수업)", expanded=False):
                with st.form("attendance_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="att_add_target")
                    att_date = st.date_input("날짜", today_kst(), key="att_add_date")
                    status = st.radio("상태", status_options, horizontal=True)
                    submit_att = st.form_submit_button("출결 저장")

                    if submit_att:
                        m_id = member_dict[target_member]
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO attendance (member_id, event_date, status)
                                VALUES (:member_id, :event_date, :status)
                            """), {"member_id": m_id, "event_date": str(att_date), "status": status})
                        st.success("출결이 클라우드에 저장되었습니다.")
                        st.rerun()

        with col_att_edit:
            with st.expander("✏️/🗑️ 출결 수정 및 삭제", expanded=False):
                with engine.connect() as conn:
                    att_for_edit = pd.read_sql_query("""
                        SELECT a.id, a.event_date, a.status, m.name, m.group_name
                        FROM attendance a
                        JOIN members m ON a.member_id = m.id
                        ORDER BY a.id DESC LIMIT 50
                    """, conn)

                if att_for_edit.empty:
                    st.info("수정할 출결 데이터가 없습니다.")
                else:
                    att_opts = {
                        f"[{row['event_date']}] {row['name']} - {row['status']} (ID:{row['id']})": row['id']
                        for _, row in att_for_edit.iterrows()
                    }
                    selected_att_label = st.selectbox("수정/삭제할 출결 선택", list(att_opts.keys()))
                    selected_att_id = att_opts[selected_att_label]
                    target_att = att_for_edit[att_for_edit['id'] == selected_att_id].iloc[0]

                    with st.form("att_edit_form"):
                        try:
                            curr_dt = datetime.strptime(target_att['event_date'], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            curr_dt = today_kst()
                        ed_att_date = st.date_input("날짜 수정", curr_dt)
                        curr_status_idx = status_options.index(target_att['status']) if target_att['status'] in status_options else 0
                        ed_att_status = st.radio("상태 수정", status_options, index=curr_status_idx, horizontal=True)

                        # [수정 6] 삭제는 체크해야만 실행
                        confirm_att_del = st.checkbox("이 출결 기록을 삭제합니다 (되돌릴 수 없음)")

                        btn_c1, btn_c2 = st.columns(2)
                        save_att = btn_c1.form_submit_button("수정 저장")
                        del_att = btn_c2.form_submit_button("기록 삭제", type="primary")

                        if save_att:
                            with engine.begin() as conn:
                                conn.execute(text("UPDATE attendance SET event_date = :dt, status = :st WHERE id = :id"),
                                             {"dt": str(ed_att_date), "st": ed_att_status, "id": selected_att_id})
                            st.success("출결이 수정되었습니다.")
                            st.rerun()

                        if del_att:
                            if not confirm_att_del:
                                st.warning("삭제하려면 위의 확인 칸을 먼저 체크해 주세요.")
                            else:
                                with engine.begin() as conn:
                                    conn.execute(text("DELETE FROM attendance WHERE id = :id"), {"id": selected_att_id})
                                st.success("출결이 삭제되었습니다.")
                                st.rerun()

        st.divider()
        st.subheader("🕒 최근 등록된 출결 (최신 10건)")
        with engine.connect() as conn:
            recent_att = pd.read_sql_query("""
                SELECT a.event_date AS 날짜, m.name AS 이름, m.group_name AS 소속,
                       COALESCE(c.name, '') AS 반, a.status AS 출결상태
                FROM attendance a
                JOIN members m ON a.member_id = m.id
                LEFT JOIN classes c ON c.id = a.class_id
                ORDER BY a.id DESC LIMIT 10
            """, conn)
        st.dataframe(recent_att, hide_index=True, width="stretch")

# 3. 성적 입력 — 시험 종류별 [수정 21]
elif menu == MENU_SCORE:
    st.header("📝 성적 및 국어 평가")
    with engine.connect() as conn:
        members = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": int(row['id']) for _, row in members.iterrows()}
        ss = st.session_state

        exam_type = st.radio("시험 종류", EXAM_TYPES, horizontal=True, key="exam_type_sel",
                             format_func=lambda x: f"{EXAM_ICONS[x]} {x}")
        st.caption(EXAM_HELP[exam_type])

        def insert_score(row):
            data = dict(SCORE_DEFAULTS)
            data.update(row)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO scores (member_id, record_date, category, score, feedback,
                        reading_score, literature_score, grammar_score, wrong_questions,
                        exam_type, max_score, grade, percentile, exam_scope, school_term,
                        objective_score, essay_score, school_avg, class_id, quiz_format)
                    VALUES (:member_id, :record_date, :category, :score, :feedback,
                        :reading_score, :literature_score, :grammar_score, :wrong_questions,
                        :exam_type, :max_score, :grade, :percentile, :exam_scope, :school_term,
                        :objective_score, :essay_score, :school_avg, :class_id, :quiz_format)
                """), data)

        def already_saved(mid, d, cat, etype):
            with engine.connect() as conn:
                return conn.execute(text("""
                    SELECT COUNT(*) FROM scores
                    WHERE member_id = :m AND record_date = :d AND category = :c AND COALESCE(exam_type, '모의고사') = :t
                """), {"m": mid, "d": str(d), "c": cat, "t": etype}).scalar() > 0

        def reset(values):
            for k, v in values.items():
                ss[k] = v

        # ---------- ① 모의고사 ----------
        def cb_mock():
            cat = ss.mk_cat.strip()
            total = (ss.mk_r or 0) + (ss.mk_l or 0) + (ss.mk_g or 0)
            mid = member_dict[ss.mk_stu]
            if not cat:
                ss.sc_msg = "시험 이름을 입력해 주세요. (예: 11월 전국연합학력평가)"
                return
            if total == 0:
                ss.sc_msg = "영역별 점수가 모두 0점이에요. 점수를 입력했는지 확인해 주세요."
                return
            if already_saved(mid, ss.mk_date, cat, "모의고사"):
                ss.sc_msg = "이 학생의 같은 날짜·같은 시험 성적이 이미 있어요. 고치려면 아래 '저장된 성적 수정·삭제'를 이용하세요."
                return
            insert_score(dict(member_id=mid, record_date=str(ss.mk_date), category=cat, score=total,
                              feedback=ss.mk_fb.strip(), reading_score=ss.mk_r, literature_score=ss.mk_l,
                              grammar_score=ss.mk_g, wrong_questions=ss.mk_wrong.strip(), exam_type="모의고사",
                              max_score=100.0, grade="" if ss.mk_grade == "-" else ss.mk_grade,
                              percentile=ss.mk_pct))
            ss.flash = f"📊 {ss.mk_stu.split('] ')[-1].split(' (ID')[0]} · {cat} {total:g}점 저장했어요."
            # 시험 이름·날짜는 남겨 두고 점수 칸만 비워서, 다음 학생을 바로 입력할 수 있게
            reset({"mk_r": 0.0, "mk_l": 0.0, "mk_g": 0.0, "mk_grade": "-", "mk_pct": None, "mk_wrong": "", "mk_fb": ""})

        # ---------- ② 학교 내신 ----------
        def cb_school():
            mid = member_dict[ss.ns_stu]
            score = ss.ns_score
            if score is None and (ss.ns_obj is not None or ss.ns_essay is not None):
                score = (ss.ns_obj or 0) + (ss.ns_essay or 0)
            if score is None:
                ss.sc_msg = "점수를 입력해 주세요. (객관식·서술형만 적으면 합계가 점수가 됩니다)"
                return
            if score > ss.ns_max:
                ss.sc_msg = f"점수({score:g})가 만점({ss.ns_max:g})보다 커요. 다시 확인해 주세요."
                return
            cat = f"{ss.ns_date.year} {ss.ns_term}" + (f" {ss.ns_subject.strip()}" if ss.ns_subject.strip() else "")
            if already_saved(mid, ss.ns_date, cat, "학교 내신"):
                ss.sc_msg = "이 학생의 같은 시험 성적이 이미 있어요. 고치려면 아래 '저장된 성적 수정·삭제'를 이용하세요."
                return
            insert_score(dict(member_id=mid, record_date=str(ss.ns_date), category=cat, score=score,
                              feedback=ss.ns_fb.strip(), wrong_questions=ss.ns_wrong.strip(), exam_type="학교 내신",
                              max_score=ss.ns_max, grade=ss.ns_grade.strip(), exam_scope=ss.ns_scope.strip(),
                              school_term=ss.ns_term, objective_score=ss.ns_obj, essay_score=ss.ns_essay,
                              school_avg=ss.ns_avg))
            ss.flash = f"🏫 {ss.ns_stu.split('] ')[-1].split(' (ID')[0]} · {cat} {score:g}/{ss.ns_max:g}점 저장했어요."
            # 학기·과목·범위·학교 평균은 남겨 두고 학생별 칸만 비움
            reset({"ns_score": None, "ns_obj": None, "ns_essay": None, "ns_grade": "", "ns_wrong": "", "ns_fb": ""})

        # ---------- ④ 기타 ----------
        def cb_etc():
            mid = member_dict[ss.et_stu]
            cat = ss.et_cat.strip()
            if not cat:
                ss.sc_msg = "시험 이름을 입력해 주세요."
                return
            if ss.et_score is None:
                ss.sc_msg = "점수를 입력해 주세요."
                return
            if ss.et_score > ss.et_max:
                ss.sc_msg = f"점수({ss.et_score:g})가 만점({ss.et_max:g})보다 커요."
                return
            if already_saved(mid, ss.et_date, cat, "기타"):
                ss.sc_msg = "이 학생의 같은 시험 성적이 이미 있어요."
                return
            insert_score(dict(member_id=mid, record_date=str(ss.et_date), category=cat, score=ss.et_score,
                              max_score=ss.et_max, feedback=ss.et_fb.strip(), exam_type="기타",
                              exam_scope=ss.et_scope.strip()))
            ss.flash = f"📁 {cat} {ss.et_score:g}/{ss.et_max:g}점 저장했어요."
            reset({"et_score": None, "et_fb": ""})

        for k, v in {"mk_r": 0.0, "mk_l": 0.0, "mk_g": 0.0, "mk_pct": None,
                     "ns_score": None, "ns_obj": None, "ns_essay": None, "et_score": None}.items():
            ss.setdefault(k, v)

        st.markdown(f"#### {EXAM_ICONS[exam_type]} {exam_type} 성적 입력")

        if exam_type == "모의고사":
            with st.form("form_mock"):
                c1, c2 = st.columns([2, 1])
                c1.selectbox("학생 선택", list(member_dict.keys()), key="mk_stu")
                c2.date_input("시험 일자", today_kst(), key="mk_date")
                st.text_input("시험 이름 *", placeholder="11월 전국연합학력평가", key="mk_cat")
                a, b, c = st.columns(3)
                a.number_input("독서", min_value=0.0, max_value=100.0, step=0.5, key="mk_r")
                b.number_input("문학", min_value=0.0, max_value=100.0, step=0.5, key="mk_l")
                c.number_input("문법·화법 (선택과목)", min_value=0.0, max_value=100.0, step=0.5, key="mk_g")
                e1, e2 = st.columns(2)
                e1.selectbox("등급", ["-"] + [f"{i}등급" for i in range(1, 10)], key="mk_grade")
                e2.number_input("백분위 (선택)", min_value=0.0, max_value=100.0, step=1.0, key="mk_pct")
                st.text_input("틀린 문항 번호 / 취약 제재", key="mk_wrong")
                st.text_area("피드백 및 처방", key="mk_fb", height=80)
                st.caption("총점은 세 영역을 더해 저장돼요. 저장하면 시험 이름·날짜는 남고 점수 칸만 비워져서 다음 학생을 바로 입력할 수 있어요.")
                st.form_submit_button("💾 모의고사 성적 저장", type="primary", on_click=cb_mock)

        elif exam_type == "학교 내신":
            with st.form("form_school"):
                c1, c2 = st.columns([2, 1])
                c1.selectbox("학생 선택", list(member_dict.keys()), key="ns_stu")
                c2.date_input("시험 일자", today_kst(), key="ns_date")
                t1, t2 = st.columns(2)
                t1.selectbox("시험", SCHOOL_TERMS, index=3, key="ns_term")
                t2.text_input("과목", placeholder="공통국어2 / 문학 / 독서와 작문", key="ns_subject")
                st.text_area("시험 범위", placeholder="공통국어2 3단원 (관동별곡 ~ 사미인곡), 학습지 1~12", key="ns_scope", height=68)
                s1, s2, s3, s4 = st.columns(4)
                s1.number_input("점수 *", min_value=0.0, max_value=200.0, step=0.5, key="ns_score")
                s2.number_input("만점", min_value=1.0, max_value=200.0, value=100.0, step=1.0, key="ns_max")
                s3.number_input("객관식 (선택)", min_value=0.0, max_value=200.0, step=0.5, key="ns_obj")
                s4.number_input("서술형 (선택)", min_value=0.0, max_value=200.0, step=0.5, key="ns_essay")
                g1, g2 = st.columns(2)
                g1.text_input("등급 / 성취도 (선택)", placeholder="2등급, B 등", key="ns_grade")
                g2.number_input("학교 평균 (선택)", min_value=0.0, max_value=200.0, value=None, step=0.1, key="ns_avg")
                st.text_input("틀린 문항 / 취약 부분", key="ns_wrong")
                st.text_area("피드백 및 처방", key="ns_fb", height=80)
                st.caption("저장하면 시험·과목·범위·학교 평균은 남고 학생별 칸만 비워져요.")
                st.form_submit_button("💾 내신 성적 저장", type="primary", on_click=cb_school)

        elif exam_type == "수업 테스트":
            with engine.connect() as conn:
                qz_classes = pd.read_sql_query("SELECT id, name FROM classes WHERE active ORDER BY name", conn)
                qz_sched = pd.read_sql_query("SELECT class_id, weekday FROM class_schedules", conn)
            if qz_classes.empty:
                st.info("'반·시간표 관리'에서 반을 만들고 학생을 배정하면, 반 학생 전체 테스트 점수를 한 번에 입력할 수 있어요.")
            else:
                q_opts = {r["name"]: int(r["id"]) for _, r in qz_classes.iterrows()}
                q_names = list(q_opts.keys())
                if ss.get("qz_class") not in q_names:
                    today_ids = set(qz_sched[qz_sched["weekday"] == today_kst().weekday()]["class_id"])
                    t_names = [n for n in q_names if q_opts[n] in today_ids]
                    ss.qz_class = t_names[0] if t_names else q_names[0]
                k1, k2 = st.columns([2, 1])
                q_cname = k1.selectbox("반 선택", q_names, key="qz_class")
                q_date = k2.date_input("테스트 날짜", today_kst(), key="qz_date")
                q_cid = q_opts[q_cname]
                m1, m2, m3 = st.columns([2, 1, 1])
                q_name = m1.text_input("테스트 이름 / 범위 *", placeholder="관동별곡 어휘 OX", key="qz_name").strip()
                q_fmt = m2.selectbox("유형", QUIZ_FORMATS, key="qz_fmt")
                q_total = m3.number_input("전체 문항 수", min_value=1, max_value=200, value=10, step=1, key="qz_total")

                with engine.connect() as conn:
                    q_roster = pd.read_sql_query(text("""
                        SELECT m.id, m.name, m.group_name
                        FROM class_members cm JOIN members m ON m.id = cm.member_id
                        WHERE cm.class_id = :c AND (m.status = '재원' OR m.status IS NULL)
                        ORDER BY m.name
                    """), conn, params={"c": q_cid})
                    q_prev = pd.read_sql_query(text("""
                        SELECT member_id, score, feedback FROM scores
                        WHERE exam_type = '수업 테스트' AND class_id = :c AND record_date = :d AND category = :n
                    """), conn, params={"c": q_cid, "d": str(q_date), "n": q_name})

                if q_roster.empty:
                    st.info("이 반에 배정된 학생이 없습니다. '반·시간표 관리 → ③ 학생 배정'에서 추가해 주세요.")
                else:
                    prev_sc = dict(zip(q_prev["member_id"], q_prev["score"]))
                    prev_fb = dict(zip(q_prev["member_id"], q_prev["feedback"].fillna("")))
                    q_df = pd.DataFrame({
                        "학생ID": q_roster["id"],
                        "이름": q_roster["name"],
                        "소속": q_roster["group_name"].fillna(""),
                        "맞은 개수": [prev_sc.get(i) for i in q_roster["id"]],
                        "메모": [prev_fb.get(i, "") for i in q_roster["id"]],
                    })
                    q_df["맞은 개수"] = pd.to_numeric(q_df["맞은 개수"], errors="coerce")
                    if not q_prev.empty:
                        st.caption(f"✅ 이 반·날짜·테스트로 저장된 점수 {len(q_prev)}명을 불러왔어요. 고친 뒤 저장하면 덮어씁니다.")
                    else:
                        st.caption("맞은 개수만 적고 저장하세요. 빈칸으로 둔 학생(결석·미응시)은 저장되지 않아요.")
                    q_edit = st.data_editor(
                        q_df,
                        column_config={
                            "학생ID": None,
                            "맞은 개수": st.column_config.NumberColumn("맞은 개수", min_value=0, max_value=int(q_total), step=1,
                                                                    help=f"0 ~ {int(q_total)}"),
                            "메모": st.column_config.TextColumn("메모", help="자주 틀린 개념, 재시험 여부 등"),
                        },
                        disabled=["이름", "소속"],
                        hide_index=True,
                        width="stretch",
                        key=f"qz_editor_{q_cid}_{q_date}_{q_name}_{int(q_total)}",
                    )
                    filled = q_edit.dropna(subset=["맞은 개수"])
                    if len(filled):
                        avg_rate = (filled["맞은 개수"].astype(float) / q_total * 100).mean()
                        st.caption(f"입력 {len(filled)}명 · 평균 정답률 {avg_rate:.0f}%")
                    if st.button(f"💾 {q_cname} 테스트 점수 한 번에 저장", type="primary", key="qz_save"):
                        if not q_name:
                            st.warning("테스트 이름을 입력해 주세요. (예: 관동별곡 어휘 OX)")
                        elif filled.empty:
                            st.warning("맞은 개수를 한 명 이상 입력해 주세요.")
                        elif (filled["맞은 개수"].astype(float) > q_total).any():
                            st.warning(f"맞은 개수가 전체 문항 수({int(q_total)})보다 큰 학생이 있어요.")
                        else:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    DELETE FROM scores WHERE exam_type = '수업 테스트' AND class_id = :c
                                      AND record_date = :d AND category = :n
                                """), {"c": q_cid, "d": str(q_date), "n": q_name})
                            for _, r in filled.iterrows():
                                insert_score(dict(member_id=int(r["학생ID"]), record_date=str(q_date), category=q_name,
                                                  score=float(r["맞은 개수"]), max_score=float(q_total),
                                                  feedback=str(r["메모"] or "").strip(), exam_type="수업 테스트",
                                                  class_id=q_cid, quiz_format=q_fmt))
                            rate = (filled["맞은 개수"].astype(float) / q_total * 100).mean()
                            ss.flash = f"✏️ {q_cname} '{q_name}' {len(filled)}명 저장 · 평균 정답률 {rate:.0f}%"
                            st.rerun()

        else:  # 기타
            with st.form("form_etc"):
                c1, c2 = st.columns([2, 1])
                c1.selectbox("학생 선택", list(member_dict.keys()), key="et_stu")
                c2.date_input("시험 일자", today_kst(), key="et_date")
                st.text_input("시험 이름 *", placeholder="사설 모의고사 3회 / 단원평가", key="et_cat")
                st.text_input("범위 (선택)", key="et_scope")
                s1, s2 = st.columns(2)
                s1.number_input("점수 *", min_value=0.0, max_value=1000.0, step=0.5, key="et_score")
                s2.number_input("만점", min_value=1.0, max_value=1000.0, value=100.0, step=1.0, key="et_max")
                st.text_area("메모 / 피드백", key="et_fb", height=80)
                st.form_submit_button("💾 성적 저장", type="primary", on_click=cb_etc)

        if "sc_msg" in ss:
            st.warning(ss.pop("sc_msg"))

        # ---------- 저장된 성적 수정 · 삭제 ----------
        st.divider()
        with st.expander("✏️/🗑️ 저장된 성적 수정·삭제", expanded=False):
            with engine.connect() as conn:
                sc_edit = pd.read_sql_query("""
                    SELECT s.*, m.name AS m_name
                    FROM scores s JOIN members m ON s.member_id = m.id
                    ORDER BY s.record_date DESC, s.id DESC LIMIT 100
                """, conn)
            if sc_edit.empty:
                st.info("저장된 성적이 없습니다.")
            else:
                sc_edit["exam_type"] = sc_edit["exam_type"].fillna("모의고사")
                f1, f2 = st.columns([1, 2])
                f_type = f1.selectbox("종류", ["전체"] + EXAM_TYPES, key="sc_edit_type")
                f_kw = f2.text_input("학생 이름 / 시험 이름 검색", key="sc_edit_kw")
                view = sc_edit if f_type == "전체" else sc_edit[sc_edit["exam_type"] == f_type]
                if f_kw.strip():
                    view = view[view["m_name"].str.contains(f_kw.strip(), na=False) |
                                view["category"].str.contains(f_kw.strip(), na=False)]
                if view.empty:
                    st.caption("조건에 맞는 성적이 없습니다.")
                else:
                    opts = {f"[{r['record_date']}] {EXAM_ICONS.get(r['exam_type'], '')} {r['m_name']} · {r['category']} "
                            f"({fmt_score(r['score'], r['max_score'])}) · #{r['id']}": int(r["id"]) for _, r in view.iterrows()}
                    sel = st.selectbox("수정/삭제할 성적", list(opts.keys()), key="sc_edit_sel")
                    sid = opts[sel]
                    row = sc_edit[sc_edit["id"] == sid].iloc[0]
                    rtype = row["exam_type"]

                    def num(v, default=None):
                        return default if v is None or pd.isna(v) else float(v)

                    def txt(v):
                        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

                    with st.form(f"sc_edit_form_{sid}"):
                        e1, e2 = st.columns([2, 1])
                        ed_cat = e1.text_input("시험 이름", value=txt(row["category"]))
                        try:
                            cur_d = datetime.strptime(row["record_date"], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            cur_d = today_kst()
                        ed_date = e2.date_input("일자", cur_d)
                        if rtype == "모의고사":
                            a, b, c = st.columns(3)
                            ed_r = a.number_input("독서", 0.0, 100.0, num(row["reading_score"], 0.0), 0.5)
                            ed_l = b.number_input("문학", 0.0, 100.0, num(row["literature_score"], 0.0), 0.5)
                            ed_g = c.number_input("문법·화법", 0.0, 100.0, num(row["grammar_score"], 0.0), 0.5)
                            ed_score, ed_max = ed_r + ed_l + ed_g, 100.0
                        else:
                            a, b = st.columns(2)
                            ed_score = a.number_input("점수" if rtype != "수업 테스트" else "맞은 개수", 0.0, 1000.0,
                                                      num(row["score"], 0.0), 0.5)
                            ed_max = b.number_input("만점" if rtype != "수업 테스트" else "전체 문항 수", 1.0, 1000.0,
                                                    num(row["max_score"], 100.0), 1.0)
                        g1, g2 = st.columns(2)
                        ed_grade = g1.text_input("등급 / 성취도", value=txt(row["grade"]))
                        ed_scope = g2.text_input("범위", value=txt(row["exam_scope"]))
                        ed_wrong = st.text_input("틀린 문항", value=txt(row["wrong_questions"]))
                        ed_fb = st.text_area("피드백 / 메모", value=txt(row["feedback"]), height=70)
                        confirm_sc = st.checkbox("이 성적을 삭제합니다 (되돌릴 수 없음)")
                        b1, b2 = st.columns(2)
                        do_save = b1.form_submit_button("수정 저장", type="primary")
                        do_del = b2.form_submit_button("삭제")
                    if do_save:
                        if ed_score > ed_max:
                            st.warning("점수가 만점보다 커요.")
                        else:
                            params = {"cat": ed_cat.strip(), "d": str(ed_date), "sc": ed_score, "mx": ed_max,
                                      "gr": ed_grade.strip(), "scope": ed_scope.strip(), "wq": ed_wrong.strip(),
                                      "fb": ed_fb.strip(), "id": sid}
                            extra = ""
                            if rtype == "모의고사":
                                extra = ", reading_score = :r, literature_score = :l, grammar_score = :g"
                                params.update({"r": ed_r, "l": ed_l, "g": ed_g})
                            with engine.begin() as conn:
                                conn.execute(text(f"""
                                    UPDATE scores SET category = :cat, record_date = :d, score = :sc, max_score = :mx,
                                        grade = :gr, exam_scope = :scope, wrong_questions = :wq, feedback = :fb {extra}
                                    WHERE id = :id
                                """), params)
                            ss.flash = f"✏️ {row['m_name']} · {ed_cat} 성적을 수정했어요."
                            st.rerun()
                    if do_del:
                        if not confirm_sc:
                            st.warning("삭제하려면 확인 칸을 먼저 체크해 주세요.")
                        else:
                            with engine.begin() as conn:
                                conn.execute(text("DELETE FROM scores WHERE id = :id"), {"id": sid})
                            ss.pop("sc_edit_sel", None)
                            ss.flash = "🗑️ 성적을 삭제했어요."
                            st.rerun()

        st.subheader("🕒 최근 입력한 성적")
        with engine.connect() as conn:
            recent_sc = pd.read_sql_query("""
                SELECT COALESCE(s.exam_type, '모의고사') AS 종류, s.record_date AS 일자, m.name AS 이름,
                       s.category AS 시험, s.score, s.max_score, COALESCE(s.grade, '') AS 등급,
                       COALESCE(NULLIF(s.exam_scope, ''), s.quiz_format, '') AS "범위·유형",
                       COALESCE(s.wrong_questions, '') AS 오답
                FROM scores s JOIN members m ON s.member_id = m.id
                ORDER BY s.id DESC LIMIT 15
            """, conn)
        if recent_sc.empty:
            st.caption("아직 입력한 성적이 없습니다.")
        else:
            recent_sc.insert(4, "점수", [fmt_score(a, b) for a, b in zip(recent_sc["score"], recent_sc["max_score"])])
            st.dataframe(recent_sc.drop(columns=["score", "max_score"]).fillna(""), hide_index=True, width="stretch")

# 4. 상담/소통 일지
elif menu == MENU_LOG:
    st.header("4. 학생/학부모 소통 및 학습 면담 일지")
    with engine.connect() as conn:
        # [수정 4] 퇴원생은 입력 목록에서 제외
        members = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        col_lg_add, col_lg_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}

        with col_lg_add:
            with st.expander("➕ 새 상담 일지 작성", expanded=True):
                with st.form("log_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="log_add_target")
                    log_date = st.date_input("상담 일자", today_kst(), key="log_add_date")
                    topic = st.text_input("면담 주제")
                    details = st.text_area("상세 내용 및 다음 주 점검 과제")
                    submit_log = st.form_submit_button("일지 저장")

                    if submit_log and topic:
                        m_id = member_dict[target_member]
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO logs (member_id, log_date, topic, details)
                                VALUES (:member_id, :log_date, :topic, :details)
                            """), {"member_id": m_id, "log_date": str(log_date), "topic": topic, "details": details})
                        st.success("상담 일지가 저장되었습니다.")
                        st.rerun()

        with col_lg_edit:
            with st.expander("✏️/🗑️ 등록된 상담 수정 및 삭제", expanded=False):
                with engine.connect() as conn:
                    logs_for_edit = pd.read_sql_query("""
                        SELECT l.id, l.log_date, l.topic, l.details, m.name, m.group_name
                        FROM logs l
                        JOIN members m ON l.member_id = m.id
                        ORDER BY l.id DESC LIMIT 50
                    """, conn)

                if logs_for_edit.empty:
                    st.info("수정할 상담 일지가 없습니다.")
                else:
                    log_options = {
                        f"[{row['log_date']}] {row['name']} - {row['topic']} (ID:{row['id']})": row['id']
                        for _, row in logs_for_edit.iterrows()
                    }
                    selected_lg_label = st.selectbox("수정/삭제할 일지 선택", list(log_options.keys()))
                    selected_lg_id = log_options[selected_lg_label]
                    target_lg = logs_for_edit[logs_for_edit['id'] == selected_lg_id].iloc[0]

                    with st.form("log_edit_form"):
                        try:
                            curr_lg_dt = datetime.strptime(target_lg['log_date'], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            curr_lg_dt = today_kst()
                        ed_log_date = st.date_input("상담 일자", curr_lg_dt)
                        ed_topic = st.text_input("상담 주제", value=target_lg['topic'])
                        ed_details = st.text_area("상세 내용", value=target_lg['details'] if target_lg['details'] else "")

                        # [수정 6] 삭제는 체크해야만 실행
                        confirm_lg_del = st.checkbox("이 상담 일지를 삭제합니다 (되돌릴 수 없음)")

                        btn_l1, btn_l2 = st.columns(2)
                        save_lg = btn_l1.form_submit_button("수정 저장")
                        del_lg = btn_l2.form_submit_button("일지 삭제", type="primary")

                        if save_lg:
                            with engine.begin() as conn:
                                conn.execute(text("UPDATE logs SET log_date = :dt, topic = :tp, details = :dtl WHERE id = :id"),
                                             {"dt": str(ed_log_date), "tp": ed_topic, "dtl": ed_details, "id": selected_lg_id})
                            st.success("상담 일지가 수정되었습니다.")
                            st.rerun()

                        if del_lg:
                            if not confirm_lg_del:
                                st.warning("삭제하려면 위의 확인 칸을 먼저 체크해 주세요.")
                            else:
                                with engine.begin() as conn:
                                    conn.execute(text("DELETE FROM logs WHERE id = :id"), {"id": selected_lg_id})
                                st.success("상담 일지가 삭제되었습니다.")
                                st.rerun()

        st.divider()
        st.subheader("🕒 최근 등록된 상담 기록 (최신 10건)")
        with engine.connect() as conn:
            recent_lg = pd.read_sql_query("""
                SELECT l.id AS 번호, l.log_date AS 상담일자, m.name AS 이름, m.group_name AS 소속, l.topic AS 주제, l.details AS 상세내용
                FROM logs l
                JOIN members m ON l.member_id = m.id
                ORDER BY l.id DESC LIMIT 10
            """, conn)
        st.dataframe(recent_lg, width="stretch")

# 5. 개인별 종합 조회 (퇴원생 기록도 볼 수 있도록 전체 학생 대상)
elif menu == MENU_PORTFOLIO:
    st.header("5. 국어 학습 종합 포트폴리오")
    with engine.connect() as conn:
        members = pd.read_sql_query("SELECT id, name, group_name, status FROM members ORDER BY name ASC", conn)

    if members.empty:
        st.warning("등록된 학생이 없습니다.")
    else:
        # [수정 23] 다른 화면에서 학생을 눌러 넘어왔으면, 필터를 풀고 그 학생을 선택
        if "pf_target_id" in st.session_state:
            tid = st.session_state.pop("pf_target_id")
            hit = members[members["id"] == tid]
            if not hit.empty:
                hr = hit.iloc[0]
                st.session_state["pf_group_filter"] = "전체 학교"
                st.session_state["pf_name_search"] = ""
                st.session_state["pf_student_select"] = (f"[{hr['group_name']}] {hr['name']} (ID:{hr['id']})"
                                                         + (" · 퇴원" if hr['status'] == '퇴원' else ""))
        f_c1, f_c2 = st.columns([1, 2])
        all_grps_portfolio = ["전체 학교"] + sorted([g for g in members['group_name'].dropna().unique() if g])
        chosen_grp = f_c1.selectbox("🏫 학교/그룹별 필터", all_grps_portfolio, key="pf_group_filter")
        search_kw_pf = f_c2.text_input("🔍 학생 이름 검색", placeholder="조회할 학생 이름을 입력하세요", key="pf_name_search")

        filtered_members = members.copy()
        if chosen_grp != "전체 학교":
            filtered_members = filtered_members[filtered_members['group_name'] == chosen_grp]
        if search_kw_pf.strip():
            filtered_members = filtered_members[filtered_members['name'].str.contains(search_kw_pf.strip(), na=False)]

        if filtered_members.empty:
            st.warning("검색 조건과 일치하는 학생이 없습니다.")
        else:
            member_dict = {
                f"[{row['group_name']}] {row['name']} (ID:{row['id']})" + (" · 퇴원" if row['status'] == '퇴원' else ""): row['id']
                for _, row in filtered_members.iterrows()
            }
            selected_label = st.selectbox("조회할 학생 선택", list(member_dict.keys()), key="pf_student_select")
            selected_id = member_dict[selected_label]

            with engine.connect() as conn:
                m_info = pd.read_sql_query(text("SELECT * FROM members WHERE id = :id"), conn, params={"id": selected_id}).iloc[0]
                st.markdown(f"### 👤 {m_info['name']} 학생 ({m_info['group_name']})")
                my_classes = pd.read_sql_query(text("""
                    SELECT c.id, c.name FROM class_members cm JOIN classes c ON c.id = cm.class_id
                    WHERE cm.member_id = :id ORDER BY c.name
                """), conn, params={"id": selected_id})
                my_sched = pd.read_sql_query(text("""
                    SELECT s.class_id, s.weekday, s.start_time, s.end_time FROM class_schedules s
                    JOIN class_members cm ON cm.class_id = s.class_id
                    WHERE cm.member_id = :id ORDER BY s.weekday, s.start_time
                """), conn, params={"id": selected_id})
                st.write(f"📞 연락처: {m_info['contact'] or '-'} | 📝 학습 메모: {m_info['notes'] or '-'}")
                if my_classes.empty:
                    st.write("🏫 수강 반: 배정된 반 없음")
                else:
                    st.write(md("🏫 수강 반: " + " / ".join(
                        f"{r.name} ({schedule_text(my_sched[my_sched['class_id'] == r.id]) or '시간 미등록'})"
                        for r in my_classes.itertuples())))
                st.divider()

                col_att, col_score = st.columns([1, 1.4])

                with col_att:
                    st.subheader("📅 최근 출결 현황")
                    df_att = pd.read_sql_query(text("SELECT event_date AS 일자, status AS 상태 FROM attendance WHERE member_id = :id ORDER BY event_date DESC"), conn, params={"id": selected_id})
                    st.dataframe(df_att, hide_index=True, width="stretch")

                # [수정 21] 시험 종류별로 그래프를 나눠서
                df_sc_all = pd.read_sql_query(text("""
                    SELECT record_date, COALESCE(exam_type, '모의고사') AS exam_type, category, score, max_score,
                           grade, exam_scope, quiz_format, reading_score, literature_score, grammar_score,
                           wrong_questions, feedback, school_avg
                    FROM scores WHERE member_id = :id ORDER BY record_date ASC, id ASC
                """), conn, params={"id": selected_id})
                with col_score:
                    st.subheader("📈 성적 추이")
                    if df_sc_all.empty:
                        st.info("등록된 성적 데이터가 없습니다.")
                    else:
                        present = [t for t in EXAM_TYPES if t in set(df_sc_all["exam_type"])]
                        for t, tab in zip(present, st.tabs([f"{EXAM_ICONS[t]} {t}" for t in present])):
                            with tab:
                                d = df_sc_all[df_sc_all["exam_type"] == t].copy()
                                d["회차"] = d["record_date"] + " " + d["category"].fillna("")
                                d["mx"] = d["max_score"].fillna(100).astype(float)
                                if t == "모의고사":
                                    d["총점"] = d["score"].astype(float)
                                    trend_chart(d, ["총점"], "총점", 100)
                                elif t == "수업 테스트":
                                    d["정답률(%)"] = (d["score"].astype(float) / d["mx"] * 100).round(0)
                                    trend_chart(d, ["정답률(%)"], "정답률(%)", 100)
                                    st.caption(f"수업 테스트 {len(d)}회 · 평균 정답률 {d['정답률(%)'].mean():.0f}%")
                                else:
                                    d["내 점수"] = (d["score"].astype(float) / d["mx"] * 100).round(1)
                                    cols_plot = ["내 점수"]
                                    if t == "학교 내신" and d["school_avg"].notna().any():
                                        d["학교 평균"] = (d["school_avg"].astype(float) / d["mx"] * 100).round(1)
                                        cols_plot.append("학교 평균")
                                    trend_chart(d, cols_plot, "점수(100점 환산)", 100)
                                if len(d) == 1:
                                    st.caption("기록이 2개 이상 쌓이면 추이가 선으로 이어져요.")

                st.subheader("📊 성적 상세 기록")
                if not df_sc_all.empty:
                    det = df_sc_all.sort_values("record_date", ascending=False).copy()
                    det_view = pd.DataFrame({
                        "종류": det["exam_type"],
                        "일자": det["record_date"],
                        "시험": det["category"],
                        "점수": [fmt_score(a, b) for a, b in zip(det["score"], det["max_score"])],
                        "등급": det["grade"],
                        "범위·유형": det["exam_scope"].where(det["exam_scope"].fillna("") != "", det["quiz_format"]),
                        "독서": det["reading_score"],
                        "문학": det["literature_score"],
                        "문법·화법": det["grammar_score"],
                        "틀린 문항": det["wrong_questions"],
                        "피드백": det["feedback"],
                    })
                    for col in ["독서", "문학", "문법·화법"]:
                        det_view[col] = [("" if v is None or pd.isna(v) else f"{float(v):g}") for v in det_view[col]]
                    st.dataframe(det_view.fillna("").astype(str), hide_index=True, width="stretch")
                else:
                    st.caption("등록된 성적이 없습니다.")

                st.divider()
                st.subheader("💬 상담 및 학습 면담 이력")
                df_lg = pd.read_sql_query(text("SELECT log_date AS 일자, topic AS 주제, details AS 상세내용 FROM logs WHERE member_id = :id ORDER BY log_date DESC"), conn, params={"id": selected_id})
                st.dataframe(df_lg, width="stretch")

# 7. 내 시간표 [수정 15]
elif menu == MENU_TIMETABLE:
    st.header("🗓️ 내 시간표")
    with engine.connect() as conn:
        tt = pd.read_sql_query(f"""
            SELECT s.weekday, s.start_time, s.end_time, c.id AS class_id, c.name, c.grade,
                   (SELECT COUNT(*) FROM class_members cm JOIN members m ON m.id = cm.member_id
                     WHERE cm.class_id = c.id AND (m.status = '재원' OR m.status IS NULL)) AS n
            FROM class_schedules s JOIN classes c ON c.id = s.class_id
            WHERE c.active
            ORDER BY s.weekday, s.start_time
        """, conn)
        # [수정 20] 반별 학생 이름 (마우스를 올리면 보여 줄 목록)
        tt_names = pd.read_sql_query("""
            SELECT cm.class_id, m.name, m.group_name
            FROM class_members cm JOIN members m ON m.id = cm.member_id
            WHERE m.status = '재원' OR m.status IS NULL
            ORDER BY m.name
        """, conn)
    names_map = {cid: list(g["name"]) for cid, g in tt_names.groupby("class_id")}

    if tt.empty:
        st.info("아직 등록된 수업이 없습니다. '반·시간표 관리'에서 반과 수업 시간을 등록해 주세요.")
    else:
        def minutes(hhmm):
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        total_min = sum(minutes(r.end_time) - minutes(r.start_time) for r in tt.itertuples())
        cap_col, tog_col = st.columns([3, 1])
        cap_col.caption(f"주간 수업 {len(tt)}회 · 반 {tt['class_id'].nunique()}개 · 총 {total_min // 60}시간 {total_min % 60}분"
                        " · 카드에 마우스를 올리면 학생 이름이 보여요")
        show_names = tog_col.toggle("학생 이름 펼쳐 보기", key="tt_show_names",
                                    help="휴대폰처럼 마우스가 없을 때 켜 두면 카드에 이름이 바로 보여요.")

        # 마우스를 올리면 뜨는 말풍선 모양
        st.markdown("""
        <style>
        .tt-card { position: relative; cursor: default; transition: box-shadow 0.12s, transform 0.12s; }
        .tt-card:hover, .tt-card:focus { box-shadow: 0 6px 14px rgba(0,0,0,0.12) !important; transform: translateY(-1px); outline: none; }
        .tt-pop {
            display: none; position: absolute; left: 0; right: 0; top: calc(100% + 6px); z-index: 1000;
            background: #262730; color: #ffffff; border-radius: 10px; padding: 10px 12px;
            font-size: 0.82rem; line-height: 1.6; box-shadow: 0 10px 24px rgba(0,0,0,0.22);
            word-break: keep-all;
        }
        .tt-pop::before {
            content: ""; position: absolute; top: -6px; left: 18px;
            border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 6px solid #262730;
        }
        .tt-pop b { color: #ffb4b4; font-weight: 700; }
        .tt-card:hover .tt-pop, .tt-card:focus .tt-pop { display: block; }
        .tt-names { margin-top: 6px; padding-top: 6px; border-top: 1px dashed #d5d9e0;
                    font-size: 0.78rem; color: #31333f; line-height: 1.55; word-break: keep-all; }
        [data-testid="stColumn"], [data-testid="column"] { overflow: visible !important; }
        /* 마우스를 올린 카드를 아래 카드보다 위로 올려서 말풍선이 가려지지 않게 */
        [data-testid="stElementContainer"]:has(.tt-card:hover),
        [data-testid="stElementContainer"]:has(.tt-card:focus),
        .element-container:has(.tt-card:hover) { position: relative; z-index: 1000; }
        </style>
        """, unsafe_allow_html=True)

        today_wd = today_kst().weekday()
        cols = st.columns(7)
        for wd in range(7):
            with cols[wd]:
                is_today = wd == today_wd
                head_bg = "#ff4b4b" if is_today else "#e8eaf0"
                head_fg = "#ffffff" if is_today else "#31333f"
                st.markdown(
                    f"<div style='text-align:center;padding:8px 0;border-radius:10px;background:{head_bg};"
                    f"color:{head_fg};font-weight:700;margin-bottom:8px'>{WEEKDAYS[wd]}{' · 오늘' if is_today else ''}</div>",
                    unsafe_allow_html=True)
                day = tt[tt["weekday"] == wd]
                if day.empty:
                    st.markdown("<div style='text-align:center;color:#a3a8b8;font-size:0.85rem;padding:12px 0'>수업 없음</div>",
                                unsafe_allow_html=True)
                for r in day.itertuples():
                    border = "#ff4b4b" if is_today else "#8b93a7"
                    stu = names_map.get(r.class_id, [])
                    stu_txt = ", ".join(html.escape(n) for n in stu) if stu else "배정된 학생이 없어요"
                    # 이름을 펼쳐 둔 상태면 카드 안에, 아니면 마우스를 올렸을 때 말풍선으로
                    names_block = (f"<div class='tt-names'>{stu_txt}</div>" if show_names else
                                   f"<div class='tt-pop'><b>{html.escape(r.name)}</b> · {len(stu)}명<br>{stu_txt}</div>")
                    st.markdown(
                        f"<div class='tt-card' tabindex='0' style='border:1px solid #d5d9e0;border-left:5px solid {border};border-radius:8px;"
                        f"padding:8px 10px;margin-bottom:8px;background:#ffffff;box-shadow:0 2px 4px rgba(0,0,0,0.06)'>"
                        f"<div style='font-size:0.8rem;color:#5f6475'>{r.start_time}~{r.end_time}</div>"
                        f"<div style='font-weight:700;font-size:0.92rem;line-height:1.35;margin:2px 0;word-break:keep-all'>{html.escape(r.name)}</div>"
                        f"<div style='font-size:0.78rem;color:#808495'>학생 {r.n}명</div>{names_block}</div>",
                        unsafe_allow_html=True)

# 8. 반·시간표 관리 [수정 12]
elif menu == MENU_CLASS:
    st.header("🏫 반·시간표 관리")
    with engine.connect() as conn:
        classes_all = pd.read_sql_query("SELECT id, name, grade, notes, active FROM classes ORDER BY active DESC, name", conn)
        sched_all = pd.read_sql_query("SELECT id, class_id, weekday, start_time, end_time FROM class_schedules ORDER BY weekday, start_time", conn)
        counts = pd.read_sql_query(f"""
            SELECT cm.class_id, COUNT(*) AS n FROM class_members cm JOIN members m ON m.id = cm.member_id
            {ACTIVE_ONLY.replace('status', 'm.status')} GROUP BY cm.class_id
        """, conn)
    count_map = dict(zip(counts["class_id"], counts["n"]))

    # 전체 반 한눈에 보기
    if classes_all.empty:
        st.info("아직 만든 반이 없습니다. 아래 '① 반 만들기·수정'에서 첫 반을 만들어 보세요.")
    else:
        overview = pd.DataFrame({
            "반 이름": classes_all["name"],
            "학년": classes_all["grade"].fillna(""),
            "수업 시간": [schedule_text(sched_all[sched_all["class_id"] == i]) or "미등록" for i in classes_all["id"]],
            "학생 수": [f"{count_map.get(i, 0)}명" for i in classes_all["id"]],
            "상태": ["운영 중" if a else "운영 종료" for a in classes_all["active"]],
        })
        st.dataframe(overview, hide_index=True, width="stretch")

    tab1, tab2, tab3 = st.tabs(["① 반 만들기·수정", "② 수업 시간", "③ 학생 배정"])

    # ---- ① 반 만들기·수정 ----
    with tab1:
        c_new, c_edit = st.columns(2)
        with c_new:
            st.markdown("##### ➕ 새 반 만들기")
            with st.form("class_new_form", clear_on_submit=True):
                new_name = st.text_input("반 이름 *", placeholder="고1 평고학사 A반")
                new_grade = st.text_input("학년", placeholder="고1")
                new_notes = st.text_input("메모", placeholder="내신반, 교재명 등")
                if st.form_submit_button("반 만들기", type="primary"):
                    nm = new_name.strip()
                    if not nm:
                        st.warning("반 이름을 입력해 주세요.")
                    elif nm in set(classes_all["name"]):
                        st.warning("같은 이름의 반이 이미 있습니다.")
                    else:
                        with engine.begin() as conn:
                            conn.execute(text("INSERT INTO classes (name, grade, notes, active) VALUES (:n, :g, :no, TRUE)"),
                                         {"n": nm, "g": new_grade.strip(), "no": new_notes.strip()})
                        st.success(f"'{nm}'을 만들었어요. 이어서 ② 수업 시간, ③ 학생 배정을 해 주세요.")
                        st.rerun()

        with c_edit:
            st.markdown("##### ✏️ 반 정보 수정")
            if classes_all.empty:
                st.caption("수정할 반이 없습니다.")
            else:
                ed_opts = {r["name"]: int(r["id"]) for _, r in classes_all.iterrows()}
                ed_name_sel = st.selectbox("수정할 반", list(ed_opts.keys()), key="class_edit_sel")
                ed_id = ed_opts[ed_name_sel]
                cur = classes_all[classes_all["id"] == ed_id].iloc[0]
                with st.form(f"class_edit_form_{ed_id}"):
                    e_name = st.text_input("반 이름 *", value=cur["name"])
                    e_grade = st.text_input("학년", value=cur["grade"] or "")
                    e_notes = st.text_input("메모", value=cur["notes"] or "")
                    e_active = st.checkbox("운영 중 (끄면 시간표·오늘 화면에서 숨겨짐)", value=bool(cur["active"]))
                    confirm_cls_del = st.checkbox("이 반을 완전히 삭제합니다 (수업 시간·학생 배정도 삭제, 출결 기록은 남음)")
                    s1, s2 = st.columns(2)
                    save_cls = s1.form_submit_button("수정 저장")
                    del_cls = s2.form_submit_button("반 삭제", type="primary")
                    if save_cls:
                        nm = e_name.strip()
                        if not nm:
                            st.warning("반 이름을 입력해 주세요.")
                        elif nm != cur["name"] and nm in set(classes_all["name"]):
                            st.warning("같은 이름의 반이 이미 있습니다.")
                        else:
                            with engine.begin() as conn:
                                conn.execute(text("UPDATE classes SET name=:n, grade=:g, notes=:no, active=:a WHERE id=:id"),
                                             {"n": nm, "g": e_grade.strip(), "no": e_notes.strip(), "a": e_active, "id": ed_id})
                            st.success("반 정보를 수정했어요.")
                            st.rerun()
                    if del_cls:
                        if not confirm_cls_del:
                            st.warning("삭제하려면 위의 확인 칸을 먼저 체크해 주세요. 학기가 끝난 반은 삭제보다 '운영 중'을 끄는 걸 권해요.")
                        else:
                            with engine.begin() as conn:
                                conn.execute(text("DELETE FROM classes WHERE id = :id"), {"id": ed_id})
                            st.success("반을 삭제했어요.")
                            st.rerun()

    # ---- ② 수업 시간 ----
    with tab2:
        if classes_all.empty:
            st.info("먼저 ①에서 반을 만들어 주세요.")
        else:
            sc_opts = {r["name"]: int(r["id"]) for _, r in classes_all.iterrows()}
            sc_name = st.selectbox("반 선택", list(sc_opts.keys()), key="sched_class_sel")
            sc_id = sc_opts[sc_name]
            my = sched_all[sched_all["class_id"] == sc_id]

            c_list, c_add = st.columns([1, 1])
            with c_list:
                st.markdown(f"##### 🕒 {sc_name} 수업 시간")
                if my.empty:
                    st.caption("등록된 수업 시간이 없습니다. 오른쪽에서 추가해 주세요.")
                for r in my.itertuples():
                    x, y = st.columns([3, 1])
                    x.markdown(md(f"**{WEEKDAYS[int(r.weekday)]}요일**　{r.start_time} ~ {r.end_time}"))
                    if y.button("🗑️ 삭제", key=f"del_sched_{r.id}"):
                        with engine.begin() as conn:
                            conn.execute(text("DELETE FROM class_schedules WHERE id = :id"), {"id": int(r.id)})
                        st.rerun()

            with c_add:
                st.markdown("##### ➕ 수업 시간 추가")
                with st.form(f"sched_add_form_{sc_id}"):
                    wd_label = st.selectbox("요일", [f"{d}요일" for d in WEEKDAYS], index=5)
                    t1, t2 = st.columns(2)
                    t_start = t1.time_input("시작", value=dtime(20, 30), step=300)
                    t_end = t2.time_input("종료", value=dtime(22, 0), step=300)
                    if st.form_submit_button("추가", type="primary"):
                        wd = [f"{d}요일" for d in WEEKDAYS].index(wd_label)
                        s_str, e_str = t_start.strftime("%H:%M"), t_end.strftime("%H:%M")
                        overlap = my[(my["weekday"] == wd) & (my["start_time"] < e_str) & (my["end_time"] > s_str)]
                        if e_str <= s_str:
                            st.warning("종료 시각이 시작 시각보다 늦어야 해요. (오후 2시는 14:00)")
                        elif not overlap.empty:
                            st.warning("같은 요일에 겹치는 수업 시간이 이미 있어요.")
                        else:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    INSERT INTO class_schedules (class_id, weekday, start_time, end_time)
                                    VALUES (:c, :w, :s, :e)
                                """), {"c": sc_id, "w": wd, "s": s_str, "e": e_str})
                            st.success(f"{wd_label} {s_str}~{e_str} 추가했어요.")
                            st.rerun()

    # ---- ③ 학생 배정 ----
    with tab3:
        if classes_all.empty:
            st.info("먼저 ①에서 반을 만들어 주세요.")
        else:
            as_opts = {r["name"]: int(r["id"]) for _, r in classes_all.iterrows()}
            as_name = st.selectbox("반 선택", list(as_opts.keys()), key="assign_class_sel")
            as_id = as_opts[as_name]
            with engine.connect() as conn:
                students = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY group_name, name", conn)
                current = pd.read_sql_query(text("SELECT member_id FROM class_members WHERE class_id = :c"), conn, params={"c": as_id})
            if students.empty:
                st.info("먼저 '구성원 등록/목록'에서 학생을 등록해 주세요.")
            else:
                label_of = {int(r["id"]): f"{r['name']} ({r['group_name'] or '소속 없음'}) · ID {r['id']}" for _, r in students.iterrows()}
                cur_ids = [int(i) for i in current["member_id"] if int(i) in label_of]
                # [수정 16] 양식(form) 안에 넣어서, 학생을 고르는 동안에는 화면을 다시 그리지 않음
                #           → '저장'을 누를 때 한 번만 실행되어 흐려짐이 없어짐
                st.caption(f"현재 저장된 학생 {len(cur_ids)}명 · 다 고른 뒤 '저장'을 누르세요")
                with st.form(f"assign_form_{as_id}"):
                    picked = st.multiselect(
                        f"{as_name} 학생 (이름을 입력하면 검색돼요)",
                        options=list(label_of.keys()),
                        default=cur_ids,
                        format_func=lambda i: label_of[i],
                    )
                    save_assign = st.form_submit_button("💾 학생 배정 저장", type="primary")
                if save_assign:
                    with engine.begin() as conn:
                        conn.execute(text("DELETE FROM class_members WHERE class_id = :c"), {"c": as_id})
                        for mid in picked:
                            conn.execute(text("INSERT INTO class_members (class_id, member_id) VALUES (:c, :m)"),
                                         {"c": as_id, "m": int(mid)})
                    added, removed = set(picked) - set(cur_ids), set(cur_ids) - set(picked)
                    st.session_state["flash"] = f"✅ {as_name} 학생 배정 저장 · 총 {len(picked)}명 (추가 {len(added)}명, 제외 {len(removed)}명)"
                    st.rerun()

# 6. 데이터 백업 (CSV/엑셀 다운로드)
elif menu == MENU_BACKUP:
    st.header("💾 6. 전체 데이터 백업 및 엑셀(CSV) 다운로드")
    st.write("클라우드 DB에 저장된 모든 데이터를 CSV 파일로 즉시 내려받을 수 있습니다.")
    st.divider()

    with engine.connect() as conn:
        df_exp_members = pd.read_sql_query("""
            SELECT id AS 학생ID, name AS 이름, group_name AS 소속_학교, contact AS 연락처, notes AS 학습메모, status AS 재원상태
            FROM members ORDER BY id ASC
        """, conn)

        df_exp_attendance = pd.read_sql_query("""
            SELECT a.id AS 출결ID, a.event_date AS 일자, m.name AS 학생이름, m.group_name AS 소속_학교, a.status AS 출결상태
            FROM attendance a
            JOIN members m ON a.member_id = m.id
            ORDER BY a.event_date DESC, a.id DESC
        """, conn)

        df_exp_scores = pd.read_sql_query("""
            SELECT s.id AS 평가ID, s.record_date AS 시험일자, m.name AS 학생이름, m.group_name AS 소속_학교,
                   COALESCE(s.exam_type, '모의고사') AS 시험종류, s.category AS 시험이름,
                   s.score AS 점수, s.max_score AS 만점, s.grade AS 등급_성취도, s.percentile AS 백분위,
                   s.exam_scope AS 시험범위, s.quiz_format AS 테스트유형,
                   s.objective_score AS 객관식, s.essay_score AS 서술형, s.school_avg AS 학교평균,
                   s.reading_score AS 독서점수, s.literature_score AS 문학점수, s.grammar_score AS 문법_화법점수,
                   s.wrong_questions AS 주요오답문항, s.feedback AS 피드백
            FROM scores s
            JOIN members m ON s.member_id = m.id
            ORDER BY s.record_date DESC, s.id DESC
        """, conn)

        df_exp_logs = pd.read_sql_query("""
            SELECT l.id AS 일지ID, l.log_date AS 상담일자, m.name AS 학생이름, m.group_name AS 소속_학교,
                   l.topic AS 상담주제, l.details AS 면담상세내용
            FROM logs l
            JOIN members m ON l.member_id = m.id
            ORDER BY l.log_date DESC, l.id DESC
        """, conn)

    def csv_bytes(df):
        return df.to_csv(index=False).encode('utf-8-sig')

    st.subheader("1️⃣ 전체 학생 명단")
    st.dataframe(df_exp_members, width="stretch")
    st.download_button(
        label="📥 학생 명단 CSV 다운로드",
        data=csv_bytes(df_exp_members),
        file_name=f"국어학원_학생명단_{today_kst()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("2️⃣ 국어 성적 및 세부 평가 기록")
    st.dataframe(df_exp_scores, width="stretch")
    st.download_button(
        label="📥 국어 성적/평가 CSV 다운로드",
        data=csv_bytes(df_exp_scores),
        file_name=f"국어학원_성적평가기록_{today_kst()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("3️⃣ 전체 출결 기록")
    st.dataframe(df_exp_attendance, width="stretch")
    st.download_button(
        label="📥 출결 기록 CSV 다운로드",
        data=csv_bytes(df_exp_attendance),
        file_name=f"국어학원_출결기록_{today_kst()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("4️⃣ 상담 및 학습 면담 일지")
    st.dataframe(df_exp_logs, width="stretch")
    st.download_button(
        label="📥 상담 일지 CSV 다운로드",
        data=csv_bytes(df_exp_logs),
        file_name=f"국어학원_상담일지_{today_kst()}.csv",
        mime="text/csv"
    )

    st.divider()

    # [수정 12] 반·시간표 백업
    st.subheader("5️⃣ 반·시간표 및 수강생")
    with engine.connect() as conn:
        df_exp_classes = pd.read_sql_query("""
            SELECT c.name AS 반이름, c.grade AS 학년,
                   CASE WHEN c.active THEN '운영 중' ELSE '운영 종료' END AS 상태,
                   (SELECT string_agg(substr('월화수목금토일', s.weekday + 1, 1) || ' ' || s.start_time || '~' || s.end_time,
                                      ' · ' ORDER BY s.weekday, s.start_time)
                      FROM class_schedules s WHERE s.class_id = c.id) AS 수업시간,
                   (SELECT string_agg(m.name, ', ' ORDER BY m.name)
                      FROM class_members cm JOIN members m ON m.id = cm.member_id
                     WHERE cm.class_id = c.id) AS 수강생,
                   c.notes AS 메모
            FROM classes c ORDER BY c.name
        """, conn)
    st.dataframe(df_exp_classes, hide_index=True, width="stretch")
    st.download_button(
        label="📥 반·시간표 CSV 다운로드",
        data=csv_bytes(df_exp_classes),
        file_name=f"국어학원_반시간표_{today_kst()}.csv",
        mime="text/csv"
    )

    st.divider()

    # [수정 19] 수업 일지 백업
    st.subheader("6️⃣ 수업 일지 (수업 내용 · 과제)")
    with engine.connect() as conn:
        df_exp_lessons = pd.read_sql_query("""
            SELECT l.lesson_date AS 수업일, c.name AS 반이름, l.content AS 수업내용,
                   l.homework AS 과제, l.due_date AS 제출일, l.notes AS 메모
            FROM lessons l JOIN classes c ON c.id = l.class_id
            ORDER BY l.lesson_date DESC, c.name
        """, conn)
    st.dataframe(df_exp_lessons.fillna(""), hide_index=True, width="stretch")
    st.download_button(
        label="📥 수업 일지 CSV 다운로드",
        data=csv_bytes(df_exp_lessons),
        file_name=f"국어학원_수업일지_{today_kst()}.csv",
        mime="text/csv"
    )
