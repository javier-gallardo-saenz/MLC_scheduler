"""Command-line interface.

    python -m mlc_scheduler examples/schedule.csv examples/preferences.csv \
        --grievances examples/grievances.csv --mode tiered --tiers 8 4 --out results
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import csv_io
from .models import MODES, Config
from .optimizer import SchedulingError
from .scheduler import run
from .validation import validate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mlc_scheduler", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schedule", help="schedule grid CSV (time x day -> TAs needed)")
    ap.add_argument("preferences", help="preferences CSV (name, hours, ranked slots...)")
    ap.add_argument("--grievances", help="grievance points CSV (name, grievance)")
    ap.add_argument("--mode", choices=MODES, default="none")
    ap.add_argument("--tiers", type=int, nargs="*", default=[], help="hour thresholds, e.g. 8 4")
    ap.add_argument("--weights", type=float, nargs="*", default=[], help="one weight per tier")
    ap.add_argument("--rank-exponent", type=float, default=1.0)
    ap.add_argument("--min-list-factor", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="results", help="output folder")
    args = ap.parse_args(argv)

    config = Config(mode=args.mode, tiers=args.tiers, weights=args.weights,
                    rank_exponent=args.rank_exponent,
                    min_list_factor=args.min_list_factor, seed=args.seed)
    schedule = csv_io.read_schedule(args.schedule)
    participants, errors = csv_io.read_participants(args.preferences, schedule)
    grievances = csv_io.read_grievances(args.grievances) if args.grievances else {}

    more_errors, warnings = validate(schedule, participants, config)
    for w in warnings:
        print("warning:", w)
    if errors + more_errors:
        for e in errors + more_errors:
            print("error:", e, file=sys.stderr)
        return 1

    try:
        result = run(schedule, participants, config, grievances)
    except SchedulingError as e:
        print("error:", e, file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    csv_io.schedule_grid(schedule, result.assignment).to_csv(out / "schedule.csv", index=False)
    csv_io.assignments_table(schedule, participants, result).to_csv(out / "assignments.csv", index=False)
    csv_io.grievance_table(result).to_csv(out / "grievances.csv", index=False)

    print(csv_io.summary_table(participants, result, config).to_string(index=False))
    print(f"\n{len(result.ties)} lost ties; results written to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
