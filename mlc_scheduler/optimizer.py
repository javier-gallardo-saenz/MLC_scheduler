"""The MILP.

Variables
    x[p, s] = 1 if participant p works slot s.

Hard constraints
    * every participant gets exactly their number of hours,
    * nobody works a slot they marked as unavailable,
    * no slot gets more TAs than it needs,
    * nobody works two overlapping slots (e.g. Mon 12:00 and Mon 12:30),
    * optional: at most max_hours_per_day hours per day, and at most
      max_in_a_row back-to-back hours (slots where the next one starts exactly
      when the previous one ends; any gap counts as a break).

Objective (lexicographic: each stage is optimised, then frozen at its optimum
before the next stage is solved, so later stages only break ties)
    1. preference cost  - sum of rank ** rank_exponent over assigned slots
                          (slots a participant did not rank cost as much as
                          rank longest_list + 1). In "weighted" mode each
                          participant's cost is multiplied by their tier weight.
                          In "tiered" mode there is one such stage per tier,
                          highest tier first.
    2. grievance        - the same cost weighted by each participant's
                          grievance points, so among equally good schedules
                          the slots go to whoever has been unlucky before.
                          (In tiered mode, each tier gets its own grievance
                          stage right after its preference stage.)
    3. random           - a random cost per (participant, slot) to settle any
                          remaining exact ties.
"""
from __future__ import annotations

import random

import pulp

from .models import SLOT_MINUTES, Config, Participant, Schedule, Slot


class SchedulingError(Exception):
    pass


def costs(
    schedule: Schedule, participants: list[Participant], config: Config
) -> dict[tuple[str, Slot], float]:
    longest = max((len(p.preferences) for p in participants), default=0)
    unranked = (longest + 1) ** config.rank_exponent
    table = {}
    for p in participants:
        for s in schedule.slots:
            r = p.rank_of(s)
            table[p.name, s] = r ** config.rank_exponent if r else unranked
    return table


def back_to_back(schedule: Schedule, length: int) -> list[list[Slot]]:
    """Every run of `length` slots that each start when the previous one ends."""
    runs = []
    for s0 in schedule.slots:
        run = [Slot(s0.day, s0.start + k * SLOT_MINUTES) for k in range(length)]
        if all(s in schedule.needed for s in run):
            runs.append(run)
    return runs


def build_model(schedule: Schedule, participants: list[Participant], config: Config):
    prob = pulp.LpProblem("mlc_schedule", pulp.LpMinimize)
    slots = schedule.slots
    x = {
        (p.name, s): prob.add_variable(f"x_{i}_{j}", cat="Binary")
        for i, p in enumerate(participants)
        for j, s in enumerate(slots)
    }
    per_day = config.max_hours_per_day
    in_a_row = config.max_in_a_row
    too_long = back_to_back(schedule, in_a_row + 1) if in_a_row else []
    for i, p in enumerate(participants):
        prob += pulp.lpSum(x[p.name, s] for s in slots) == p.hours, f"hours_{i}"
        for j, s in enumerate(slots):
            if s in p.unavailable:
                prob += x[p.name, s] == 0, f"unavailable_{i}_{j}"
        # (limits that someone's total hours already satisfy are skipped)
        if per_day and p.hours > per_day:
            for d, day in enumerate(schedule.days):
                prob += pulp.lpSum(x[p.name, s] for s in slots if s.day == day) <= per_day, f"day_{i}_{d}"
        if in_a_row and p.hours > in_a_row:
            for k, run in enumerate(too_long):
                prob += pulp.lpSum(x[p.name, s] for s in run) <= in_a_row, f"row_{i}_{k}"
    for j, s in enumerate(slots):
        prob += pulp.lpSum(x[p.name, s] for p in participants) <= schedule.needed[s], f"need_{j}"

    # Intervals overlap iff they share some start instant, so it is enough to
    # say: at the start of each slot, a participant is in at most one slot.
    for j, s0 in enumerate(slots):
        active = [s for s in slots if s.day == s0.day and s.start <= s0.start < s.end]
        if len(active) > 1:
            for i, p in enumerate(participants):
                prob += pulp.lpSum(x[p.name, s] for s in active) <= 1, f"overlap_{i}_{j}"
    return prob, x


def objectives(schedule, participants, x, config: Config):
    """The list of (stage name, linear expression) to minimise in order."""
    cost = costs(schedule, participants, config)

    if config.mode == "tiered":
        groups = [
            (f"tier {k + 1}", [p for p in participants if config.tier_of(p.hours) == k])
            for k in range(len(config.tiers) + 1)
        ]
    else:
        groups = [("everyone", participants)]

    stages = []
    for label, members in groups:
        if not members:
            continue
        stages.append((f"preferences ({label})", pulp.lpSum(
            config.weight_of(p.hours) * cost[p.name, s] * x[p.name, s]
            for p in members for s in schedule.slots
        )))
        if any(p.grievance > 0 for p in members):
            stages.append((f"grievance ({label})", pulp.lpSum(
                p.grievance * cost[p.name, s] * x[p.name, s]
                for p in members for s in schedule.slots
            )))

    rng = random.Random(config.seed)
    stages.append(("random tie-break", pulp.lpSum(rng.random() * v for v in x.values())))
    return stages


def solve(
    schedule: Schedule, participants: list[Participant], config: Config
) -> tuple[dict[str, list[Slot]], list[tuple[str, float]]]:
    """Returns (assignment, [(stage, optimal value), ...])."""
    prob, x = build_model(schedule, participants, config)
    # gapRel=0: each stage must be truly optimal, otherwise freezing it would
    # leave the later (tie-breaking) stages working on a suboptimal schedule
    solver = pulp.HiGHS(msg=False, gapRel=0, timeLimit=config.time_limit)

    history = []
    for k, (name, expr) in enumerate(objectives(schedule, participants, x, config)):
        prob.setObjective(expr)
        prob.solve(solver)
        if prob.status != pulp.LpStatusOptimal:
            raise SchedulingError(
                f"no feasible schedule ({pulp.LpStatus[prob.status]}) at stage "
                f"'{name}'. Check that hours fit the schedule, and that nobody "
                "has more hours than their available slots and the hours-per-day / "
                "in-a-row limits allow."
            )
        value = pulp.value(expr) or 0.0
        history.append((name, value))
        # freeze this stage at its optimum (small slack for float round-off)
        prob += expr <= value + 1e-6 * max(1.0, abs(value)), f"stage_{k}"

    assignment = {
        p.name: [s for s in schedule.slots if x[p.name, s].value() > 0.5]
        for p in participants
    }
    return assignment, history
