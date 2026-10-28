import streamlit as st
import pandas as pd
from datetime import date, datetime
from sqlalchemy import create_engine, text

# ----------------- DB 연결 설정 (Supabase PostgreSQL) -----------------
st.set_page_config(page_title="국어 전문 학생 종합 관리 시스템", layout="wide")

try:
    DB_URL = st.secrets["database"]["url"]
    ADMIN_PW = st.secrets["passwords"]["admin"]
except Exception:
    st.error("보안 설정(secrets.toml)을 찾을 수 없습니다. .streamlit/secrets.toml 파일을 확인해 주세요.")
    st.stop()

engine = create_engine(DB_URL)

def init_db():
    with engine.begin() as conn:
        # 1. 학생 테이블
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                group_name TEXT,
                contact TEXT,
                notes TEXT
            );
        """))
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

init_db()

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
st.title("📚 국어 종합 관리 시스템 (클라우드 연동)")

menu = st.sidebar.selectbox("메뉴 선택", [
    "구성원 등록/목록", 
    "출결 체크", 
    "성적 및 국어 평가 입력", 
    "상담/소통 일지", 
    "개인별 종합 조회", 
    "💾 데이터 백업 (CSV/엑셀)"
])

# 1. 구성원 등록 및 목록
if menu == "구성원 등록/목록":
    st.header("1. 학생 관리")
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
                            INSERT INTO members (name, group_name, contact, notes)
                            VALUES (:name, :group_name, :contact, :notes)
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
                edit_options = {f"[ID:{row['id']}] {row['name']} ({row['group_name']})": row['id'] for _, row in m_list_edit.iterrows()}
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
        with st.expander("🗑️ 학생 삭제 (퇴원/중복 정리)", expanded=False):
            with engine.connect() as conn:
                m_list = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY id ASC", conn)
            
            if m_list.empty:
                st.info("등록된 인원이 없습니다.")
            else:
                del_options = {f"[ID:{row['id']}] {row['name']} ({row['group_name']})": row['id'] for _, row in m_list.iterrows()}
                target_del = st.selectbox("삭제할 대상 선택", list(del_options.keys()), key="del_selector")
                
                confirm_del = st.checkbox("선택한 학생의 모든 기록을 영구 삭제합니다.")
                if st.button("영구 삭제 실행", type="primary"):
                    if confirm_del:
                        del_id = del_options[target_del]
                        with engine.begin() as conn:
                            conn.execute(text("DELETE FROM members WHERE id = :id"), {"id": del_id})
                        st.success(f"'{target_del}' 정보가 완전히 삭제되었습니다.")
                        st.rerun()
                    else:
                        st.warning("확인 체크박스를 체크한 후 삭제 버튼을 눌러주세요.")

    st.divider()
    st.subheader("📋 전체 학생 명단 검색 및 조회")
    
    with engine.connect() as conn:
        df_members_raw = pd.read_sql_query("SELECT id AS ID, name AS 이름, group_name AS 소속_학교, contact AS 연락처, notes AS 기본메모 FROM members ORDER BY id ASC", conn)

    if df_members_raw.empty:
        st.info("등록된 학생이 없습니다. 새 학생을 등록해 보세요.")
    else:
        f_col1, f_col2 = st.columns([1, 2])
        all_groups = ["전체"] + sorted([g for g in df_members_raw['소속_학교'].dropna().unique() if g])
        selected_grp = f_col1.selectbox("🏫 학교/그룹별 필터", all_groups, key="filter_group_list")
        search_kw = f_col2.text_input("🔍 이름 또는 메모 검색", placeholder="학생 이름이나 메모 키워드를 입력하세요", key="search_kw_list")

        filtered_df = df_members_raw.copy()
        if selected_grp != "전체":
            filtered_df = filtered_df[filtered_df['소속_학교'] == selected_grp]
        if search_kw.strip():
            kw = search_kw.strip()
            filtered_df = filtered_df[
                filtered_df['이름'].str.contains(kw, na=False) |
                filtered_df['기본메모'].str.contains(kw, na=False)
            ]

        st.caption(f"검색 결과: 총 **{len(filtered_df)}명** / 전체 {len(df_members_raw)}명")
        st.dataframe(filtered_df, use_container_width=True)

# 2. 출결 체크
elif menu == "출결 체크":
    st.header("2. 출석 및 수업 참여 관리")
    with engine.connect() as conn:
        members = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        col_att_add, col_att_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}
        status_options = ["출석", "지각", "결석", "보강완료"]

        with col_att_add:
            with st.expander("➕ 새 출결 입력", expanded=True):
                with st.form("attendance_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="att_add_target")
                    att_date = st.date_input("날짜", date.today(), key="att_add_date")
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
                        except:
                            curr_dt = date.today()
                        ed_att_date = st.date_input("날짜 수정", curr_dt)
                        curr_status_idx = status_options.index(target_att['status']) if target_att['status'] in status_options else 0
                        ed_att_status = st.radio("상태 수정", status_options, index=curr_status_idx, horizontal=True)

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
                            with engine.begin() as conn:
                                conn.execute(text("DELETE FROM attendance WHERE id = :id"), {"id": selected_att_id})
                            st.success("출결이 삭제되었습니다.")
                            st.rerun()

        st.divider()
        st.subheader("🕒 최근 등록된 출결 (최신 10건)")
        with engine.connect() as conn:
            recent_att = pd.read_sql_query("""
                SELECT a.id AS 번호, a.event_date AS 날짜, m.name AS 이름, m.group_name AS 소속, a.status AS 출결상태
                FROM attendance a
                JOIN members m ON a.member_id = m.id
                ORDER BY a.id DESC LIMIT 10
            """, conn)
        st.dataframe(recent_att, use_container_width=True)

# 3. 국어 성적 및 평가 입력
elif menu == "성적 및 국어 평가 입력":
    st.header("3. 국어 성적 및 영역별 평가 분석 (통합 공통국어)")
    with engine.connect() as conn:
        members = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        col_sc_add, col_sc_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}

        with col_sc_add:
            with st.expander("➕ 새 국어 시험/평가 기록", expanded=True):
                with st.form("score_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="score_add_target")
                    score_date = st.date_input("시험/평가 일자", date.today(), key="score_add_date")
                    category = st.text_input("평가 회차명 (예: 전국연합학력평가, 1학기 중간고사 국어)")
                    
                    st.markdown("##### 📝 통합 국어 영역별 세부 득점")
                    c_sc1, c_sc2, c_sc3 = st.columns(3)
                    reading_sc = c_sc1.number_input("독서 점수", min_value=0.0, max_value=50.0, value=35.0, step=0.5)
                    literature_sc = c_sc2.number_input("문학 점수", min_value=0.0, max_value=50.0, value=35.0, step=0.5)
                    grammar_sc = c_sc3.number_input("문법·어휘/화법 점수", min_value=0.0, max_value=50.0, value=20.0, step=0.5)
                    
                    calc_total = reading_sc + literature_sc + grammar_sc
                    st.info(f"💡 자동 합산 총점: **{calc_total:.1f}점**")

                    wrong_q = st.text_input("주요 오답 문항 번호 / 취약 제재")
                    feedback = st.text_area("국어 피드백 및 약점 처방")
                    submit_score = st.form_submit_button("평가 데이터 저장")
                    
                    if submit_score and category:
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
                        ed_read = c_ed1.number_input("독서 점수", min_value=0.0, max_value=50.0, value=float(target_row['reading_score'] or 0.0), step=0.5)
                        ed_lit = c_ed2.number_input("문학 점수", min_value=0.0, max_value=50.0, value=float(target_row['literature_score'] or 0.0), step=0.5)
                        ed_gram = c_ed3.number_input("문법/화법 점수", min_value=0.0, max_value=50.0, value=float(target_row['grammar_score'] or 0.0), step=0.5)
                        
                        ed_total = ed_read + ed_lit + ed_gram
                        st.caption(f"수정 후 자동 계산 총점: {ed_total:.1f}점")

                        ed_wrong = st.text_input("오답 문항 메모", value=target_row['wrong_questions'] if target_row['wrong_questions'] else "")
                        ed_feedback = st.text_area("피드백", value=target_row['feedback'] if target_row['feedback'] else "")
                        
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
                            st.success("국어 성적 수정이 완료되었습니다.")
                            st.rerun()

                        if del_sc_btn:
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
        st.dataframe(recent_sc, use_container_width=True)

# 4. 상담/소통 일지
elif menu == "상담/소통 일지":
    st.header("4. 학생/학부모 소통 및 학습 면담 일지")
    with engine.connect() as conn:
        members = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY name ASC", conn)

    if members.empty:
        st.warning("먼저 학생을 등록해 주세요.")
    else:
        col_lg_add, col_lg_edit = st.columns([1.2, 1])
        member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in members.iterrows()}

        with col_lg_add:
            with st.expander("➕ 새 상담 일지 작성", expanded=True):
                with st.form("log_form"):
                    target_member = st.selectbox("학생 선택", list(member_dict.keys()), key="log_add_target")
                    log_date = st.date_input("상담 일자", date.today(), key="log_add_date")
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
                        except:
                            curr_lg_dt = date.today()
                        ed_log_date = st.date_input("상담 일자", curr_lg_dt)
                        ed_topic = st.text_input("상담 주제", value=target_lg['topic'])
                        ed_details = st.text_area("상세 내용", value=target_lg['details'] if target_lg['details'] else "")

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
        st.dataframe(recent_lg, use_container_width=True)

# 5. 개인별 종합 조회
elif menu == "개인별 종합 조회":
    st.header("5. 국어 학습 종합 포트폴리오")
    with engine.connect() as conn:
        members = pd.read_sql_query("SELECT id, name, group_name FROM members ORDER BY name ASC", conn)

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
            member_dict = {f"[{row['group_name']}] {row['name']} (ID:{row['id']})": row['id'] for _, row in filtered_members.iterrows()}
            selected_label = st.selectbox("조회할 학생 선택", list(member_dict.keys()), key="pf_student_select")
            selected_id = member_dict[selected_label]

            with engine.connect() as conn:
                m_info = pd.read_sql_query(text("SELECT * FROM members WHERE id = :id"), conn, params={"id": selected_id}).iloc[0]
                st.markdown(f"### 👤 {m_info['name']} 학생 ({m_info['group_name']})")
                st.write(f"📞 연락처: {m_info['contact']} | 📝 학습 메모: {m_info['notes']}")
                st.divider()

                col_att, col_score = st.columns([1, 1.4])
                
                with col_att:
                    st.subheader("📅 최근 출결 현황")
                    df_att = pd.read_sql_query(text("SELECT event_date AS 일자, status AS 상태 FROM attendance WHERE member_id = :id ORDER BY event_date DESC"), conn, params={"id": selected_id})
                    st.dataframe(df_att, use_container_width=True)

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
                st.dataframe(df_sc_detail, use_container_width=True)

                st.divider()
                st.subheader("💬 상담 및 학습 면담 이력")
                df_lg = pd.read_sql_query(text("SELECT log_date AS 일자, topic AS 주제, details AS 상세내용 FROM logs WHERE member_id = :id ORDER BY log_date DESC"), conn, params={"id": selected_id})
                st.dataframe(df_lg, use_container_width=True)

# 6. 데이터 백업 (CSV/엑셀 다운로드)
elif menu == "💾 데이터 백업 (CSV/엑셀)":
    st.header("6. 전체 데이터 백업 및 엑셀(CSV) 다운로드")
    st.write("클라우드 DB에 저장된 모든 데이터를 CSV 파일로 즉시 내려받을 수 있습니다.")
    st.divider()

    with engine.connect() as conn:
        df_exp_members = pd.read_sql_query("""
            SELECT id AS 학생ID, name AS 이름, group_name AS 소속_학교, contact AS 연락처, notes AS 학습메모
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

    st.subheader("1️⃣ 전체 학생 명단")
    st.dataframe(df_exp_members, use_container_width=True)
    st.download_button(
        label="📥 학생 명단 CSV 다운로드",
        data=df_exp_members.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
        file_name=f"국어학원_학생명단_{date.today()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("2️⃣ 국어 성적 및 세부 평가 기록")
    st.dataframe(df_exp_scores, use_container_width=True)
    st.download_button(
        label="📥 국어 성적/평가 CSV 다운로드",
        data=df_exp_scores.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
        file_name=f"국어학원_성적평가기록_{date.today()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("3️⃣ 전체 출결 기록")
    st.dataframe(df_exp_attendance, use_container_width=True)
    st.download_button(
        label="📥 출결 기록 CSV 다운로드",
        data=df_exp_attendance.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
        file_name=f"국어학원_출결기록_{date.today()}.csv",
        mime="text/csv"
    )

    st.divider()

    st.subheader("4️⃣ 상담 및 학습 면담 일지")
    st.dataframe(df_exp_logs, use_container_width=True)
    st.download_button(
        label="📥 상담 일지 CSV 다운로드",
        data=df_exp_logs.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
        file_name=f"국어학원_상담일지_{date.today()}.csv",
        mime="text/csv"
    )