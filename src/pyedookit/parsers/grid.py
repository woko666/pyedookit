"""Helpers for the `ul.table_row` grids that the exams and assignments pages share.

Both draw a row per item with an `assignment=<id>` onclick and a
`course<id>` class, the date in `li.c1 b`, the course in the second `li.c1`
paragraph, and who created it and when in `li.c4`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time

from bs4 import Tag
from bs4.element import NavigableString

from ..models import PORTAL_TZ
from .dates import TIME_RE, find_days
from .text import tag_text

_ASSIGNMENT_ID_RE = re.compile(r"assignment=(\d+)")
_COURSE_CLASS_RE = re.compile(r"^course(\d+)$")
# A numeric date with no year after it: "22. 10." but not "22. 10. 2026".
NO_YEAR_RE = re.compile(r"\d{1,2}\.\s*\d{1,2}\.(?!\s*\d{4})")


def row_id(row: Tag) -> int | None:
    match = _ASSIGNMENT_ID_RE.search(str(row.get("onclick", "")))
    return int(match.group(1)) if match else None


def row_created(row: Tag, today: date) -> datetime | None:
    """ "St 14. 10. 2026, 10:32"; seen with the year even in the current one."""
    lines = row.select("li.c4 .smaller p")
    text = tag_text(lines[-1]) if lines else ""
    days = find_days(text, today)
    found = TIME_RE.search(text)
    if not days or found is None:
        return None
    return datetime.combine(
        days[0], time(int(found.group(1)), int(found.group(2))), tzinfo=PORTAL_TZ
    )


def row_created_by(row: Tag) -> str:
    return tag_text(row.select_one("li.c4 .smaller b"))


def row_course(row: Tag) -> tuple[str, str | None]:
    """The subject name and the course code: ("Matematika", "M T - 1.A")."""
    paragraphs = row.select("li.c1 > p")
    course = paragraphs[1] if len(paragraphs) > 1 else None
    if course is None:
        return "", None
    return loose_text(course), tag_text(course.select_one("span.link_info")) or None


def row_course_id(row: Tag) -> int | None:
    for name in row.get("class") or []:
        if match := _COURSE_CLASS_RE.match(name):
            return int(match.group(1))
    return None


def row_description(row: Tag) -> str | None:
    return " ".join(text for p in row.select("li.c2 > p") if (text := tag_text(p))) or None


def nearest_year(day: date, anchor: date) -> date:
    """The day's year that puts it closest to `anchor`, so a date in January
    entered in December lands in the next year."""
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(day.replace(year=year))
        except ValueError:  # 29 February
            continue
    return min(candidates, key=lambda d: abs(d - anchor))


def loose_text(tag: Tag) -> str:
    """The tag's own text, leaving out its child elements' text."""
    own = "".join(str(child) for child in tag.children if isinstance(child, NavigableString))
    return " ".join(own.split())
