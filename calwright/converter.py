"""Core conversion logic for calwright: CSV rows to RFC 5545 iCalendar text.

Everything here uses the Python standard library only.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRODID = "-//calwright//calwright 1.0.0//EN"

# ---------------------------------------------------------------------------
# Column aliases
# ---------------------------------------------------------------------------

COLUMN_ALIASES = {
    "title": {"title", "summary", "event", "name", "subject"},
    "start": {"start", "start_time", "starttime", "begin", "dtstart", "date"},
    "end": {"end", "end_time", "endtime", "finish", "dtend"},
    "duration": {"duration", "length"},
    "description": {"description", "desc", "details", "notes"},
    "location": {"location", "place", "venue", "where"},
    "timezone": {"timezone", "tzid", "tz", "time_zone"},
    "recurrence": {"recurrence", "rrule", "repeat", "repeats"},
    "uid": {"uid", "id", "event_id"},
    "allday": {"allday", "all_day", "all-day", "full_day", "fullday"},
    "exdate": {"exdate", "exception", "exceptions", "exception_date", "exclude", "skip"},
}


def normalize_headers(headers):
    """Map raw CSV header names to canonical field names."""
    mapping = {}
    for header in headers:
        key = header.strip().lower().replace(" ", "_")
        canonical = None
        for canon, aliases in COLUMN_ALIASES.items():
            if key in aliases:
                canonical = canon
                break
        mapping[header] = canonical
    return mapping


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%m-%d-%Y %H:%M",
    "%m-%d-%Y",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%b %d, %Y %H:%M",
    "%b %d, %Y",
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%Y-%m-%d",
    "%Y%m%dT%H%M%S%z",
    "%Y%m%dT%H%M%S",
    "%Y%m%d",
]

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{4}$")


def parse_datetime(value):
    """Parse a datetime string. Returns (datetime, is_date_only)."""
    value = value.strip()
    if not value:
        raise ValueError("empty datetime")
    # Normalize a trailing Z to +0000 and strip fractional seconds beyond 6 digits.
    candidate = value
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+0000"
    candidate = re.sub(r"(\.\d{6})\d+", r"\1", candidate)
    for fmt in _DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt)
            date_only = "%H" not in fmt and "%I" not in fmt
            return parsed, date_only
        except ValueError:
            continue
    raise ValueError("unrecognized datetime format: %r" % value)

def split_date_list(text):
    """Split comma-separated dates, preserving dates with internal commas like 'Nov 02, 2026'."""
    tokens = [part.strip() for part in text.split(",") if part.strip()]
    items = []
    i = 0
    while i < len(tokens):
        candidate = tokens[i]
        try:
            parse_datetime(candidate)
            items.append(candidate)
            i += 1
            continue
        except ValueError:
            pass
        if i + 1 < len(tokens):
            merged = candidate + ", " + tokens[i + 1]
            try:
                parse_datetime(merged)
                items.append(merged)
                i += 2
                continue
            except ValueError:
                pass
        items.append(candidate)
        i += 1
    return items


def is_true(value):
    return value.strip().lower() in {"1", "true", "yes", "y", "all-day", "allday", "x"}


_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)

_SIMPLE_DURATION_RE = re.compile(
    r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*(?:(?P<minutes>\d+)\s*m(?:in(?:utes?)?)?)?\s*$",
    re.IGNORECASE,
)


def parse_duration(value):
    """Parse an ISO 8601 duration or a simple '2h30m' / '90' style duration."""
    value = value.strip()
    if not value:
        raise ValueError("empty duration")
    if value.isdigit():
        return timedelta(minutes=int(value))
    match = _DURATION_RE.match(value.upper())
    if match and any(match.groupdict().values()):
        parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
        return timedelta(days=parts["days"], hours=parts["hours"],
                         minutes=parts["minutes"], seconds=parts["seconds"])
    match = _SIMPLE_DURATION_RE.match(value)
    if match and (match.group("hours") or match.group("minutes")):
        return timedelta(hours=int(match.group("hours") or 0),
                         minutes=int(match.group("minutes") or 0))
    raise ValueError("unrecognized duration: %r" % value)


# ---------------------------------------------------------------------------
# RRULE validation
# ---------------------------------------------------------------------------

_RRULE_KEYS = {
    "FREQ", "UNTIL", "COUNT", "INTERVAL", "BYSECOND", "BYMINUTE", "BYHOUR",
    "BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH", "BYSETPOS",
    "WKST",
}

_FREQS = {"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}

_BYDAY_RE = re.compile(r"^[+-]?\d{0,2}(MO|TU|WE|TH|FR|SA|SU)$")


def validate_rrule(value):
    """Validate an RRULE string. Returns a list of problem strings (empty = valid)."""
    problems = []
    value = value.strip()
    if not value:
        return ["empty RRULE"]
    upper = value.upper()
    if upper.startswith("RRULE:"):
        upper = upper[len("RRULE:"):]
    parts = upper.split(";")
    seen = {}
    for part in parts:
        if "=" not in part:
            problems.append("RRULE part %r is not KEY=VALUE" % part)
            continue
        key, _, val = part.partition("=")
        key = key.strip()
        val = val.strip()
        if key not in _RRULE_KEYS:
            problems.append("RRULE has unknown property %r" % key)
            continue
        if key in seen:
            problems.append("RRULE repeats property %r" % key)
        seen[key] = val
    if "FREQ" not in seen:
        problems.append("RRULE is missing required FREQ")
    elif seen["FREQ"] not in _FREQS:
        problems.append("RRULE has invalid FREQ %r" % seen["FREQ"])
    if "COUNT" in seen:
        if not seen["COUNT"].isdigit() or int(seen["COUNT"]) < 1:
            problems.append("RRULE COUNT must be a positive integer")
    if "INTERVAL" in seen:
        if not seen["INTERVAL"].isdigit() or int(seen["INTERVAL"]) < 1:
            problems.append("RRULE INTERVAL must be a positive integer")
    if "UNTIL" in seen:
        try:
            parse_datetime(seen["UNTIL"])
        except ValueError:
            problems.append("RRULE UNTIL is not a valid date/datetime: %r" % seen["UNTIL"])
    if "BYDAY" in seen:
        for day in seen["BYDAY"].split(","):
            if not _BYDAY_RE.match(day):
                problems.append("RRULE BYDAY has invalid value %r" % day)
    if "COUNT" in seen and "UNTIL" in seen:
        problems.append("RRULE must not set both COUNT and UNTIL")
    return problems


def check_rrule_until(rrule_str, start):
    """Verify that RRULE UNTIL is not earlier than start."""
    parts = rrule_str.split(";")
    until_raw = None
    for part in parts:
        if "=" in part:
            key, _, val = part.partition("=")
            if key.strip().upper() == "UNTIL":
                until_raw = val.strip()
                break
    if not until_raw or start is None:
        return None
    try:
        until_dt, is_date_only = parse_datetime(until_raw)
    except ValueError:
        return None

    if isinstance(start, datetime) and start.tzinfo is not None:
        if until_dt.tzinfo is not None:
            until_dt = until_dt.astimezone(start.tzinfo)
        else:
            until_dt = until_dt.replace(tzinfo=start.tzinfo)
    elif isinstance(start, datetime) and start.tzinfo is None and until_dt.tzinfo is not None:
        until_dt = until_dt.astimezone(timezone.utc).replace(tzinfo=None)

    if is_date_only or (isinstance(start, date) and not isinstance(start, datetime)):
        start_date = start.date() if isinstance(start, datetime) else start
        until_date = until_dt.date() if isinstance(until_dt, datetime) else until_dt
        if until_date < start_date:
            return "RRULE UNTIL is before start"
    else:
        if until_dt < start:
            return "RRULE UNTIL is before start"
    return None


# ---------------------------------------------------------------------------
# ICS text encoding
# ---------------------------------------------------------------------------

def escape_text(value):
    """Escape a string for use as an RFC 5545 TEXT property value."""
    return (value.replace("\\", "\\\\")
                 .replace(";", "\\;")
                 .replace(",", "\\,")
                 .replace("\r\n", "\\n")
                 .replace("\n", "\\n")
                 .replace("\r", "\\n"))


def fold_line(line):
    """Fold a content line at 75 octets per RFC 5545 section 3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks = []
    while len(encoded) > 75:
        cut = 75
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
    chunks.append(encoded.decode("utf-8"))
    return "\r\n ".join(chunks)


def content_line(name, value, params=None):
    """Build a folded content line, optionally with parameters like TZID."""
    head = name
    if params:
        for key, val in params.items():
            head += ";%s=%s" % (key, val)
    return fold_line("%s:%s" % (head, value))


def format_datetime_ics(dt):
    """Format a datetime for ICS: UTC gets a Z suffix, else local floating."""
    if dt.tzinfo is not None:
        utc = dt.astimezone(timezone.utc)
        return utc.strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

@dataclass
class Event:
    row_number: int
    title: str
    start: datetime | date
    end: datetime | date | None
    description: str = ""
    location: str = ""
    tzid: str | None = None
    rrule: str = ""
    uid: str = ""
    all_day: bool = False
    exdates: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def resolve_tzid(value, default_tz):
    """Return (tzid_or_None, error_or_None)."""
    raw = (value or "").strip()
    if not raw and default_tz:
        raw = default_tz
    if not raw:
        return None, None
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return None, "unknown timezone %r" % raw
    return raw, None


def build_events(rows, default_tz=None, convert_to_utc=False):
    """Turn parsed CSV rows into Event objects, collecting per-row errors."""
    events = []
    for row_number, row in rows:
        errors = []
        title = (row.get("title") or "").strip()
        if not title:
            errors.append("missing title")
        start_raw = (row.get("start") or "").strip()
        start = None
        start_date_only = False
        if not start_raw:
            errors.append("missing start")
        else:
            try:
                start, start_date_only = parse_datetime(start_raw)
            except ValueError as exc:
                errors.append("unparseable start: %s" % exc)
        end_raw = (row.get("end") or "").strip()
        duration_raw = (row.get("duration") or "").strip()
        end = None
        end_date_only = False
        if end_raw:
            try:
                end, end_date_only = parse_datetime(end_raw)
            except ValueError as exc:
                errors.append("unparseable end: %s" % exc)
        elif duration_raw and start is not None:
            try:
                end = start + parse_duration(duration_raw)
            except ValueError as exc:
                errors.append("unparseable duration: %s" % exc)

        exdate_raw = (row.get("exdate") or "").strip()
        raw_exdates = []
        if exdate_raw:
            for item in split_date_list(exdate_raw):
                try:
                    dt, is_date_only = parse_datetime(item)
                    raw_exdates.append((dt, is_date_only))
                except ValueError as exc:
                    errors.append("unparseable exdate: %s" % exc)

        all_day = is_true(row.get("allday") or "") or (
            start_date_only and not end_raw and not duration_raw
        )
        if all_day and isinstance(start, datetime):
            start = start.date()
        if all_day and isinstance(end, datetime):
            end = end.date()

        if start is not None and end is not None and end < start:
            errors.append("end is before start")
        if start is None:
            pass
        elif all_day:
            if end is None and isinstance(start, date):
                end = start + timedelta(days=1)
        else:
            if end is None:
                end = start + timedelta(hours=1)

        tzid = None
        event_tz = None
        if not all_day and isinstance(start, datetime):
            event_tz, tz_error = resolve_tzid(row.get("timezone"), default_tz)
            if tz_error:
                errors.append(tz_error)
            elif event_tz:
                zone = ZoneInfo(event_tz)
                start = start.replace(tzinfo=zone) if start.tzinfo is None else start.astimezone(zone)
                if end is not None:
                    end = end.replace(tzinfo=zone) if end.tzinfo is None else end.astimezone(zone)

        exdates = []
        for dt, is_date_only in raw_exdates:
            if all_day:
                exdates.append(dt.date() if isinstance(dt, datetime) else dt)
            else:
                if is_date_only:
                    time_part = start.time() if isinstance(start, datetime) else datetime.min.time()
                    tz_part = start.tzinfo if isinstance(start, datetime) else (ZoneInfo(event_tz) if event_tz else None)
                    dt = datetime.combine(dt.date(), time_part, tzinfo=tz_part)
                elif event_tz:
                    zone = ZoneInfo(event_tz)
                    dt = dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)
                if convert_to_utc and isinstance(dt, datetime) and dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc)
                exdates.append(dt)

        if not all_day and isinstance(start, datetime):
            if convert_to_utc:
                if start.tzinfo is not None:
                    start = start.astimezone(timezone.utc)
                if end is not None and end.tzinfo is not None:
                    end = end.astimezone(timezone.utc)
                tzid = None
            else:
                tzid = event_tz

        rrule_raw = (row.get("recurrence") or "").strip()
        rrule = ""
        if rrule_raw:
            problems = validate_rrule(rrule_raw)
            if problems:
                errors.extend("bad RRULE: %s" % p for p in problems)
            else:
                rrule = rrule_raw.upper()
                if rrule.startswith("RRULE:"):
                    rrule = rrule[len("RRULE:"):]
                if start is not None:
                    until_error = check_rrule_until(rrule, start)
                    if until_error:
                        errors.append(until_error)

        uid = (row.get("uid") or "").strip()
        if not uid and title and start is not None:
            seed = "calwright:%s|%s|%s" % (title, start.isoformat(), tzid or "")
            uid = "%s@calwright" % uuid.uuid5(uuid.NAMESPACE_URL, seed)
        elif not uid:
            uid = "%s@calwright" % uuid.uuid4()

        events.append(Event(
            row_number=row_number,
            title=title,
            start=start,
            end=end,
            description=(row.get("description") or "").strip(),
            location=(row.get("location") or "").strip(),
            tzid=tzid,
            rrule=rrule,
            uid=uid,
            all_day=all_day,
            exdates=exdates,
            errors=errors,
        ))
    return events


# ---------------------------------------------------------------------------
# VTIMEZONE generation from zoneinfo
# ---------------------------------------------------------------------------

def _offsets_for_year(tzid, year):
    """Sample the UTC offset at noon UTC for each day of the year."""
    zone = ZoneInfo(tzid)
    offsets = []
    day = date(year, 1, 1)
    while day.year == year:
        noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)
        offsets.append((day, noon.astimezone(zone).utcoffset()))
        day += timedelta(days=1)
    return offsets


def _format_offset(offset):
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%02d%02d" % (sign, total // 3600, (total % 3600) // 60)


def _nth_weekday_of_month(day):
    """Return (n, weekday) where n is 1..5 (5 means last) for the month."""
    weekday = day.weekday()  # Monday=0
    first = date(day.year, day.month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=delta)
    n = (day.day - first_occurrence.day) // 7 + 1
    # Check if it is the last such weekday in the month.
    following = day + timedelta(days=7)
    if following.month != day.month:
        n = 5
    return n, ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][weekday]


def build_vtimezone(tzid, reference_year):
    """Build a VTIMEZONE block for a TZID using zoneinfo transition data."""
    lines = ["BEGIN:VTIMEZONE", "TZID:%s" % tzid]
    zone = ZoneInfo(tzid)
    years = [reference_year - 1, reference_year, reference_year + 1, reference_year + 2]
    transitions = []
    prev_offset = None
    for year in years:
        for day, offset in _offsets_for_year(tzid, year):
            if prev_offset is not None and offset != prev_offset:
                at = datetime(day.year, day.month, day.day, 2, 0, 0)
                transitions.append((at, prev_offset, offset))
            prev_offset = offset
    if not transitions:
        offset = datetime.now(zone).utcoffset()
        lines.append("BEGIN:STANDARD")
        lines.append("DTSTART:19700101T000000")
        lines.append("TZOFFSETFROM:%s" % _format_offset(offset))
        lines.append("TZOFFSETTO:%s" % _format_offset(offset))
        lines.append("END:STANDARD")
    else:
        # Group transitions by direction: offset increase = daylight, decrease = standard.
        by_kind = {"DAYLIGHT": [], "STANDARD": []}
        for at, before, after in transitions:
            kind = "DAYLIGHT" if after > before else "STANDARD"
            by_kind[kind].append((at, before, after))
        for kind in ("STANDARD", "DAYLIGHT"):
            group = by_kind[kind]
            if not group:
                continue
            at, before, after = group[len(group) // 2]
            n, weekday = _nth_weekday_of_month(at.date())
            byday = ("-1" if n == 5 else str(n)) + weekday
            lines.append("BEGIN:%s" % kind)
            lines.append("DTSTART:%s" % at.strftime("%Y%m%dT%H%M%S"))
            lines.append("RRULE:FREQ=YEARLY;BYMONTH=%d;BYDAY=%s" % (at.month, byday))
            lines.append("TZOFFSETFROM:%s" % _format_offset(before))
            lines.append("TZOFFSETTO:%s" % _format_offset(after))
            lines.append("END:%s" % kind)
    lines.append("END:VTIMEZONE")
    return "\r\n".join(fold_line(line) for line in lines)


# ---------------------------------------------------------------------------
# ICS rendering
# ---------------------------------------------------------------------------

def _format_prop_datetime(prop_name, dt, all_day, tzid):
    """Format a date or datetime property line with parameters (TZID, VALUE=DATE, or UTC)."""
    if all_day:
        val = dt.strftime("%Y%m%d") if isinstance(dt, (date, datetime)) else str(dt)
        return content_line(prop_name, val, {"VALUE": "DATE"})
    elif tzid:
        local = dt.strftime("%Y%m%dT%H%M%S")
        return content_line(prop_name, local, {"TZID": tzid})
    return content_line(prop_name, format_datetime_ics(dt))


def render_ics(events, prodid=PRODID):
    """Render a list of valid Events as an ICS calendar string."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:%s" % prodid,
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    tzids = sorted({e.tzid for e in events if e.tzid and e.ok})
    if tzids:
        years = []
        for event in events:
            if isinstance(event.start, datetime):
                years.append(event.start.year)
        reference_year = max(years) if years else datetime.now().year
        for tzid in tzids:
            lines.append(build_vtimezone(tzid, reference_year))
    for event in events:
        if not event.ok or event.start is None:
            continue
        lines.append("BEGIN:VEVENT")
        lines.append(content_line("UID", event.uid))
        lines.append(content_line("DTSTAMP", now))
        lines.append(content_line("SUMMARY", escape_text(event.title)))
        lines.append(_format_prop_datetime("DTSTART", event.start, event.all_day, event.tzid))
        if event.end:
            lines.append(_format_prop_datetime("DTEND", event.end, event.all_day, event.tzid))
        if event.description:
            lines.append(content_line("DESCRIPTION", escape_text(event.description)))
        if event.location:
            lines.append(content_line("LOCATION", escape_text(event.location)))
        if event.rrule:
            lines.append(content_line("RRULE", event.rrule))
        for exdate in event.exdates:
            lines.append(_format_prop_datetime("EXDATE", exdate, event.all_day, event.tzid))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# CSV loading and validation reporting
# ---------------------------------------------------------------------------

def load_rows(csv_text):
    """Parse CSV text into (row_number, dict) pairs with normalized headers."""
    sample = csv_text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(csv_text, newline=""), dialect=dialect)
    if reader.fieldnames is None:
        raise ValueError("no header row found in CSV")
    mapping = normalize_headers(reader.fieldnames)
    rows = []
    for index, raw in enumerate(reader, start=2):  # row 1 is the header
        if all((value or "").strip() == "" for value in raw.values()):
            continue
        normalized = {}
        for header, value in raw.items():
            canonical = mapping.get(header)
            if canonical and canonical not in normalized:
                normalized[canonical] = value or ""
        rows.append((index, normalized))
    return rows


def validate_events(events):
    """Return a list of (row_number, [problems]) for events with errors."""
    return [(e.row_number, e.errors) for e in events if not e.ok]


def format_check_report(problems, total_rows):
    """Render a human-readable validation report."""
    out = ["calwright check: %d row(s) examined" % total_rows]
    if not problems:
        out.append("OK: no problems found.")
        return "\n".join(out)
    out.append("FOUND %d problem(s):" % sum(len(p) for _, p in problems))
    for row_number, errors in problems:
        for error in errors:
            out.append("  row %d: %s" % (row_number, error))
    return "\n".join(out)


def format_summary(events):
    """Render a human-readable preview of the events."""
    out = ["calwright preview: %d event(s)" % len(events)]
    for event in events:
        if not event.ok or event.start is None:
            out.append("  [row %d] INVALID: %s" % (event.row_number, "; ".join(event.errors)))
            continue
        if event.all_day:
            when = event.start.isoformat()
            if event.end:
                when += " to %s (all day)" % (event.end - timedelta(days=1)).isoformat()
        else:
            when = event.start.isoformat()
            if event.end:
                when += " to " + event.end.isoformat()
        line = "  - %s | %s" % (event.title, when)
        if event.location:
            line += " | %s" % event.location
        if event.rrule:
            line += " | repeats: %s" % event.rrule
        if event.exdates:
            formatted_exdates = [
                ex.strftime("%Y-%m-%d") if isinstance(ex, datetime) else ex.isoformat()
                for ex in event.exdates
            ]
            line += " | exceptions: %s" % ", ".join(formatted_exdates)
        out.append(line)
    return "\n".join(out)
