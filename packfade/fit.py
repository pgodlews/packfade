"""FIT decoding with a strict allowlist for derived data."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re

import numpy as np
from garmin_fit_sdk import Decoder, Stream

CHANNELS = ("heart_rate", "cadence", "power", "speed", "altitude", "distance")


def is_ebike(messages):
    """Conservatively exclude explicit FIT e-bike tags, profiles or telemetry."""
    metadata = messages.get("session_mesgs", []) + messages.get("sport_mesgs", [])
    for record in metadata:
        if str(record.get("sport", "")).startswith("e_bik") or str(record.get("sub_sport", "")).startswith("e_bike"):
            return True
        name = str(record.get("sport_profile_name", record.get("name", "")))
        if re.search(r"(?i)(?:\be[\s_-]*(?:bike|biking|mtb)\b|\belectric\b)", name):
            return True
    return any(any(str(k).startswith("ebike_") and v is not None for k, v in r.items())
               for r in messages.get("record_mesgs", []))


def has_battery_info(messages):
    """Check if record messages contain battery information (SOC/level).

    Filters purely on the presence of battery data, regardless of whether
    the activity is tagged with an e-bike sport or profile.
    """
    for r in messages.get("record_mesgs", []):
        for k, v in r.items():
            if "battery" in str(k).lower() and v is not None:
                if isinstance(v, (int, float)) and np.isfinite(v):
                    return True
                elif isinstance(v, str) and v.strip():
                    return True
    return False


def exclusion(summary, year=None):
    if year is not None and int(summary["date"][:4]) != year:
        return "outside_year"
    if not summary.get("has_battery") and not summary.get("ebike"):
        return "no_battery_or_ebike_telemetry"
    return None


def number(value):
    if isinstance(value, (int, float)) and np.isfinite(value):
        return float(value)
    return np.nan


def decode(path):
    # Reject corrupt/truncated files rather than using partial decoder output.
    with Path(path).open("rb") as file:
        messages, errors = Decoder(Stream.from_buffered_reader(file)).read()
    if errors:
        raise ValueError(f"FIT decode failed ({len(errors)} errors)")
    records = messages.get("record_mesgs", [])
    records = [r for r in records if isinstance(r.get("timestamp"), datetime)]
    if not records:
        raise ValueError("No timestamped FIT records")
    records.sort(key=lambda r: r["timestamp"])
    start = records[0]["timestamp"]
    arrays = {"time": np.array([(r["timestamp"] - start).total_seconds() for r in records])}
    for channel in CHANNELS:
        arrays[channel] = np.array([number(r.get("enhanced_" + channel, r.get(channel))) for r in records])
    # Negative and invalid sensor values are missing; genuine zero watts/cadence stay zero.
    for channel in ("heart_rate", "cadence", "power", "speed", "distance"):
        values = arrays[channel]
        values[values < (1 if channel == "heart_rate" else 0)] = np.nan
    # Keep timer state so pauses do not become calibration or lag samples.
    timer = sorted(
        [e for e in messages.get("event_mesgs", [])
         if e.get("event") == "timer" and isinstance(e.get("timestamp"), datetime)],
        key=lambda e: e["timestamp"],
    )
    active = np.ones(len(records), dtype=bool)
    running, event_index = True, 0
    for i, record in enumerate(records):
        while event_index < len(timer) and timer[event_index]["timestamp"] <= record["timestamp"]:
            kind = timer[event_index].get("event_type")
            if kind == "start":
                running = True
            elif kind in ("stop", "stop_all", "stop_disable", "stop_disable_all"):
                running = False
            event_index += 1
        active[i] = running
    arrays["active"] = active
    sessions = messages.get("session_mesgs", [])
    sports = sorted({str(s.get("sport", "unknown")) for s in sessions})
    sub_sports = sorted({str(s.get("sub_sport", "unknown")) for s in sessions})
    valid = np.isfinite(arrays["heart_rate"]) & np.isfinite(arrays["cadence"]) & np.isfinite(arrays["power"]) & active
    summary = {
        "date": start.date().isoformat(), "records": len(records),
        "duration_s": float(arrays["time"][-1]), "sports": sports, "sub_sports": sub_sports,
        "indoor": "indoor_cycling" in sub_sports,
        "cycling_only": sports == ["cycling"] and len(sessions) == 1,
        "ebike": is_ebike(messages),
        "has_battery": has_battery_info(messages),
        "channels": {c: int(np.isfinite(arrays[c]).sum()) for c in CHANNELS},
        "paired_records": int(valid.sum()),
        "positive_power_records": int(np.sum(arrays["power"] > 0)),
    }
    return arrays, summary


def import_files(source: Path, root: Path, year: int | None = None):
    raw, processed = root / "raw", root / "processed"
    for folder in (root, raw, processed):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".fit")
    if not paths:
        raise ValueError("No FIT files found")
    manifest_path = root / "inventory.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"year": year, "require_battery": True, "rides": {}, "rejected": {}, "excluded": {}}
    if manifest.get("year") != year:
        raise ValueError("Use a fresh data directory when changing the dataset policy")
    imported = 0
    for path in paths:
        payload = path.read_bytes()
        ride_id = hashlib.sha256(payload).hexdigest()
        if ride_id in manifest["rides"]:
            continue
        try:
            arrays, summary = decode(path)
        except (ValueError, OSError) as error:
            manifest["rejected"][ride_id] = str(error)
            continue
        reason = exclusion(summary, year)
        if reason:
            manifest["excluded"][ride_id] = reason
            continue
        target = raw / f"{ride_id}.fit"
        if not target.exists():
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != ride_id:
            raise ValueError("Archived FIT hash mismatch")
        temporary = processed / f".{ride_id}.npz"
        np.savez_compressed(temporary, **arrays)
        temporary.replace(processed / f"{ride_id}.npz")
        manifest["rides"][ride_id] = summary
        manifest["rejected"].pop(ride_id, None)
        imported += 1
        if imported % 20 == 0:
            print(f"Decoded {imported} activities", flush=True)
    temporary = root / ".inventory.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(manifest_path)
    if not manifest["rides"]:
        raise ValueError("No usable activities decoded; see data/inventory.json")
    return imported, manifest
