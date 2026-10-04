"""Grievance points: detecting lost ties and updating the running totals.

A *tie* happens when two or more participants of the same group (everyone in
mode "none", the same tier otherwise) ranked a slot at the same position and
only some of them got it. A participant counts as having *lost* the tie only if
they would actually have preferred that slot to one they were given (i.e. they
received something they ranked lower, or something they did not rank).

Bookkeeping, per tie:
    * every loser gains 1 point;
    * every winner who had more points than some loser spends 1 point (their
      points are what tipped the balance). Totals never drop below 0.

Points are therefore *used* in the current run (as the second criterion of the
optimisation) and whatever is left is carried over to the next run.
"""
from __future__ import annotations

from collections import defaultdict

from .models import Config, Participant, Slot, TieEvent


def find_ties(
    participants: list[Participant], assignment: dict[str, list[Slot]], config: Config
) -> list[TieEvent]:
    worst = {}  # worst rank each participant received (inf if any unranked slot)
    for p in participants:
        ranks = [p.rank_of(s) or float("inf") for s in assignment[p.name]]
        worst[p.name] = max(ranks, default=0)

    contenders = defaultdict(list)  # (slot, rank, group) -> participants
    for p in participants:
        for rank, slot in enumerate(p.preferences, start=1):
            contenders[slot, rank, config.group_of(p.hours)].append(p.name)

    ties = []
    for (slot, rank, _), names in contenders.items():
        if len(names) < 2:
            continue
        winners = [n for n in names if slot in assignment[n]]
        losers = [n for n in names if slot not in assignment[n] and worst[n] > rank]
        if winners and losers:
            ties.append(TieEvent(slot, rank, winners, losers))
    return sorted(ties, key=lambda t: (t.slot, t.rank))


def update_grievances(
    before: dict[str, int], participants: list[Participant], ties: list[TieEvent]
) -> dict[str, int]:
    """New totals for everyone in `before` plus every participant of this run."""
    after = dict(before)
    for p in participants:
        after.setdefault(p.name, 0)
    for tie in ties:
        lowest_loser = min(before.get(n, 0) for n in tie.losers)
        for n in tie.losers:
            after[n] += 1
        for n in tie.winners:
            if before.get(n, 0) > lowest_loser:
                after[n] = max(0, after[n] - 1)
    return after
