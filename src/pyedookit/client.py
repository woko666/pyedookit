from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping

import aiohttp

from . import auth, transport
from .exceptions import ParseError, SessionExpired
from .models import (
    Assignment,
    Exam,
    Grades,
    Lesson,
    Message,
    MessageSummary,
    Payments,
    SchoolEvent,
    Term,
    TimetableWeek,
)
from .parsers.assignments import parse_assignments
from .parsers.events import parse_events
from .parsers.exams import parse_exams
from .parsers.grades import parse_grade_listing, parse_subjects
from .parsers.inbox import parse_inbox
from .parsers.message import parse_message
from .parsers.payments import parse_payments
from .parsers.timetable import (
    parse_periods,
    parse_terms,
    parse_timetable,
    parse_timetable_events,
)
from .session import Session

_LOGGER = logging.getLogger(__name__)

_TIMETABLE_PATH = "/timetable/"
_THIS_WEEK = {"do": "familyTimetable-resetFilter"}
_EVENTS_PATH = "/timetable/upcoming"
_PAYMENTS_PATH = "/payments/"
_INBOX_PATH = "/overview/updates"
_MESSAGE_PATH = "/messages/detail"
_GRADES_PATH = "/evaluation/listing"
_SUBJECTS_PATH = "/evaluation/"
_EXAMS_PATH = "/exams/"
_ASSIGNMENTS_PATH = "/assignments/"

CAPTURE_PAGES: dict[str, str] = {
    "consents": "/consents/",
    # Term marks (orange on the portal) have no known markup until published.
    "evaluation_overall": "/evaluation/overall",
}


def _parse_week(html: str) -> TimetableWeek:
    periods = parse_periods(html)
    try:
        lessons = parse_timetable(html)
    except ParseError:
        # A holiday week renders the bell-schedule header but no lessons;
        # a page with neither is not a timetable at all.
        if not periods:
            raise
        lessons = []
    return TimetableWeek(lessons, periods, parse_timetable_events(html))


def _parse_grades(listing: str, subjects: str) -> Grades:
    term, grades = parse_grade_listing(listing)
    return Grades(term, grades, parse_subjects(subjects, term))


class EdookitClient:
    """Fetches parsed data from one school's Edookit parent portal.

    Renews an expired edookit session by itself from the Plus4U sign-in and
    retries once; `on_session_update` receives each renewed `Session` so the
    caller can persist it. `AuthError` means the Plus4U sign-in expired too
    and a full `login()` is needed.

    Pass `http` to reuse an existing `aiohttp.ClientSession` across requests
    instead of opening a new connection per call; otherwise one is created
    and closed per request. Cookies are sent by hand, so give it a
    `aiohttp.DummyCookieJar()` rather than a shared session whose jar would
    also collect the portal's cookies.

    Pages are parsed in a worker thread so a large timetable doesn't stall
    the caller's event loop.
    """

    def __init__(
        self,
        session: Session,
        http: aiohttp.ClientSession | None = None,
        *,
        on_session_update: Callable[[Session], None] | None = None,
    ) -> None:
        self._session = session
        self._http = http
        self._on_session_update = on_session_update
        self._renew_lock = asyncio.Lock()
        self._week_lock = asyncio.Lock()

    @property
    def session(self) -> Session:
        return self._session

    async def get_terms(self) -> list[Term]:
        return await asyncio.to_thread(parse_terms, await self.fetch_timetable_html())

    async def get_timetable(self, week_offset: int = 0) -> list[Lesson]:
        html = await self.fetch_timetable_html(week_offset)
        return await asyncio.to_thread(parse_timetable, html)

    async def get_timetable_week(self, week_offset: int = 0) -> TimetableWeek:
        """Lessons and bell schedule of one week, parsed from a single fetch."""
        html = await self.fetch_timetable_html(week_offset)
        return await asyncio.to_thread(_parse_week, html)

    async def fetch_timetable_html(self, week_offset: int = 0) -> str:
        """Raw `/timetable/` HTML for one week - also used by
        scripts/capture_fixtures.py, so the request shape lives in one place.

        The portal keeps the shown week in the server session and its arrows
        move it by a number of days from wherever it is (confirmed), so each
        fetch first resets it to the current week. Resetting is absolute, but
        reset-then-move is not: the lock keeps two such fetches from
        interleaving into a double move.
        """
        if week_offset == 0:
            return await self._get(_TIMETABLE_PATH, params=_THIS_WEEK)
        async with self._week_lock:
            await self._get(_TIMETABLE_PATH, params=_THIS_WEEK)
            return await self._get(
                _TIMETABLE_PATH,
                params={
                    "familyTimetable-value": 7 * week_offset,
                    "do": "familyTimetable-changeFilter",
                },
            )

    async def get_events(self) -> list[SchoolEvent]:
        return await asyncio.to_thread(parse_events, await self._get(_EVENTS_PATH))

    async def get_payments(self) -> Payments:
        return await asyncio.to_thread(parse_payments, await self._get(_PAYMENTS_PATH))

    async def get_inbox(self) -> list[MessageSummary]:
        return await asyncio.to_thread(parse_inbox, await self._get(_INBOX_PATH, ajax=False))

    async def get_grades(self) -> Grades:
        """The current term's grades and the subjects with their attendance."""
        listing, subjects = await asyncio.gather(
            self._get(_GRADES_PATH, ajax=False), self._get(_SUBJECTS_PATH, ajax=False)
        )
        return await asyncio.to_thread(_parse_grades, listing, subjects)

    async def get_exams(self) -> list[Exam]:
        """Upcoming, today's and recent exams; older ones are only in the portal's archive."""
        return await asyncio.to_thread(parse_exams, await self._get(_EXAMS_PATH))

    async def get_assignments(self) -> list[Assignment]:
        """Today's, upcoming and recent homework; older ones are only in the portal's archive."""
        return await asyncio.to_thread(parse_assignments, await self._get(_ASSIGNMENTS_PATH))

    async def get_message(self, message_id: int) -> Message:
        """One message in full; fetching it marks the message read on the portal."""
        html = await self._get(_MESSAGE_PATH, params={"message": message_id}, ajax=False)
        return await asyncio.to_thread(parse_message, html, message_id, self._session.base_url)

    async def fetch_page(self, name: str) -> str:
        """Raw HTML of one of `CAPTURE_PAGES`, for pages without a parser yet."""
        try:
            path = CAPTURE_PAGES[name]
        except KeyError:
            raise ValueError(f"{name!r} is not one of {sorted(CAPTURE_PAGES)}") from None
        return await self._get(path)

    async def _get(
        self, path: str, params: Mapping[str, str | int] | None = None, *, ajax: bool = True
    ) -> str:
        """GET a portal page, renewing the session once if it has expired.

        `ajax=False` requests the page as a browser's first load does: for
        an AJAX request the portal renders the inbox's first-run guide with
        sample items instead of the real ones.
        """
        session = self._session
        try:
            return await self._fetch(session, path, params, ajax)
        except SessionExpired:
            _LOGGER.debug("Edookit session expired, renewing it")
            await self._renew(session)
        return await self._fetch(self._session, path, params, ajax)

    async def _renew(self, stale: Session) -> None:
        async with self._renew_lock:
            if self._session is not stale:
                return
            self._session = await auth.refresh(stale, http=self._http)
            if self._on_session_update is not None:
                self._on_session_update(self._session)

    async def _fetch(
        self, session: Session, path: str, params: Mapping[str, str | int] | None, ajax: bool
    ) -> str:
        headers = (
            {"X-CSRF-Token": session.csrf_token, "X-Requested-With": "XMLHttpRequest"}
            if ajax
            else None
        )
        async with transport.http_session(self._http) as http:
            response = await transport.request(
                http,
                "GET",
                f"{session.base_url}{path}",
                session.cookies,
                headers=headers,
                params=params,
            )
        if (
            response.is_redirect
            or _is_ajax_redirect(response.content_type, response.text)
            or (not ajax and _is_login_page(response.text))
        ):
            raise SessionExpired("edookit session has expired")
        return response.text


def _is_login_page(body: str) -> bool:
    """Whether a plain GET was answered with the sign-in page.

    An expired session has only been seen as an AJAX redirect; a plain GET is
    expected to get an HTTP redirect, but a 200 sign-in page is caught here
    too. Every signed-in portal page carries the logout link.
    """
    return "/user/login" in body and "/user/logout" not in body


def _is_ajax_redirect(content_type: str, body: str) -> bool:
    """Whether the portal answered with an AJAX-style redirect.

    Requests carry X-Requested-With, so an expired session gets a 200 with a
    JSON {"redirect": url} rather than an HTTP redirect. Both shapes seen on a
    real portal are expired sessions: /user/login?message=1 and
    /oidc/logout?performCallback=.... A redirect answer never carries the
    page, so the target URL is deliberately not inspected.
    """
    if not content_type.startswith("application/json"):
        return False
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("redirect"), str)
