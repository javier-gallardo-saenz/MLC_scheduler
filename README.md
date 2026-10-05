# MLC_scheduler
Scheduling for UBC's Math Learning Centre (MLC).

Right now TAs race each other to sign up in a shared spreadsheet. This tool
replaces that: every TA submits a **ranked list of slots**, and a mixed-integer
linear program (MILP) builds the schedule that gives each TA slots as high in
their own ranking as possible, while staffing every slot.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

streamlit run app.py            # interactive app in the browser
```

The app has two pages:

* **Submit preferences**: the form TAs fill in (see below).
* **Build schedule**: for the MLC. Load the schedule, open the form, and once
  TAs have submitted, choose the options in the sidebar, press **Build
  schedule** and download the results. Instead of using the form you can also
  upload a preferences CSV, or switch on the example data to try things out.

There is also a command-line version:

```bash
python -m mlc_scheduler examples/schedule.csv examples/preferences.csv \
    --grievances examples/grievances.csv --mode tiered --tiers 6 4 --seed 1 --out results
```

## Collecting preferences with the form

1. On **Build schedule**, upload the MLC schedule and press **Open the form**.
   The form offers the slots of that schedule and asks for the sidebar's minimum
   list length.
2. Send TAs the app's link. On **Submit preferences** each TA enters their name
   and hours, the days and times they can't work (e.g. their classes), and then
   picks slots one by one, favourite first. A timetable preview shows their
   ranking, and the form won't submit until the list is long enough and doesn't
   clash with their busy times. Submitting again under the same name (case and
   spacing don't matter) replaces the earlier answers.
3. Back on **Build schedule**, the submissions are used automatically (an
   uploaded preferences file takes priority). You can view and delete
   submissions, download them as a preferences CSV, and **Close the form**.

Submissions are stored as plain files in `data/` (or the folder in the
`MLC_DATA_DIR` environment variable). See [Sharing the app](#sharing-the-app)
before hosting it.

## Input files

Examples are in [examples/](examples/); `python examples/make_example.py [n] [seed]`
generates new random ones.

**Schedule** (`schedule.csv`): first column is the start time, then one column
per day; each cell is how many TAs that slot needs (blank or 0 = no slot). Every
slot lasts one hour, so `12:00` and `12:30` overlap and nobody gets both.

```
time,Mon,Tue,Wed,Thu,Fri
12:00,3,3,3,3,3
12:30,1,1,1,1,1
...
```

**Preferences** (`preferences.csv`): one row per TA: name, hours, then their
slots from most to least wanted. Any number of columns; blanks are ignored.
Days can be written `Mon` or `Monday`, times `12:30` or `12`.

An optional `unavailable` column lists times the TA **cannot** work, separated
by `;`. Each entry is one of:

* a whole day: `Fri`
* a single slot: `Mon 12:30`
* a busy time range: `Tue 13:00-14:30`. This rules out *every* slot that
  overlaps it, here Tue 12:30, 13:00, 13:30 and 14:00.

Unavailable slots are never assigned to that TA. Unranked but available slots
can still be assigned if the schedule needs them.

```
name,hours,unavailable,pref1,pref2,pref3,...
Alice,4,Tue 13:00-14:30; Fri,Mon 12:00,Wed 14:30,Tue 15:00,...
Bob,2,,Wed 12:00,Thu 16:30,Mon 17:00,...
```

**Grievance points** (`grievances.csv`, optional): `name,grievance`. Use the file
produced by the previous run. People in it who are not scheduled this time are
carried over unchanged.

## Output files

| file | content |
|---|---|
| `schedule.csv` | the timetable: times x days, each cell lists the TAs |
| `assignments.csv` | one row per TA and slot, with the rank the TA gave it (blank = not in their list) |
| `grievances.csv` | updated grievance points, the input for next time |

## How it works

**Variables:** `x[p, s] = 1` if TA `p` works slot `s`.

**Hard constraints:** each TA gets exactly their hours; no slot gets more TAs
than it needs; no TA works two overlapping slots; no TA works a slot they marked
unavailable. Optionally:

* **max hours per day**: no TA works more than this many hours in one day;
* **max hours in a row**: no TA works more than this many *back-to-back* slots,
  where each starts exactly when the previous one ends. 12:00, 13:00, 14:00 is
  3 in a row, while 12:00, 13:00, 14:30 is 2 then a break (any gap counts).

Before solving, the inputs are checked. A slot that is both ranked and
unavailable is an error. So is a TA whose hours don't fit around their busy
times and the limits above, or a set of slots that too few TAs are free for.

**Cost of a slot for a TA:** `rank ** e`, where `rank` is its position in the
TA's list (1 = favourite) and `e` is the *rank exponent* (default 1). A slot the
TA did not rank costs as much as rank `L + 1`, where `L` is the longest list
submitted, so it is worse than any ranked slot for everyone. If none of a TA's
listed slots can be given, the model fills their hours with whatever slots keep
the rest of the schedule best.

With `e = 1` the model minimises the plain sum of ranks. Larger `e` makes bad
ranks disproportionately expensive, so it prefers giving everyone their 3rd
choice over giving one person their 1st and another their 5th.

**Lexicographic objective.** The model is solved in stages. After each stage its
optimal value is frozen, so later stages only choose among schedules that are
equally good for earlier ones:

1. **Preferences**: minimise the total cost.
2. **Grievance points**: minimise the cost weighted by each TA's points. This
   only matters when there are ties, and it gives the slot to whoever has lost
   more ties before.
3. **Random**: settle any remaining exact tie randomly (reproducible with `--seed`).

### Priority by hours (sidebar "Priority by hours" / `--mode`)

* `none` (**Equal**): everyone's preferences count the same.
* `tiered`: given thresholds such as `6 4`, TAs with 6+ hours are scheduled
  first (stage 1 only counts their costs), then TAs with 4-5 hours, then
  everyone else. Each tier has its own grievance stage. Any number of tiers
  works. Lower tiers are guaranteed to remain feasible because everyone is
  in the model from the start.
* `weighted`: one single optimisation where each TA's costs are multiplied by
  their tier's weight (`--tiers 6 4 --weights 3 2`: 6+ hours weigh 3, 4-5 hours
  weigh 2, the rest weigh 1).

### Grievance points

A **tie** happens when TAs in the same group (everyone in `none` mode, the same
tier otherwise) ranked a slot at the *same position* and only some of them got
it. A TA who missed out counts as having **lost** the tie only if they actually
ended up with a slot they ranked lower, or one they did not rank. A TA who got
better slots anyway has not lost anything.

Points are used in the current run *and* carried over:

* they are the second stage of the optimisation (see above), so they decide ties now;
* each TA who lost a tie gains **1 point**;
* each winner whose points were higher than a loser's (so the points made the
  difference) **spends 1 point**. Totals never go below 0.

TAs in a lower tier who lose a slot to a higher tier get no points; in tiered
and weighted modes that is intended, not a tie.

### Options

| option | default | meaning |
|---|---|---|
| minimum list length | `2` x hours | each TA must rank at least this many slots; `0` disables the check |
| max hours per day | no limit | `--max-per-day` in the CLI |
| max hours in a row | no limit | `--max-in-a-row` in the CLI |
| rank exponent | `1` | fairness knob (see above) |
| seed | `0` in the app | makes the random tie-break reproducible |

## Project layout

```
app.py                     Streamlit entry point (page navigation)
views/
  form.py                  "Submit preferences" page for TAs
  admin.py                 "Build schedule" page for the MLC
mlc_scheduler/
  models.py                data types (Slot, Participant, Schedule, Config, Result)
  csv_io.py                reading input CSVs, building output tables
  validation.py            input checks (errors block the run, warnings don't)
  optimizer.py             the MILP (PuLP + HiGHS) and its lexicographic stages
  grievance.py             tie detection and grievance bookkeeping
  scheduler.py             run(): ties everything together
  storage.py               where the form keeps the schedule and submissions
  __main__.py              command-line interface
examples/                  sample inputs + generator
tests/                     pytest suite  (python -m pytest)
```

## Sharing the app

Anyone with the link can open both pages, so **protect the admin page** with
a password. Put this in `.streamlit/secrets.toml` (never commit it), or in the
hosting service's secrets settings:

```toml
admin_password = "choose-something"
```

Without a password set, the admin page is open, which is fine when running
the app on your own computer.

**Where to host.** The form keeps submissions in files, so it needs a server
whose disk persists: a UBC or department server, or any small virtual
machine. `streamlit run app.py --server.port 80` and point the link at it.
[Streamlit Community Cloud](https://streamlit.io/cloud) is free and easy (point
it at this repository and `app.py`), but its disk is wiped whenever the app
restarts or goes to sleep, which would lose submissions. Only use it if you
download the submissions regularly, or after switching
[storage.py](mlc_scheduler/storage.py) to an external store (e.g. a database or
Google Sheet). Only that one class would need to change.

## Possible extensions

* Let a TA see and edit their previous submission instead of starting over.
* Load a roster (names + hours) so the form offers a name list instead of free text.
* A persistent storage backend for free hosting (see above).
