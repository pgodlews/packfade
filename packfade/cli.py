"""Packfade CLI entry point."""
import argparse
import os
import sys
from pathlib import Path


from packfade import __version__


def main():
    parser = argparse.ArgumentParser(
        prog="packfade",
        description="Packfade: Local-first e-bike pack fade from Garmin FIT files."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Launch the local web dashboard & USB bridge")
    serve_parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")

    # analyze command
    subparsers.add_parser("analyze", help="Run multi-covariate Huber-WLS battery degradation analysis")

    # charging command
    subparsers.add_parser("charging", help="Analyze inter-ride charging habits and idle storage sag")

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if args.command == "serve" or args.command is None:
        port = getattr(args, "port", 8080)
        from scripts.serve_web import run_server
        print(f"Starting Packfade Studio on http://localhost:{port} ...")
        run_server(port=port)
    elif args.command == "analyze":
        from scripts.analyze_ebike_battery import main as run_analysis
        run_analysis()
    elif args.command == "charging":
        from scripts.analyze_ebike_charging import main as run_charging
        run_charging()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
