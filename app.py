"""Streamlit front-end:  streamlit run app.py"""
from pathlib import Path

import pandas as pd
import streamlit as st

from mlc_scheduler import Config, SchedulingError, csv_io, run
from mlc_scheduler.validation import validate

EXAMPLES = Path(__file__).parent / "examples"

st.set_page_config(page_title="MLC Scheduler", layout="wide")
st.title("MLC Scheduler")
st.caption(
    "Everyone submits a ranked list of slots; an optimisation model finds the "
    "schedule that gives each TA slots as high in their ranking as possible."
)


def numbers(text: str, kind=float) -> list:
    return [kind(x) for x in text.replace(",", " ").split()]


def source(upload, example: str, use_example: bool):
    """An uploaded file (rewound), or the bundled example, or None."""
    if upload is not None:
        upload.seek(0)
        return upload
    return EXAMPLES / example if use_example else None


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
                            help="name, hours, optional 'unavailable' column, then ranked slots like 'Mon 12:30'")
up_griev = c3.file_uploader("Grievance points (optional)", type="csv", help="name, grievance")
with st.expander("File formats & templates"):
    st.markdown(
        "- **Schedule**: `time,Mon,Tue,...` then rows like `12:30,1,1,2,1,1`. Every slot lasts one hour; blank/0 = no slot.\n"
        "- **Preferences**: `name,hours,pref1,pref2,...` then rows like `Alice,4,Mon 12:00,Wed 14:30,...` (best first).\n"
        "  An optional `unavailable` column lists, separated by `;`, whole days (`Fri`), single slots "
        "(`Mon 12:30`) or busy times (`Tue 13:00-14:30`, which rules out every slot overlapping it). "
        "Those slots are never assigned to that TA.\n"
        "- **Grievance points**: `name,grievance`. Use the file produced by the previous run."
    )
    d1, d2, d3 = st.columns(3)
    for col, f in zip((d1, d2, d3), ("schedule.csv", "preferences.csv", "grievances.csv")):
        col.download_button(f"Example {f}", (EXAMPLES / f).read_bytes(), f, "text/csv")

src_schedule = source(up_schedule, "schedule.csv", use_example)
src_prefs = source(up_prefs, "preferences.csv", use_example)
src_griev = source(up_griev, "grievances.csv", use_example)
if src_schedule is None or src_prefs is None:
    st.info("Upload a schedule and a preferences file (or switch on the example data) to begin.")
    st.stop()

try:
    config = Config(mode=mode, tiers=numbers(tiers_text, int), weights=numbers(weights_text),
                    rank_exponent=exponent, min_list_factor=min_factor, seed=int(seed))
    schedule = csv_io.read_schedule(src_schedule)
    participants, errors = csv_io.read_participants(src_prefs, schedule)
    grievances = csv_io.read_grievances(src_griev) if src_griev is not None else {}
except ValueError as e:
    st.error(f"Could not read the inputs: {e}")
    st.stop()

more_errors, warnings = validate(schedule, participants, config)
errors += more_errors
st.write(f"**{len(participants)}** participants asking for **{sum(p.hours for p in participants)}** "
         f"hours; the schedule has **{len(schedule.needed)}** slots needing **{schedule.capacity}** TA-hours.")
for w in warnings:
    st.warning(w)
if errors:
    st.error("Please fix these problems:\n\n" + "\n".join(f"- {e}" for e in errors))
    st.stop()

# ---------------------------------------------------------------------- run
st.subheader("2. Build the schedule")
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
if set(result.assignment) != {p.name for p in participants} or used_config != config:
    st.warning("Inputs or options changed since this schedule was built. Press the button again.")

# ------------------------------------------------------------------ results
st.subheader("3. Results")
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
