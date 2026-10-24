# calwright

[![GitHub stars](https://img.shields.io/github/stars/aamirtechs/calwright?style=social)](https://github.com/aamirtechs/calwright/stargazers) [![GitHub forks](https://img.shields.io/github/forks/aamirtechs/calwright?style=social)](https://github.com/aamirtechs/calwright/network/members) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/calwright/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/calwright/issues)

A zero-dependency Python CLI that turns a CSV file into a valid RFC 5545
`.ics` calendar, with a built-in validator that audits your CSV first.

Spreadsheets, exports from CRMs, scraped schedules, and hand-rolled event
lists all live as CSV. Getting them into Google Calendar, Outlook, or Apple
Calendar usually means pasting rows one by one or trusting a flaky online
converter with your data. calwright converts locally, validates the input,
and emits a calendar file that imports cleanly.

## Install

Requires Python 3.9 or newer. No dependencies beyond the standard library.

```bash
pip install calwright
```

Or run from source without installing:

```bash
git clone https://github.com/aamirtechs/calwright
cd calwright
python -m calwright.cli events.csv -o events.ics
```

## Quick start

Create `events.csv`:

```csv
title,start,end,description,location,timezone,recurrence,all_day
Team standup,2026-10-26 09:00,2026-10-26 09:30,Daily sync with the remote crew,Zoom,America/New_York,FREQ=DAILY,
Sprint planning,2026-10-28 14:00,2026-10-28 15:30,"Plan next sprint; bring estimates, blockers",Meeting room 3,Europe/London,FREQ=WEEKLY;BYDAY=WE,
Release day,2026-11-01,,Ship v2.0 to production,,,,yes
Dentist appointment,10/29/2026 3:00 PM,,Routine checkup,Smile Clinic,America/Chicago,,
```

Convert it:

```bash
calwright events.csv -o events.ics
```

Preview what will be written before committing:

```bash
calwright events.csv --summary -o events.ics
```

Real output from the sample above:

```
calwright preview: 4 event(s)
  - Team standup | 2026-10-26T09:00:00-04:00 to 2026-10-26T09:30:00-04:00 | Zoom | repeats: FREQ=DAILY
  - Sprint planning | 2026-10-28T14:00:00+00:00 to 2026-10-28T15:30:00+00:00 | Meeting room 3 | repeats: FREQ=WEEKLY;BYDAY=WE
  - Release day | 2026-11-01 to 2026-11-01 (all day)
  - Dentist appointment | 2026-10-29T15:00:00-05:00 to 2026-10-29T16:00:00-05:00 | Smile Clinic
calwright: wrote 4 event(s) to events.ics
```

The generated `events.ics` (trimmed; DTSTAMP values vary per run):

```
BEGIN:VCALENDAR
PRODID:-//calwright//calwright 1.0.0//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20271107T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20270314T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
END:VTIMEZONE
... (one VTIMEZONE block per TZID used: America/Chicago, Europe/London) ...
BEGIN:VEVENT
UID:71c62dab-9274-5d5f-bd85-224b7f7be135@calwright
DTSTAMP:20261023T054218Z
SUMMARY:Team standup
DTSTART;TZID=America/New_York:20261026T090000
DTEND;TZID=America/New_York:20261026T093000
DESCRIPTION:Daily sync with the remote crew
LOCATION:Zoom
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12db2a5a-84c1-5d11-a5b6-b17f14da1661@calwright
DTSTAMP:20261023T054218Z
SUMMARY:Release day
DTSTART;VALUE=DATE:20261101
DTEND;VALUE=DATE:20261102
DESCRIPTION:Ship v2.0 to production
END:VEVENT
... (Sprint planning and Dentist appointment VEVENTs) ...
END:VCALENDAR
```

Validate a CSV without writing anything:

```bash
calwright messy.csv --check
```

Real output on a broken file:

```
calwright check: 2 row(s) examined
FOUND 4 problem(s):
  row 2: missing title
  row 2: unparseable start: unrecognized datetime format: 'not-a-date'
  row 2: bad RRULE: RRULE has invalid FREQ 'SOMETIMES'
  row 3: end is before start
```

Exit codes: `0` means clean (or a successful conversion), `1` means `--check`
found problems, `2` means a usage or conversion error.

## CSV columns

Column names are matched flexibly (case-insensitive, common aliases accepted):

| Column | Aliases | Notes |
|---|---|---|
| `title` | summary, event, name, subject | Required. |
| `start` | start_time, begin, dtstart, date | Required. See datetime formats below. |
| `end` | end_time, finish, dtend | Optional. Defaults to start + 1 hour. |
| `duration` | length | Optional alternative to `end`. ISO 8601 (`PT1H30M`), `2h30m`, or bare minutes (`90`). |
| `description` | desc, details, notes | Optional. Escaped per RFC 5545. |
| `location` | place, venue, where | Optional. |
| `timezone` | tzid, tz | Optional. IANA name like `America/New_York`. |
| `recurrence` | rrule, repeat | Optional. RRULE string, grammar-validated. |
| `uid` | id, event_id | Optional. A stable UUID is generated when missing. |
| `all_day` | all-day, full_day | Optional. Truthy values (`yes`, `true`, `1`) make an all-day event. Date-only starts become all-day automatically. |

Unknown columns are ignored. The delimiter is auto-detected (comma, tab,
semicolon, pipe). UTF-8 with BOM is handled.

## Datetime formats

`2026-10-23T14:30:00`, `2026-10-23 14:30`, `2026-10-23 14:30:00+0200`,
`10/23/2026 2:30 PM`, `Oct 23, 2026`, `2026-10-23`, and more. Values with no
time component become all-day events.

Naive datetimes are floating by default. Use `--default-tz America/New_York`
to assign a timezone, or `--utc` to normalize everything to UTC (`Z` suffix).

## Usage

```
calwright [-h] [-o OUTPUT] [--check] [--summary] [--default-tz TZID] [--utc] [--from-json] [--version] [input]
```

- `input`: CSV file, or omit to read from standard input.
- `--from-json`: treat the input as a JSON array of objects with the same canonical field names (`title`, `start`, `end`, ...) instead of CSV — handy for pipelines that already emit JSON:

  ```bash
  curl -s https://api.example.com/events | calwright --from-json -o schedule.ics
  ```
- `-o`, `--output`: write the `.ics` here instead of standard output.
- `--check`: audit the CSV and report problems with row numbers. Writes nothing.
- `--summary`: print a human-readable preview of the events before writing.
- `--default-tz`: IANA timezone applied to naive datetimes.
- `--utc`: convert all event times to UTC in the output.

Pipe-friendly:

```bash
cat events.csv | calwright --default-tz Asia/Karachi > events.ics
```

## What the output contains

- `VCALENDAR` with `PRODID` `-//calwright//calwright 1.0.0//EN`.
- One `VTIMEZONE` block per TZID used, generated from the system timezone
  database with real STANDARD/DAYLIGHT transition rules.
- One `VEVENT` per valid row: stable `UID`, `DTSTAMP`, `SUMMARY`, `DESCRIPTION`
  and `LOCATION` (RFC 5545 escaped), `DTSTART`/`DTEND` with `TZID`, UTC `Z`
  suffix, or `VALUE=DATE` for all-day events, and validated `RRULE`.
- Lines folded at 75 octets per RFC 5545 section 3.1.

## Development

```bash
python -m unittest discover -s tests   # 66 tests, all passing
```

The suite covers datetime parsing, RRULE validation, text escaping and line
folding, event building, VTIMEZONE generation, ICS rendering (including a
structural parse of the generated calendar), validation reporting, and the CLI.

## Contributing

See [GOOD_FIRST_ISSUES.md](GOOD_FIRST_ISSUES.md) for scoped starter tasks.

## License

MIT. See [LICENSE](LICENSE).
