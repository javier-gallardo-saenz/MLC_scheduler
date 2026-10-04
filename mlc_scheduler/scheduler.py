"""High-level entry point tying the pieces together."""
from __future__ import annotations

from . import grievance, optimizer
from .models import Config, Participant, Result, Schedule


def run(
    schedule: Schedule,
    participants: list[Participant],
    config: Config,
    grievances: dict[str, int] | None = None,
) -> Result:
    grievances = dict(grievances or {})
    for p in participants:
        p.grievance = grievances.get(p.name, 0)

    assignment, stages = optimizer.solve(schedule, participants, config)
    ties = grievance.find_ties(participants, assignment, config)
    return Result(
        assignment=assignment,
        ties=ties,
        grievance_before={p.name: p.grievance for p in participants},
        grievance_after=grievance.update_grievances(grievances, participants, ties),
        stages=stages,
    )
