"""Storage for the preference form: a folder of plain files.

    <folder>/form_schedule.csv   the schedule TAs choose from (absent = form closed)
    <folder>/form_settings.json  e.g. the minimum list length
    <folder>/submissions.json    one entry per TA; resubmitting replaces it
    <folder>/roster.json         optional: who works at the MLC, hours, email

The folder defaults to data/ in the repository and can be changed with the MLC_DATA_DIR
environment variable. Swapping this class for e.g. a database or Google
Sheets backend only requires keeping the same methods.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .models import name_key as key

_lock = threading.Lock()  # the app serves every TA from one process
DEFAULT_FOLDER = Path(__file__).resolve().parent.parent / "data"


class FormStore:
    def __init__(self, folder: str | Path | None = None):
        self.folder = Path(folder or os.environ.get("MLC_DATA_DIR", DEFAULT_FOLDER))

    def _path(self, name: str) -> Path:
        return self.folder / name

    def _write(self, name: str, text: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self._path(name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path(name))  # atomic: readers never see half a file

    # ------------------------------------------------------------ the form

    def open_form(self, schedule_csv: str, min_list_factor: float) -> None:
        with _lock:
            self._write("form_schedule.csv", schedule_csv)
            self._write("form_settings.json", json.dumps({"min_list_factor": min_list_factor}))

    def close_form(self) -> None:
        with _lock:
            self._path("form_schedule.csv").unlink(missing_ok=True)

    def schedule_path(self) -> Path | None:
        path = self._path("form_schedule.csv")
        return path if path.exists() else None

    def settings(self) -> dict:
        path = self._path("form_settings.json")
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return {"min_list_factor": 2.0, **saved}

    # --------------------------------------------------------------- roster

    def roster(self) -> list[dict]:
        path = self._path("roster.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def set_roster(self, roster: list[dict]) -> None:
        with _lock:
            self._write("roster.json", json.dumps(roster, indent=1))

    def clear_roster(self) -> None:
        with _lock:
            self._path("roster.json").unlink(missing_ok=True)

    # ---------------------------------------------------------- submissions

    def submissions(self) -> list[dict]:
        path = self._path("submissions.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def get(self, name: str) -> dict | None:
        return next((s for s in self.submissions() if key(s["name"]) == key(name)), None)

    def save(self, name: str, hours: int, unavailable: str, preferences: list[str]) -> None:
        entry = {
            "name": " ".join(name.split()),
            "hours": hours,
            "unavailable": unavailable,
            "preferences": preferences,
            "submitted": datetime.now().isoformat(timespec="seconds"),
        }
        with _lock:
            rest = [s for s in self.submissions() if key(s["name"]) != key(name)]
            self._write("submissions.json", json.dumps(rest + [entry], indent=1))

    def delete(self, name: str) -> None:
        with _lock:
            rest = [s for s in self.submissions() if key(s["name"]) != key(name)]
            self._write("submissions.json", json.dumps(rest, indent=1))

    def to_csv(self, include_missing: bool = False) -> str:
        """All submissions in the preferences-CSV format the scheduler reads.

        With a roster, names and hours come from the roster, and
        `include_missing` adds roster TAs who never submitted (with no
        preferences, so they get whatever slots are left).
        """
        roster = {key(r["name"]): r for r in self.roster()}
        subs = self.submissions()
        rows = []
        for s in subs:
            r = roster.get(key(s["name"]), s)
            rows.append([r["name"], r["hours"], s["unavailable"], s["submitted"], *s["preferences"]])
        if include_missing:
            done = {key(s["name"]) for s in subs}
            rows += [[r["name"], r["hours"], "", ""] for k, r in roster.items() if k not in done]

        width = max((len(r) - 4 for r in rows), default=0)
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["name", "hours", "unavailable", "submitted", *(f"pref{i + 1}" for i in range(width))])
        w.writerows(rows)
        return out.getvalue()
