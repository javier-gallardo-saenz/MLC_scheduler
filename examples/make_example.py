"""Generate a realistic random example (schedule, preferences, grievances, roster).

    python examples/make_example.py [n_participants] [seed]
"""
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
TIMES = [f"{h}:{m}" for h in range(12, 19) for m in ("00", "30")][:-1]  # 12:00 ... 18:00


def main(n: int = 40, seed: int = 0) -> None:
    rng = random.Random(seed)

    # demand: more TAs on the hour, fewer on Friday afternoons
    needed = {}
    for d in DAYS:
        for t in TIMES:
            base = 3 if t.endswith(":00") else 1
            if d == "Fri" and t >= "15":
                base -= 1
            needed[d, t] = max(base, 0)
    capacity = sum(needed.values())

    # hours per participant, adjusted so they exactly fill the schedule
    hours = [rng.choice([2, 2, 3, 3, 4, 4, 6, 8]) for _ in range(n)]
    i = 0
    while sum(hours) != capacity:
        step = 1 if sum(hours) < capacity else -1
        if 1 <= hours[i % n] + step <= 10:
            hours[i % n] += step
        i += 1

    # popularity: early afternoon Mon-Thu is in demand, Friday late is not
    slots = [(d, t) for d in DAYS for t in TIMES if needed[d, t]]
    popularity = {
        (d, t): (3.0 if "13" <= t < "16" else 1.0) * (0.4 if d == "Fri" else 1.0)
        for d, t in slots
    }

    with open(HERE / "schedule.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", *DAYS])
        for t in TIMES:
            w.writerow([t, *(needed[d, t] for d in DAYS)])

    def minutes(t):
        hh, mm = t.split(":")
        return int(hh) * 60 + int(mm)

    rows = []
    for k, h in enumerate(hours):
        # about half the TAs have a class or two, a few have a whole day off
        busy, blocked = [], set()
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            d, a = rng.choice(DAYS), rng.choice([12, 13, 14, 15, 16, 17]) * 60
            busy.append(f"{d} {a // 60}:00-{(a + 90) // 60}:{(a + 90) % 60:02d}")
            blocked |= {(d, t) for t in TIMES if minutes(t) < a + 90 and a < minutes(t) + 60}
        if rng.random() < 0.1:
            d = rng.choice(DAYS)
            busy.append(d)
            blocked |= {(d, t) for t in TIMES}

        pool, prefs = [s for s in slots if s not in blocked], []
        length = min(len(pool), rng.randint(2 * h, 3 * h + 2))
        for _ in range(length):  # weighted sampling without replacement
            pick = rng.choices(pool, weights=[popularity[s] for s in pool])[0]
            pool.remove(pick)
            prefs.append(f"{pick[0]} {pick[1]}")
        rows.append([f"TA{k + 1:02d}", h, "; ".join(busy), *prefs])
    width = max(len(r) for r in rows) - 3
    with open(HERE / "preferences.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "hours", "unavailable", *(f"pref{i + 1}" for i in range(width))])
        w.writerows(rows)

    with open(HERE / "grievances.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "grievance"])
        for k in range(n):
            w.writerow([f"TA{k + 1:02d}", rng.choice([0, 0, 0, 1, 2])])

    with open(HERE / "roster.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "hours", "email"])
        for k, h in enumerate(hours):
            w.writerow([f"TA{k + 1:02d}", h, f"ta{k + 1:02d}@example.com"])

    print(f"{n} participants, {capacity} TA-slots -> {HERE}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
