"""Tests for calwright. Run with: python -m unittest discover -s tests"""

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calwright.converter import (  # noqa: E402
    build_events,
    build_vtimezone,
    content_line,
    escape_text,
    fold_line,
    format_check_report,
    format_duration_iso,
    format_summary,
    load_rows,
    normalize_headers,
    parse_datetime,
    parse_duration,
    render_ics,
    validate_events,
    validate_rrule,
)
from calwright.cli import build_parser, main  # noqa: E402


def make_rows(text):
    return load_rows(text)


def events_from(text, **kwargs):
    return build_events(make_rows(text), **kwargs)


SAMPLE = """title,start,end,description,location,timezone,recurrence
Team standup,2026-10-26 09:00,2026-10-26 09:30,Daily sync,Zoom,America/New_York,FREQ=DAILY
Release day,2026-11-01,,,Ship v2.0,,,
"""


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

class TestNormalizeHeaders(unittest.TestCase):
    def test_title_aliases(self):
        mapping = normalize_headers(["Summary", "start_time", "DTEND", "Where", "TZ"])
        self.assertEqual(mapping["Summary"], "title")
        self.assertEqual(mapping["start_time"], "start")
        self.assertEqual(mapping["DTEND"], "end")
        self.assertEqual(mapping["Where"], "location")
        self.assertEqual(mapping["TZ"], "timezone")

    def test_unknown_header_is_none(self):
        mapping = normalize_headers(["Foo", "title"])
        self.assertIsNone(mapping["Foo"])
        self.assertEqual(mapping["title"], "title")

    def test_allday_aliases(self):
        mapping = normalize_headers(["All-Day", "event_id", "Repeats"])
        self.assertEqual(mapping["All-Day"], "allday")
        self.assertEqual(mapping["event_id"], "uid")
        self.assertEqual(mapping["Repeats"], "recurrence")


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------

class TestParseDatetime(unittest.TestCase):
    def test_iso_with_t(self):
        dt, date_only = parse_datetime("2026-10-23T14:30:00")
        self.assertEqual(dt, datetime(2026, 10, 23, 14, 30, 0))
        self.assertFalse(date_only)

    def test_space_separated(self):
        dt, _ = parse_datetime("2026-10-23 14:30")
        self.assertEqual(dt, datetime(2026, 10, 23, 14, 30))

    def test_date_only(self):
        dt, date_only = parse_datetime("2026-10-23")
        self.assertEqual(dt, datetime(2026, 10, 23, 0, 0))
        self.assertTrue(date_only)

    def test_us_format(self):
        dt, _ = parse_datetime("10/23/2026 2:30 PM")
        self.assertEqual(dt, datetime(2026, 10, 23, 14, 30))

    def test_trailing_z(self):
        dt, _ = parse_datetime("2026-10-23T14:30:00Z")
        self.assertEqual(dt.tzinfo.utcoffset(dt), timedelta(0))

    def test_explicit_offset(self):
        dt, _ = parse_datetime("2026-10-23T14:30:00+0200")
        self.assertEqual(dt.utcoffset(), timedelta(hours=2))

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_datetime("not a date at all")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_datetime("   ")


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

class TestParseDuration(unittest.TestCase):
    def test_iso_duration(self):
        self.assertEqual(parse_duration("PT1H30M"), timedelta(hours=1, minutes=30))

    def test_iso_days(self):
        self.assertEqual(parse_duration("P2D"), timedelta(days=2))

    def test_simple_hours_minutes(self):
        self.assertEqual(parse_duration("2h30m"), timedelta(hours=2, minutes=30))

    def test_bare_number_means_minutes(self):
        self.assertEqual(parse_duration("90"), timedelta(minutes=90))

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("a while")

    def test_format_duration_iso(self):
        self.assertEqual(format_duration_iso(timedelta(minutes=90)), "PT1H30M")
        self.assertEqual(format_duration_iso(timedelta(hours=2)), "PT2H")
        self.assertEqual(format_duration_iso(timedelta(seconds=45)), "PT45S")
        self.assertEqual(format_duration_iso(timedelta(days=1, hours=2, minutes=30)), "P1DT2H30M")
        self.assertEqual(format_duration_iso(timedelta(days=2)), "P2D")
        self.assertEqual(format_duration_iso(timedelta(0)), "PT0S")


# ---------------------------------------------------------------------------
# RRULE validation
# ---------------------------------------------------------------------------

class TestValidateRrule(unittest.TestCase):
    def test_valid_simple(self):
        self.assertEqual(validate_rrule("FREQ=WEEKLY;BYDAY=MO,WE,FR"), [])

    def test_valid_with_count(self):
        self.assertEqual(validate_rrule("FREQ=DAILY;COUNT=10"), [])

    def test_valid_with_until(self):
        self.assertEqual(validate_rrule("FREQ=MONTHLY;UNTIL=20261231T000000Z"), [])

    def test_missing_freq(self):
        problems = validate_rrule("COUNT=5")
        self.assertTrue(any("FREQ" in p for p in problems))

    def test_bad_freq(self):
        problems = validate_rrule("FREQ=SOMETIMES")
        self.assertTrue(any("FREQ" in p for p in problems))

    def test_unknown_property(self):
        problems = validate_rrule("FREQ=DAILY;EVERY=2")
        self.assertTrue(any("EVERY" in p for p in problems))

    def test_count_and_until_conflict(self):
        problems = validate_rrule("FREQ=DAILY;COUNT=5;UNTIL=20261231T000000Z")
        self.assertTrue(any("COUNT and UNTIL" in p for p in problems))

    def test_bad_byday(self):
        problems = validate_rrule("FREQ=WEEKLY;BYDAY=XX")
        self.assertTrue(any("BYDAY" in p for p in problems))

    def test_numeric_byday_ok(self):
        self.assertEqual(validate_rrule("FREQ=MONTHLY;BYDAY=-1FR"), [])


# ---------------------------------------------------------------------------
# Text escaping and folding
# ---------------------------------------------------------------------------

class TestEscaping(unittest.TestCase):
    def test_escape_specials(self):
        self.assertEqual(escape_text("a;b,c\\d\ne"), "a\\;b\\,c\\\\d\\ne")

    def test_fold_short_line_untouched(self):
        line = "SUMMARY:short"
        self.assertEqual(fold_line(line), line)

    def test_fold_long_line(self):
        line = "DESCRIPTION:" + "x" * 200
        folded = fold_line(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 76)
        self.assertTrue(folded.split("\r\n")[1].startswith(" "))

    def test_unfold_roundtrip(self):
        line = "DESCRIPTION:" + "y" * 200
        unfolded = fold_line(line).replace("\r\n ", "")
        self.assertEqual(unfolded, line)

    def test_content_line_with_params(self):
        line = content_line("DTSTART", "20261023T143000", {"TZID": "America/New_York"})
        self.assertTrue(line.startswith("DTSTART;TZID=America/New_York:"))


# ---------------------------------------------------------------------------
# Event building
# ---------------------------------------------------------------------------

class TestBuildEvents(unittest.TestCase):
    def test_basic_event_fields(self):
        events = events_from(SAMPLE)
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertTrue(first.ok)
        self.assertEqual(first.title, "Team standup")
        self.assertEqual(first.tzid, "America/New_York")
        self.assertEqual(first.rrule, "FREQ=DAILY")
        self.assertEqual(first.location, "Zoom")

    def test_date_only_becomes_all_day(self):
        events = events_from(SAMPLE)
        second = events[1]
        self.assertTrue(second.all_day)
        self.assertEqual(second.start, date(2026, 11, 1))
        self.assertEqual(second.end, date(2026, 11, 2))

    def test_missing_title_is_error(self):
        events = events_from("title,start\n,2026-10-23 10:00\n")
        self.assertFalse(events[0].ok)
        self.assertTrue(any("title" in e for e in events[0].errors))

    def test_unparseable_start_is_error(self):
        events = events_from("title,start\nParty,someday\n")
        self.assertTrue(any("start" in e for e in events[0].errors))

    def test_end_before_start_is_error(self):
        events = events_from("title,start,end\nX,2026-10-23 12:00,2026-10-23 10:00\n")
        self.assertTrue(any("before start" in e for e in events[0].errors))

    def test_unknown_timezone_is_error(self):
        events = events_from("title,start,timezone\nX,2026-10-23 10:00,Mars/Olympus\n")
        self.assertTrue(any("timezone" in e for e in events[0].errors))

    def test_default_tz_applied(self):
        events = events_from("title,start\nX,2026-10-23 10:00\n", default_tz="Europe/London")
        self.assertTrue(events[0].ok)
        self.assertEqual(events[0].tzid, "Europe/London")
        self.assertEqual(events[0].start.tzinfo, ZoneInfo("Europe/London"))

    def test_utc_conversion(self):
        events = events_from("title,start,timezone\nX,2026-10-23 10:00,America/New_York\n",
                             convert_to_utc=True)
        event = events[0]
        self.assertTrue(event.ok)
        self.assertIsNone(event.tzid)
        self.assertEqual(event.start.utcoffset(), timedelta(0))

    def test_uid_generated_is_stable(self):
        first = events_from(SAMPLE)[0].uid
        second = events_from(SAMPLE)[0].uid
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("@calwright"))

    def test_uid_respected_when_given(self):
        events = events_from("title,start,uid\nX,2026-10-23 10:00,my-custom-id\n")
        self.assertEqual(events[0].uid, "my-custom-id")

    def test_duration_column(self):
        events = events_from("title,start,duration\nX,2026-10-23 10:00,90\n")
        self.assertEqual(events[0].end, datetime(2026, 10, 23, 11, 30))

    def test_default_one_hour_end(self):
        events = events_from("title,start\nX,2026-10-23 10:00\n")
        self.assertEqual(events[0].end, datetime(2026, 10, 23, 11, 0))


# ---------------------------------------------------------------------------
# VTIMEZONE
# ---------------------------------------------------------------------------

class TestVtimezone(unittest.TestCase):
    def test_new_york_has_dst_components(self):
        block = build_vtimezone("America/New_York", 2026)
        self.assertIn("BEGIN:VTIMEZONE", block)
        self.assertIn("TZID:America/New_York", block)
        self.assertIn("BEGIN:DAYLIGHT", block)
        self.assertIn("BEGIN:STANDARD", block)
        self.assertIn("END:VTIMEZONE", block)

    def test_no_dst_zone_has_single_standard(self):
        block = build_vtimezone("Asia/Karachi", 2026)
        self.assertIn("BEGIN:STANDARD", block)
        self.assertNotIn("BEGIN:DAYLIGHT", block)

    def test_offset_format(self):
        block = build_vtimezone("America/New_York", 2026)
        self.assertRegex(block, r"TZOFFSETFROM:[+-]\d{4}")
        self.assertRegex(block, r"TZOFFSETTO:[+-]\d{4}")


# ---------------------------------------------------------------------------
# ICS rendering, including a structural parse test
# ---------------------------------------------------------------------------

def parse_ics_structure(text):
    """Minimal structural parse: unfold lines, group into components."""
    unfolded = []
    for raw in text.split("\r\n"):
        if raw.startswith(" ") and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw)
    stack = []
    components = []
    for line in unfolded:
        if not line:
            continue
        if line.startswith("BEGIN:"):
            stack.append({"name": line[6:], "props": {}, "children": []})
        elif line.startswith("END:"):
            node = stack.pop()
            assert node["name"] == line[4:], "mismatched END:%s" % line[4:]
            if stack:
                stack[-1]["children"].append(node)
            else:
                components.append(node)
        else:
            name = line.split(";", 1)[0].split(":", 1)[0]
            stack[-1]["props"].setdefault(name, []).append(line)
    return components


class TestRenderIcs(unittest.TestCase):
    def test_calendar_envelope(self):
        ics = render_ics(events_from(SAMPLE))
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertIn("PRODID:-//calwright//calwright", ics)
        self.assertIn("VERSION:2.0", ics)

    def test_structural_parse(self):
        ics = render_ics(events_from(SAMPLE))
        components = parse_ics_structure(ics)
        self.assertEqual(len(components), 1)
        cal = components[0]
        self.assertEqual(cal["name"], "VCALENDAR")
        vevents = [c for c in cal["children"] if c["name"] == "VEVENT"]
        self.assertEqual(len(vevents), 2)
        vtimezones = [c for c in cal["children"] if c["name"] == "VTIMEZONE"]
        self.assertEqual(len(vtimezones), 1)
        self.assertIn("TZID:America/New_York", vtimezones[0]["props"]["TZID"][0])

    def test_vevent_required_properties(self):
        ics = render_ics(events_from(SAMPLE))
        cal = parse_ics_structure(ics)[0]
        uids = set()
        for vevent in [c for c in cal["children"] if c["name"] == "VEVENT"]:
            props = vevent["props"]
            for required in ("UID", "DTSTAMP", "SUMMARY", "DTSTART"):
                self.assertIn(required, props, "VEVENT missing %s" % required)
            uids.add(props["UID"][0])
            self.assertRegex(props["DTSTAMP"][0], r"DTSTAMP:\d{8}T\d{6}Z")
        self.assertEqual(len(uids), 2, "UIDs must be unique")

    def test_tzid_params_on_datetimes(self):
        ics = render_ics(events_from(SAMPLE))
        self.assertIn("DTSTART;TZID=America/New_York:20261026T090000", ics)

    def test_all_day_uses_date_value(self):
        ics = render_ics(events_from(SAMPLE))
        self.assertIn("DTSTART;VALUE=DATE:20261101", ics)
        self.assertIn("DTEND;VALUE=DATE:20261102", ics)

    def test_rrule_passthrough(self):
        ics = render_ics(events_from(SAMPLE))
        self.assertIn("RRULE:FREQ=DAILY", ics)

    def test_description_escaping(self):
        text = 'title,start,description\nX,2026-10-23 10:00,"meet at 5; bring snacks, drinks"\n'
        ics = render_ics(events_from(text))
        self.assertIn("DESCRIPTION:meet at 5\\; bring snacks\\, drinks", ics)
        text = "title,start,description\nX,2026-10-23 10:00,path C:\\\\tmp\\\\x\n"
        ics = render_ics(events_from(text))
        self.assertIn("DESCRIPTION:path C:\\\\\\\\tmp\\\\\\\\x", ics)

    def test_invalid_events_skipped(self):
        text = "title,start\n,2026-10-23 10:00\nGood,2026-10-23 11:00\n"
        ics = render_ics(events_from(text))
        cal = parse_ics_structure(ics)[0]
        vevents = [c for c in cal["children"] if c["name"] == "VEVENT"]
        self.assertEqual(len(vevents), 1)

    def test_prefer_duration_emits_duration_and_no_dtend(self):
        text = "title,start,duration\nMeeting,2026-10-26 09:00,90\n"
        ics_duration = render_ics(events_from(text), prefer_duration=True)
        self.assertIn("DURATION:PT1H30M", ics_duration)
        self.assertNotIn("DTEND", ics_duration)

        ics_default = render_ics(events_from(text), prefer_duration=False)
        self.assertIn("DTEND:20261026T103000", ics_default)
        self.assertNotIn("DURATION", ics_default)

    def test_prefer_duration_with_timezone(self):
        text = "title,start,duration,timezone\nMeeting,2026-10-26 09:00,1h30m,America/New_York\n"
        ics = render_ics(events_from(text), prefer_duration=True)
        self.assertIn("DTSTART;TZID=America/New_York:20261026T090000", ics)
        self.assertIn("DURATION:PT1H30M", ics)
        self.assertNotIn("DTEND", ics)

    def test_prefer_duration_ignores_events_with_end_column(self):
        text = "title,start,end\nMeeting,2026-10-26 09:00,2026-10-26 10:30\n"
        ics = render_ics(events_from(text), prefer_duration=True)
        self.assertIn("DTEND:20261026T103000", ics)
        self.assertNotIn("DURATION", ics)

    def test_prefer_duration_ignores_all_day_events(self):
        text = "title,start,all_day\nHoliday,2026-10-26,yes\n"
        ics = render_ics(events_from(text), prefer_duration=True)
        self.assertIn("DTSTART;VALUE=DATE:20261026", ics)
        self.assertIn("DTEND;VALUE=DATE:20261027", ics)
        self.assertNotIn("DURATION", ics)


# ---------------------------------------------------------------------------
# Validation reporting
# ---------------------------------------------------------------------------

class TestValidation(unittest.TestCase):
    def test_validate_events_collects_row_numbers(self):
        events = events_from("title,start\n,2026-10-23 10:00\nOK,2026-10-23 11:00\n")
        problems = validate_events(events)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 2)

    def test_clean_report(self):
        report = format_check_report([], 5)
        self.assertIn("OK: no problems found.", report)

    def test_problem_report_lists_rows(self):
        problems = [(3, ["missing title", "unparseable start: boom"])]
        report = format_check_report(problems, 4)
        self.assertIn("row 3: missing title", report)
        self.assertIn("row 3: unparseable start: boom", report)

    def test_summary_lists_events(self):
        summary = format_summary(events_from(SAMPLE))
        self.assertIn("Team standup", summary)
        self.assertIn("Release day", summary)
        self.assertIn("repeats: FREQ=DAILY", summary)

    def test_blank_rows_skipped(self):
        rows = make_rows("title,start\nA,2026-10-23 10:00\n\n\n")
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    def write_csv(self, directory, text):
        path = Path(directory) / "events.csv"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_check_clean_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(directory, SAMPLE)
            self.assertEqual(main([path, "--check"]), 0)

    def test_check_dirty_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(directory, "title,start\n,broken\n")
            self.assertEqual(main([path, "--check"]), 1)

    def test_convert_writes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, SAMPLE)
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out]), 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("BEGIN:VCALENDAR", text)
            self.assertEqual(text.count("BEGIN:VEVENT"), 2)

    def test_convert_with_errors_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(directory, "title,start\n,broken\n")
            self.assertEqual(main([path]), 2)

    def test_missing_file_exits_two(self):
        self.assertEqual(main(["/nonexistent/events.csv"]), 2)

    def test_summary_flag_prints_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, SAMPLE)
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out, "--summary"]), 0)

    def test_default_tz_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, "title,start\nX,2026-10-23 10:00\n")
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out, "--default-tz", "Asia/Karachi"]), 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("TZID:Asia/Karachi", text)

    def test_utc_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, "title,start,timezone\nX,2026-10-23 10:00,America/New_York\n")
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out, "--utc"]), 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("DTSTART:20261023T140000Z", text)

    def test_parser_has_prefer_duration_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--prefer-duration"])
        self.assertTrue(args.prefer_duration)

    def test_prefer_duration_flag_emits_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, "title,start,duration\nMeeting,2026-10-26 09:00,90\n")
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out, "--prefer-duration"]), 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("DURATION:PT1H30M", text)
            self.assertNotIn("DTEND", text)

    def test_prefer_duration_flag_off_keeps_dtend(self):
        with tempfile.TemporaryDirectory() as directory:
            src = self.write_csv(directory, "title,start,duration\nMeeting,2026-10-26 09:00,90\n")
            out = str(Path(directory) / "out.ics")
            self.assertEqual(main([src, "-o", out]), 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("DTEND:20261026T103000", text)
            self.assertNotIn("DURATION", text)


if __name__ == "__main__":
    unittest.main()
