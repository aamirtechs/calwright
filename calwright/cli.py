"""Command-line interface for calwright."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .converter import (
    build_events,
    format_check_report,
    format_summary,
    load_rows,
    render_ics,
    validate_events,
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="calwright",
        description="Convert a CSV file of events into an RFC 5545 .ics calendar, "
                    "or validate the CSV before converting.",
    )
    parser.add_argument("input", nargs="?",
                        help="CSV file to read (default: standard input)")
    parser.add_argument("-o", "--output",
                        help="Write the .ics calendar to this file "
                             "(default: standard output)")
    parser.add_argument("--check", "--validate", dest="check", action="store_true",
                        help="Audit the CSV and report problems without writing a calendar")
    parser.add_argument("--summary", action="store_true",
                        help="Print a human-readable preview of the events before writing")
    parser.add_argument("--default-tz", metavar="TZID",
                        help="IANA timezone applied to naive datetimes, "
                             "for example America/New_York")
    parser.add_argument("--utc", action="store_true",
                        help="Convert all event times to UTC (Z suffix) in the output")
    parser.add_argument("--prefer-duration", action="store_true",
                        help="Emit DURATION instead of DTEND for timed events given a duration column")
    parser.add_argument("--version", action="version", version="calwright %s" % __version__)
    return parser


def read_input(path):
    if path:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return handle.read()
    return sys.stdin.read()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        csv_text = read_input(args.input)
    except OSError as exc:
        print("calwright: cannot read input: %s" % exc, file=sys.stderr)
        return 2
    if not csv_text.strip():
        print("calwright: input is empty", file=sys.stderr)
        return 2

    try:
        rows = load_rows(csv_text)
    except ValueError as exc:
        print("calwright: %s" % exc, file=sys.stderr)
        return 2

    events = build_events(rows, default_tz=args.default_tz, convert_to_utc=args.utc)
    problems = validate_events(events)

    if args.check:
        print(format_check_report(problems, len(rows)))
        return 1 if problems else 0

    if problems:
        print(format_check_report(problems, len(rows)), file=sys.stderr)
        print("calwright: fix the problems above or run with --check for details",
              file=sys.stderr)
        return 2

    if args.summary:
        print(format_summary(events))

    ics_text = render_ics(events, prefer_duration=args.prefer_duration)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as handle:
                handle.write(ics_text)
        except OSError as exc:
            print("calwright: cannot write output: %s" % exc, file=sys.stderr)
            return 2
        print("calwright: wrote %d event(s) to %s" % (len(events), args.output))
    else:
        sys.stdout.write(ics_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
