"""Sanity checks run before optimising. Errors block the run; warnings don't."""
from __future__ import annotations

import math
from functools import lru_cache

from .models import Config, Participant, Schedule, Slot


def max_hours(slots: list[Slot], per_day: int | None = None, in_a_row: int | None = None) -> int:
    """Most hours that can be worked from `slots` without overlaps, within the limits."""
    total = 0
    for day in {s.day for s in slots}:
        starts = sorted(s.start for s in slots if s.day == day)

        @lru_cache(maxsize=None)
        def best(free_at: int, run: int) -> int:
            # most slots starting at/after `free_at`, given `run` back-to-back
            # hours ending exactly at `free_at`
            options = [0]
            for start in starts:
                if start < free_at:
                    continue
                new_run = run + 1 if start == free_at else 1
                if in_a_row is None or new_run <= in_a_row:
                    options.append(1 + best(start + 60, new_run))
            return max(options)

        total += min(best(-1, 0), per_day or len(starts))
    return total


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
        if p.preferences and len(p.preferences) < required:  # empty lists: see below
            errors.append(
                f"{p.name}: ranked {len(p.preferences)} slots but must rank at least "
                f"{required} ({config.min_list_factor:g} x {p.hours} hours)"
            )
        clash = [s.label for s in p.preferences if s in p.unavailable]
        if clash:
            errors.append(f"{p.name}: ranked slots also marked unavailable: {', '.join(clash)}")
        fits = max_hours([s for s in schedule.slots if s not in p.unavailable],
                         config.max_hours_per_day, config.max_in_a_row)
        if fits < p.hours:
            errors.append(
                f"{p.name}: only {fits} hours fit around their unavailable times "
                f"and the daily / in-a-row limits, but they need {p.hours}"
            )

    no_list = [p.name for p in participants if not p.preferences]
    if no_list:
        warnings.append(f"{len(no_list)} ranked no slots, so they will get whatever slots "
                        f"are left: {', '.join(no_list)}")

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
