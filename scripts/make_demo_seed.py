"""Anonymise the demo seed (web/seed_data.js) before publishing it.

Every recording is moved to 00:00 UTC on a new date up to +-3 days from the
original (the first ride keeps its date, so the demo baseline stays fixed).
Rides keep their order and never share a day, so no two recordings overlap;
recording ids become ride_001, ride_002, ...
Durations, SOC readings and per-ride measurements are unchanged. Models,
quarterly medians and charging gaps are recomputed from the new dates with the
same code as the analysis scripts.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/make_demo_seed.py          # rewrite the seed
    PYTHONPATH=. .venv/bin/python scripts/make_demo_seed.py --check  # verify only
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "web/seed_data.js"
PREFIX = "window.SEED_EBIKE_DATA = "
MAX_SHIFT_DAYS = 3
TIME_KEYS = ("record_start", "record_end", "start_observed_at", "end_observed_at")


def load(path=SEED):
    text = path.read_text()
    if not text.startswith(PREFIX):
        raise ValueError(f"{path} does not start with {PREFIX!r}")
    return json.loads(text[len(PREFIX):].rstrip().rstrip(";"))


def save(seed, path=SEED):
    path.write_text(PREFIX + json.dumps(seed, allow_nan=False) + ";\n")


def jitter_days(days, max_shift=MAX_SHIFT_DAYS, rand=secrets.SystemRandom()):
    """Strictly increasing dates, each within +-max_shift of the original where possible."""
    step = timedelta(days=1)
    latest = [None] * len(days)
    for i in reversed(range(len(days))):
        latest[i] = days[i] + max_shift * step
        if i + 1 < len(days):
            latest[i] = min(latest[i], latest[i + 1] - step)
    result = []
    for i, day in enumerate(days):
        if not result:
            result.append(day)
            continue
        lo = max(day - max_shift * step, result[-1] + step)
        hi = max(lo, latest[i])
        result.append(lo + rand.randint(0, (hi - lo).days) * step)
    return result


def anonymise(seed):
    # Imported here so --check (used in CI) needs only the standard library.
    from scripts.analyze_ebike_battery import build_models, quarterly_summary
    from scripts.analyze_ebike_charging import summarize

    rides, endpoints = seed["analysis"]["per_ride"], seed["charging"]["endpoints"]
    starts = [datetime.fromisoformat(r["date"]) for r in rides]
    days = jitter_days([s.date() for s in starts])
    moved = {r["date"]: datetime(d.year, d.month, d.day, tzinfo=timezone.utc) for r, d in zip(rides, days)}
    if len(moved) != len(rides) or {e["record_start"] for e in endpoints} != set(moved):
        raise ValueError("per_ride dates and charging endpoints do not describe the same recordings")
    for i, e in enumerate(endpoints, 1):
        e["id"] = f"ride_{i:03d}"  # never publish FIT content hashes
        old, new = datetime.fromisoformat(e["record_start"]), moved[e["record_start"]]
        for key in TIME_KEYS:
            if e.get(key):
                e[key] = (new + (datetime.fromisoformat(e[key]) - old)).isoformat()
    for r in rides:
        r["date"] = moved[r["date"]].isoformat()

    analysis, valid = seed["analysis"], [r for r in rides if not r["reasons"]]
    origin, end = datetime.fromisoformat(rides[0]["date"]), datetime.fromisoformat(valid[-1]["date"])
    analysis.update({"baseline_date": origin.date().isoformat(), "latest_date": end.date().isoformat(),
                     "latest_recording_date": rides[-1]["date"][:10],
                     "quarterly": quarterly_summary(valid), "models": build_models(valid, origin, end)})
    seed["charging"] = summarize(endpoints)
    shifts = [(d - s.date()).days for d, s in zip(days, starts)]
    return min(shifts), max(shifts)


def problems(seed):
    """Reasons the seed is not safe to publish (empty list when it is)."""
    found = []
    rides, endpoints = seed["analysis"]["per_ride"], seed["charging"]["endpoints"]
    for r in rides:
        if not r["date"].endswith("T00:00:00+00:00"):
            found.append(f"ride {r['date']} does not start at 00:00 UTC")
    dates = [r["date"][:10] for r in rides]
    if len(set(dates)) != len(dates):
        found.append("two or more rides share a date")
    if dates != sorted(dates):
        found.append("rides are not in date order")
    if any(not re.fullmatch(r"ride_\d{3}", e["id"]) for e in endpoints):
        found.append("endpoint ids are not anonymous ride_NNN labels")
    for a, b in zip(endpoints, endpoints[1:]):
        if a["record_end"] >= b["record_start"]:
            found.append(f"recording {a['id']} overlaps {b['id']}")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only verify the current seed")
    args = parser.parse_args()
    seed = load()
    if not args.check:
        lo, hi = anonymise(seed)
        issues = problems(seed)
        if issues:
            raise SystemExit("Refusing to write seed:\n  " + "\n  ".join(issues))
        save(seed)
        print(f"Rewrote {SEED.relative_to(ROOT)}: {len(seed['analysis']['per_ride'])} rides shifted {lo:+d}..{hi:+d} days")
    issues = problems(seed)
    if issues:
        raise SystemExit("Seed is not anonymised:\n  " + "\n  ".join(issues))
    print("Seed OK: all rides at 00:00 UTC on distinct, ordered dates with no overlapping recordings")


if __name__ == "__main__":
    main()
