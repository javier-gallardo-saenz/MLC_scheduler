"""Reading input CSVs and building output tables.

Input formats (see examples/ for samples):

schedule CSV - a grid; first column is the start time, one column per day,
               each cell is the number of TAs needed (blank or 0 = no slot):
                   time,Mon,Tue,Wed,Thu,Fri
                   12:00,2,2,3,2,1
                   12:30,1,1,1,1,1

preferences CSV - one row per participant: name, hours, then the ranked slots
                  in order (any number of columns, blanks ignored):
                   name,hours,pref1,pref2,pref3,...
                   Alice,4,Mon 12:00,Wed 14:30,...

grievances CSV - name,grievance  (optional; missing names start at 0)

All readers accept a path or a file-like object (e.g. a Streamlit upload).
"""
from __future__ import annotations

import pandas as pd

from .models import Config, Participant, Result, Schedule, Slot, fmt_time, parse_time


def _read(src) -> pd.DataFrame:
    df = pd.read_csv(src, dtype=str, keep_default_na=False, skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]
    return df.apply(lambda col: col.str.strip())


def read_schedule(src) -> Schedule:
    df = _read(src)
    if df.shape[1] < 2:
        raise ValueError("schedule CSV needs a time column plus one column per day")
    days = list(df.columns[1:])
    times, needed = [], {}
    for _, row in df.iterrows():
        start = parse_time(row.iloc[0])
        times.append(start)
        for day in days:
            count = int(float(row[day] or 0))
            if count > 0:
                needed[Slot(day, start)] = count
    return Schedule(needed=needed, days=days, times=times)


def parse_slot(text: str, schedule: Schedule) -> Slot:
    """'Mon 12:30', 'monday 12:30' or 'Mon 12' -> Slot (must exist in the schedule)."""
    parts = text.split()
    if len(parts) != 2:
        raise ValueError(f"cannot read slot {text!r} (expected e.g. 'Mon 12:30')")
    word, time = parts
    matches = [d for d in schedule.days if d.lower()[:3] == word.lower()[:3]]
    if len(matches) != 1:
        raise ValueError(f"unknown day in slot {text!r}")
    slot = Slot(matches[0], parse_time(time))
    if slot not in schedule.needed:
        raise ValueError(f"slot {text!r} is not in the MLC schedule")
    return slot


def read_participants(src, schedule: Schedule) -> tuple[list[Participant], list[str]]:
    """Returns (participants, errors). Rows with errors are still returned when possible."""
    df = _read(src)
    lower = {c.lower(): c for c in df.columns}
    if "name" not in lower or "hours" not in lower:
        raise ValueError("preferences CSV needs 'name' and 'hours' columns")
    pref_cols = [c for c in df.columns if c not in (lower["name"], lower["hours"])]

    participants, errors = [], []
    for i, row in df.iterrows():
        name = row[lower["name"]]
        if not name:
            continue
        try:
            hours = int(float(row[lower["hours"]]))
        except ValueError:
            errors.append(f"{name}: hours {row[lower['hours']]!r} is not a number")
            continue
        prefs = []
        for col in pref_cols:
            if not row[col]:
                continue
            try:
                prefs.append(parse_slot(row[col], schedule))
            except ValueError as e:
                errors.append(f"{name}: {e}")
        participants.append(Participant(name, hours, prefs))
    return participants, errors


def read_grievances(src) -> dict[str, int]:
    df = _read(src)
    lower = {c.lower(): c for c in df.columns}
    if "name" not in lower or "grievance" not in lower:
        raise ValueError("grievances CSV needs 'name' and 'grievance' columns")
    return {
        row[lower["name"]]: int(float(row[lower["grievance"]] or 0))
        for _, row in df.iterrows()
        if row[lower["name"]]
    }


# ---------------------------------------------------------------- outputs


def schedule_grid(schedule: Schedule, assignment: dict[str, list[Slot]]) -> pd.DataFrame:
    """Timetable: one row per start time, one column per day, cells list the TAs."""
    who: dict[Slot, list[str]] = {s: [] for s in schedule.needed}
    for name, slots in assignment.items():
        for s in slots:
            who[s].append(name)
    rows = []
    for t in schedule.times:
        row = {"time": f"{fmt_time(t)}-{fmt_time(Slot('', t).end)}"}
        for day in schedule.days:
            s = Slot(day, t)
            row[day] = ", ".join(sorted(who[s])) if s in who else ""
        rows.append(row)
    return pd.DataFrame(rows)


def assignments_table(
    schedule: Schedule, participants: list[Participant], result: Result
) -> pd.DataFrame:
    rows = []
    for p in participants:
        for s in sorted(result.assignment[p.name], key=lambda s: (schedule.days.index(s.day), s.start)):
            rows.append({
                "name": p.name,
                "day": s.day,
                "start": fmt_time(s.start),
                "end": fmt_time(s.end),
                "rank": p.rank_of(s) or "",
            })
    return pd.DataFrame(rows, columns=["name", "day", "start", "end", "rank"])


def summary_table(participants: list[Participant], result: Result, config: Config) -> pd.DataFrame:
    """One row per participant: how well their preferences were met."""
    rows = []
    for p in participants:
        ranks = sorted(p.rank_of(s) or float("inf") for s in result.assignment[p.name])
        ranked = [r for r in ranks if r != float("inf")]
        rows.append({
            "name": p.name,
            "hours": p.hours,
            "tier": config.tier_of(p.hours) + 1 if config.mode != "none" else "",
            "ranks received": " ".join(str(r) for r in ranked) + " -" * (len(ranks) - len(ranked)),
            "in top choices": sum(r <= p.hours for r in ranked),
            "unranked": len(ranks) - len(ranked),
            "grievance before": result.grievance_before.get(p.name, 0),
            "grievance after": result.grievance_after.get(p.name, 0),
        })
    return pd.DataFrame(rows)


def grievance_table(result: Result) -> pd.DataFrame:
    return pd.DataFrame(
        sorted(result.grievance_after.items()), columns=["name", "grievance"]
    )


def ties_table(result: Result) -> pd.DataFrame:
    return pd.DataFrame(
        [{"slot": t.slot.label, "rank": t.rank,
          "got it": ", ".join(t.winners), "missed out": ", ".join(t.losers)}
         for t in result.ties],
        columns=["slot", "rank", "got it", "missed out"],
    )
