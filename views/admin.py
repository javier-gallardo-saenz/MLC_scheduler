"""Admin page: manage the preference form and build the schedule."""
import hmac
import io
import smtplib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from mlc_scheduler import Config, SchedulingError, csv_io, google_forms, notify, run
from mlc_scheduler.roster import apply_roster, emails as roster_emails
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
st.subheader("1. Schedule")
use_example = st.toggle("Use example data for anything not uploaded", value=False)
c1, c3 = st.columns(2)
up_schedule = c1.file_uploader("MLC schedule", type="csv",
                               help="grid: first column = start time, one column per day, cells = TAs needed")
up_griev = c3.file_uploader("Grievance points (optional)", type="csv",
                            help="name, grievance: the file produced by the previous run")
with st.expander("File formats & templates"):
    st.markdown(
        "- **Schedule**: `time,Mon,Tue,...` then rows like `12:30,1,1,2,1,1`. Every slot lasts one hour; blank/0 = no slot.\n"
        "- **Roster**: `name,hours,email`, one row per TA working this term.\n"
        "- **Preferences**: `name,hours,pref1,pref2,...` then rows like `Alice,4,Mon 12:00,Wed 14:30,...` (best first). "
        "With a roster the `hours` column can be left out. "
        "An optional `unavailable` column lists, separated by `;`, whole days (`Fri`), single slots "
        "(`Mon 12:30`) or busy times (`Tue 13:00-14:30`, which rules out every slot overlapping it). "
        "A Google Forms export made with the script from section 3 can be used as it is.\n"
        "- **Grievance points**: `name,grievance`. Use the file produced by the previous run."
    )
    files = ("schedule.csv", "roster.csv", "preferences.csv", "grievances.csv")
    for col, f in zip(st.columns(len(files)), files):
        col.download_button(f"Example {f}", (EXAMPLES / f).read_bytes(), f, "text/csv")


def pick(upload, fallbacks):
    """First available source: the upload, else the first fallback that exists."""
    if upload is not None:
        upload.seek(0)
        return upload, "the uploaded file"
    return next(((src, why) for src, why, ok in fallbacks if ok), (None, None))


src_schedule, why_schedule = pick(up_schedule, [
    (store.schedule_path(), "the schedule the in-app form is using", store.schedule_path() is not None),
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

# ------------------------------------------------------------------ roster
st.subheader("2. Roster")
up_roster = st.file_uploader(
    "Roster: name, hours, email (optional, recommended)", type="csv",
    help="Who works at the MLC this term. With a roster, TAs pick their name from a list on the "
         "form and are told their hours, the roster's hours are the ones used, and schedules "
         "can be emailed to the addresses in its email column.")
if up_roster is not None:  # an uploaded roster is used (and saved for the in-app form) right away
    up_roster.seek(0)
    try:
        new_roster, roster_errors = csv_io.read_roster(up_roster)
    except ValueError as e:
        new_roster, roster_errors = [], [str(e)]
    if roster_errors:
        st.error("Problems in the roster:\n\n" + "\n".join(f"- {e}" for e in roster_errors))
        st.stop()
    if new_roster != store.roster():
        store.set_roster(new_roster)
roster = store.roster()
if roster:
    st.caption("Using the uploaded roster." if up_roster is not None else "Using the saved roster.")
elif use_example:
    roster = csv_io.read_roster(EXAMPLES / "roster.csv")[0]
    st.caption("Using the example roster.")

if roster:
    st.write(f"**{len(roster)}** TAs working **{sum(r['hours'] for r in roster)}** hours per week "
             f"({schedule.capacity} TA-hours needed); "
             f"{sum(bool(r['email']) for r in roster)} have an email address.")
    with st.expander("Roster"):
        st.dataframe(pd.DataFrame(roster), hide_index=True, width="stretch")
        if store.roster() and up_roster is None and st.button("Remove roster"):
            store.clear_roster()
            st.rerun()
else:
    st.caption("No roster: TAs type their own name and hours.")

# ------------------------------------------------------------- preferences
st.subheader("3. Preferences")
SOURCES = {
    "form": "TAs fill in the form in this app",
    "csv": "Upload a preferences CSV (your own file, or a Google Forms export)",
}
saved_source = store.settings()["source"]
source = st.radio("Where do the preferences come from?", list(SOURCES), format_func=SOURCES.get,
                  index=list(SOURCES).index(saved_source),
                  help="Only one source is used, never both, so nobody's answers are silently overridden.")
if source != saved_source:
    store.set_setting("source", source)

submissions = store.submissions()
form_open = store.schedule_path() is not None
src_prefs = None
if source == "form":
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
    if roster and not store.roster():
        st.warning("The example roster is not used by the form; upload a roster for that.")
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
        src_prefs = io.StringIO(store.to_csv())
else:
    if form_open:
        st.error("The in-app form is still open, so TAs could submit answers that would be ignored. "
                 "Close it to use a preferences CSV.")
        if st.button("Close the in-app form"):
            store.close_form()
            st.rerun()
        st.stop()
    if submissions:
        st.caption(f"The {len(submissions)} submissions made through the in-app form are not used.")
    with st.expander("Collect preferences with Google Forms"):
        st.markdown(
            "1. Download the script below. It creates a Google Form for the schedule loaded above"
            + (" in which TAs pick their name from the roster and see their hours" if roster else "")
            + ".\n2. Open [script.google.com](https://script.google.com), click **New project**, replace "
            "the code with the script, save and click **Run**. Allow the permissions it asks for.\n"
            "3. The execution log shows the link to send to TAs.\n"
            "4. When everyone has answered, download the responses as a CSV (in the form's "
            "*Responses* tab) and upload that file below."
        )
        st.download_button("Download the Google Form script",
                           google_forms.apps_script(schedule, roster, min_factor),
                           "create_form.gs", "text/plain")
    up_prefs = st.file_uploader("Preferences CSV or Google Forms responses", type="csv")
    src_prefs, why_prefs = pick(up_prefs, [(EXAMPLES / "preferences.csv", "example data", use_example)])
    if why_prefs:
        st.caption(f"Using {why_prefs}.")

if src_prefs is None:
    st.info("Waiting for preferences: " + ("open the form and let TAs submit." if source == "form"
                                           else "upload a preferences CSV."))
    st.stop()

try:
    config = Config(mode=mode, tiers=numbers(tiers_text, int), weights=numbers(weights_text),
                    rank_exponent=exponent, min_list_factor=min_factor, seed=int(seed),
                    max_hours_per_day=max_day or None, max_in_a_row=max_row or None)
    participants, errors, warnings = csv_io.read_participants(src_prefs, schedule)
    grievances = csv_io.read_grievances(src_griev) if src_griev is not None else {}
except ValueError as e:
    st.error(f"Could not read the inputs: {e}")
    st.stop()

if roster:
    check = apply_roster(participants, roster)
    errors += [f"{n}: not on the roster" for n in check.unknown]
    st.write(f"**{len(roster) - len(check.missing)}** of the {len(roster)} TAs on the roster have sent preferences.")
    if check.missing and st.checkbox(
            f"Also schedule the {len(check.missing)} who haven't", value=True,
            help="They get whatever slots are left once everyone else is placed "
                 f"(missing: {', '.join(check.missing)})"):
        check = apply_roster(participants, roster, include_missing=True)
    participants = check.participants

more_errors, more_warnings = validate(schedule, participants, config)
errors += more_errors
warnings += more_warnings

# ---------------------------------------------------------------------- run
st.subheader("4. Build the schedule")
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
st.subheader("5. Results")
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
st.subheader("6. Send each TA their schedule")
emails = roster_emails(roster)
if not roster:
    st.info("To email TAs their schedules, load a roster (columns name, hours, email) in section 2.")
    st.stop()
if not any(emails.values()):
    st.info("The roster has no email addresses. Fill in its email column to email TAs their schedules.")
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
