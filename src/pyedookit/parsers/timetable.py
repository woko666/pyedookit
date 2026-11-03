from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time

from bs4 import BeautifulSoup, Tag

from ..exceptions import ParseError
from ..models import PORTAL_TZ, Lesson, Period, SchoolEvent, Term
from .dates import find_days, portal_today

_TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")
_PERIOD_TITLE_RE = re.compile(r"^\s*(\d+)\s+(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")
_TERM_ID_RE = re.compile(r"termSelector-new=(\d+)")
_TILE_DAY_RE = re.compile(r"^(\d{4})_(\d{1,2})_(\d{1,2})$")
_EVENT_ID_RE = re.compile(r"event=(\d+)")
# "Mat - I.A", but also "Aj2- 1.A" (seen on a real portal).
_CODE_GROUP_SEP_RE = re.compile(r"\s*-\s+")
_NOTE_PREFIX = "Učivo:"
# A changed lesson gets a second, orange border after the default #999 one;
# there is no class or attribute for it (seen on 30 changed and 136
# cancelled lessons over ten weeks of a real portal, never on a regular one).
_CHANGED_BORDER_RE = re.compile(r"border:\s*1px solid #ea8400", re.IGNORECASE)
# One per student on the account, each directly after a heading with the name.
_STUDENT_SECTION = 'div[id^="snippet-familyTimetable-personTimetable-"]'
# An event tile's tooltip spans the whole day when the event covers it.
_ALL_DAY = (time(0, 0), time(23, 59))

_LOGGER = logging.getLogger(__name__)


def parse_timetable(html: str, today: date | None = None) -> list[Lesson]:
    """Parse the lessons of a rendered `/timetable/` page.

    Confirmed against real portal captures: the visible week grid is an
    absolutely-positioned canvas of `<a class="... lesson ...">` tiles. Each
    tile has a hover tooltip (`div.hoverLesson`, its id is the tile's with
    `boxId` swapped for `longDesc`) reading "M (Matematika) / Po 26. 10. /
    08:55–09:40", and a `span[data-day="2026_10_26"][data-lesson-id]` marker.
    The lesson's details are in a hidden `div.lesson-info#lesson-info-{id}`
    block with a fixed field order.

    The date comes from the tile marker (it has the year, the tooltip
    doesn't) and the time from the tooltip. The info block's own date/time
    field is only a fallback for a lesson without a tile: on a cancelled
    lesson it is empty (confirmed 2026-10-29), and it writes "Včera"/"Dnes"
    for recent days, resolved against `today` (defaults to the current date
    in `PORTAL_TZ`).

    A lesson that doesn't take place has its code struck through under an
    orange label: "Zrušeno" when cancelled, "Událost" when a school event
    replaces it (both confirmed). A lesson that takes place but differs from
    the regular timetable - another teacher, room or subject - only gets an
    orange border (confirmed). `is_changed` is set for both, `is_cancelled`
    only for the first.

    Each student on the account has their own section under a heading with
    their name, which goes in `student` (confirmed); a page without those
    sections leaves it `None`.
    """
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one("div.lesson-info") is None:
        # A week without lessons (summer holidays, confirmed) still renders
        # its empty grid; a page without one isn't a timetable.
        if soup.select_one("div.timetable-container") is None:
            raise ParseError("no lesson-info blocks found on timetable page")
        return []

    if today is None:
        today = portal_today()
    lessons = []
    for student, section in _student_sections(soup):
        tiles = {
            lesson_id: tile
            for tag, tile in _read_tiles(section)
            if (lesson_id := _tile_lesson_id(tag)) is not None
        }
        for block in section.select("div.lesson-info"):
            lesson_id = str(block.get("id") or "").removeprefix("lesson-info-")
            lesson = _parse_lesson_info(block, tiles.get(lesson_id), today, student)
            if lesson is None:
                _LOGGER.warning("skipping unparseable lesson block %s", block.get("id"))
            else:
                lessons.append(lesson)
    return lessons


def parse_timetable_events(html: str) -> list[SchoolEvent]:
    """Parse the school events drawn across the `/timetable/` week grid.

    Confirmed against a real capture: an event is an `a.lesson.event` tile
    per day it covers, linking to `/timetable/detail?event={id}`, with a
    tooltip giving the day and "00:00–23:59" for a whole day, or the
    event's hours ("08:00–11:59", confirmed). Its title is in bold on the
    tile. Each tile gives one single-day event, as the grid draws it: a
    multi-day event comes back once per day under the same id. A whole day
    gets `date` start and end, otherwise they are times on that day.

    An event that concerns several students is drawn in each one's section
    (confirmed), and comes back once per student with `student` set.
    """
    soup = BeautifulSoup(html, "lxml")
    events: dict[tuple[str | None, str, date], SchoolEvent] = {}
    for student, section in _student_sections(soup):
        for tag, tile in _read_tiles(section):
            if "event" not in (tag.get("class") or []):
                continue
            match = _EVENT_ID_RE.search(str(tag.get("href") or ""))
            if match is None:
                _LOGGER.warning("skipping event tile without an event link %s", tag.get("id"))
                continue
            key = (student, match.group(1), tile.day)
            if key in events:
                continue
            heading = tag.select_one(".row b, .lessonRow b")
            start: datetime | date
            end: datetime | date
            if (tile.start, tile.end) == _ALL_DAY:
                start = end = tile.day
            else:
                start = datetime.combine(tile.day, tile.start, tzinfo=PORTAL_TZ)
                end = datetime.combine(tile.day, tile.end, tzinfo=PORTAL_TZ)
            events[key] = SchoolEvent(
                id=match.group(1),
                title=heading.get_text(" ", strip=True) if heading else "",
                start=start,
                end=end,
                student=student,
            )
    return sorted(events.values(), key=lambda event: _event_day(event.start))


def _event_day(start: datetime | date) -> date:
    return start.date() if isinstance(start, datetime) else start


def parse_periods(html: str) -> list[Period]:
    """Parse the bell schedule from the `period-time` header cells of the week grid.

    Each cell carries a title like "1\xa008:00–08:45 " (number, non-breaking
    space, time range). Returns an empty list when the page has no header.

    The students' grids are merged: each draws only the periods its week
    needs, and a period's times never differed between them on a real portal.
    """
    soup = BeautifulSoup(html, "lxml")
    periods: dict[int, Period] = {}
    for cell in soup.select("div.period-time[title]"):
        title = str(cell.get("title") or "").replace("\xa0", " ")
        match = _PERIOD_TITLE_RE.match(title)
        if not match:
            _LOGGER.debug("skipping unparseable period title %r", title)
            continue
        number, sh, sm, eh, em = (int(g) for g in match.groups())
        periods.setdefault(number, Period(number, time(sh, sm), time(eh, em)))
    return [periods[number] for number in sorted(periods)]


def parse_terms(html: str) -> list[Term]:
    soup = BeautifulSoup(html, "lxml")
    terms: list[Term] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="termSelector-new"]'):
        href = str(link.get("href") or "")
        match = _TERM_ID_RE.search(href)
        if not match:
            continue
        term_id = match.group(1)
        if term_id in seen:
            continue
        seen.add(term_id)
        terms.append(Term(id=term_id, name=link.get_text(strip=True)))
    return terms


@dataclass(frozen=True, slots=True)
class _Tile:
    day: date
    start: time
    end: time
    cancelled: bool
    changed: bool


def _student_sections(soup: BeautifulSoup) -> list[tuple[str | None, Tag]]:
    sections = soup.select(_STUDENT_SECTION)
    if not sections:
        return [(None, soup)]
    return [(_student_name(section), section) for section in sections]


def _student_name(section: Tag) -> str | None:
    # Only the immediate sibling: a section missing its heading mustn't take
    # the previous student's. The class and the PDF link are child tags of
    # the heading, so its own strings are just the name.
    heading = section.find_previous_sibling()
    if heading is None or heading.name != "h2":
        return None
    return " ".join(" ".join(heading.find_all(string=True, recursive=False)).split()) or None


def _read_tiles(root: Tag) -> list[tuple[Tag, _Tile]]:
    tooltips = {str(t.get("id")): t for t in root.select("div.hoverLesson[id]")}
    tiles = []
    for tag in root.select("a.lesson[id]"):
        tooltip = tooltips.get(str(tag.get("id")).replace("boxId", "longDesc"))
        tile = _parse_tile(tag, tooltip)
        if tile is None:
            _LOGGER.warning("skipping unparseable timetable tile %s", tag.get("id"))
        else:
            tiles.append((tag, tile))
    return tiles


def _parse_tile(tag: Tag, tooltip: Tag | None) -> _Tile | None:
    marker = tag.select_one("span[data-day]")
    day_match = _TILE_DAY_RE.match(str(marker.get("data-day") or "")) if marker else None
    if tooltip is None or day_match is None:
        return None
    time_match = _TIME_RANGE_RE.search(tooltip.get_text(" ", strip=True))
    if time_match is None:
        return None
    year, month, day = (int(g) for g in day_match.groups())
    sh, sm, eh, em = (int(g) for g in time_match.groups())
    cancelled = tag.select_one(".lessonRow s") is not None
    try:
        return _Tile(
            day=date(year, month, day),
            start=time(sh, sm),
            end=time(eh, em),
            cancelled=cancelled,
            changed=cancelled or bool(_CHANGED_BORDER_RE.search(str(tag.get("style") or ""))),
        )
    except ValueError:
        return None


def _tile_lesson_id(tag: Tag) -> str | None:
    marker = tag.select_one("span[data-lesson-id]")
    lesson_id = str(marker.get("data-lesson-id") or "") if marker else ""
    return lesson_id or None


def _parse_lesson_info(
    block: Tag, tile: _Tile | None, today: date, student: str | None = None
) -> Lesson | None:
    # The date/time field comes first; it is empty on a cancelled lesson.
    fields = block.find_all("div", recursive=False)
    if not fields:
        return None
    if tile is not None:
        lesson_date, start, end = tile.day, tile.start, tile.end
    else:
        text = fields[0].get_text(" ", strip=True)
        days = find_days(text, today)
        time_match = _TIME_RANGE_RE.search(text)
        if not days or not time_match:
            return None
        sh, sm, eh, em = (int(g) for g in time_match.groups())
        lesson_date, start, end = days[0], time(sh, sm), time(eh, em)

    # Field order after the date/time div is fixed on every lesson observed:
    # "{code} - {group}", full subject name, teacher(s), room, curriculum
    # notes (last one is sometimes absent).
    rest = fields[1:]
    code_group = rest[0].get_text(strip=True) if len(rest) > 0 else ""
    subject = rest[1].get_text(strip=True) if len(rest) > 1 else code_group
    teacher = rest[2].get_text(strip=True) if len(rest) > 2 else None
    room = rest[3].get_text(strip=True) if len(rest) > 3 else None

    note = None
    if len(rest) > 4:
        note_text = rest[4].get_text(" ", strip=True)
        if note_text.startswith(_NOTE_PREFIX):
            note = note_text[len(_NOTE_PREFIX) :].strip() or None

    code, _, group = _CODE_GROUP_SEP_RE.sub(" - ", code_group, count=1).partition(" - ")

    return Lesson(
        start=datetime.combine(lesson_date, start, tzinfo=PORTAL_TZ),
        end=datetime.combine(lesson_date, end, tzinfo=PORTAL_TZ),
        subject=subject or code_group,
        teacher=teacher or None,
        room=room or None,
        group=group or None,
        is_changed=tile.changed if tile is not None else False,
        is_cancelled=tile.cancelled if tile is not None else False,
        note=note,
        code=code.strip() or None,
        student=student,
    )
