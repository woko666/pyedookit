from datetime import date, datetime
from pathlib import Path

import pytest

from pyedookit.exceptions import ParseError
from pyedookit.models import PORTAL_TZ
from pyedookit.parsers.assignments import parse_assignments

MAIN = (Path(__file__).parent / "fixtures" / "assignments_structure.html").read_text()
TODAY = date(2026, 10, 29)


def _row(
    assignment_id: int,
    due: str,
    created: str = "Po 14.&thinsp;12.&thinsp;2026, 7:55",
    classes: str = "incomplete",
    link_class: str = "unread",
) -> str:
    href = f"/assignments/detail?assignment={assignment_id}"
    return f"""
<ul class="anyCourse course501 {classes} table_row" onclick="window.location.href = '{href}'">
  <li class="c1"><p><b>{due}</b></p><p>Matematika<span class="link_info">M - 1.A</span></p></li>
  <li class="c2"><p></p><div class="bold"><a class="{link_class}" href="{href}">Úkol</a></div>
    <p></p><p>PS str. 5</p></li>
  <li class="c4"><div class="smaller"><p><b>Jana Nováková</b></p><p>{created}</p></div></li>
</ul>"""


def _page(rows: str) -> str:
    return (
        '<section class="content"><div class="assignmentsContainer">'
        f'<div class="boxes1">{rows}</div></div></section>'
    )


def test_skips_the_header_rows_and_keeps_page_order():
    assert [a.id for a in parse_assignments(MAIN, today=TODAY)] == [9101, 9102, 9103]


def test_assignment_fields():
    assignment = parse_assignments(MAIN, today=TODAY)[0]
    assert assignment.title == "Čj - 1.A (Český jazyk) - Domácí úkol"
    assert assignment.due == datetime(2026, 11, 2, 8, 55, tzinfo=PORTAL_TZ)
    assert (assignment.subject, assignment.course_code) == ("Český jazyk", "Čj - 1.A")
    assert (assignment.course_id, assignment.short) == (501, "Čj")
    assert assignment.description == "Naučit se vyjmenovaná slova po B"
    assert assignment.created_by == "Jana Nováková"
    assert assignment.created == datetime(2026, 10, 19, 9, 30, tzinfo=PORTAL_TZ)
    assert assignment.unread


def test_done_follows_the_incomplete_class():
    assert [a.done for a in parse_assignments(MAIN, today=TODAY)] == [True, False, True]


def test_empty_description():
    assert parse_assignments(MAIN, today=TODAY)[2].description is None


def test_read_assignment():
    page = _page(_row(1, "do 2.&thinsp;11., 8:55", link_class=""))
    assert not parse_assignments(page, today=TODAY)[0].unread


def test_page_with_no_assignments():
    page = _page("<p><em>Žádné úkoly</em></p>")
    assert parse_assignments(page, today=TODAY) == []


def test_year_follows_the_creation_across_new_year():
    page = _page(_row(1, "do 8.&thinsp;1., 8:00"))
    assert parse_assignments(page, today=date(2027, 3, 1))[0].due.date() == date(2027, 1, 8)


def test_an_assignment_in_two_grids_is_returned_once():
    row = _row(1, "do 2.&thinsp;11., 8:55")
    assert len(parse_assignments(_page(row + row), today=TODAY)) == 1


def test_unparseable_row_is_skipped():
    page = _page(_row(1, "bez termínu") + _row(2, "do 2.&thinsp;11., 8:55"))
    assert [a.id for a in parse_assignments(page, today=TODAY)] == [2]


def test_only_unparseable_rows_raise():
    with pytest.raises(ParseError):
        parse_assignments(_page(_row(1, "bez termínu")), today=TODAY)


def test_other_page_raises():
    with pytest.raises(ParseError):
        parse_assignments("<html><body><p>Přihlášení</p></body></html>", today=TODAY)
