"""Sanity checks run before optimising. Errors block the run; warnings don't."""
from __future__ import annotations

import math

from .models import Config, Participant, Schedule


def validate(
    schedule: Schedule, participants: list[Participant], config: Config
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    names = [p.name for p in participants]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"{dup}: appears more than once in the preferences file")

    for p in participants:
        if p.hours <= 0:
            errors.append(f"{p.name}: hours must be positive (got {p.hours})")
        if len(set(p.preferences)) != len(p.preferences):
            errors.append(f"{p.name}: the same slot is listed more than once")
        required = math.ceil(config.min_list_factor * p.hours)
        if len(p.preferences) < required:
            errors.append(
                f"{p.name}: ranked {len(p.preferences)} slots but must rank at least "
                f"{required} ({config.min_list_factor:g} x {p.hours} hours)"
            )

    total = sum(p.hours for p in participants)
    if total > schedule.capacity:
        errors.append(
            f"participants want {total} hours in total but the schedule only has "
            f"{schedule.capacity} TA-slots"
        )
    elif total < schedule.capacity:
        warnings.append(
            f"only {total} of {schedule.capacity} TA-slots can be filled; "
            "some slots will be understaffed"
        )

    if config.mode != "none" and not config.tiers:
        errors.append(f"mode '{config.mode}' needs at least one tier threshold")
    if config.mode == "weighted" and len(config.weights) != len(config.tiers):
        errors.append("weighted mode needs one weight per tier threshold")

    return errors, warnings
