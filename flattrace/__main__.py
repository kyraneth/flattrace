# SPDX-License-Identifier: MIT
import argparse
import sys

from .check import read_trace, read_events, check_events
from .export import export
from .stats import analyse, format_text
import json


def main():
    parser = argparse.ArgumentParser(description="Local flattrace check/export/stats")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("files", nargs="+")
    check.add_argument("--events", help="tool-owned event list")
    check.add_argument("--command", dest="command_id", help="explicit command selector, including for legacy traces")
    copy = commands.add_parser("export")
    copy.add_argument("files", nargs="+")
    copy.add_argument("--out", required=True, help="new output directory")
    for kind in ("path", "host", "user"):
        copy.add_argument("--" + kind, action="append", default=[])
    stats = commands.add_parser("stats", help="stream trace corpus statistics")
    stats.add_argument("files", nargs="+", help="trace files or recursive directories")
    stats.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args()
    try:
        if args.command == "stats":
            report = analyse(args.files)
            print(json.dumps(report, ensure_ascii=False, allow_nan=False) if args.json else format_text(report))
            return int(bool(report["invalid_files"]) or any(run["issues"] for group in ("runs", "duplicates") for run in report[group]))
        if args.command == "export":
            issues = export(args.files, args.out, paths=args.path, hosts=args.host, users=args.user)
            print("\n".join(issues) if issues else f"exported to {args.out}")
            return int(bool(issues))
        contract = read_events(args.events) if args.events else None
        failed = False
        for path in args.files:
            rows, issues = read_trace(path)
            if contract is not None:
                issues.extend(check_events(rows, contract, args.command_id))
            print(f"{path}: " + ("; ".join(issues) if issues else f"{len(rows)} valid events"))
            failed |= bool(issues)
        return int(failed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
