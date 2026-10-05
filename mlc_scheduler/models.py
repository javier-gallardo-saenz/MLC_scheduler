"""Core data types shared by every module."""
from __future__ import annotations

from dataclasses import dataclass, field

SLOT_MINUTES = 60  # every MLC shift lasts one hour

MODES = ("none", "tiered", "weighted")


@dataclass(frozen=True, order=True)
class Slot:
    """A one-hour shift, e.g. Mon 12:30-13:30."""

    day: str
    start: int  # minutes since midnight

    @property
    def end(self) -> int:
        return self.start + SLOT_MINUTES

    @property
    def label(self) -> str:
        return f"{self.day} {fmt_time(self.start)}"

    def __str__(self) -> str:
        return self.label


@dataclass
class Participant:
    name: str
    hours: int
    preferences: list[Slot]  # ordered, most wanted first
    grievance: int = 0
    unavailable: set[Slot] = field(default_factory=set)  # never assigned

    def rank_of(self, slot: Slot) -> int | None:
        """1-based position of `slot` in the preference list, or None if unranked."""
        try:
            return self.preferences.index(slot) + 1
        except ValueError:
            return None


@dataclass
class Schedule:
    """The MLC timetable: which slots exist and how many TAs each one needs."""

    needed: dict[Slot, int]
    days: list[str]  # display order
    times: list[int]  # display order (minutes since midnight)

    @property
    def slots(self) -> list[Slot]:
        return list(self.needed)

    @property
    def capacity(self) -> int:
        return sum(self.needed.values())


@dataclass
class Config:
    """Options controlling the optimisation.

    mode:
      "none"     - every participant's preferences count equally.
      "tiered"   - participants with hours >= tiers[0] are scheduled first, then
                   hours >= tiers[1], ..., then everyone else.
      "weighted" - one single optimisation, but the preferences of participants
                   with hours >= tiers[k] are multiplied by weights[k].
    """

    mode: str = "none"
    tiers: list[int] = field(default_factory=list)  # hour thresholds, e.g. [8, 4]
    weights: list[float] = field(default_factory=list)  # weighted mode only
    rank_exponent: float = 1.0  # cost of the k-th choice is k ** rank_exponent
    min_list_factor: float = 2.0  # each TA must rank >= factor * hours slots
    seed: int | None = None  # random tie-breaking; set for reproducible runs
    time_limit: int = 60  # seconds per solver stage

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        # keep tiers (and their weights) sorted from highest threshold to lowest
        if self.weights and len(self.weights) != len(self.tiers):
            raise ValueError("weights must have one entry per tier threshold")
        pairs = sorted(
            zip(self.tiers, self.weights or [1.0] * len(self.tiers)), reverse=True
        )
        self.tiers = [t for t, _ in pairs]
        if self.weights:
            self.weights = [w for _, w in pairs]

    def tier_of(self, hours: int) -> int:
        """0 for the highest tier, len(tiers) for participants below every threshold."""
        for k, threshold in enumerate(self.tiers):
            if hours >= threshold:
                return k
        return len(self.tiers)

    def weight_of(self, hours: int) -> float:
        if self.mode != "weighted":
            return 1.0
        k = self.tier_of(hours)
        return self.weights[k] if k < len(self.weights) else 1.0

    def group_of(self, hours: int) -> int:
        """Participants in the same group compete on equal terms (used for ties)."""
        return 0 if self.mode == "none" else self.tier_of(hours)


@dataclass
class TieEvent:
    """Participants who ranked `slot` at the same position; only some got it."""

    slot: Slot
    rank: int
    winners: list[str]
    losers: list[str]


@dataclass
class Result:
    assignment: dict[str, list[Slot]]  # participant name -> assigned slots
    ties: list[TieEvent]
    grievance_before: dict[str, int]
    grievance_after: dict[str, int]
    stages: list[tuple[str, float]]  # (stage name, optimal objective value)


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_time(text: str) -> int:
    """'12:30' -> 750, '9' -> 540."""
    text = text.strip()
    hh, _, mm = text.partition(":")
    hour, minute = int(hh), int(mm or 0)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"invalid time {text!r}")
    return hour * 60 + minute
