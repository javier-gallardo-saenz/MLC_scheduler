"""Page where TAs submit their ranked slots and busy times."""
import math

import pandas as pd
import streamlit as st

from mlc_scheduler import csv_io
from mlc_scheduler.models import Slot, fmt_time
from mlc_scheduler.storage import FormStore

store = FormStore()

st.title("MLC preference form")
if store.schedule_path() is None:
    st.info("The preference form is not open right now.")
    st.stop()

schedule = csv_io.read_schedule(store.schedule_path())
factor = store.settings()["min_list_factor"]
order = {d: i for i, d in enumerate(schedule.days)}
slots = sorted(schedule.slots, key=lambda s: (order[s.day], s.start))
by_label = {s.label: s for s in slots}

st.write(
    "Rank the MLC slots you would like to work. You don't need to be fast: "
    "everyone's lists are combined at the end to give each TA slots as high "
    "in their ranking as possible. Every slot lasts one hour."
)

# --------------------------------------------------------------- about you
st.subheader("1. About you")
c1, c2 = st.columns([3, 1])
name = c1.text_input("Full name", help="Use the same name if you submit again; "
                                       "a new submission replaces the old one.")
hours = c2.number_input("Hours per week", 1, 20, 2)
previous = store.get(name) if name.strip() else None
if previous:
    st.info(f"We already have a submission from **{previous['name']}** "
            f"({previous['submitted'].replace('T', ' ')}). Submitting again will replace it.")

# ---------------------------------------------------------- unavailability
st.subheader("2. When you can't work")
days_off = st.multiselect("Days you can't work at all", schedule.days)
busy = st.text_input(
    "Other busy times (e.g. classes)",
    placeholder="Tue 13:00-14:30; Thu 13:00-14:30",
    help="Separate entries with ';'. A time range rules out every slot that "
         "overlaps it (a 13:00-14:30 class rules out the 12:30, 13:00, 13:30 and "
         "14:00 slots). A single slot like 'Mon 12:30' rules out just that slot.",
)
unavailable_text = "; ".join(days_off + ([busy.strip()] if busy.strip() else []))
problems = []
try:
    blocked = csv_io.parse_unavailable(unavailable_text, schedule)
except ValueError as e:
    blocked = set()
    problems.append(f"Busy times: {e}")

# ----------------------------------------------------------------- ranking
st.subheader("3. Your ranked slots")
required = math.ceil(factor * hours)
picked = st.multiselect(
    f"Pick slots one by one, favourite first (at least {required})",
    list(by_label),
    format_func=lambda label: f"{label}-{fmt_time(by_label[label].end)}",
    placeholder="Choose your favourite slot, then your second favourite, ...",
    help="The order you pick them in is your ranking. To move a slot, remove it (x) and "
         "add it again. Listing more slots than the minimum can only help you.",
)
clash = [p for p in picked if by_label[p] in blocked]
if clash:
    problems.append(f"These slots clash with your busy times: {', '.join(clash)}")
if len(picked) < required:
    problems.append(f"Rank at least {required} slots ({factor:g} per hour you work); "
                    f"you have ranked {len(picked)}.")

# your choices drawn on the timetable
rank = {by_label[p]: i + 1 for i, p in enumerate(picked)}
grid = pd.DataFrame(
    [[("✗" if s in blocked else str(rank.get(s, ""))) if s in schedule.needed else "·"
      for s in (Slot(d, t) for d in schedule.days)] for t in schedule.times],
    index=[fmt_time(t) for t in schedule.times], columns=schedule.days,
)
st.caption("Your choices on the timetable (numbers = your ranking, ✗ = busy, · = no MLC slot)")
st.dataframe(grid, width="stretch", height=36 * (len(grid) + 1) + 2)

# ------------------------------------------------------------------ submit
if not name.strip():
    problems.insert(0, "Enter your name.")
for p in problems:
    st.warning(p)
if st.button("Submit preferences", type="primary", disabled=bool(problems)):
    store.save(name, int(hours), unavailable_text, picked)
    st.success(f"Thanks {name.strip()}! Your preferences were saved. "
               "You can come back and submit again until the form closes.")
