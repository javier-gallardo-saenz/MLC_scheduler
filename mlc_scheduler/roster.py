"""The roster: who works at the MLC this term, their hours and email.

When a roster is loaded it is the authority on names and hours, whichever way
the preferences were collected (in-app form, Google Forms, a CSV).
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Participant, name_key


@dataclass
class RosterCheck:
    participants: list[Participant]
    missing: list[str]  # on the roster, but no preferences
    unknown: list[str]  # sent preferences, but not on the roster


def apply_roster(
    participants: list[Participant], roster: list[dict], include_missing: bool = False
) -> RosterCheck:
    """Take names and hours from the roster; optionally add TAs who sent nothing.

    TAs added this way have no preferences, so they get whatever slots are left.
    """
    by_key = {name_key(r["name"]): r for r in roster}
    unknown = []
    for p in participants:
        r = by_key.get(name_key(p.name))
        if r:
            p.name, p.hours = r["name"], r["hours"]
        else:
            unknown.append(p.name)
    sent = {name_key(p.name) for p in participants}
    missing = [r["name"] for k, r in by_key.items() if k not in sent]
    if include_missing:
        participants = participants + [Participant(r["name"], r["hours"], [])
                                       for r in roster if r["name"] in missing]
    return RosterCheck(participants, missing, unknown)


def emails(roster: list[dict]) -> dict[str, str]:
    return {r["name"]: r.get("email", "") for r in roster}
