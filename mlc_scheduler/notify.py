"""Telling each TA their schedule: email text, calendar files and sending.

Sending uses any SMTP account (e.g. a UBC or Gmail address with an app
password), configured as a dict with host, port, user, password and
optionally sender (defaults to user). Port 465 uses SSL; anything else
(usually 587) uses STARTTLS.
"""
from __future__ import annotations

import csv
import smtplib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from .models import Schedule, Slot, fmt_time, name_key

DEFAULT_SUBJECT = "Your MLC schedule"
DEFAULT_BODY = """Hi {name},

Here is your Math Learning Centre schedule ({hours} hours per week):

{schedule}

Thanks,
The MLC team"""

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Email:
    name: str
    to: str
    subject: str
    body: str
    calendar: str | None = None  # .ics file contents


def schedule_lines(schedule: Schedule, slots: list[Slot]) -> str:
    order = {d: i for i, d in enumerate(schedule.days)}
    return "\n".join(
        f"  {s.day} {fmt_time(s.start)}-{fmt_time(s.end)}"
        for s in sorted(slots, key=lambda s: (order[s.day], s.start))
    )


def fill(template: str, name: str, slots: list[Slot], schedule: Schedule) -> str:
    """Replace {name}, {hours} and {schedule} (other braces are left alone)."""
    return (template.replace("{name}", name)
                    .replace("{hours}", str(len(slots)))
                    .replace("{schedule}", schedule_lines(schedule, slots)))


def calendar(slots: list[Slot], first_day: date, last_day: date, title: str = "MLC shift") -> str:
    """An .ics file with one weekly repeating event per slot, between the two dates.

    Times are 'floating' (no time zone), i.e. read in the TA's local time.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MLC Scheduler//EN"]
    for s in slots:
        weekday = WEEKDAYS.index(s.day.lower()[:3])  # ValueError for non-weekday names
        day = first_day + timedelta(days=(weekday - first_day.weekday()) % 7)
        if day > last_day:
            continue
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=s.start)
        end = start + timedelta(minutes=s.end - s.start)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@mlc-scheduler",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            f"RRULE:FREQ=WEEKLY;UNTIL={last_day:%Y%m%d}T235959",
            f"SUMMARY:{title}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def compose(
    schedule: Schedule,
    assignment: dict[str, list[Slot]],
    emails: dict[str, str],
    subject: str = DEFAULT_SUBJECT,
    body: str = DEFAULT_BODY,
    term: tuple[date, date] | None = None,
) -> list[Email]:
    """One Email per scheduled TA who has an address in `emails` (name -> address)."""
    address = {name_key(n): e for n, e in emails.items() if e}
    out = []
    for name, slots in assignment.items():
        if name_key(name) in address:
            out.append(Email(
                name=name,
                to=address[name_key(name)],
                subject=fill(subject, name, slots, schedule),
                body=fill(body, name, slots, schedule),
                calendar=calendar(slots, *term) if term else None,
            ))
    return out


def to_message(e: Email, sender: str = "") -> EmailMessage:
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"], msg["Subject"] = e.to, e.subject
    msg.set_content(e.body)
    if e.calendar:
        msg.add_attachment(e.calendar.encode(), maintype="text", subtype="calendar",
                           filename="mlc_schedule.ics")
    return msg


def save_drafts(emails: list[Email], folder: Path, sender: str = "") -> None:
    """Writes each email as an .eml file (opens in Outlook, Apple Mail, Thunderbird)
    plus emails.csv (name, email, subject, body) for a mail merge."""
    folder.mkdir(parents=True, exist_ok=True)
    for e in emails:
        safe = "".join(c if c.isalnum() else "_" for c in e.name)
        msg = to_message(e, sender)
        msg["X-Unsent"] = "1"  # Outlook opens it as a draft, ready to send
        (folder / f"{safe}.eml").write_bytes(bytes(msg))
    with open(folder / "emails.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "subject", "body"])
        w.writerows([e.name, e.to, e.subject, e.body] for e in emails)


def send(emails: list[Email], smtp: dict, on_sent=None) -> dict[str, str]:
    """Sends every email over one SMTP connection. Returns name -> "sent" or the error.

    `on_sent(i, email)` is called after each attempt (e.g. to update a progress bar).
    """
    host, port = smtp["host"], int(smtp.get("port", 587))
    sender = smtp.get("sender") or smtp["user"]
    results = {}
    server = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
    with server:
        if port != 465:
            server.starttls()
        server.login(smtp["user"], smtp["password"])
        for i, e in enumerate(emails):
            try:
                server.send_message(to_message(e, sender))
                results[e.name] = "sent"
            except smtplib.SMTPException as err:
                results[e.name] = f"failed: {err}"
            if on_sent:
                on_sent(i, e)
    return results
