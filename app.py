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
    return True


init_db()

WEEKDAYS = "월화수목금토일"
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
    st.title("🔒 국어 학원 관리 시스템 로그인")
    input_pw = st.text_input("관리자 비밀번호를 입력하세요", type="password")
    if st.button("로그인"):
        if input_pw == ADMIN_PW:
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ----------------- 본 프로그램 화면 -----------------

# [수정 1] 메뉴 이름을 상수로 두고, 아래 조건문에서도 같은 이름을 쓰도록 통일
MENU_TODAY = "🏠  오늘"
MENU_MEMBERS = "👥  구성원 등록/목록"
MENU_ATT = "📅  출결 체크"
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
/* 메뉴 글자 크기 */
section[data-testid="stSidebar"] [role="radiogroup"] p {
    font-size: 1.02rem;
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### 📚 국어 관리 시스템")
    st.caption(f"오늘 {today_kst().strftime('%Y년 %m월 %d일')} ({'월화수목금토일'[today_kst().weekday()]})")
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
            SELECT l.log_date AS 일자, m.name AS 이름, m.group_name AS 소속, l.topic AS 주제
            FROM logs l JOIN members m ON l.member_id = m.id
            ORDER BY l.log_date DESC, l.id DESC LIMIT 5
        """, conn)
        recent_scores = pd.read_sql_query("""
            SELECT s.record_date AS 일자, m.name AS 이름, s.category AS 평가명, s.score AS 총점
            FROM scores s JOIN members m ON s.member_id = m.id
            ORDER BY s.record_date DESC, s.id DESC LIMIT 5
        """, conn)

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
            for r in today_classes.itertuples():
                roster_ids = set(class_roster[class_roster["class_id"] == r.id]["member_id"])
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
                    b.button("출결 입력 →", key=f"go_att_{r.id}_{r.start_time}",
                             on_click=go_class_attendance, args=(r.name,), width="stretch")

        st.subheader("📅 오늘 출결")
        if att_today.empty:
            st.info("오늘 입력된 출결이 아직 없습니다.")
        else:
            st.dataframe(att_today.drop(columns=["member_id", "class_id"]), hide_index=True, width="stretch")

    with col_r:
        st.subheader("💬 최근 상담")
        if recent_logs.empty:
            st.info("상담 기록이 없습니다.")
        else:
            st.dataframe(recent_logs, hide_index=True, width="stretch")

        st.subheader("📝 최근 시험 결과")
        if recent_scores.empty:
            st.info("성적 기록이 없습니다.")
        else:
            st.dataframe(recent_scores, hide_index=True, width="stretch")

# 1. 구성원 등록 및 목록
elif menu == MENU_MEMBERS:
    st.header("👥 구성원 등록 및 목록")
    col_add, col_edit, col_del = st.columns([1, 1, 1])

    with col_add:
        with st.expander("➕ 새 학생 등록", expanded=False):
            with st.form("add_member_form"):
                name = st.text_input("학생 이름 *")
                group_name = st.text_input("학교 및 학년/반 (예: 비전고1, 한광고2)")
                contact = st.text_input("연락처")
                notes = st.text_input("기본 메모 (취약 영역, 목표 등급/대학)")
                submit = st.form_submit_button("등록하기")

                if submit and name:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO members (name, group_name, contact, notes, status)
                            VALUES (:name, :group_name, :contact, :notes, '재원')
                        """), {"name": name, "group_name": group_name, "contact": contact, "notes": notes})
                    st.success(f"'{name}' 학생이 클라우드 DB에 등록되었습니다.")
                    st.rerun()

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
        with st.expander("⚙️ 퇴원 처리 및 원생 상태 변경", expanded=False):
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
                        st.warning("퇴원 처리되었습니다. 메인 목록에서 숨겨집니다.")
                        st.rerun()

                with col_b2:
                    if st.button("🔄 '재원' 복원", width="stretch"):
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE members SET status = '재원' WHERE id = :id"), {"id": target_id})
                        st.success("재원생으로 다시 복원되었습니다.")
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

# 3. 국어 성적 및 평가 입력
elif menu == MENU_SCORE:
    st.header("3. 국어 성적 및 영역별 평가 분석 (통합 공통국어)")
    with engine.connect() as conn:
        # [수정 4] 퇴원생은 입력 목록에서 제외
        members = pd.read_sql_query(f"SELECT id, name, group_name FROM members {ACTIVE_ONLY} ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        col_sc_add, col_sc_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}

        with col_sc_add:
            with st.expander("➕ 새 국어 시험/평가 기록", expanded=True):
                with st.form("score_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="score_add_target")
                    score_date = st.date_input("시험/평가 일자", today_kst(), key="score_add_date")
                    category = st.text_input("평가 회차명 (예: 전국연합학력평가, 1학기 중간고사 국어)")

                    st.markdown("##### 📝 통합 국어 영역별 세부 득점")
                    c_sc1, c_sc2, c_sc3 = st.columns(3)
                    # [수정 3] 기본값을 0으로 (깜빡해도 가짜 점수가 저장되지 않도록)
                    reading_sc = c_sc1.number_input("독서 점수", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
                    literature_sc = c_sc2.number_input("문학 점수", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
                    grammar_sc = c_sc3.number_input("문법·어휘/화법 점수", min_value=0.0, max_value=100.0, value=0.0, step=0.5)

                    calc_total = reading_sc + literature_sc + grammar_sc
                    st.caption("※ 총점은 '평가 데이터 저장'을 누를 때 세 영역을 더해 저장됩니다.")

                    wrong_q = st.text_input("주요 오답 문항 번호 / 취약 제재")
                    feedback = st.text_area("국어 피드백 및 약점 처방")
                    submit_score = st.form_submit_button("평가 데이터 저장")

                    if submit_score:
                        if not category:
                            st.warning("평가 회차명을 입력해 주세요.")
                        elif calc_total == 0:
                            st.warning("영역별 점수가 모두 0점입니다. 점수를 입력했는지 확인해 주세요.")
                        else:
                            m_id = member_dict[target_member]
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    INSERT INTO scores (
                                        member_id, record_date, category, score, feedback,
                                        reading_score, literature_score, grammar_score, wrong_questions
                                    ) VALUES (
                                        :m_id, :r_dt, :cat, :sc, :fb, :r_sc, :l_sc, :g_sc, :wq
                                    )
                                """), {
                                    "m_id": m_id, "r_dt": str(score_date), "cat": category, "sc": calc_total, "fb": feedback,
                                    "r_sc": reading_sc, "l_sc": literature_sc, "g_sc": grammar_sc, "wq": wrong_q
                                })
                            st.success(f"{category} (총점 {calc_total:.1f}점) 저장이 완료되었습니다.")
                            st.rerun()

        with col_sc_edit:
            with st.expander("✏️/🗑️ 등록된 성적 수정 및 삭제", expanded=False):
                with engine.connect() as conn:
                    scores_for_edit = pd.read_sql_query("""
                        SELECT s.id, s.record_date, s.category, s.score, s.feedback,
                               s.reading_score, s.literature_score, s.grammar_score, s.wrong_questions,
                               m.name, m.group_name
                        FROM scores s
                        JOIN members m ON s.member_id = m.id
                        ORDER BY s.id DESC LIMIT 50
                    """, conn)

                if scores_for_edit.empty:
                    st.info("수정할 성적 데이터가 없습니다.")
                else:
                    score_options = {
                        f"[{row['record_date']}] {row['name']} - {row['category']} (총 {row['score']}점)": row['id']
                        for _, row in scores_for_edit.iterrows()
                    }
                    selected_sc_label = st.selectbox("수정/삭제할 성적 선택", list(score_options.keys()))
                    selected_sc_id = score_options[selected_sc_label]
                    target_row = scores_for_edit[scores_for_edit['id'] == selected_sc_id].iloc[0]

                    with st.form("score_edit_form"):
                        ed_category = st.text_input("평가 회차명", value=target_row['category'])
                        c_ed1, c_ed2, c_ed3 = st.columns(3)
                        ed_read = c_ed1.number_input("독서 점수", min_value=0.0, max_value=100.0, value=float(target_row['reading_score'] or 0.0), step=0.5)
                        ed_lit = c_ed2.number_input("문학 점수", min_value=0.0, max_value=100.0, value=float(target_row['literature_score'] or 0.0), step=0.5)
                        ed_gram = c_ed3.number_input("문법/화법 점수", min_value=0.0, max_value=100.0, value=float(target_row['grammar_score'] or 0.0), step=0.5)

                        ed_total = ed_read + ed_lit + ed_gram

                        ed_wrong = st.text_input("오답 문항 메모", value=target_row['wrong_questions'] if target_row['wrong_questions'] else "")
                        ed_feedback = st.text_area("피드백", value=target_row['feedback'] if target_row['feedback'] else "")

                        # [수정 6] 삭제는 체크해야만 실행
                        confirm_sc_del = st.checkbox("이 성적 기록을 삭제합니다 (되돌릴 수 없음)")

                        btn_col1, btn_col2 = st.columns(2)
                        save_sc_btn = btn_col1.form_submit_button("수정 저장")
                        del_sc_btn = btn_col2.form_submit_button("기록 삭제", type="primary")

                        if save_sc_btn:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    UPDATE scores
                                    SET category = :cat, score = :sc, feedback = :fb,
                                        reading_score = :r_sc, literature_score = :l_sc, grammar_score = :g_sc, wrong_questions = :wq
                                    WHERE id = :id
                                """), {
                                    "cat": ed_category, "sc": ed_total, "fb": ed_feedback,
                                    "r_sc": ed_read, "l_sc": ed_lit, "g_sc": ed_gram, "wq": ed_wrong, "id": selected_sc_id
                                })
                            st.success(f"국어 성적 수정이 완료되었습니다. (총점 {ed_total:.1f}점)")
                            st.rerun()

                        if del_sc_btn:
                            if not confirm_sc_del:
                                st.warning("삭제하려면 위의 확인 칸을 먼저 체크해 주세요.")
                            else:
                                with engine.begin() as conn:
                                    conn.execute(text("DELETE FROM scores WHERE id = :id"), {"id": selected_sc_id})
                                st.success("선택한 성적 기록이 삭제되었습니다.")
                                st.rerun()

        st.divider()
        st.subheader("🕒 최근 등록된 국어 평가 (최신 10건)")
        with engine.connect() as conn:
            recent_sc = pd.read_sql_query("""
                SELECT s.record_date AS 일자, m.name AS 이름, s.category AS 평가명,
                       s.score AS 총점, s.reading_score AS 독서, s.literature_score AS 문학,
                       s.grammar_score AS 문법_화법, s.wrong_questions AS 주요오답, s.feedback AS 피드백
                FROM scores s
                JOIN members m ON s.member_id = m.id
                ORDER BY s.id DESC LIMIT 10
            """, conn)
        st.dataframe(recent_sc, width="stretch")

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
                    st.dataframe(df_att, width="stretch")

                with col_score:
                    st.subheader("📈 국어 총점 추이 그래프")
                    df_sc_chart = pd.read_sql_query(text(
                        "SELECT record_date, category, score FROM scores WHERE member_id = :id ORDER BY record_date ASC, id ASC"
                    ), conn, params={"id": selected_id})

                    if not df_sc_chart.empty:
                        df_sc_chart['회차'] = df_sc_chart['record_date'] + " (" + df_sc_chart['category'] + ")"
                        chart_data = df_sc_chart.set_index('회차')[['score']].rename(columns={'score': '국어 총점'})
                        st.line_chart(chart_data)
                    else:
                        st.info("등록된 성적 데이터가 없습니다.")

                st.subheader("📊 통합 국어 영역별 세부 성적 및 오답 기록")
                df_sc_detail = pd.read_sql_query(text("""
                    SELECT record_date AS 일자, category AS 평가명, score AS 총점,
                           reading_score AS 독서, literature_score AS 문학,
                           grammar_score AS 문법_화법,
                           wrong_questions AS 주요오답, feedback AS 피드백
                    FROM scores WHERE member_id = :id ORDER BY record_date DESC
                """), conn, params={"id": selected_id})
                st.dataframe(df_sc_detail, width="stretch")

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

    if tt.empty:
        st.info("아직 등록된 수업이 없습니다. '반·시간표 관리'에서 반과 수업 시간을 등록해 주세요.")
    else:
        def minutes(hhmm):
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        total_min = sum(minutes(r.end_time) - minutes(r.start_time) for r in tt.itertuples())
        st.caption(f"주간 수업 {len(tt)}회 · 반 {tt['class_id'].nunique()}개 · 총 {total_min // 60}시간 {total_min % 60}분")

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
                    st.markdown(
                        f"<div style='border:1px solid #d5d9e0;border-left:5px solid {border};border-radius:8px;"
                        f"padding:8px 10px;margin-bottom:8px;background:#ffffff;box-shadow:0 2px 4px rgba(0,0,0,0.06)'>"
                        f"<div style='font-size:0.8rem;color:#5f6475'>{r.start_time}~{r.end_time}</div>"
                        f"<div style='font-weight:700;font-size:0.92rem;line-height:1.35;margin:2px 0;word-break:keep-all'>{r.name}</div>"
                        f"<div style='font-size:0.78rem;color:#808495'>학생 {r.n}명</div></div>",
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
                   s.category AS 평가회차명, s.score AS 국어총점,
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
