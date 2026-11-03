from __future__ import annotations

import logging
from datetime import date, datetime, time

from bs4 import BeautifulSoup, Tag

from ..exceptions import ParseError
from ..models import PORTAL_TZ, Assignment
from .dates import TIME_RE, find_days, portal_today
from .grid import (
    NO_YEAR_RE,
    nearest_year,
    row_course,
    row_course_id,
    row_created,
    row_created_by,
    row_description,
    row_id,
)
from .text import tag_text as _text

_LOGGER = logging.getLogger(__name__)


def parse_assignments(html: str, today: date | None = None) -> list[Assignment]:
    """Parse Domácí úkoly (`/assignments/`).

    The page has three grids: today's, upcoming and recent homework.
    Assignments are returned in page order, each once. Confirmed against a
    real capture: the upcoming and recent grids, the empty-grid message,
    and the "incomplete" row class matching the checkbox (not done) versus
    the finished marker (done). A filled today's grid, deadlines without a
    time and attachments haven't been seen. Older homework is only in the
    archive (`/assignments/archive`), which isn't read.
    """
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("section.content div.assignmentsContainer")
    if content is None or content.select_one("div.boxes1") is None:
        raise ParseError("assignments page has no assignment grids")

    if today is None:
        today = portal_today()
    rows = content.select("ul.table_row[onclick]")
    assignments: list[Assignment] = []
    seen: set[int] = set()
    for row in rows:
        assignment = _parse_row(row, today)
        if assignment is None:
            _LOGGER.warning("skipping unparseable assignment row")
        elif assignment.id not in seen:
            seen.add(assignment.id)
            assignments.append(assignment)
    if rows and not assignments:
        raise ParseError("no assignment row on the assignments page could be read")
    return assignments


def _parse_row(row: Tag, today: date) -> Assignment | None:
    assignment_id = row_id(row)
    created = row_created(row, today)
    due = row.select_one("li.c1 b")
    link = row.select_one("li.c2 div.bold a")
    if assignment_id is None or created is None or due is None or link is None:
        return None
    text = _text(due)
    days = find_days(text, today)
    found = TIME_RE.search(text)
    if len(days) != 1 or found is None:
        return None
    day = days[0]
    if NO_YEAR_RE.search(text):
        # "do 2. 11., 8:55": seen without a year throughout, so as on the
        # exams page the year is taken from when it was set.
        day = nearest_year(day, created.date())

    subject, code = row_course(row)
    return Assignment(
        id=assignment_id,
        title=_text(link),
        due=datetime.combine(day, time(int(found.group(1)), int(found.group(2))), tzinfo=PORTAL_TZ),
        subject=subject,
        created_by=row_created_by(row),
        created=created,
        unread="unread" in (link.get("class") or []),
        done="incomplete" not in (row.get("class") or []),
        description=row_description(row),
        course_id=row_course_id(row),
        course_code=code,
    )
