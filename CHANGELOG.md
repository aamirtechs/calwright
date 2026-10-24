# Changelog

All notable changes to calwright are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `--prefer-duration` CLI flag to emit RFC 5545 `DURATION` properties instead of `DTEND` for timed events specified with a duration column.

## [1.0.0] - 2026-10-23

### Added
- CSV to ICS converter: reads event rows and emits a valid RFC 5545 `.ics` calendar.
- Flexible column names: `title`/`summary`, `start`, `end` or `duration`, `description`,
  `location`, `timezone`/`tzid`, `recurrence` (RRULE), `uid`, and an all-day flag.
- Flexible datetime parsing: ISO 8601, `2026-10-23 14:30`, US `10/23/2026 2:30 PM`,
  and date-only values that become all-day events.
- Timezone handling: per-row IANA TZID, `--default-tz` for naive datetimes,
  and `--utc` to normalize everything to UTC. VTIMEZONE blocks are generated
  from the system timezone database for every TZID used.
- Stable UID generation: rows without a UID get a deterministic UUID derived
  from title, start, and timezone.
- RRULE passthrough with grammar validation (FREQ required, COUNT/INTERVAL
  positive, UNTIL parseable, BYDAY values checked, COUNT and UNTIL conflict flagged).
- `--check` mode: audits the CSV and reports problems with row numbers
  (missing titles, unparseable dates, end before start, bad RRULE, unknown
  timezone). Exit code 0 when clean, 1 when problems are found.
- `--summary` mode: prints a human-readable preview of the parsed events.
- RFC 5545 text escaping and 75-octet line folding.
- Zero dependencies: Python standard library only (requires Python 3.9+).
- Test suite: 66 tests covering parsing, validation, rendering, and the CLI.
