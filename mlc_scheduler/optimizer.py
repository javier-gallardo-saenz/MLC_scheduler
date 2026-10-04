"""The MILP.

Variables
    x[p, s] = 1 if participant p works slot s.

Hard constraints
    * every participant gets exactly their number of hours,
    * no slot gets more TAs than it needs,
    * nobody works two overlapping slots (e.g. Mon 12:00 and Mon 12:30).

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

from .models import Config, Participant, Schedule, Slot


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


def build_model(schedule: Schedule, participants: list[Participant]):
    prob = pulp.LpProblem("mlc_schedule", pulp.LpMinimize)
    slots = schedule.slots
    x = {
        (p.name, s): prob.add_variable(f"x_{i}_{j}", cat="Binary")
        for i, p in enumerate(participants)
        for j, s in enumerate(slots)
    }
    for i, p in enumerate(participants):
        prob += pulp.lpSum(x[p.name, s] for s in slots) == p.hours, f"hours_{i}"
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
    prob, x = build_model(schedule, participants)
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
                "has more hours than non-overlapping slots allow."
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
