"""Admin page: manage the preference form and build the schedule."""
import hmac
import io
import smtplib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from mlc_scheduler import Config, SchedulingError, csv_io, notify, run
from mlc_scheduler.models import name_key
from mlc_scheduler.storage import FormStore
from mlc_scheduler.validation import validate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
store = FormStore()

st.title("MLC Scheduler")
st.caption(
    "Everyone submits a ranked list of slots; an optimisation model finds the "
    "schedule that gives each TA slots as high in their ranking as possible."
)


def secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:  # no secrets file: running locally
        return None


password = secret("admin_password")
if password and not st.session_state.get("admin_ok"):
    entered = st.text_input("Admin password", type="password")
    if not hmac.compare_digest(entered.encode(), password.encode()):
        if entered:
            st.error("Wrong password.")
        st.stop()
    st.session_state.admin_ok = True


def numbers(text: str, kind=float) -> list:
    return [kind(x) for x in text.replace(",", " ").split()]


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Options")
    mode_label = st.radio(
        "Priority by hours",
        ["Equal", "Tiered", "Weighted"],
        help="**Equal**: everyone's preferences count the same.  \n"
             "**Tiered**: TAs with many hours are fully scheduled first, then the next tier, ...  \n"
             "**Weighted**: one optimisation, but preferences of TAs with many hours count more.",
    )
    mode = {"Equal": "none", "Tiered": "tiered", "Weighted": "weighted"}[mode_label]
    tiers_text = weights_text = ""
    if mode != "none":
        tiers_text = st.text_input("Tier thresholds (hours)", "6, 4",
                                   help="e.g. '6, 4': tier 1 = 6+ hours, tier 2 = 4-5 hours, tier 3 = the rest")
    if mode == "weighted":
        weights_text = st.text_input("Weight of each tier", "3, 2",
                                     help="one number per threshold; everyone else has weight 1")
    max_day = st.number_input("Max hours per day (0 = no limit)", 0, 12, 0)
    max_row = st.number_input("Max hours in a row (0 = no limit)", 0, 12, 0,
                              help="back-to-back slots, e.g. 12:00, 13:00, 14:00 is 3 in a row; "
                                   "any gap (even 30 min) counts as a break")
    min_factor = st.number_input("Minimum list length (x hours)", 0.0, 10.0, 2.0, 0.5,
                                 help="each TA must rank at least this many slots per hour they work")
    exponent = st.slider("Fairness (rank exponent)", 1.0, 3.0, 1.0, 0.5,
                         help="1 = minimise the plain sum of ranks. Higher values penalise bad "
                              "ranks more, spreading satisfaction more evenly across TAs.")
    seed = st.number_input("Random seed", 0, 10**6, 0,
                           help="settles exact ties; same seed + same inputs = same schedule")

# ------------------------------------------------------------------- inputs
st.subheader("1. Inputs")
use_example = st.toggle("Use example data for files not uploaded", value=False)
c1, c2, c3 = st.columns(3)
up_schedule = c1.file_uploader("MLC schedule", type="csv",
                               help="grid: first column = start time, one column per day, cells = TAs needed")
up_prefs = c2.file_uploader("Preferences", type="csv",
                            help="leave empty to use the form submissions")
up_griev = c3.file_uploader("Grievance points (optional)", type="csv", help="name, grievance")
with st.expander("File formats & templates"):
    st.markdown(
        "- **Schedule**: `time,Mon,Tue,...` then rows like `12:30,1,1,2,1,1`. Every slot lasts one hour; blank/0 = no slot.\n"
        "- **Preferences**: `name,hours,pref1,pref2,...` then rows like `Alice,4,Mon 12:00,Wed 14:30,...` (best first).\n"
        "  An optional `unavailable` column lists, separated by `;`, whole days (`Fri`), single slots "
        "(`Mon 12:30`) or busy times (`Tue 13:00-14:30`, which rules out every slot overlapping it). "
        "Those slots are never assigned to that TA.\n"
        "- **Grievance points**: `name,grievance`. Use the file produced by the previous run.\n"
        "- **Roster** (section 2): `name,hours,email`, one row per TA working this term."
    )
    files = ("schedule.csv", "preferences.csv", "grievances.csv", "roster.csv")
    for col, f in zip(st.columns(len(files)), files):
        col.download_button(f"Example {f}", (EXAMPLES / f).read_bytes(), f, "text/csv")


def pick(upload, fallbacks):
    """First available source: the upload, else the first fallback that exists."""
    if upload is not None:
        upload.seek(0)
        return upload, "the uploaded file"
    return next(((src, why) for src, why, ok in fallbacks if ok), (None, None))


submissions = store.submissions()
roster = store.roster()
src_schedule, why_schedule = pick(up_schedule, [
    (store.schedule_path(), "the schedule the form is using", store.schedule_path() is not None),
    (EXAMPLES / "schedule.csv", "example data", use_example),
])
src_griev, why_griev = pick(up_griev, [(EXAMPLES / "grievances.csv", "example data", use_example)])
for col, why in zip((c1, c3), (why_schedule, why_griev)):
    if why:
        col.caption(f"Using {why}.")

if src_schedule is None:
    st.info("Upload the MLC schedule (or switch on the example data) to begin.")
    st.stop()
try:
    schedule = csv_io.read_schedule(src_schedule)
except ValueError as e:
    st.error(f"Could not read the schedule: {e}")
    st.stop()

# ----------------------------------------------------------- roster & form
st.subheader("2. Roster and preference form")
st.markdown("**Roster**")
up_roster = st.file_uploader(
    "Roster (name, hours, email)", type="csv",
    help="Who works at the MLC this term. With a roster, TAs pick their name on the form "
         "and are told their hours, and their schedules can be emailed to them.")
if up_roster is not None:
    up_roster.seek(0)
    try:
        new_roster, roster_errors = csv_io.read_roster(up_roster)
    except ValueError as e:
        new_roster, roster_errors = [], [str(e)]
    if roster_errors:
        st.error("Problems in the roster:\n\n" + "\n".join(f"- {e}" for e in roster_errors))
    elif new_roster != roster and st.button(f"Use this roster ({len(new_roster)} TAs)"):
        store.set_roster(new_roster)
        st.rerun()

done = {name_key(s["name"]) for s in submissions}
missing = [r["name"] for r in roster if name_key(r["name"]) not in done]
if roster:
    st.write(
        f"**{len(roster)}** TAs on the roster, working **{sum(r['hours'] for r in roster)}** hours "
        f"per week ({schedule.capacity} TA-hours needed); "
        f"{sum(bool(r['email']) for r in roster)} have an email address. "
        f"**{len(roster) - len(missing)}** have submitted their preferences."
    )
    with st.expander("Roster"):
        st.dataframe(pd.DataFrame([{**r, "submitted": name_key(r["name"]) in done} for r in roster]),
                     hide_index=True, width="stretch")
        if st.button("Remove roster"):
            store.clear_roster()
            st.rerun()
    on_roster = {name_key(r["name"]) for r in roster}
    strangers = [s["name"] for s in submissions if name_key(s["name"]) not in on_roster]
    if strangers:
        st.warning(f"Submitted but not on the roster: {', '.join(strangers)}. "
                   "They are scheduled with the hours they entered (delete them below if needed).")

st.markdown("**Preference form**")
form_open = store.schedule_path() is not None
schedule_csv = csv_io.schedule_to_csv(schedule)
if form_open:
    st.success(f"The form is **open**: TAs submit on the *Submit preferences* page. "
               f"{len(submissions)} submissions so far.")
    if csv_io.read_schedule(store.schedule_path()).needed != schedule.needed:
        st.warning("The form is offering a different schedule from the one loaded above.")
    if store.settings()["min_list_factor"] != min_factor:
        st.warning("The form asks for a different minimum list length from the one in the sidebar.")
else:
    st.info("The form is **closed**. Opening it lets TAs rank the slots of the schedule loaded above.")
f1, f2, _ = st.columns([1, 1, 2])
if f1.button("Update the form" if form_open else "Open the form",
             help="the form uses the schedule loaded above and the sidebar's minimum list length"):
    store.open_form(schedule_csv, min_factor)
    st.rerun()
if form_open and f2.button("Close the form"):
    store.close_form()
    st.rerun()

if submissions:
    with st.expander(f"Submissions ({len(submissions)})"):
        st.dataframe(pd.DataFrame([{
            "name": s["name"], "hours": s["hours"], "slots ranked": len(s["preferences"]),
            "unavailable": s["unavailable"], "submitted": s["submitted"].replace("T", " "),
        } for s in submissions]), hide_index=True, width="stretch")
        g1, g2, g3 = st.columns([2, 1, 2])
        who = g1.selectbox("Submission", [s["name"] for s in submissions], label_visibility="collapsed")
        if g2.button("Delete"):
            store.delete(who)
            st.rerun()
        g3.download_button("Download as preferences CSV", store.to_csv(), "preferences.csv", "text/csv")

include_missing = False
if roster and missing:
    include_missing = st.checkbox(
        f"Also schedule the {len(missing)} roster TAs who haven't submitted", value=True,
        help="They get whatever slots are left once everyone else is placed "
             f"(still missing: {', '.join(missing)})")

src_prefs, why_prefs = pick(up_prefs, [
    (io.StringIO(store.to_csv(include_missing)),
     f"the {len(submissions)} form submissions" + (f" + {len(missing)} roster TAs without one" if include_missing else ""),
     bool(submissions) or include_missing),
    (EXAMPLES / "preferences.csv", "example data", use_example),
])
if why_prefs:
    c2.caption(f"Using {why_prefs}.")

if src_prefs is None:
    st.info("Waiting for preferences: collect them with the form, or upload a preferences file.")
    st.stop()

try:
    config = Config(mode=mode, tiers=numbers(tiers_text, int), weights=numbers(weights_text),
                    rank_exponent=exponent, min_list_factor=min_factor, seed=int(seed),
                    max_hours_per_day=max_day or None, max_in_a_row=max_row or None)
    participants, errors = csv_io.read_participants(src_prefs, schedule)
    grievances = csv_io.read_grievances(src_griev) if src_griev is not None else {}
except ValueError as e:
    st.error(f"Could not read the inputs: {e}")
    st.stop()

more_errors, warnings = validate(schedule, participants, config)
errors += more_errors

# ---------------------------------------------------------------------- run
st.subheader("3. Build the schedule")
st.write(f"**{len(participants)}** participants asking for **{sum(p.hours for p in participants)}** "
         f"hours; the schedule has **{len(schedule.needed)}** slots needing **{schedule.capacity}** TA-hours.")
for w in warnings:
    st.warning(w)
if errors:
    st.error("Please fix these problems:\n\n" + "\n".join(f"- {e}" for e in errors))
    st.stop()

if st.button("Build schedule", type="primary"):
    with st.spinner("Optimising..."):
        try:
            st.session_state.result = (run(schedule, participants, config, grievances), config)
        except SchedulingError as e:
            st.session_state.pop("result", None)
            st.error(str(e))

if "result" not in st.session_state:
    st.stop()
result, used_config = st.session_state.result
stale = set(result.assignment) != {p.name for p in participants} or used_config != config
if stale:
    st.warning("Inputs or options changed since this schedule was built. Press the button again.")

# ------------------------------------------------------------------ results
st.subheader("4. Results")
summary = csv_io.summary_table(participants, result, used_config)
total = int(summary["hours"].sum())
ranked = total - int(summary["unranked"].sum())
m1, m2, m3, m4 = st.columns(4)
m1.metric("Hours from TAs' lists", f"{ranked}/{total}")
m2.metric("Hours in TAs' top choices", f"{int(summary['in top choices'].sum())}/{total}",
          help="a TA with N hours counts slots they ranked 1..N")
m3.metric("TAs with only ranked slots", f"{int((summary['unranked'] == 0).sum())}/{len(summary)}")
m4.metric("Lost ties", len(result.ties))

grid = csv_io.schedule_grid(schedule, result.assignment)
assignments = csv_io.assignments_table(schedule, participants, result)
griev_out = csv_io.grievance_table(result)

t1, t2, t3, t4 = st.tabs(["Schedule", "Per TA", "Ties", "Grievance points"])
with t1:
    st.dataframe(grid, hide_index=True, width="stretch", height=36 * (len(grid) + 1) + 2)
with t2:
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption("'ranks received': position of each assigned slot in the TA's list; '-' = a slot they did not rank.")
with t3:
    if result.ties:
        st.dataframe(csv_io.ties_table(result), hide_index=True, width="stretch")
    st.caption("A tie: TAs in the same tier ranked a slot at the same position and only some got it. "
               "Each TA who missed out gains 1 grievance point; a winner who had more points than "
               "a loser spends 1 point.")
with t4:
    st.dataframe(griev_out, hide_index=True)

st.write("**Download**")
b1, b2, b3 = st.columns(3)
b1.download_button("Schedule grid (CSV)", grid.to_csv(index=False), "schedule.csv", "text/csv")
b2.download_button("Assignments per TA (CSV)", assignments.to_csv(index=False), "assignments.csv", "text/csv")
b3.download_button("Updated grievance points (CSV)", griev_out.to_csv(index=False), "grievances.csv", "text/csv")
with st.expander("Solver details"):
    st.dataframe(pd.DataFrame(result.stages, columns=["stage", "optimal value"]), hide_index=True)

# ------------------------------------------------------------------- send
st.subheader("5. Send each TA their schedule")
emails = {r["name"]: r["email"] for r in roster}
if not any(emails.values()):
    st.info("Load a roster with an 'email' column (section 2) to email TAs their schedules.")
    st.stop()

subject = st.text_input("Subject", notify.DEFAULT_SUBJECT)
body = st.text_area("Message", notify.DEFAULT_BODY, height=230,
                    help="{name}, {hours} and {schedule} are filled in for each TA")
term = None
if st.checkbox("Attach a calendar file", value=True,
               help="an .ics file with a weekly repeating event per shift, which TAs can "
                    "open to add their shifts to Google Calendar, Outlook, ..."):
    e1, e2 = st.columns(2)
    first = e1.date_input("First day of shifts", date.today())
    last = e2.date_input("Last day of shifts", date.today() + timedelta(weeks=13))
    if last < first:
        st.error("The last day is before the first day.")
        st.stop()
    term = (first, last)
try:
    messages = notify.compose(schedule, result.assignment, emails, subject, body, term)
except ValueError:
    st.error("Calendar files need weekday names (Mon, Tue, ...) as the schedule's days.")
    st.stop()

no_email = sorted(set(result.assignment) - {m.name for m in messages})
if no_email:
    st.warning(f"No email address on the roster for: {', '.join(no_email)}")
if not messages:
    st.stop()

with st.expander("Preview"):
    shown = st.selectbox("TA", [m.name for m in messages])
    m = next(m for m in messages if m.name == shown)
    st.text(f"To: {m.to}\nSubject: {m.subject}" + ("\nAttachment: mlc_schedule.ics" if m.calendar else "")
            + f"\n\n{m.body}")

mail_merge = pd.DataFrame([{"name": m.name, "email": m.to, "subject": m.subject, "body": m.body}
                           for m in messages]).to_csv(index=False)
smtp = secret("smtp")
if not smtp:
    st.info("To send the emails from here, add an email account to the app's secrets "
            "(see the README). Meanwhile you can download them for a mail merge.")
    st.download_button("Download emails (CSV)", mail_merge, "emails.csv", "text/csv")
    st.stop()

already = st.session_state.get("sent_for") is result
sender = smtp.get("sender") or smtp.get("user")
confirm = st.checkbox(f"The schedule is final: send {len(messages)} emails from {sender}",
                      disabled=stale or already)
if already:
    st.caption("These schedules were already sent. Build the schedule again to send new ones.")
if st.button("Send emails", type="primary", disabled=not confirm or stale or already):
    bar = st.progress(0.0, "Sending...")
    try:
        sent = notify.send(messages, dict(smtp),
                           on_sent=lambda i, _: bar.progress((i + 1) / len(messages), "Sending..."))
    except (smtplib.SMTPException, OSError) as e:
        st.error(f"Could not connect to the email server: {e}")
    else:
        st.session_state.sent_for, st.session_state.sent = result, sent
        st.rerun()
if already:
    report = st.session_state.sent
    failed = {n: r for n, r in report.items() if r != "sent"}
    (st.warning if failed else st.success)(f"Sent {len(report) - len(failed)} of {len(report)} emails.")
    if failed:
        st.dataframe(pd.DataFrame(failed.items(), columns=["name", "error"]), hide_index=True)
st.download_button("Download emails (CSV)", mail_merge, "emails.csv", "text/csv")
