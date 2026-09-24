"""Copy Garmin activities into temporary staging, then retain only eligible rides."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from packfade.fit import import_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=None, help="Optional year filter (default: all years)")
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    helper = Path(__file__).resolve().parent.parent / "build" / "pull_edge"
    args.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Staging belongs to this process. The device is never changed. Only files
    # admitted by the battery telemetry policy enter the persistent archive.
    try:
        with tempfile.TemporaryDirectory(prefix=".usb-staging-", dir=args.data) as staging:
            subprocess.run([str(helper), staging], check=True)
            count, manifest = import_files(Path(staging), args.data, args.year)
        print(f"Imported {count} new eligible rides; archive now contains {len(manifest['rides'])} rides")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Import failed: {error}\n")


if __name__ == "__main__":
    main()
