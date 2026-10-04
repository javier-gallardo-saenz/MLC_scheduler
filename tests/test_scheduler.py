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
    people, errors = csv_io.read_participants(io.StringIO(
        "name,hours,p1,p2,p3\nAna,1,monday 12:00,Tue 12,\nBo,1,Wed 12:00,Tue 12:30,Mon 13:00\n"
    ), sched)
    assert people[0].preferences == [S("Mon 12:00"), S("Tue 12:00")]
    assert len(errors) == 2  # Wed and Mon 13:00 are not in the schedule


def test_example_files_run():
    sched = csv_io.read_schedule(EXAMPLES / "schedule.csv")
    people, errors = csv_io.read_participants(EXAMPLES / "preferences.csv", sched)
    assert not errors
    grievances = csv_io.read_grievances(EXAMPLES / "grievances.csv")
    res = run(sched, people, Config(mode="tiered", tiers=[6, 4], seed=1), grievances)
    filled = sum(len(v) for v in res.assignment.values())
    assert filled == sum(p.hours for p in people)
    grid = csv_io.schedule_grid(sched, res.assignment)
    assert list(grid.columns) == ["time", *sched.days]
