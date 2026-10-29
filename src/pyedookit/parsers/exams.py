from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from ..exceptions import ParseError
from ..models import PORTAL_TZ, Exam
from .dates import TIME_RE, find_days, portal_today
from .text import tag_text as _text

_ASSIGNMENT_ID_RE = re.compile(r"assignment=(\d+)")
_COURSE_CLASS_RE = re.compile(r"^course(\d+)$")
# A numeric date with no year after it: "22. 10." but not "22. 10. 2026".
_NO_YEAR_RE = re.compile(r"\d{1,2}\.\s*\d{1,2}\.(?!\s*\d{4})")

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
    id_match = _ASSIGNMENT_ID_RE.search(str(row.get("onclick", "")))
    created = _created(row, today)
    when = row.select_one("li.c1 b")
    link = row.select_one("li.c2 div.bold a")
    if id_match is None or created is None or when is None or link is None:
        return None
    text = _text(when)
    days = find_days(text, today)
    times = [time(int(h), int(m)) for h, m in TIME_RE.findall(text)]
    if len(days) != 1 or len(times) != 2:
        return None
    day = days[0]
    if _NO_YEAR_RE.search(text):
        # Seen without a year ("22. 10., od 8:00 do 8:45") even for exams of
        # the current school year, so the year is taken from when it was
        # announced rather than from today.
        day = _nearest_year(day, created.date())

    paragraphs = row.select("li.c1 > p")
    course = paragraphs[1] if len(paragraphs) > 1 else None
    code = course.select_one("span.link_info") if course else None
    subject = _loose_text(course) if course else ""
    description = " ".join(text for p in row.select("li.c2 > p") if (text := _text(p)))
    return Exam(
        id=int(id_match.group(1)),
        title=_text(link),
        start=datetime.combine(day, times[0], tzinfo=PORTAL_TZ),
        end=datetime.combine(day, times[1], tzinfo=PORTAL_TZ),
        subject=subject,
        kind=_text(paragraphs[2]) if len(paragraphs) > 2 else "",
        category=_category(row),
        created_by=_text(row.select_one("li.c4 .smaller b")),
        created=created,
        unread="unread" in (link.get("class") or []),
        description=description or None,
        course_id=_course_id(row),
        course_code=_text(code) or None,
    )


def _created(row: Tag, today: date) -> datetime | None:
    """"St 14. 10. 2026, 10:32"; seen with the year even in the current one."""
    lines = row.select("li.c4 .smaller p")
    text = _text(lines[-1]) if lines else ""
    days = find_days(text, today)
    found = TIME_RE.search(text)
    if not days or found is None:
        return None
    return datetime.combine(
        days[0], time(int(found.group(1)), int(found.group(2))), tzinfo=PORTAL_TZ
    )


def _nearest_year(day: date, anchor: date) -> date:
    """The day's year that puts it closest to `anchor`, so an exam in January
    announced in December lands in the next year."""
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(day.replace(year=year))
        except ValueError:  # 29 February
            continue
    return min(candidates, key=lambda d: abs(d - anchor))


def _category(row: Tag) -> str:
    """The text after the last paragraph of the first column ("Písemná práce"),
    which the portal leaves outside any element."""
    cell = row.select_one("li.c1")
    return _loose_text(cell) if cell else ""


def _loose_text(tag: Tag) -> str:
    """The tag's own text, leaving out its child elements' text."""
    own = "".join(str(child) for child in tag.children if isinstance(child, NavigableString))
    return " ".join(own.split())


def _course_id(row: Tag) -> int | None:
    for name in row.get("class") or []:
        if match := _COURSE_CLASS_RE.match(name):
            return int(match.group(1))
    return None
