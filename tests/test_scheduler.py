import io
from pathlib import Path

import pytest

from mlc_scheduler import Config, Participant, Schedule, SchedulingError, Slot, csv_io, run
from mlc_scheduler.validation import validate

EXAMPLES = Path(__file__).parent.parent / "examples"


def make_schedule(needed: dict[str, int]) -> Schedule:
    """{'Mon 12:00': 1, ...} -> Schedule."""
    slots = {}
    for label, n in needed.items():
        day, time = label.split()
        h, m = time.split(":")
        slots[Slot(day, int(h) * 60 + int(m))] = n
    days = list(dict.fromkeys(s.day for s in slots))
    times = sorted({s.start for s in slots})
    return Schedule(slots, days, times)


def S(label: str) -> Slot:
    day, time = label.split()
    h, m = time.split(":")
    return Slot(day, int(h) * 60 + int(m))


def P(name, hours, *prefs) -> Participant:
    return Participant(name, hours, [S(x) for x in prefs])


def test_everyone_gets_first_choice_when_possible():
    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1, "Tue 12:00": 1})
    people = [P("A", 1, "Mon 12:00", "Tue 12:00"), P("B", 2, "Mon 13:00", "Tue 12:00", "Mon 12:00")]
    res = run(sched, people, Config(seed=0))
    assert res.assignment == {"A": [S("Mon 12:00")], "B": [S("Mon 13:00"), S("Tue 12:00")]}
    assert res.ties == []


def test_tie_goes_to_more_grievance_and_points_are_updated():
    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1})
    for seed in range(5):  # must not depend on the random stage
        people = [P("A", 1, "Mon 12:00", "Mon 13:00"), P("B", 1, "Mon 12:00", "Mon 13:00")]
        res = run(sched, people, Config(seed=seed), grievances={"A": 0, "B": 2, "C": 7})
        assert res.assignment["B"] == [S("Mon 12:00")]
        assert len(res.ties) == 1 and res.ties[0].losers == ["A"]
        # A lost the tie (+1); B won thanks to its points (-1); C is carried over
        assert res.grievance_after == {"A": 1, "B": 1, "C": 7}


def test_random_tie_break_spends_no_points_and_is_reproducible():
    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1})
    winners = set()
    for seed in range(20):
        people = [P("A", 1, "Mon 12:00", "Mon 13:00"), P("B", 1, "Mon 12:00", "Mon 13:00")]
        res = run(sched, people, Config(seed=seed))
        (tie,) = res.ties
        winners.add(tie.winners[0])
        assert res.grievance_after[tie.winners[0]] == 0
        assert res.grievance_after[tie.losers[0]] == 1
        again = run(sched, people, Config(seed=seed))
        assert again.assignment == res.assignment
    assert winners == {"A", "B"}


def test_no_overlapping_slots():
    sched = make_schedule({"Mon 12:00": 1, "Mon 12:30": 1, "Mon 14:00": 1})
    people = [P("A", 2, "Mon 12:00", "Mon 12:30", "Mon 14:00"), P("B", 1, "Mon 14:00", "Mon 12:30")]
    res = run(sched, people, Config(seed=0))
    assert S("Mon 12:00") in res.assignment["A"]
    assert S("Mon 12:30") not in res.assignment["A"]


def test_unranked_slots_are_filled_when_needed():
    sched = make_schedule({"Mon 12:00": 1, "Fri 18:00": 1})
    people = [P("A", 1, "Mon 12:00"), P("B", 1, "Mon 12:00")]
    res = run(sched, people, Config(seed=0))
    assert sorted(s for v in res.assignment.values() for s in v) == [S("Fri 18:00"), S("Mon 12:00")]


def contested():
    # Big (4h) and Small (1h) both want Mon 12:00 most. Giving it to Small is
    # better overall (Big's alternatives are almost as good), but tiering or
    # weighting should hand it to Big.
    sched = make_schedule({f"Mon {h}:00": 1 for h in range(12, 17)} | {"Tue 12:00": 1})
    big = P("Big", 4, "Mon 12:00", "Mon 13:00", "Mon 14:00", "Mon 15:00", "Mon 16:00")
    small = P("Small", 1, "Mon 12:00")
    return sched, [big, small]


def test_tiered_and_weighted_favour_more_hours():
    sched, people = contested()
    plain = run(sched, people, Config(seed=0))
    assert S("Mon 12:00") in plain.assignment["Small"]

    tiered = run(sched, people, Config(mode="tiered", tiers=[4], seed=0))
    assert S("Mon 12:00") in tiered.assignment["Big"]

    weighted = run(sched, people, Config(mode="weighted", tiers=[4], weights=[3.0], seed=0))
    assert S("Mon 12:00") in weighted.assignment["Big"]
    # different tiers -> not a tie, so no grievance for Small
    assert weighted.ties == [] and tiered.ties == []


def test_multiple_tiers_are_sorted():
    c = Config(mode="weighted", tiers=[4, 8], weights=[2, 3])
    assert c.tiers == [8, 4] and c.weights == [3, 2]
    assert [c.tier_of(h) for h in (10, 8, 5, 2)] == [0, 0, 1, 2]
    assert [c.weight_of(h) for h in (10, 5, 2)] == [3, 2, 1.0]


def test_infeasible_raises():
    sched = make_schedule({"Mon 12:00": 1, "Mon 12:30": 1})
    with pytest.raises(SchedulingError):
        run(sched, [P("A", 2, "Mon 12:00", "Mon 12:30")], Config(seed=0))


def test_validation():
    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1})
    errors, _ = validate(sched, [P("A", 2, "Mon 12:00"), P("A", 1, "Mon 13:00", "Mon 13:00")],
                         Config())
    text = "\n".join(errors)
    assert "more than once in the preferences" in text
    assert "must rank at least 4" in text
    assert "listed more than once" in text
    assert "only has 2 TA-slots" in text


def test_csv_round_trip():
    sched = csv_io.read_schedule(io.StringIO("time,Mon,Tue\n12:00,2,1\n12:30,,1\n"))
    assert sched.needed == {S("Mon 12:00"): 2, S("Tue 12:00"): 1, S("Tue 12:30"): 1}
    people, errors, _ = csv_io.read_participants(io.StringIO(
        "name,hours,p1,p2,p3\nAna,1,monday 12:00,Tue 12,\nBo,1,Wed 12:00,Tue 12:30,Mon 13:00\n"
    ), sched)
    assert people[0].preferences == [S("Mon 12:00"), S("Tue 12:00")]
    assert len(errors) == 2  # Wed and Mon 13:00 are not in the schedule


def test_example_files_run():
    sched = csv_io.read_schedule(EXAMPLES / "schedule.csv")
    people, errors, _ = csv_io.read_participants(EXAMPLES / "preferences.csv", sched)
    assert not errors
    grievances = csv_io.read_grievances(EXAMPLES / "grievances.csv")
    res = run(sched, people, Config(mode="tiered", tiers=[6, 4], seed=1), grievances)
    filled = sum(len(v) for v in res.assignment.values())
    assert filled == sum(p.hours for p in people)
    grid = csv_io.schedule_grid(sched, res.assignment)
    assert list(grid.columns) == ["time", *sched.days]


def test_parse_unavailable():
    sched = csv_io.read_schedule(io.StringIO(
        "time,Mon,Tue\n12:00,1,1\n12:30,1,1\n13:00,1,1\n13:30,1,1\n14:00,1,1\n"
    ))
    got = csv_io.parse_unavailable("Tue; mon 13:00-14:00 ;Mon 12:00", sched)
    # the 13:00-14:00 class also rules out the slots starting 12:30 and 13:30
    assert got == {S(f"Tue {t}") for t in ("12:00", "12:30", "13:00", "13:30", "14:00")} | {
        S("Mon 12:00"), S("Mon 12:30"), S("Mon 13:00"), S("Mon 13:30")}
    with pytest.raises(ValueError):
        csv_io.parse_unavailable("Mon 14:00-13:00", sched)

    people, errors, _ = csv_io.read_participants(io.StringIO(
        "name,hours,unavailable,p1\nAna,1,Tue,Mon 12:00\nBo,1,,Tue 12:00\n"
    ), sched)
    assert not errors
    assert people[0].preferences == [S("Mon 12:00")] and len(people[0].unavailable) == 5
    assert people[1].unavailable == set()


def test_unavailable_slots_are_never_assigned():
    # Only Mon 12:00 is left free for Fri-busy B, even though A ranked it first
    sched = make_schedule({"Mon 12:00": 1, "Fri 12:00": 1})
    a = P("A", 1, "Mon 12:00", "Fri 12:00")
    b = P("B", 1)
    b.unavailable = {S("Fri 12:00")}
    res = run(sched, [a, b], Config(seed=0))
    assert res.assignment == {"A": [S("Fri 12:00")], "B": [S("Mon 12:00")]}


def test_validation_of_unavailable():
    sched = make_schedule({"Mon 12:00": 2, "Mon 12:30": 1, "Mon 13:00": 1})
    a = P("A", 2, "Mon 12:00", "Mon 12:30")
    a.unavailable = {S("Mon 12:30"), S("Mon 13:00")}  # only Mon 12:00 left: 1 hour
    b = P("B", 2, "Mon 12:00", "Mon 13:00")
    b.unavailable = {S("Mon 12:00")}
    errors, _ = validate(sched, [a, b], Config(min_list_factor=0))
    text = "\n".join(errors)
    assert "A: ranked slots also marked unavailable: Mon 12:30" in text
    assert "A: only 1 hours fit around" in text
    assert "Mon 12:00 (1/2)" in text and "Mon 13:00 (1/1)" not in text


def test_max_hours_per_day():
    sched = make_schedule({f"Mon {h}:00": 1 for h in range(12, 16)} | {"Tue 12:00": 1, "Tue 13:00": 1})
    a = P("A", 3, "Mon 12:00", "Mon 13:00", "Mon 14:00", "Mon 15:00", "Tue 12:00")
    res = run(sched, [a], Config(seed=0, max_hours_per_day=2))
    assert res.assignment["A"] == [S("Mon 12:00"), S("Mon 13:00"), S("Tue 12:00")]


def test_max_in_a_row():
    sched = make_schedule({t: 1 for t in ("Mon 12:00", "Mon 13:00", "Mon 13:30", "Mon 14:00", "Mon 14:30", "Mon 15:00")})
    a = P("A", 3, "Mon 12:00", "Mon 13:00", "Mon 14:00", "Mon 15:00", "Mon 14:30", "Mon 13:30")
    # ranks 1-2-3 (12, 13, 14) would be 3 in a row; best allowed is 12, 13, then 15 (rank 4)
    res = run(sched, [a], Config(seed=0, max_in_a_row=2))
    assert res.assignment["A"] == [S("Mon 12:00"), S("Mon 13:00"), S("Mon 15:00")]
    assert run(sched, [a], Config(seed=0)).assignment["A"][2] == S("Mon 14:00")


def test_limits_in_validation():
    from mlc_scheduler.validation import max_hours
    day = [S(f"Mon {h}:{m}") for h in range(12, 18) for m in ("00", "30")]  # 12:00 ... 17:30
    assert max_hours(day) == 6
    assert max_hours(day, in_a_row=2) == 5  # e.g. 12, 13, (break) 14:30, 15:30, (break) 17:00
    assert max_hours(day, per_day=3) == 3
    sched = make_schedule({s.label: 1 for s in day} | {"Tue 12:00": 1})
    errors, _ = validate(sched, [P("A", 3, "Mon 12:00")], Config(min_list_factor=0, max_hours_per_day=1))
    assert any("only 2 hours fit" in e for e in errors)


def test_form_store(tmp_path):
    from mlc_scheduler.storage import FormStore
    store = FormStore(tmp_path)
    assert store.schedule_path() is None and store.submissions() == []

    sched = csv_io.read_schedule(io.StringIO("time,Mon,Tue\n12:00,2,1\n12:30,,1\n"))
    store.open_form(csv_io.schedule_to_csv(sched), 1.5)
    assert csv_io.read_schedule(store.schedule_path()).needed == sched.needed
    assert store.settings()["min_list_factor"] == 1.5

    store.save("Ana  Ruiz", 1, "Tue", ["Mon 12:00"])
    store.save("Bo", 2, "", ["Tue 12:00", "Mon 12:00", "Tue 12:30"])
    store.save("ana ruiz", 1, "", ["Tue 12:30", "Mon 12:00"])  # replaces the first one
    assert [s["name"] for s in store.submissions()] == ["Bo", "ana ruiz"]
    assert store.get("ANA RUIZ")["preferences"] == ["Tue 12:30", "Mon 12:00"]

    # the export is a valid preferences file (the 'submitted' column is ignored)
    people, errors, _ = csv_io.read_participants(io.StringIO(store.to_csv()), sched)
    assert not errors
    assert [p.preferences for p in people] == [
        [S("Tue 12:00"), S("Mon 12:00"), S("Tue 12:30")], [S("Tue 12:30"), S("Mon 12:00")]]

    store.delete("Bo")
    store.close_form()
    assert [s["name"] for s in store.submissions()] == ["ana ruiz"] and store.schedule_path() is None


def test_roster_drives_hours_and_includes_non_submitters():
    from mlc_scheduler.roster import apply_roster
    roster, errors = csv_io.read_roster(io.StringIO(
        "name,hours,email\nAna Ruiz,1,ana@x.ca\nBo,2,bo@x.ca\nCy,1,\n"))
    assert not errors and roster[2] == {"name": "Cy", "hours": 1, "email": ""}
    _, errors = csv_io.read_roster(io.StringIO("name,hours,email\nA,1,nope\na,2,\n"))
    assert len(errors) == 2  # bad address + duplicate

    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1, "Tue 12:00": 2})
    # hours on the roster win; 'Zed' is not on the roster
    people, _, _ = csv_io.read_participants(io.StringIO(
        "name,hours,p1\nana ruiz,5,Mon 12:00\nZed,1,Mon 13:00\n"), sched)
    check = apply_roster(people, roster)
    assert [(p.name, p.hours) for p in check.participants] == [("Ana Ruiz", 1), ("Zed", 1)]
    assert check.unknown == ["Zed"] and check.missing == ["Bo", "Cy"]

    people, _, _ = csv_io.read_participants(io.StringIO("name,p1\nAna Ruiz,Mon 12:00\n"), sched)
    assert people[0].hours == 0  # no hours column: the roster provides them
    people = apply_roster(people, roster, include_missing=True).participants
    assert [(p.name, p.hours, len(p.preferences)) for p in people] == [
        ("Ana Ruiz", 1, 1), ("Bo", 2, 0), ("Cy", 1, 0)]

    # no list at all is only a warning; they fill the leftovers
    errors, warnings = validate(sched, people, Config(min_list_factor=1))
    assert not errors and any("2 ranked no slots" in w and "Bo, Cy" in w for w in warnings)
    res = run(sched, people, Config(seed=0))
    assert res.assignment["Ana Ruiz"] == [S("Mon 12:00")]


def test_emails_and_calendar():
    from datetime import date
    from mlc_scheduler import notify
    sched = make_schedule({"Mon 12:00": 1, "Wed 13:30": 1, "Fri 12:00": 1})
    assignment = {"Ana": [S("Wed 13:30"), S("Mon 12:00")], "Bo": [S("Fri 12:00")]}
    msgs = notify.compose(sched, assignment, {"ana": "ana@x.ca", "Bo": ""},
                          subject="Shifts for {name}", term=(date(2026, 9, 2), date(2026, 12, 5)))
    assert [m.name for m in msgs] == ["Ana"]  # Bo has no address
    (m,) = msgs
    assert m.to == "ana@x.ca" and m.subject == "Shifts for Ana"
    assert "(2 hours per week)" in m.body
    assert "  Mon 12:00-13:00\n  Wed 13:30-14:30" in m.body
    # 2026-09-02 is a Wednesday: first Wed shift that day, first Mon shift on Sep 7
    assert "DTSTART:20260902T133000" in m.calendar and "DTEND:20260902T143000" in m.calendar
    assert "DTSTART:20260907T120000" in m.calendar
    assert m.calendar.count("RRULE:FREQ=WEEKLY;UNTIL=20261205T235959") == 2


def test_send_uses_one_smtp_session(monkeypatch):
    from mlc_scheduler import notify
    log = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            log.append(("connect", host, port))
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            log.append(("quit",))
        def starttls(self):
            log.append(("starttls",))
        def login(self, user, password):
            log.append(("login", user))
        def send_message(self, msg):
            if msg["To"] == "bad@x.ca":
                raise notify.smtplib.SMTPRecipientsRefused({})
            log.append(("send", msg["From"], msg["To"], msg["Subject"],
                        [p.get_filename() for p in msg.iter_attachments()]))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    emails = [notify.Email("Ana", "ana@x.ca", "Hi", "body", calendar="BEGIN:VCALENDAR"),
              notify.Email("Bo", "bad@x.ca", "Hi", "body")]
    result = notify.send(emails, {"host": "smtp.x.ca", "port": 587, "user": "mlc@x.ca", "password": "pw"})
    assert result["Ana"] == "sent" and result["Bo"].startswith("failed")
    assert log == [("connect", "smtp.x.ca", 587), ("starttls",), ("login", "mlc@x.ca"),
                   ("send", "mlc@x.ca", "ana@x.ca", "Hi", ["mlc_schedule.ics"]), ("quit",)]


def test_google_forms_export_is_read_directly():
    sched = make_schedule({"Mon 12:00": 1, "Mon 13:00": 1, "Tue 12:00": 1, "Fri 12:00": 1})
    export = (
        "Timestamp,Name,Unavailable days,Unavailable times,Choice 1 (favourite),Choice 2,Choice 3\n"
        "10/1/2026 9:00:00,Ana (2 h/week),Fri,,Mon 12:00-13:00,Tue 12:00-13:00,\n"
        "10/1/2026 9:30:00,Bo (1 h/week),\"Mon, Fri\",,Tue 12:00-13:00,Tue 12:00-13:00,\n"
        "10/2/2026 8:00:00,Ana (2 h/week),,Tue 11:30 - 12:30,Mon 13:00-14:00,Mon 12:00-13:00,Fri 12:00-13:00\n"
    )
    people, errors, warnings = csv_io.read_participants(io.StringIO(export), sched)
    assert not errors
    ana, bo = people  # Ana's second answer replaced her first
    assert (ana.name, ana.hours) == ("Ana", 0)  # hours come from the roster
    assert ana.preferences == [S("Mon 13:00"), S("Mon 12:00"), S("Fri 12:00")]
    assert ana.unavailable == {S("Tue 12:00")}
    assert bo.preferences == [S("Tue 12:00")] and bo.unavailable == {S("Mon 12:00"), S("Mon 13:00"), S("Fri 12:00")}
    assert any("Ana answered the form more than once" in w for w in warnings)
    assert any("Bo picked Tue 12:00 for more than one choice" in w for w in warnings)


def test_google_form_script():
    from mlc_scheduler import google_forms
    sched = make_schedule({"Mon 12:00": 1, "Mon 12:30": 1, "Tue 12:00": 2})
    script = google_forms.apps_script(sched, [{"name": "Ana", "hours": 2, "email": ""}], 2.0)
    assert 'const NAMES = [\n  "Ana (2 h/week)"\n]' in script
    assert '"Mon 12:00-13:00"' in script and "const CHOICES = 3;" in script  # capped by #slots
    assert "function createForm()" in script and "{{" not in script
    no_roster = google_forms.apps_script(sched)
    assert "const NAMES = []" in no_roster and "const REQUIRED_CHOICES = 1;" in no_roster


def test_cli_emails(tmp_path, monkeypatch):
    from mlc_scheduler import notify
    from mlc_scheduler.__main__ import main
    (tmp_path / "schedule.csv").write_text("time,Mon,Tue\n12:00,1,1\n13:00,1,0\n")
    (tmp_path / "assignments.csv").write_text(
        "name,day,start,end,rank\nAna,Mon,12:00,13:00,1\nAna,Tue,12:00,13:00,\nBo,Mon,13:00,14:00,2\n")
    (tmp_path / "roster.csv").write_text("name,hours,email\nAna,2,ana@x.ca\nBo,1,\n")
    (tmp_path / "smtp.toml").write_text('[smtp]\nhost = "smtp.x.ca"\nport = 587\nuser = "mlc@x.ca"\npassword = "pw"\n')
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *e): pass
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, m): sent.append(m)

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    args = [str(tmp_path / f) for f in ("assignments.csv", "schedule.csv", "roster.csv")]
    out = tmp_path / "emails"
    assert main(["emails", *args, "--out", str(out), "--term", "2026-09-08", "2026-12-04"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["Ana.eml", "emails.csv"]  # Bo has no address
    draft = (out / "Ana.eml").read_text()
    assert "X-Unsent: 1" in draft and "Mon 12:00-13:00" in draft and "mlc_schedule.ics" in draft
    assert sent == []  # nothing is sent without --send

    assert main(["emails", *args, "--out", str(out), "--send", "--smtp", str(tmp_path / "smtp.toml"), "--yes"]) == 0
    assert [(m["From"], m["To"]) for m in sent] == [("mlc@x.ca", "ana@x.ca")]


def test_excel_style_csvs():
    # byte-order mark + ';' separators, as saved by Excel in many European locales
    raw = "\ufeffname;hours;email\r\nAna;2;ana@x.ca\r\nBo;1;\r\n".encode("utf-8")
    roster, errors = csv_io.read_roster(io.BytesIO(raw))
    assert not errors and roster == [{"name": "Ana", "hours": 2, "email": "ana@x.ca"},
                                     {"name": "Bo", "hours": 1, "email": ""}]
    sched = csv_io.read_schedule(io.BytesIO("\ufefftime;Mon\r\n12:00;1\r\n".encode("utf-8")))
    # ';' inside a comma-separated file (the unavailable column) is not mistaken for a separator
    people, errors, _ = csv_io.read_participants(io.StringIO(
        'name,hours,unavailable,p1\nAna,1,"Mon 12:00; Tue",Mon 12:00\n'), make_schedule({"Mon 12:00": 1, "Tue 12:00": 1}))
    assert people[0].unavailable == {S("Mon 12:00"), S("Tue 12:00")}
    assert sched.needed == {S("Mon 12:00"): 1}
