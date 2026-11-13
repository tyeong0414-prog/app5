# -*- coding: utf-8 -*-
"""빌려준 물건·돈 기록 앱 (Streamlit) - 실행: streamlit run app.py"""
import base64, io, json, sqlite3, urllib.parse
from datetime import date, timedelta

import pandas as pd
import qrcode
import streamlit as st

DB = "loans.db"
TODAY = date.today()
st.set_page_config(page_title="빌려줌 장부", page_icon="🤝", layout="centered")

# ---------------- DB ----------------
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS loans(
          id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, person TEXT, kind TEXT,
          title TEXT, amount INTEGER DEFAULT 0, lent_date TEXT, due_date TEXT,
          done INTEGER DEFAULT 0, memo TEXT, photo TEXT);
        CREATE TABLE IF NOT EXISTS repayments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, loan_id INTEGER, pay_date TEXT, amount INTEGER, note TEXT);
        CREATE TABLE IF NOT EXISTS reminders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, loan_id INTEGER, sent_date TEXT, tone TEXT);
        CREATE TABLE IF NOT EXISTS notes(person TEXT PRIMARY KEY, memo TEXT);
        """)

def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]

def x(sql, args=()):
    with conn() as c:
        c.execute(sql, args)

def load_df():
    loans = q("SELECT * FROM loans")
    if not loans:
        return pd.DataFrame()
    df = pd.DataFrame(loans)
    rep = pd.DataFrame(q("SELECT loan_id, SUM(amount) paid FROM repayments GROUP BY loan_id"))
    df = df.merge(rep, how="left", left_on="id", right_on="loan_id") if not rep.empty else df.assign(paid=0)
    df["paid"] = df["paid"].fillna(0).astype(int)
    df["remain"] = df.apply(lambda r: max(r["amount"] - r["paid"], 0) if r["kind"] == "돈" else 0, axis=1)
    def status(r):
        if r["done"]: return "반환완료"
        if r["due_date"] and r["due_date"] < TODAY.isoformat(): return "연체"
        if r["paid"] > 0: return "일부상환"
        return "대기"
    df["status"] = df.apply(status, axis=1)
    return df

won = lambda n: f"{int(n):,}원"

# ---------------- 잠금 ----------------
def get_pin():
    try:
        return str(st.secrets.get("PIN", ""))
    except Exception:
        return ""

def gate():
    pin = get_pin()
    if not pin or st.session_state.get("ok"):
        return
    st.title("🔒 잠금")
    v = st.text_input("PIN", type="password")
    if st.button("열기"):
        if v == pin:
            st.session_state.ok = True
            st.rerun()
        else:
            st.error("PIN이 맞지 않습니다.")
    st.stop()

# ---------------- 메시지 ----------------
TONES = ["친구(말랑)", "직장 동료(정중)", "가족(편하게)", "단호하게"]
def make_msg(r, tone):
    who, what = r["person"], r["title"]
    amt = f"{won(r['remain'])}" if r["kind"] == "돈" else f"'{what}'"
    due = f" ({r['due_date']}까지)" if r["due_date"] else ""
    T = {
     "친구(말랑)": f"{who}야~ 지난번에 빌려준 {amt} 기억나? 슬슬 챙겨야 할 것 같아서ㅎㅎ 편할 때 알려줘!{due}",
     "직장 동료(정중)": f"{who}님, 안녕하세요. 이전에 빌려드린 {amt} 건으로 연락드립니다{due}. 편하신 시간에 말씀 부탁드립니다.",
     "가족(편하게)": f"{who}, 빌려간 {amt} 언제쯤 돌려줄 수 있어? 날짜 정해서 알려줘{due}.",
     "단호하게": f"{who}님, 약속하신 {amt} 반환 기한{due}이 지났습니다. 이번 주 내로 일정을 확정해 연락 주세요.",
    }
    return T[tone]

# ---------------- 화면 조각 ----------------
def alerts(df):
    if df.empty: return
    act = df[(df.done == 0)]
    over = act[act.status == "연체"]
    soon = act[act.due_date.fillna("").between(TODAY.isoformat(), (TODAY + timedelta(days=1)).isoformat())]
    if len(over): st.error(f"⚠️ 연체 {len(over)}건: " + ", ".join(over.person + "·" + over.title))
    if len(soon): st.warning(f"⏰ 오늘·내일 기한 {len(soon)}건: " + ", ".join(soon.person + "·" + soon.title))
    cnt = act[act.direction == "빌려줌"].groupby("person").size()
    for p, n in cnt.items():
        if n >= 3: st.info(f"✋ {p}님에게 미회수 {n}건 - 추가로 빌려주기 전에 확인하세요.")

def add_form():
    with st.form("add", clear_on_submit=True):
        direction = st.radio("구분", ["빌려줌", "빌림"], horizontal=True)
        person = st.text_input("상대방 이름*")
        kind = st.radio("종류", ["돈", "물건"], horizontal=True)
        title = st.text_input("물건명 / 용도*", placeholder="예: 전동드릴, 점심값")
        amount = st.number_input("금액(원) - 돈일 때", min_value=0, step=1000)
        c1, c2 = st.columns(2)
        d = c1.date_input("빌려준 날", TODAY)
        has_due = c2.checkbox("기한 있음", True)
        due = c2.date_input("돌려받을 기한", TODAY + timedelta(days=14))
        memo = st.text_area("메모")
        pic = st.file_uploader("사진/송금내역 (선택)", type=["png", "jpg", "jpeg"])
        if st.form_submit_button("➕ 저장", width="stretch"):
            if not person.strip() or not title.strip():
                st.error("이름과 물건명은 필수입니다.")
            elif kind == "돈" and amount <= 0:
                st.error("금액을 입력하세요.")
            else:
                ph = base64.b64encode(pic.getvalue()).decode() if pic else None
                x("INSERT INTO loans(direction,person,kind,title,amount,lent_date,due_date,memo,photo) VALUES(?,?,?,?,?,?,?,?,?)",
                  (direction, person.strip(), kind, title.strip(), int(amount) if kind == "돈" else 0,
                   d.isoformat(), due.isoformat() if has_due else None, memo, ph))
                st.success("저장했습니다!")

def card(r):
    icon = {"대기": "🟦", "일부상환": "🟨", "연체": "🟥", "반환완료": "✅"}[r["status"]]
    amt = f" · 남은 {won(r['remain'])}/{won(r['amount'])}" if r["kind"] == "돈" else ""
    due = f" · 기한 {r['due_date']}" if r["due_date"] else ""
    with st.expander(f"{icon} {r['person']} - {r['title']}{amt}{due}"):
        st.caption(f"{r['direction']} · {r['lent_date']} · 상태: {r['status']}")
        if r["memo"]: st.write(r["memo"])
        if r["photo"]: st.image(base64.b64decode(r["photo"]), width=240)
        reps = q("SELECT * FROM repayments WHERE loan_id=? ORDER BY pay_date", (r["id"],))
        if reps:
            st.dataframe(pd.DataFrame(reps)[["pay_date", "amount", "note"]].rename(
                columns={"pay_date": "날짜", "amount": "금액", "note": "메모"}), hide_index=True)
        k = r["id"]
        if not r["done"]:
            if r["kind"] == "돈":
                c1, c2 = st.columns([2, 1])
                pay = c1.number_input("상환 금액", 0, int(r["remain"]), 0, 1000, key=f"p{k}")
                if c2.button("상환 기록", key=f"pb{k}") and pay > 0:
                    x("INSERT INTO repayments(loan_id,pay_date,amount,note) VALUES(?,?,?,?)",
                      (k, TODAY.isoformat(), int(pay), ""))
                    if pay >= r["remain"]: x("UPDATE loans SET done=1 WHERE id=?", (k,))
                    st.rerun()
            if st.button("✅ 반환 완료", key=f"d{k}"):
                x("UPDATE loans SET done=1 WHERE id=?", (k,)); st.rerun()
            if r["direction"] == "빌려줌":
                tone = st.selectbox("말투", TONES, key=f"t{k}")
                msg = make_msg(r, tone)
                st.text_area("추천 문구 (복사해서 사용)", msg, key=f"m{k}")
                enc = urllib.parse.quote(msg)
                st.markdown(f"[📱 문자로 보내기](sms:?&body={enc})")
                if st.button("📨 독촉 기록 남기기", key=f"r{k}"):
                    x("INSERT INTO reminders(loan_id,sent_date,tone) VALUES(?,?,?)", (k, TODAY.isoformat(), tone))
                    st.rerun()
        else:
            if st.button("↩️ 완료 취소", key=f"u{k}"):
                x("UPDATE loans SET done=0 WHERE id=?", (k,)); st.rerun()
        rem = q("SELECT sent_date, tone FROM reminders WHERE loan_id=?", (k,))
        if rem: st.caption("독촉 이력: " + ", ".join(f"{a['sent_date']}({a['tone']})" for a in rem))
        if st.button("🗑️ 삭제", key=f"x{k}"):
            for t, col in (("repayments", "loan_id"), ("reminders", "loan_id")):
                x(f"DELETE FROM {t} WHERE {col}=?", (k,))
            x("DELETE FROM loans WHERE id=?", (k,)); st.rerun()

def list_tab(df, direction):
    if df.empty:
        st.info("아직 기록이 없습니다. '➕ 등록' 탭에서 추가하세요."); return
    d = df[df.direction == direction]
    c1, c2, c3 = st.columns([2, 2, 2])
    kw = c1.text_input("검색(이름/물건)", key=f"kw{direction}")
    stt = c2.multiselect("상태", ["대기", "일부상환", "연체", "반환완료"], ["대기", "일부상환", "연체"], key=f"st{direction}")
    sort = c3.selectbox("정렬", ["기한 임박순", "최근 등록순", "사람별"], key=f"so{direction}")
    d = d[d.status.isin(stt)]
    if kw: d = d[d.person.str.contains(kw) | d.title.str.contains(kw)]
    d = {"기한 임박순": d.sort_values("due_date", na_position="last"),
         "최근 등록순": d.sort_values("id", ascending=False),
         "사람별": d.sort_values("person")}[sort]
    if d.empty: st.caption("조건에 맞는 항목이 없습니다."); return
    for _, r in d.iterrows(): card(r)

def people_tab(df):
    if df.empty: st.info("데이터가 없습니다."); return
    m = df[(df.kind == "돈") & (df.done == 0)]
    if not m.empty:
        g = m.pivot_table(index="person", columns="direction", values="remain", aggfunc="sum", fill_value=0)
        for c in ("빌려줌", "빌림"):
            if c not in g: g[c] = 0
        g["순잔액(받을 돈)"] = g["빌려줌"] - g["빌림"]
        st.subheader("사람별 잔액"); st.dataframe(g, width="stretch")
    st.subheader("사람별 메모")
    p = st.selectbox("사람", sorted(df.person.unique()))
    cur = q("SELECT memo FROM notes WHERE person=?", (p,))
    memo = st.text_area("개인 메모(예: 잘 갚는 편)", cur[0]["memo"] if cur else "")
    if st.button("메모 저장"):
        x("INSERT OR REPLACE INTO notes VALUES(?,?)", (p, memo)); st.success("저장됨")

def summary_tab(df):
    if df.empty: st.info("데이터가 없습니다."); return
    ym = TODAY.strftime("%Y-%m")
    mm = df[(df.kind == "돈") & (df.direction == "빌려줌")]
    this = mm[mm.lent_date.str.startswith(ym)]
    a, b, c = st.columns(3)
    a.metric("이번 달 빌려준 돈", won(this.amount.sum()))
    b.metric("누적 회수액", won(mm.paid.sum()))
    c.metric("미회수 잔액", won(mm[mm.done == 0].remain.sum()))
    st.metric("빌린 돈 잔액(갚을 돈)", won(df[(df.kind == "돈") & (df.direction == "빌림") & (df.done == 0)].remain.sum()))
    st.bar_chart(df.groupby("status").size())

def backup_tab():
    data = {"loans": q("SELECT * FROM loans"), "repayments": q("SELECT * FROM repayments"),
            "reminders": q("SELECT * FROM reminders"), "notes": q("SELECT * FROM notes")}
    st.warning("무료 클라우드 서버는 재시작 시 데이터가 초기화될 수 있습니다. 주기적으로 백업하세요.")
    st.download_button("⬇️ 백업 내려받기(JSON)", json.dumps(data, ensure_ascii=False), f"loans_{TODAY}.json", "application/json")
    up = st.file_uploader("백업 복원(JSON)", type="json")
    if up and st.button("복원 실행(현재 데이터 덮어씀)"):
        d = json.load(up)
        with conn() as c:
            for t in ("loans", "repayments", "reminders", "notes"):
                c.execute(f"DELETE FROM {t}")
                for row in d.get(t, []):
                    cols = ",".join(row); ph = ",".join("?" * len(row))
                    c.execute(f"INSERT INTO {t}({cols}) VALUES({ph})", list(row.values()))
        st.success("복원 완료"); st.rerun()
    st.divider()
    st.subheader("📱 QR로 접속")
    url = st.text_input("배포된 앱 주소", st.session_state.get("url", ""), placeholder="https://내앱.streamlit.app")
    if url:
        st.session_state.url = url
        img = qrcode.make(url); buf = io.BytesIO(); img.save(buf, "PNG")
        st.image(buf.getvalue(), width=220, caption="휴대폰 카메라로 스캔하세요")
        st.download_button("QR 이미지 저장", buf.getvalue(), "app_qr.png", "image/png")

# ---------------- main ----------------
init_db()
gate()
st.title("🤝 빌려줌 장부")
st.caption("기록은 10초, 독촉은 앱이 대신")
df = load_df()
alerts(df)
t = st.tabs(["➕ 등록", "📤 빌려준 것", "📥 빌린 것", "👥 사람별", "📊 요약", "⚙️ 백업·QR"])
with t[0]: add_form()
with t[1]: list_tab(df, "빌려줌")
with t[2]: list_tab(df, "빌림")
with t[3]: people_tab(df)
with t[4]: summary_tab(df)
with t[5]: backup_tab()
