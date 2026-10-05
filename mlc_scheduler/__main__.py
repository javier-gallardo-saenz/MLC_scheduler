"""Command-line interface: everything runs locally, inputs and outputs are CSVs.

  1. python -m mlc_scheduler google-form schedule.csv --roster roster.csv
         writes create_form.gs, a script that creates the Google Form
  2. python -m mlc_scheduler run schedule.csv responses.csv --roster roster.csv
         builds the schedule (the preferences file can be a Google Forms export)
  3. python -m mlc_scheduler emails results/assignments.csv schedule.csv roster.csv
         writes each TA's email (and sends them with --send)

Run any command with -h for its options.
"""
from __future__ import annotations

import argparse
import getpass
import sys
import tomllib
from datetime import date
from pathlib import Path

from . import csv_io, google_forms, notify
from .models import MODES, Config
from .optimizer import SchedulingError
from .roster import apply_roster, emails
from .scheduler import run
from .validation import validate


def read_roster(path: str | None) -> list[dict]:
    if not path:
        return []
    roster, errors = csv_io.read_roster(path)
    if errors:
        sys.exit("roster errors:\n  " + "\n  ".join(errors))
    return roster


# ------------------------------------------------------------- google-form

def cmd_google_form(args) -> int:
    schedule = csv_io.read_schedule(args.schedule)
    script = google_forms.apps_script(schedule, read_roster(args.roster),
                                      args.min_list_factor, args.choices)
    Path(args.output).write_text(script, encoding="utf-8")
    print(f"Wrote {args.output}. To create the form: open https://script.google.com, click "
          "'New project', replace the code with this file, save and click Run.")
    return 0


# --------------------------------------------------------------------- run

def cmd_run(args) -> int:
    config = Config(mode=args.mode, tiers=args.tiers, weights=args.weights,
                    rank_exponent=args.rank_exponent,
                    min_list_factor=args.min_list_factor, seed=args.seed,
                    max_hours_per_day=args.max_per_day, max_in_a_row=args.max_in_a_row)
    schedule = csv_io.read_schedule(args.schedule)
    participants, errors, warnings = csv_io.read_participants(args.preferences, schedule)
    grievances = csv_io.read_grievances(args.grievances) if args.grievances else {}
    roster = read_roster(args.roster)
    if roster:
        check = apply_roster(participants, roster, args.include_missing)
        participants = check.participants
        errors += [f"{n}: not on the roster" for n in check.unknown]
        if check.missing:
            warnings.append(
                f"{len(check.missing)} on the roster sent no preferences: {', '.join(check.missing)}"
                + ("" if args.include_missing else " (use --include-missing to schedule them anyway)"))

    more_errors, more_warnings = validate(schedule, participants, config)
    for w in warnings + more_warnings:
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
    summary = csv_io.summary_table(participants, result, config)
    csv_io.schedule_grid(schedule, result.assignment).to_csv(out / "schedule.csv", index=False)
    csv_io.assignments_table(schedule, participants, result).to_csv(out / "assignments.csv", index=False)
    csv_io.grievance_table(result).to_csv(out / "grievances.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)

    print(summary.to_string(index=False))
    print(f"\n{len(result.ties)} lost ties. Wrote schedule.csv, assignments.csv, "
          f"grievances.csv and summary.csv to {out}/")
    return 0


# ------------------------------------------------------------------ emails

def cmd_emails(args) -> int:
    schedule = csv_io.read_schedule(args.schedule)
    assignment = csv_io.read_assignments(args.assignments, schedule)
    roster = read_roster(args.roster)
    body = Path(args.message).read_text(encoding="utf-8") if args.message else notify.DEFAULT_BODY
    term = (date.fromisoformat(args.term[0]), date.fromisoformat(args.term[1])) if args.term else None
    messages = notify.compose(schedule, assignment, emails(roster), args.subject, body, term)
    no_email = sorted(set(assignment) - {m.name for m in messages})
    if no_email:
        print(f"warning: no email address on the roster for: {', '.join(no_email)}")

    smtp = {}
    if args.send:
        smtp = tomllib.loads(Path(args.smtp).read_text(encoding="utf-8"))
        smtp = smtp.get("smtp", smtp)  # same layout as .streamlit/secrets.toml, or top-level keys
    sender = smtp.get("sender") or smtp.get("user", "")

    out = Path(args.out)
    notify.save_drafts(messages, out, sender)
    print(f"Wrote {len(messages)} emails to {out}/ (one .eml per TA, plus emails.csv for a mail merge).")
    if not args.send:
        print("Check them, then rerun with --send --smtp smtp.toml to send them, or open the "
              ".eml files and send them from your email program.")
        return 0

    if not args.yes and input(f"Send {len(messages)} emails from {sender}? [y/N] ").strip().lower() != "y":
        print("Nothing sent.")
        return 1
    if "password" not in smtp:
        smtp["password"] = getpass.getpass(f"Password for {smtp['user']}: ")
    results = notify.send(messages, smtp, on_sent=lambda i, e: print(f"  {i + 1}/{len(messages)} {e.name}"))
    failed = {n: r for n, r in results.items() if r != "sent"}
    for n, r in failed.items():
        print(f"error: {n}: {r}", file=sys.stderr)
    print(f"Sent {len(results) - len(failed)} of {len(results)} emails.")
    return 1 if failed else 0


# -------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mlc_scheduler", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("google-form", help="write a script that creates the Google Form")
    g.add_argument("schedule", help="schedule grid CSV")
    g.add_argument("--roster", help="roster CSV (name, hours, email): TAs pick their name from a list")
    g.add_argument("--min-list-factor", type=float, default=2.0)
    g.add_argument("--choices", type=int, help="number of 'Choice' questions (default: enough for everyone)")
    g.add_argument("-o", "--output", default="create_form.gs")
    g.set_defaults(func=cmd_google_form)

    r = sub.add_parser("run", help="build the schedule")
    r.add_argument("schedule", help="schedule grid CSV (time x day -> TAs needed)")
    r.add_argument("preferences", help="preferences CSV or Google Forms responses CSV")
    r.add_argument("--roster", help="roster CSV: names and hours come from here")
    r.add_argument("--include-missing", action="store_true",
                   help="also schedule roster TAs who sent no preferences (they get leftover slots)")
    r.add_argument("--grievances", help="grievance points CSV (name, grievance)")
    r.add_argument("--mode", choices=MODES, default="none")
    r.add_argument("--tiers", type=int, nargs="*", default=[], help="hour thresholds, e.g. 8 4")
    r.add_argument("--weights", type=float, nargs="*", default=[], help="one weight per tier")
    r.add_argument("--rank-exponent", type=float, default=1.0)
    r.add_argument("--min-list-factor", type=float, default=2.0)
    r.add_argument("--max-per-day", type=int, default=None, help="max hours per day")
    r.add_argument("--max-in-a-row", type=int, default=None, help="max back-to-back hours")
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--out", default="results", help="output folder")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("emails", help="write (and optionally send) each TA's schedule email")
    e.add_argument("assignments", help="assignments.csv written by 'run'")
    e.add_argument("schedule", help="schedule grid CSV")
    e.add_argument("roster", help="roster CSV with an email column")
    e.add_argument("--subject", default=notify.DEFAULT_SUBJECT)
    e.add_argument("--message", help="text file with the message; {name}, {hours} and {schedule} are filled in")
    e.add_argument("--term", nargs=2, metavar=("FIRST", "LAST"),
                   help="attach a calendar file with weekly shifts between these dates (YYYY-MM-DD)")
    e.add_argument("--out", default="results/emails", help="folder for the email files")
    e.add_argument("--send", action="store_true", help="send the emails (needs --smtp)")
    e.add_argument("--smtp", default="smtp.toml", help="email account settings (see README)")
    e.add_argument("--yes", action="store_true", help="don't ask for confirmation before sending")
    e.set_defaults(func=cmd_emails)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
