from datetime import date, datetime, time
from pathlib import Path

import pytest

from pyedookit.client import _parse_week
from pyedookit.exceptions import ParseError
from pyedookit.models import PORTAL_TZ, Period, SchoolEvent
from pyedookit.parsers.timetable import (
    parse_periods,
    parse_terms,
    parse_timetable,
    parse_timetable_events,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = (FIXTURES / "timetable_structure.html").read_text()
HOLIDAY_WEEK = (FIXTURES / "timetable_cancelled_structure.html").read_text()


def test_parses_each_lesson_info_block_with_its_own_date():
    lessons = parse_timetable(FIXTURE)
    assert len(lessons) == 2

    monday = lessons[0]
    assert monday.start == datetime(2026, 9, 14, 8, 0, tzinfo=PORTAL_TZ)
    assert monday.end == datetime(2026, 9, 14, 8, 45, tzinfo=PORTAL_TZ)
    assert monday.subject == "Matematika"
    assert monday.teacher == "Novák J."
    assert monday.room == "1.A"
    assert monday.group == "I.A"
    assert monday.note == "Sčítání do dvaceti"


def test_handles_a_lesson_with_no_curriculum_note():
    lessons = parse_timetable(FIXTURE)
    tuesday = lessons[1]
    assert tuesday.start.date() == date(2026, 9, 15)
    assert tuesday.start.time() == time(9, 0)
    assert tuesday.subject == "Tělesná výchova"
    assert tuesday.room == "TV 1"
    assert tuesday.note is None


def test_raises_parse_error_when_no_lesson_info_blocks_found():
    with pytest.raises(ParseError):
        parse_timetable("<html><body>no lessons here</body></html>")


def test_parse_terms_reads_term_selector_links():
    terms = parse_terms(FIXTURE)
    assert {t.id: t.name for t in terms} == {"61": "2026/2027", "58": "2025/2026"}


def _block(lesson_id: int, when: str, subject: str) -> str:
    # Same shape as the fixture; the portal writes "Včera"/"Dnes" instead of
    # a date for yesterday's and today's lessons (confirmed 2026-09-22).
    return f"""
<div class="lesson-info" style="display:none" id="lesson-info-{lesson_id}">
    <h3 class="attendance-heading">Informace o hodině</h3>
    <div>
        {when}, <span class="date_time">8:00</span>&ndash;<span class="date_time">8:45</span>
    </div>
    <div>Čj - I.A</div>
    <div>{subject}</div>
    <div>Novák J.</div>
    <div>1.A</div>
</div>"""


def test_resolves_relative_day_names_against_today():
    html = "".join(
        [
            _block(1, "Včera ", "Včerejší"),
            _block(2, "Dnes ", "Dnešní"),
            _block(3, "Zítra ", "Zítřejší"),
            _block(4, "Čt 24.&thinsp;9.&thinsp;2026", "Čtvrteční"),
        ]
    )

    lessons = parse_timetable(html, today=date(2026, 9, 22))

    assert [(ls.subject, ls.start) for ls in lessons] == [
        ("Včerejší", datetime(2026, 9, 21, 8, 0, tzinfo=PORTAL_TZ)),
        ("Dnešní", datetime(2026, 9, 22, 8, 0, tzinfo=PORTAL_TZ)),
        ("Zítřejší", datetime(2026, 9, 23, 8, 0, tzinfo=PORTAL_TZ)),
        ("Čtvrteční", datetime(2026, 9, 24, 8, 0, tzinfo=PORTAL_TZ)),
    ]


def test_warns_about_a_lesson_whose_date_cannot_be_read(caplog):
    html = _block(1, "Předevčírem ", "Záhadná") + _block(2, "Dnes ", "Dnešní")

    lessons = parse_timetable(html, today=date(2026, 9, 22))

    assert [ls.subject for ls in lessons] == ["Dnešní"]
    assert "lesson-info-1" in caplog.text


def test_lesson_keeps_the_subject_code_before_the_group():
    assert parse_timetable(FIXTURE)[0].code == "Mat"


def test_parses_the_bell_schedule_from_the_period_header():
    assert parse_periods(FIXTURE) == [
        Period(1, time(8, 0), time(8, 45)),
        Period(2, time(8, 55), time(9, 40)),
        Period(3, time(10, 0), time(10, 45)),
    ]


def test_no_period_header_gives_no_periods():
    assert parse_periods("<html><body></body></html>") == []


def test_code_without_a_group_is_the_whole_field():
    html = """
    <div class="lesson-info" id="lesson-info-1">
        <div>
            Po 14.&thinsp;9.&thinsp;2026,
            <span class="date_time">8:00</span>&ndash;<span class="date_time">8:45</span>
        </div>
        <div>Čj</div>
        <div>Český jazyk</div>
        <div>Novák J.</div>
        <div>1.A</div>
    </div>
    """
    lessons = parse_timetable(html)
    assert lessons[0].code == "Čj" and lessons[0].group is None


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=PORTAL_TZ)


def test_lessons_take_date_and_time_from_their_tile():
    # A cancelled lesson's lesson-info block has an empty date/time field.
    lessons = parse_timetable(HOLIDAY_WEEK, today=date(2026, 10, 29))

    assert [(ls.code, ls.start, ls.end) for ls in lessons] == [
        ("Čj", _at(26, 8, 0), _at(26, 8, 45)),
        ("Aj2", _at(26, 10, 0), _at(26, 10, 45)),
        ("ŠD 4", _at(26, 12, 35), _at(26, 14, 0)),
        ("Tv", _at(27, 8, 0), _at(27, 8, 45)),
        ("M T", _at(27, 8, 55), _at(27, 9, 40)),
        ("OSV", _at(27, 10, 0), _at(27, 10, 45)),
    ]


def test_cancelled_changed_and_regular_lessons():
    lessons = parse_timetable(HOLIDAY_WEEK)

    assert [(ls.code, ls.is_changed, ls.is_cancelled) for ls in lessons] == [
        ("Čj", True, True),  # "Zrušeno"
        ("Aj2", True, True),
        ("ŠD 4", True, True),
        ("Tv", False, False),
        ("M T", True, False),  # substitute teacher: orange border only
        ("OSV", True, True),  # "Událost": replaced by an event
    ]


def test_cancelled_lesson_keeps_its_details():
    czech = parse_timetable(HOLIDAY_WEEK)[0]
    assert czech.subject == "Český jazyk"
    assert czech.teacher == "NOV, SVO (Novák J., Svobodová P.)"
    assert czech.room == "1.A"
    assert czech.group == "1.A"


def test_changed_lesson_keeps_its_details():
    maths = parse_timetable(HOLIDAY_WEEK)[4]
    assert maths.teacher == "NOV, HOR (Novák J., Horáková E.)"
    assert maths.note == "Násobilka - procvičení"


def test_group_split_tolerates_no_space_before_the_dash():
    english = parse_timetable(HOLIDAY_WEEK)[1]
    assert (english.code, english.group) == ("Aj2", "1.A")


def test_lesson_without_a_subject_name_falls_back_to_its_code():
    assert parse_timetable(HOLIDAY_WEEK)[2].subject == "ŠD 4"


def test_each_day_of_an_event_is_its_own_event():
    assert parse_timetable_events(HOLIDAY_WEEK) == [
        SchoolEvent(
            id="3364",
            title="Ředitelské volno",
            start=date(2026, 10, 26),
            end=date(2026, 10, 26),
        ),
        SchoolEvent(
            id="3364",
            title="Ředitelské volno",
            start=date(2026, 10, 27),
            end=date(2026, 10, 27),
        ),
        SchoolEvent(
            id="3437",
            title="Den převleků",
            start=_at(27, 8, 0),
            end=_at(27, 11, 59),
        ),
        SchoolEvent(
            id="3362",
            title="STÁTNÍ SVÁTEK - Den vzniku samostatného československého státu",
            start=date(2026, 10, 28),
            end=date(2026, 10, 28),
        ),
    ]


def test_week_without_events_has_none():
    assert parse_timetable_events(FIXTURE) == []


def test_week_without_lessons_is_empty_not_an_error():
    # Summer weeks render the grid with no lessons and no periods.
    html = '<div class="timetable-container" id="tt1"><div class="lessonsBox"></div></div>'
    assert parse_timetable(html) == []


def test_timetable_week_carries_the_events():
    week = _parse_week(HOLIDAY_WEEK)
    assert len(week.lessons) == 6
    assert [e.id for e in week.events] == ["3364", "3364", "3437", "3362"]


def test_an_event_drawn_twice_on_a_day_is_kept_once():
    tile = """
    <div>
      <a class="lesson event" href="/timetable/detail?event=7" id="{grid}boxId0">
        <span data-day="2026_10_28" data-lesson-id=""></span>
        <div class="row"><b>Svátek</b></div>
      </a>
      <div class="hoverLesson" id="{grid}longDesc0">
        <b>Svátek</b><br>St 28. 10. 00:00&ndash;23:59
      </div>
    </div>
    """
    html = tile.format(grid="a") + tile.format(grid="b")
    assert len(parse_timetable_events(html)) == 1


FAMILY = (FIXTURES / "timetable_family_structure.html").read_text()
FIRST_STUDENT = "Jan Karel Novák"
SECOND_STUDENT = "Eliška Nováková"


def test_lessons_carry_the_student_from_their_sections_heading():
    assert [(ls.student, ls.start) for ls in parse_timetable(FAMILY)] == [
        (FIRST_STUDENT, datetime(2026, 10, 19, 8, 0, tzinfo=PORTAL_TZ)),
        (SECOND_STUDENT, datetime(2026, 10, 19, 8, 55, tzinfo=PORTAL_TZ)),
    ]


def test_events_carry_the_student_and_a_shared_event_comes_back_for_each():
    events = parse_timetable_events(FAMILY)
    assert [(e.id, e.student) for e in events] == [
        ("5001", FIRST_STUDENT),
        ("5001", SECOND_STUDENT),
        ("5002", FIRST_STUDENT),
    ]


def test_a_page_without_student_sections_has_no_student():
    assert {ls.student for ls in parse_timetable(FIXTURE)} == {None}
    assert {ls.student for ls in parse_timetable(HOLIDAY_WEEK)} == {None}
    assert {e.student for e in parse_timetable_events(HOLIDAY_WEEK)} == {None}


def test_a_section_without_its_own_heading_does_not_take_the_previous_one():
    html = """
    <h2 class="heading">Jan Novák <span class="class-name">1.A</span></h2>
    <div id="snippet-familyTimetable-personTimetable-1"></div>
    <div class="cleaner"></div>
    <div id="snippet-familyTimetable-personTimetable-2">
      <div class="lesson-info" id="lesson-info-1">
        <div>Po 14.&thinsp;9.&thinsp;2026,
          <span class="date_time">8:00</span>&ndash;<span class="date_time">8:45</span></div>
        <div>Čj</div>
      </div>
    </div>
    """
    assert [ls.student for ls in parse_timetable(html)] == [None]


def test_periods_merge_across_students():
    assert [p.number for p in parse_periods(FAMILY)] == [1, 2]
