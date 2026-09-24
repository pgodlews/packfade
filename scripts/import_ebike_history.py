"""Read-only USB import of e-bike history, with no year restriction.

Only activities containing traction battery telemetry are retained.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from garmin_fit_sdk import Decoder, Stream
from packfade.fit import has_battery_info


def main():
    root = Path(__file__).resolve().parent.parent
    archive = root / "data/ebike_history"
    raw = archive / "raw"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = archive / "inventory.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "ebike_only": True, "year_filter": None, "rides": {}, "rejected": {}, "excluded": {}}
    manifest.setdefault("rides", {})
    manifest.setdefault("rejected", {})
    manifest.setdefault("excluded", {})
    if manifest.get("ebike_only") is not True or manifest.get("year_filter") is not None:
        raise ValueError("Wrong archive policy")

    # Prune any prior rides that have no battery information in record_fields
    pruned = [k for k, v in manifest.get("rides", {}).items()
              if not any("battery" in f.lower() for f in v.get("record_fields", {}))]
    for k in pruned:
        del manifest["rides"][k]
        manifest["excluded"][k] = "no_battery_in_record_fields"
        raw_file = raw / f"{k}.fit"
        if raw_file.exists():
            raw_file.unlink(missing_ok=True)

    new_excluded = []
    new_rejected = []
    with tempfile.TemporaryDirectory(prefix=".usb-staging-", dir=archive) as stage:
        subprocess.run([str(root / "build/pull_edge"), stage], check=True)
        paths = sorted(Path(stage).glob("*.fit"))
        total = len(paths)
        print(f"Staged {total} files from Garmin Edge. Filtering for battery telemetry...", flush=True)
        for idx, path in enumerate(paths, 1):
            if idx % 15 == 0 or idx == total:
                print(f"Filtering activity files: {idx}/{total} inspected ({len(manifest['rides'])} battery rides kept, {len(new_excluded)} without battery excluded)...", flush=True)
            payload = path.read_bytes()
            key = hashlib.sha256(payload).hexdigest()
            if key in manifest["rides"]:
                continue
            if key in manifest["rejected"]:
                continue
            if key in manifest["excluded"]:
                continue
            with path.open("rb") as f:
                messages, errors = Decoder(Stream.from_buffered_reader(f)).read()
            if errors:
                manifest["rejected"][key] = f"decode_errors: {len(errors)}"
                new_rejected.append(key)
                continue
            if not has_battery_info(messages):
                manifest["excluded"][key] = "no_battery_info"
                new_excluded.append(key)
                continue
            records = [r for r in messages.get("record_mesgs", []) if isinstance(r.get("timestamp"), datetime)]
            if not records:
                manifest["rejected"][key] = "no_records_with_timestamp"
                new_rejected.append(key)
                continue
            records.sort(key=lambda r: r["timestamp"])
            fields = Counter(str(k) for r in records for k, v in r.items() if v is not None)
            target = raw / f"{key}.fit"
            if not target.exists():
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    file.write(payload)
            manifest["rides"][key] = {"date": records[0]["timestamp"].isoformat(),
                                      "duration_s": (records[-1]["timestamp"]-records[0]["timestamp"]).total_seconds(),
                                      "records": len(records), "record_fields": dict(fields)}
            manifest["rejected"].pop(key, None)
            manifest["excluded"].pop(key, None)
    manifest.update({
        "last_import_no_battery_excluded": len(new_excluded),
        "last_import_rejected": new_rejected,
    })
    temporary = archive / ".inventory.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(manifest_path)
    years = Counter(r["date"][:4] for r in manifest["rides"].values())
    fields = Counter()
    for ride in manifest["rides"].values():
        fields.update(ride["record_fields"].keys())
    print(json.dumps({"battery_rides": len(manifest["rides"]), "years": dict(years),
                      "rides_with_field": dict(fields), "no_battery_excluded": len(new_excluded),
                      "rejected": len(new_rejected)}, indent=2))
    if new_rejected:
        print(f"Warning: {len(new_rejected)} FIT file(s) failed decoding; recorded in inventory.json", flush=True)


if __name__ == "__main__":
    main()

