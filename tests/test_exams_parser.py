from datetime import date, datetime
from pathlib import Path

import pytest

from pyedookit.exceptions import ParseError
from pyedookit.models import PORTAL_TZ
from pyedookit.parsers.exams import parse_exams

FIXTURE = (Path(__file__).parent / "fixtures" / "exams_structure.html").read_text()
TODAY = date(2026, 10, 29)


def _row(exam_id: int, when: str, created: str = "Po 14.&thinsp;12.&thinsp;2026, 7:55") -> str:
    href = f"/exams/detail?assignment={exam_id}"
    return f"""
<ul class="anyCourse course501 table_row" onclick="window.location.href = '{href}'">
  <li class="c1"><p><b>{when}</b></p><p>Matematika<span class="link_info">M - 1.A</span></p>
    <p>Malá písemka</p>Písemná práce</li>
  <li class="c2"><p><div class="bold"><a href="{href}">Test</a></div></p></li>
  <li class="c4"><div class="smaller"><p><b>Jana Nováková</b></p><p>{created}</p></div></li>
</ul>"""


def _page(rows: str) -> str:
    return f'<section class="content"><div class="boxes1">{rows}</div></section>'


def test_skips_the_header_row_and_keeps_page_order():
    assert [e.id for e in parse_exams(FIXTURE, today=TODAY)] == [9003, 9002, 9001]


def test_exam_fields():
    exam = parse_exams(FIXTURE, today=TODAY)[0]
    assert exam.title == "Opakování"
    assert exam.start == datetime(2026, 10, 22, 8, 0, tzinfo=PORTAL_TZ)
    assert exam.end == datetime(2026, 10, 22, 8, 45, tzinfo=PORTAL_TZ)
    assert (exam.subject, exam.course_code, exam.course_id) == ("Matematika", "M T - 1.A", 503)
    assert exam.short == "M"
    assert (exam.kind, exam.category) == ("Malá písemka", "Písemná práce")
    assert exam.description == "Násobilka a sčítání do 100"
    assert exam.created_by == "Jana Nováková"
    assert exam.created == datetime(2026, 10, 14, 10, 32, tzinfo=PORTAL_TZ)
    assert exam.unread


def test_empty_description_and_dashed_course_code():
    exam = parse_exams(FIXTURE, today=TODAY)[1]
    assert exam.description is None
    assert exam.short == "Aj2"


def test_page_with_no_exams():
    page = _page("<p><em>Žádné písemky, zkoušení</em></p>")
    assert parse_exams(page, today=TODAY) == []


def test_year_follows_the_announcement_across_new_year():
    page = _page(_row(1, "8.&thinsp;1., od 8:00 do 8:45"))
    assert parse_exams(page, today=date(2027, 3, 1))[0].start.date() == date(2027, 1, 8)


def test_relative_and_explicit_dates():
    page = _page(
        _row(1, "Zítra, od 8:00 do 8:45", created="Dnes, 9:00")
        + _row(2, "8.&thinsp;1.&thinsp;2028, od 8:00 do 8:45")
    )
    first, second = parse_exams(page, today=TODAY)
    assert first.start == datetime(2026, 10, 30, 8, 0, tzinfo=PORTAL_TZ)
    assert first.created == datetime(2026, 10, 29, 9, 0, tzinfo=PORTAL_TZ)
    assert second.start.date() == date(2028, 1, 8)


def test_an_exam_in_two_grids_is_returned_once():
    row = _row(1, "29.&thinsp;10., od 8:00 do 8:45")
    assert len(parse_exams(_page(row) + _page(row), today=TODAY)) == 1


def test_unparseable_row_is_skipped():
    page = _page(_row(1, "bez data") + _row(2, "29.&thinsp;10., od 8:00 do 8:45"))
    assert [e.id for e in parse_exams(page, today=TODAY)] == [2]


def test_only_unparseable_rows_raise():
    with pytest.raises(ParseError):
        parse_exams(_page(_row(1, "bez data")), today=TODAY)


def test_other_page_raises():
    with pytest.raises(ParseError):
        parse_exams("<html><body><p>Přihlášení</p></body></html>", today=TODAY)
