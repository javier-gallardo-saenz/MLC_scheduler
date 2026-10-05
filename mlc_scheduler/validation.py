"""Sanity checks run before optimising. Errors block the run; warnings don't."""
from __future__ import annotations

import math

from .models import Config, Participant, Schedule, Slot


def max_hours(slots: list[Slot]) -> int:
    """Most one-hour shifts that can be worked from `slots` without overlaps."""
    count, last_end = 0, {}
    for s in sorted(slots, key=lambda s: s.end):  # greedy interval scheduling
        if s.start >= last_end.get(s.day, -1):
            count, last_end[s.day] = count + 1, s.end
    return count


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
        clash = [s.label for s in p.preferences if s in p.unavailable]
        if clash:
            errors.append(f"{p.name}: ranked slots also marked unavailable: {', '.join(clash)}")
        fits = max_hours([s for s in schedule.slots if s not in p.unavailable])
        if fits < p.hours:
            errors.append(
                f"{p.name}: only {fits} non-overlapping hours fit around their "
                f"unavailable times, but they need {p.hours}"
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

    # slots that too few people can work
    short = {}
    for s, need in schedule.needed.items():
        free = sum(s not in p.unavailable for p in participants)
        if free < need:
            short[s] = free
    fillable = schedule.capacity - sum(schedule.needed[s] - f for s, f in short.items())
    if short:
        listing = ", ".join(f"{s.label} ({f}/{schedule.needed[s]})" for s, f in short.items())
        if total > fillable:
            errors.append(
                f"not enough available TAs to fill these slots (available/needed): {listing}. "
                f"Only {fillable} TA-slots can be filled but {total} hours must be assigned"
            )
        else:
            warnings.append(f"these slots will be understaffed (available/needed): {listing}")

    if config.mode != "none" and not config.tiers:
        errors.append(f"mode '{config.mode}' needs at least one tier threshold")
    if config.mode == "weighted" and len(config.weights) != len(config.tiers):
        errors.append("weighted mode needs one weight per tier threshold")

    return errors, warnings
