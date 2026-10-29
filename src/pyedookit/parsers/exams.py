from __future__ import annotations

import logging
from datetime import date, datetime, time

from bs4 import BeautifulSoup, Tag

from ..exceptions import ParseError
from ..models import PORTAL_TZ, Exam
from .dates import TIME_RE, find_days, portal_today
from .grid import (
    NO_YEAR_RE,
    loose_text,
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


def parse_exams(html: str, today: date | None = None) -> list[Exam]:
    """Parse Písemky, zkoušení (`/exams/`).

    The page has three grids: upcoming, today's and recent exams. Exams are
    returned in page order, each once. Confirmed against a real capture: the
    recent grid's rows (`ul.table_row` with an `assignment=<id>` onclick,
    columns c1/c2/c4) and the empty-grid message. Upcoming and today's grids
    were empty in it and are assumed to use the same rows. Older exams are
    only in the archive (`/exams/archive`), which isn't read.
    """
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("section.content")
    if content is None or content.select_one("div.boxes1") is None:
        raise ParseError("exams page has no exam grids")

    if today is None:
        today = portal_today()
    rows = content.select("ul.table_row[onclick]")
    exams: list[Exam] = []
    seen: set[int] = set()
    for row in rows:
        exam = _parse_row(row, today)
        if exam is None:
            _LOGGER.warning("skipping unparseable exam row")
        elif exam.id not in seen:
            seen.add(exam.id)
            exams.append(exam)
    if rows and not exams:
        raise ParseError("no exam row on the exams page could be read")
    return exams


def _parse_row(row: Tag, today: date) -> Exam | None:
    exam_id = row_id(row)
    created = row_created(row, today)
    when = row.select_one("li.c1 b")
    link = row.select_one("li.c2 div.bold a")
    if exam_id is None or created is None or when is None or link is None:
        return None
    text = _text(when)
    days = find_days(text, today)
    times = [time(int(h), int(m)) for h, m in TIME_RE.findall(text)]
    if len(days) != 1 or len(times) != 2:
        return None
    day = days[0]
    if NO_YEAR_RE.search(text):
        # Seen without a year ("22. 10., od 8:00 do 8:45") even for exams of
        # the current school year, so the year is taken from when it was
        # announced rather than from today.
        day = nearest_year(day, created.date())

    paragraphs = row.select("li.c1 > p")
    subject, code = row_course(row)
    return Exam(
        id=exam_id,
        title=_text(link),
        start=datetime.combine(day, times[0], tzinfo=PORTAL_TZ),
        end=datetime.combine(day, times[1], tzinfo=PORTAL_TZ),
        subject=subject,
        kind=_text(paragraphs[2]) if len(paragraphs) > 2 else "",
        category=_category(row),
        created_by=row_created_by(row),
        created=created,
        unread="unread" in (link.get("class") or []),
        description=row_description(row),
        course_id=row_course_id(row),
        course_code=code,
    )


def _category(row: Tag) -> str:
    """The text after the last paragraph of the first column ("Písemná práce"),
    which the portal leaves outside any element."""
    cell = row.select_one("li.c1")
    return loose_text(cell) if cell else ""
