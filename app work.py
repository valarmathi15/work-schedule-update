"""Work Tracker (multi-employee): Streamlit + SQLite.

Run:  pip install -r requirements.txt
      streamlit run app.py

Every add, edit, status change, note, completion and removal is saved to
work_tracker.db (next to this file) and written to the Activity log tab.
Set the WT_DB environment variable to keep the database somewhere else.
"""
import io
import os
import sqlite3
from datetime import date, datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Work Tracker", page_icon="📋", layout="wide")

DB_PATH = os.environ.get(
    "WT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_tracker.db")
)

CFG = {
    "work": dict(nav="Work plan", tag="Project or skill name", ph="Task, e.g. Compare SMA and Cloud strategies",
                 statuses=["incomplete", "future"]),
    "follow": dict(nav="Follow-ups", tag="Client (optional)", ph="Follow-up, e.g. Send insurance document",
                   statuses=["new", "talk"]),
    "exec": dict(nav="Execution", tag="Client", ph="Plan, e.g. Buy silver 5k weekly",
                 statuses=["open", "pending", "placed"]),
    "mf": dict(nav="Mutual funds", tag="Client", ph="Status or action, e.g. SIP missed, informed",
               statuses=["new", "talk"]),
}
LAB = {"open": "Planned", "pending": "Pending", "placed": "Order placed", "future": "Future",
       "talk": "In talks", "new": "New", "incomplete": "Incomplete"}

# SIP failure reasons for the Mutual funds tab: category -> [(reason, explanation)]
MF_OTHERS = "Others"
MF_REASONS = {
    "Bank Account & Balance Related Issues": [
        ("Insufficient Balance", "SIP debit date-la bank account-la thevaiyaana panam illai."),
        ("Minimum Balance Hold / Lien", "Panam irundhum loan EMI, pending bank charges, lien hold-naala available balance kuraivu."),
        ("Account Dormant / Freeze", "Account inactive (dormant) illa KYC update illaama freeze aagi debit nadakala."),
    ],
    "Bank Mandate (NACH / e-Mandate / Autopay) Issues": [
        ("Mandate Limit Exceeded", "NACH / UPI Autopay max limit-a vida SIP amount adhigam."),
        ("Mandate Expired or Not Activated", "Mandate validity mudinjiduchu illa bank side-la innum approve aagala."),
        ("UPI Autopay Approval Pending", "Rs.15,000-ku mela pre-debit notification-ku manual approval thevai; approve pannala."),
    ],
    "Technical & Operational Reasons": [
        ("Bank Server / NACH Clearing Delay", "Bank server down illa NPCI/NACH clearing-la technical glitch."),
        ("Bank Account Closed / Changed", "Old account close panni AMC-la pudhu bank mandate update pannala."),
        ("Holiday / Weekend Cut-off", "SIP date holiday/weekend; next working day balance illai."),
    ],
    "Compliance & Folio Issues": [
        ("KYC Non-Compliance", "PAN-Aadhaar link illai illa KRA KYC On-Hold / Rejected."),
        ("SIP Tenure Ended", "SIP start-la kuduthu end date mudinjiduchu, auto-stop aagiduchu."),
    ],
    MF_OTHERS: [],
}


# ---------- database ----------
@st.cache_resource
def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT, tab TEXT, grp TEXT, title TEXT, kind TEXT,
            status TEXT, added TEXT, due TEXT, done_at TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, ts TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS daily(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT);
        CREATE TABLE IF NOT EXISTS daily_log(day TEXT, daily_id INTEGER, PRIMARY KEY(day, daily_id));
        CREATE TABLE IF NOT EXISTS log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, item_id INTEGER, tab TEXT,
            title TEXT, action TEXT, detail TEXT);
        """
    )
    c.executescript("CREATE TABLE IF NOT EXISTS employees(name TEXT PRIMARY KEY);")
    for t in ("items", "daily", "log"):
        if "owner" not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
            c.execute(f"ALTER TABLE {t} ADD COLUMN owner TEXT")
    if c.execute("SELECT 1 FROM items WHERE owner IS NULL UNION SELECT 1 FROM daily WHERE owner IS NULL").fetchone():
        for t in ("items", "daily", "log"):
            c.execute(f"UPDATE {t} SET owner='Me' WHERE owner IS NULL")
        c.execute("INSERT OR IGNORE INTO employees(name) VALUES('Me')")
    c.commit()
    return c


def q(sql, args=()):
    return conn().execute(sql, args).fetchall()


def x(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


def now():
    return datetime.now().isoformat(timespec="seconds")


TEAM = "👥 Team overview"


def emp():
    v = st.session_state.get("view")
    return None if v == TEAM else v


def log(tab, title, action, detail="", item_id=None, owner=None):
    x("INSERT INTO log(ts,item_id,tab,title,action,detail,owner) VALUES(?,?,?,?,?,?,?)",
      (now(), item_id, tab, title, action, detail, owner or emp()))


def get(i):
    return q("SELECT * FROM items WHERE id=?", (i,))[0]


def fmt_dt(s):
    return datetime.fromisoformat(s).strftime("%d %b, %I:%M %p")


def fmt_d(s):
    return date.fromisoformat(s).strftime("%d %b %Y")


def days_text(n):
    return "same day" if n <= 0 else f"{n} day" + ("s" if n > 1 else "")


def when_text(it):
    if it["done_at"]:
        s = "Completed " + fmt_dt(it["done_at"])
        if it["added"]:
            n = (datetime.fromisoformat(it["done_at"]).date() - date.fromisoformat(it["added"])).days
            s += f" · took {days_text(n)}"
        return s
    if it["added"]:
        n = (date.today() - date.fromisoformat(it["added"])).days
        return "Added today" if n <= 0 else f"Open for {days_text(n)}"
    return ""


# ---------- callbacks (each one saves + logs) ----------
def add_item(tab, grp, title, kind, status, added, due):
    cur = x("INSERT INTO items(tab,grp,title,kind,status,added,due,created_at,owner) VALUES(?,?,?,?,?,?,?,?,?)",
            (tab, grp, title, kind, status, added.isoformat(), due.isoformat() if due else None, now(), emp()))
    log(tab, title, "Added", f"Added date {added.isoformat()}" + (f", due {due.isoformat()}" if due else ""), cur.lastrowid)


def on_done(i, key):
    it = get(i)
    if st.session_state[key]:
        x("UPDATE items SET done_at=? WHERE id=?", (now(), i))
        log(it["tab"], it["title"], "Completed", "", i)
    else:
        x("UPDATE items SET done_at=NULL WHERE id=?", (i,))
        log(it["tab"], it["title"], "Reopened", "", i)


def on_status(i, key):
    it, new = get(i), st.session_state[key]
    x("UPDATE items SET status=? WHERE id=?", (new, i))
    log(it["tab"], it["title"], "Status changed", f"{LAB[it['status']]} → {LAB[new]}", i)


def on_date(i, field, key):
    it, v = get(i), st.session_state[key]
    x(f"UPDATE items SET {field}=? WHERE id=?", (v.isoformat() if v else None, i))
    log(it["tab"], it["title"], f"{field.capitalize()} date changed",
        f"{it[field] or 'none'} → {v.isoformat() if v else 'none'}", i)


def on_note(i, key):
    text = st.session_state[key].strip()
    if text:
        it = get(i)
        x("INSERT INTO notes(item_id,ts,text) VALUES(?,?,?)", (i, now(), text))
        log(it["tab"], it["title"], "Note added", text, i)
        st.session_state[key] = ""


def on_owner(i, key):
    it, new = get(i), st.session_state[key]
    x("UPDATE items SET owner=? WHERE id=?", (new, i))
    log(it["tab"], it["title"], "Reassigned", f"{it['owner']} → {new}", i, owner=it["owner"])


def on_remove(i):
    it = get(i)
    x("DELETE FROM notes WHERE item_id=?", (i,))
    x("DELETE FROM items WHERE id=?", (i,))
    log(it["tab"], it["title"], "Removed", "", i)


def on_daily_add(key):
    t = st.session_state[key].strip()
    if t:
        x("INSERT INTO daily(title,owner) VALUES(?,?)", (t, emp()))
        log("daily", t, "Daily item added")
        st.session_state[key] = ""


def on_daily_tick(i, key, title):
    day = date.today().isoformat()
    if st.session_state[key]:
        x("INSERT OR IGNORE INTO daily_log(day,daily_id) VALUES(?,?)", (day, i))
        log("daily", title, "Daily ticked")
    else:
        x("DELETE FROM daily_log WHERE day=? AND daily_id=?", (day, i))
        log("daily", title, "Daily unticked")


def on_daily_remove(i, title):
    x("DELETE FROM daily WHERE id=?", (i,))
    x("DELETE FROM daily_log WHERE daily_id=?", (i,))
    log("daily", title, "Daily item removed")


# ---------- UI pieces ----------
def render_item(it, tab):
    i = it["id"]
    done = bool(it["done_at"])
    late = (not done) and it["due"] and date.fromisoformat(it["due"]) < date.today()
    c1, c2, c3 = st.columns([0.06, 0.70, 0.24], vertical_alignment="center")
    c1.checkbox("Done", value=done, key=f"done_{i}", label_visibility="collapsed",
                on_change=on_done, args=(i, f"done_{i}"))
    label = (f"**{it['grp']}** · " if it["grp"] else "") + it["title"]
    if it["kind"] and it["kind"] != "Project":
        label += f" _({it['kind']})_"
    c2.markdown(f"~~{label}~~" if done else label)
    meta = when_text(it)
    if it["due"] and not done:
        meta += (" · " if meta else "") + ("🔴 Overdue, due " if late else "Due ") + fmt_d(it["due"])
    c2.caption(meta)
    notes = q("SELECT * FROM notes WHERE item_id=? ORDER BY id", (i,))
    recent = False
    if notes:
        last = notes[-1]
        recent = (datetime.now() - datetime.fromisoformat(last["ts"])).total_seconds() < 86400
        c2.markdown(("🆕 " if recent else "📝 ") + f"_{fmt_dt(last['ts'])}_ — {last['text']}")
    if done:
        c3.success("Done")
    else:
        sts = CFG[tab]["statuses"]
        c3.selectbox("Status", sts, index=sts.index(it["status"]) if it["status"] in sts else 0,
                     format_func=LAB.get, key=f"st_{i}", label_visibility="collapsed",
                     on_change=on_status, args=(i, f"st_{i}"))
    label = "➕ Dates, notes and remove"
    if notes:
        label += f"  ·  {len(notes)} note" + ("s" if len(notes) > 1 else "") + ("  ·  🆕 new" if recent else "")
    with st.expander(label):
        d1, d2 = st.columns(2)
        d1.date_input("Added date", value=date.fromisoformat(it["added"]) if it["added"] else None,
                      key=f"ad_{i}", on_change=on_date, args=(i, "added", f"ad_{i}"))
        d2.date_input("Due date", value=date.fromisoformat(it["due"]) if it["due"] else None,
                      key=f"du_{i}", on_change=on_date, args=(i, "due", f"du_{i}"))
        names = [r["name"] for r in q("SELECT name FROM employees ORDER BY name")]
        if len(names) > 1:
            st.selectbox("Assigned to", names, index=names.index(it["owner"]) if it["owner"] in names else 0,
                         key=f"ow_{i}", on_change=on_owner, args=(i, f"ow_{i}"))
        for n in notes:
            st.markdown(f"- _{fmt_dt(n['ts'])}_ — {n['text']}")
        st.text_input("Add note: what did you do?", key=f"nt_{i}", on_change=on_note, args=(i, f"nt_{i}"))
        st.button("Remove item", key=f"rm_{i}", on_click=on_remove, args=(i,))


def _mf_reset():
    for k, v in {"mf_client": "", "mf_fund": "", "mf_cat": "(select)", "mf_reason": "(select)",
                 "mf_other": "", "mf_amount": 0.0, "mf_status": "new", "mf_added": date.today(), "mf_due": None}.items():
        st.session_state[k] = v


def on_mf_add():
    ss = st.session_state
    fund = ss.get("mf_fund", "").strip()
    cat, reason = ss.get("mf_cat"), ss.get("mf_reason")
    amount = ss.get("mf_amount") or 0
    if not fund:
        ss["mf_err"] = "Fund name type pannunga."
        return
    if amount <= 0:
        ss["mf_err"] = "Amount type pannunga."
        return
    amt = f"₹{amount:,.0f}" if float(amount).is_integer() else f"₹{amount:,.2f}"
    if cat in (None, "(select)"):
        ss["mf_err"] = "Reason category select pannunga."
        return
    if cat == MF_OTHERS:
        detail = ss.get("mf_other", "").strip()
        if not detail:
            ss["mf_err"] = "Others-na issue enna nu type pannunga."
            return
        title = f"{fund} · {amt} · Others: {detail}"
    else:
        if reason in (None, "(select)"):
            ss["mf_err"] = "Specific reason select pannunga."
            return
        title = f"{fund} · {amt} · {cat} → {reason}"
    add_item("mf", ss.get("mf_client", "").strip(), title, None, ss["mf_status"], ss["mf_added"], ss.get("mf_due"))
    ss.pop("mf_err", None)
    _mf_reset()
    ss["mf_show"] = False


def mf_add_form():
    """Add box for Mutual funds. Not an st.form, so the reason list can change with the category."""
    ss = st.session_state
    ss.setdefault("mf_show", False)

    def toggle():
        ss["mf_show"] = not ss["mf_show"]
        ss.pop("mf_err", None)

    st.button("✖ Close" if ss["mf_show"] else "➕ Add item", key="mf_toggle", on_click=toggle)
    if not ss["mf_show"]:
        return
    if "mf_cat" not in ss:
        _mf_reset()
    with st.container(border=True):
        a, b = st.columns(2)
        a.text_input("Client", key="mf_client")
        b.text_input("Fund name", key="mf_fund", placeholder="e.g. HDFC Flexi Cap Fund")
        c1, c2 = st.columns(2)
        c1.selectbox("Reason category", ["(select)"] + list(MF_REASONS), key="mf_cat")
        cat = ss["mf_cat"]
        if cat == MF_OTHERS:
            c2.text_input("Issue (type here)", key="mf_other", placeholder="Type the issue")
        elif cat != "(select)":
            reasons = MF_REASONS[cat]
            names = [r for r, _ in reasons]
            if ss.get("mf_reason") not in names:
                ss["mf_reason"] = "(select)"
            c2.selectbox("Specific reason", ["(select)"] + names, key="mf_reason")
            exp = dict(reasons).get(ss["mf_reason"])
            if exp:
                c2.caption(exp)
        else:
            c2.selectbox("Specific reason", ["(select category first)"], key="mf_reason_dummy", disabled=True)
        d = st.columns(4)
        d[0].selectbox("Status", CFG["mf"]["statuses"], format_func=LAB.get, key="mf_status")
        d[1].number_input("Amount (₹)", min_value=0.0, step=500.0, format="%g", key="mf_amount")
        d[2].date_input("Added date", key="mf_added")
        d[3].date_input("Due date (optional)", key="mf_due")
        st.button("Add", type="primary", key="mf_add_btn", on_click=on_mf_add)
        if ss.get("mf_err"):
            st.error(ss["mf_err"])


def tab_view(tab, search):
    cfg = CFG[tab]
    if tab == "mf":
        mf_add_form()
    else:
      with st.form(f"add_{tab}", clear_on_submit=True):
        a, b = st.columns([1, 3])
        grp = a.text_input(cfg["tag"])
        title = b.text_input("Task / plan", placeholder=cfg["ph"])
        c = st.columns(5)
        status = c[0].selectbox("Status", cfg["statuses"], format_func=LAB.get)
        kind = c[1].selectbox("Type", ["Project", "Skill", "Work"]) if tab == "work" else None
        added = c[2].date_input("Added date", value=date.today())
        due = c[3].date_input("Due date (optional)", value=None)
        if st.form_submit_button("Add", type="primary") and title.strip():
            add_item(tab, grp.strip(), title.strip(), kind, status, added, due)

    rows = q("SELECT * FROM items WHERE tab=? AND owner=? AND done_at IS NULL ORDER BY (due IS NULL), due, id DESC", (tab, emp()))
    hist = q("SELECT * FROM items WHERE tab=? AND owner=? AND done_at IS NOT NULL ORDER BY done_at DESC", (tab, emp()))
    if search:
        s = search.lower()
        rows = [r for r in rows if s in f"{r['grp']} {r['title']}".lower()]
        hist = [r for r in hist if s in f"{r['grp']} {r['title']}".lower()]

    st.subheader(cfg["nav"])
    present = sorted({r["status"] for r in rows}, key=lambda z: cfg["statuses"].index(z) if z in cfg["statuses"] else 99)
    if len(present) > 1:
        pick = st.radio("Filter", ["all"] + present, format_func=lambda z: "All" if z == "all" else LAB[z],
                        horizontal=True, key=f"flt_{tab}", label_visibility="collapsed")
        rows = [r for r in rows if pick == "all" or r["status"] == pick]
    if rows:
        for r in rows:
            render_item(r, tab)
            st.divider()
    else:
        st.info("Nothing open here yet. Add your first item above.")

    st.subheader("Completed history")
    if hist:
        for r in hist:
            render_item(r, tab)
            st.divider()
    else:
        st.caption("Nothing completed yet. Tick an item and it moves here with the added date, completed date and days taken.")


def overview():
    today = date.today()
    open_n = lambda t, extra="": q(f"SELECT COUNT(*) n FROM items WHERE tab=? AND owner=? AND done_at IS NULL {extra}", (t, emp()))[0]["n"]
    fin = q("SELECT added, done_at FROM items WHERE owner=? AND done_at IS NOT NULL AND added IS NOT NULL", (emp(),))
    avg = (sum((datetime.fromisoformat(r["done_at"]).date() - date.fromisoformat(r["added"])).days for r in fin) / len(fin)) if fin else None
    m = st.columns(6)
    m[0].metric("Open work items", open_n("work", "AND status!='future'"))
    m[1].metric("Client follow-ups open", open_n("follow"))
    m[2].metric("Execution plans active", open_n("exec"))
    m[3].metric("Waiting on action", open_n("exec", "AND status='pending'"))
    m[4].metric("Mutual fund items open", open_n("mf"))
    m[5].metric("Avg days to finish", f"{avg:.1f}" if avg is not None else "–")

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Due today and overdue")
        due = q("SELECT * FROM items WHERE owner=? AND done_at IS NULL AND due IS NOT NULL AND due<=? ORDER BY due", (emp(), today.isoformat()))
        if due:
            for r in due:
                late = date.fromisoformat(r["due"]) < today
                st.markdown(f"{'🔴 **Overdue**' if late else '🟢 **Today**'} · {r['title']}  \n"
                            f"<span style='color:gray;font-size:13px'>{CFG[r['tab']]['nav']}"
                            f"{' · ' + r['grp'] if r['grp'] else ''}"
                            f"{' · was due ' + fmt_d(r['due']) if late else ''}</span>", unsafe_allow_html=True)
        else:
            st.caption("Nothing due. Set a due date on a task and it shows up here.")
    with right:
        st.subheader("Daily checklist")
        items = q("SELECT * FROM daily WHERE owner=? ORDER BY id", (emp(),))
        ticked = {r["daily_id"] for r in q("SELECT daily_id FROM daily_log WHERE day=?", (today.isoformat(),))}
        if items:
            st.progress(len([d for d in items if d["id"] in ticked]) / len(items),
                        text=f"{len([d for d in items if d['id'] in ticked])} of {len(items)} done today")
        for d in items:
            a, b = st.columns([0.85, 0.15], vertical_alignment="center")
            a.checkbox(d["title"], value=d["id"] in ticked, key=f"dl_{d['id']}",
                       on_change=on_daily_tick, args=(d["id"], f"dl_{d['id']}", d["title"]))
            b.button("✕", key=f"dr_{d['id']}", on_click=on_daily_remove, args=(d["id"], d["title"]), help="Remove")
        st.text_input("Add a daily routine item", key="daily_new", on_change=on_daily_add, args=("daily_new",))

    st.subheader("Project progress")
    prog = q("""SELECT grp, COUNT(*) total, SUM(done_at IS NOT NULL) done FROM items
                WHERE tab='work' AND kind='Project' AND owner=? GROUP BY grp""", (emp(),))
    if prog:
        for p in prog:
            st.progress(p["done"] / p["total"], text=f"{p['grp'] or 'No name'}: {p['done']}/{p['total']}")
    else:
        st.caption("Start in Work plan: add a task under a Project name and its progress shows here.")


def activity_log():
    st.subheader("Activity log")
    st.caption("Every add, edit, status change, note, completion and removal, newest first.")
    rows = q("SELECT ts, tab, title, action, detail FROM log WHERE owner=? ORDER BY id DESC LIMIT 1000", (emp(),))
    if not rows:
        st.info("No activity yet.")
        return
    df = pd.DataFrame([dict(r) for r in rows])
    df["ts"] = df["ts"].map(fmt_dt)
    df["tab"] = df["tab"].map(lambda t: CFG.get(t, {}).get("nav", "Daily checklist"))
    df.columns = ["Time", "Section", "Item", "Action", "Detail"]
    st.dataframe(df, hide_index=True)


def export_excel():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        cols = ["owner", "grp", "title", "kind", "status", "added", "due", "done_at"]
        for t, cfg in CFG.items():
            df = pd.DataFrame([dict(r) for r in q(f"SELECT {','.join(cols)} FROM items WHERE tab=? ORDER BY owner", (t,))], columns=cols)
            df.columns = ["Employee", "Project/Client", "Task/Plan", "Type", "Status", "Added", "Due", "Completed at"]
            df.to_excel(w, sheet_name=cfg["nav"], index=False)
        lc = ["ts", "owner", "tab", "title", "action", "detail"]
        pd.DataFrame([dict(r) for r in q(f"SELECT {','.join(lc)} FROM log ORDER BY id DESC")], columns=lc).to_excel(
            w, sheet_name="Activity log", index=False)
    return buf.getvalue()


def team_overview(employees):
    today = date.today()
    st.subheader("Workload by employee")
    data = []
    for e in employees:
        n = lambda sql, *a: q(sql, (e, *a))[0]["n"]
        fin = q("SELECT added, done_at FROM items WHERE owner=? AND done_at IS NOT NULL AND added IS NOT NULL", (e,))
        avg = sum((datetime.fromisoformat(r["done_at"]).date() - date.fromisoformat(r["added"])).days for r in fin) / len(fin) if fin else None
        data.append({
            "Employee": e,
            "Work plan": n("SELECT COUNT(*) n FROM items WHERE owner=? AND tab=? AND done_at IS NULL", "work"),
            "Follow-ups": n("SELECT COUNT(*) n FROM items WHERE owner=? AND tab=? AND done_at IS NULL", "follow"),
            "Execution": n("SELECT COUNT(*) n FROM items WHERE owner=? AND tab=? AND done_at IS NULL", "exec"),
            "Mutual funds": n("SELECT COUNT(*) n FROM items WHERE owner=? AND tab=? AND done_at IS NULL", "mf"),
            "Overdue": n("SELECT COUNT(*) n FROM items WHERE owner=? AND done_at IS NULL AND due<?", today.isoformat()),
            "Completed": n("SELECT COUNT(*) n FROM items WHERE owner=? AND done_at IS NOT NULL"),
            "Avg days to finish": round(avg, 1) if avg is not None else None,
        })
    st.dataframe(pd.DataFrame(data), hide_index=True)

    pick = st.multiselect("Show employees", employees, default=employees)
    st.subheader("Open items")
    rows = q("SELECT * FROM items WHERE done_at IS NULL ORDER BY owner, (due IS NULL), due")
    open_df = [{"Employee": r["owner"], "Section": CFG[r["tab"]]["nav"], "Project / client": r["grp"], "Task": r["title"],
                "Status": LAB.get(r["status"]), "Added": r["added"], "Due": r["due"],
                "Open days": (today - date.fromisoformat(r["added"])).days if r["added"] else None}
               for r in rows if r["owner"] in pick]
    if open_df:
        st.dataframe(pd.DataFrame(open_df), hide_index=True)
    else:
        st.caption("No open items.")

    st.subheader("Recently completed")
    rows = q("SELECT * FROM items WHERE done_at IS NOT NULL ORDER BY done_at DESC LIMIT 100")
    done_df = [{"Employee": r["owner"], "Section": CFG[r["tab"]]["nav"], "Project / client": r["grp"], "Task": r["title"],
                "Added": r["added"], "Completed": fmt_dt(r["done_at"]),
                "Took (days)": (datetime.fromisoformat(r["done_at"]).date() - date.fromisoformat(r["added"])).days if r["added"] else None}
               for r in rows if r["owner"] in pick]
    if done_df:
        st.dataframe(pd.DataFrame(done_df), hide_index=True)
    else:
        st.caption("Nothing completed yet.")


# ---------- page ----------
def add_emp():
    name = " ".join(st.session_state["new_emp"].split())
    if name:
        same = [r["name"] for r in q("SELECT name FROM employees WHERE lower(name)=lower(?)", (name,))]
        if not same:
            x("INSERT INTO employees(name) VALUES(?)", (name,))
        st.session_state["view"] = same[0] if same else name
        st.session_state["new_emp"] = ""


employees = [r["name"] for r in q("SELECT name FROM employees ORDER BY name")]
with st.sidebar:
    st.header("Team")
    st.text_input("➕ Add employee: type name, press Enter", key="new_emp", on_change=add_emp)
    employees = [r["name"] for r in q("SELECT name FROM employees ORDER BY name")]
    options = [TEAM] + employees
    if st.session_state.get("view") not in options:
        st.session_state["view"] = employees[0] if employees else TEAM
    open_cnt = {r["owner"]: r["n"] for r in q(
        "SELECT owner, COUNT(*) n FROM items WHERE done_at IS NULL GROUP BY owner")}
    st.radio("Click a name to open their work schedule", options, key="view",
             format_func=lambda o: o if o == TEAM else f"{o}  ·  {open_cnt.get(o, 0)} open")
    mode = "Team overview" if st.session_state["view"] == TEAM else "Employee work"
    search = st.text_input("Search tabs") if mode == "Employee work" else ""
    try:
        st.download_button("Download all as Excel", export_excel(), file_name="work_tracker.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except ImportError:
        rows = q("SELECT owner,tab,grp,title,kind,status,added,due,done_at FROM items")
        st.download_button("Download all as CSV", pd.DataFrame([dict(r) for r in rows]).to_csv(index=False),
                           file_name="work_tracker.csv", mime="text/csv")
    st.caption(f"Data file: {DB_PATH}")

if not employees:
    st.title("Work Tracker")
    st.info("Add your first employee in the sidebar to start.")
    st.stop()

if mode == "Team overview":
    st.title("Team overview")
    st.caption(date.today().strftime("%A, %d %B %Y"))
    team_overview(employees)
else:
    st.title(f"Work Tracker · {emp()}")
    st.caption(date.today().strftime("%A, %d %B %Y"))
    tabs = st.tabs(["Overview"] + [c["nav"] for c in CFG.values()] + ["Activity log"])
    with tabs[0]:
        overview()
    for t, tb in zip(CFG, tabs[1:-1]):
        with tb:
            tab_view(t, search)
    with tabs[-1]:
        activity_log()
