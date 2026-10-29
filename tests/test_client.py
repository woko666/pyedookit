import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest

from pyedookit import auth
from pyedookit.client import EdookitClient
from pyedookit.exceptions import AuthError, EdookitConnectionError, ParseError, SessionExpired
from pyedookit.session import Session

from .fakes import FailingResponse, FakeClientSession, ok, redirect

SCHOOL = "zs-example"
TIMETABLE_URL = f"https://{SCHOOL}.edookit.net/timetable/"

SESSION = Session.from_cookies(
    SCHOOL, edookit={"PHPSESSID": "s", "uu.app.csrf": "csrf"}, plus4u={}
)


EXPIRED = '{"redirect":"https://zs-example.edookit.net/user/login?message=1","state":[]}'


def _expired() -> object:
    return ok(TIMETABLE_URL, body=EXPIRED, content_type="application/json")


def _renewed(cookie: str) -> Session:
    return Session.from_cookies(SCHOOL, edookit={"PHPSESSID": cookie}, plus4u={"uoid.ps": "p"})


@pytest.mark.asyncio
async def test_a_redirect_is_never_followed_and_counts_as_expired(monkeypatch):
    # A redirect must never be followed here: our Cookie header is set
    # manually, and following it would forward the school's session cookie
    # to whatever host the redirect points at (see transport.request).
    monkeypatch.setattr(auth, "refresh", AsyncMock(side_effect=AuthError("stop here")))
    fake = FakeClientSession(
        [("GET", TIMETABLE_URL, redirect("https://uuidentity.plus4u.net/oidc/auth"))]
    )

    with pytest.raises(AuthError):
        await EdookitClient(SESSION, http=fake).fetch_timetable_html()


@pytest.mark.asyncio
async def test_get_raises_session_expired_on_the_ajax_login_redirect(monkeypatch):
    monkeypatch.setattr(auth, "refresh", AsyncMock(side_effect=AuthError("stop here")))
    response = ok(TIMETABLE_URL, body=EXPIRED, content_type="application/json; charset=utf-8")
    client = EdookitClient(SESSION, http=FakeClientSession([("GET", TIMETABLE_URL, response)]))

    with pytest.raises(AuthError):
        await client.fetch_timetable_html()


@pytest.mark.asyncio
async def test_expired_session_is_renewed_reported_and_retried(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    updates: list[Session] = []
    fake = FakeClientSession(
        [("GET", TIMETABLE_URL, _expired()), ("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body="tt"))]
    )
    client = EdookitClient(SESSION, http=fake, on_session_update=updates.append)

    assert await client.fetch_timetable_html() == "tt"
    assert [s.cookies.get(f"{SCHOOL}.edookit.net", "PHPSESSID") for s in updates] == ["new"]
    assert client.session is updates[0]
    refresh.assert_awaited_once_with(SESSION, http=fake)


@pytest.mark.asyncio
async def test_still_expired_after_renewal_raises_without_looping(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    fake = FakeClientSession(
        [("GET", TIMETABLE_URL, _expired()), ("GET", TIMETABLE_URL, _expired())]
    )
    client = EdookitClient(SESSION, http=fake)

    with pytest.raises(SessionExpired):
        await client.fetch_timetable_html()
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_plus4u_sign_in_raises_auth_error(monkeypatch):
    monkeypatch.setattr(auth, "refresh", AsyncMock(side_effect=AuthError("expired")))
    client = EdookitClient(SESSION, http=FakeClientSession([("GET", TIMETABLE_URL, _expired())]))

    with pytest.raises(AuthError):
        await client.fetch_timetable_html()


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_renewal(monkeypatch):
    gate = asyncio.Event()

    async def slow_refresh(session, *, http=None):
        await gate.wait()
        return _renewed("new")

    refresh = AsyncMock(side_effect=slow_refresh)
    monkeypatch.setattr(auth, "refresh", refresh)
    fake = FakeClientSession(
        [
            ("GET", TIMETABLE_URL, _expired()),
            ("GET", TIMETABLE_URL, _expired()),
            ("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body="a")),
            ("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body="b")),
        ]
    )
    client = EdookitClient(SESSION, http=fake)

    calls = asyncio.gather(client.fetch_timetable_html(), client.fetch_timetable_html())
    for _ in range(5):  # let both calls hit the expiry and queue on the lock
        await asyncio.sleep(0)
    gate.set()

    assert sorted(await calls) == ["a", "b"]
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_oidc_logout_ajax_redirect_triggers_renewal(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    logout = ok(
        TIMETABLE_URL,
        body='{"redirect":"https://zs-example.edookit.net/oidc/logout?performCallback=1","state":[]}',
        content_type="application/json; charset=utf-8",
    )
    fake = FakeClientSession(
        [("GET", TIMETABLE_URL, logout), ("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body="tt"))]
    )
    client = EdookitClient(SESSION, http=fake)

    assert await client.fetch_timetable_html() == "tt"
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_returns_a_json_body_without_a_redirect_as_is(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    response = ok(TIMETABLE_URL, body='{"state":[]}', content_type="application/json")
    client = EdookitClient(SESSION, http=FakeClientSession([("GET", TIMETABLE_URL, response)]))

    assert await client.fetch_timetable_html() == '{"state":[]}'
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_returns_body_on_success():
    fake = FakeClientSession([("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body="<html>tt</html>"))])
    client = EdookitClient(SESSION, http=fake)

    html = await client.fetch_timetable_html()

    assert html == "<html>tt</html>"


@pytest.mark.asyncio
async def test_get_raises_connection_error_on_timeout():
    fake = FakeClientSession([("GET", TIMETABLE_URL, FailingResponse(TimeoutError()))])
    client = EdookitClient(SESSION, http=fake)

    with pytest.raises(EdookitConnectionError):
        await client.fetch_timetable_html()


@pytest.mark.asyncio
async def test_get_raises_connection_error_on_client_error():
    fake = FakeClientSession(
        [("GET", TIMETABLE_URL, FailingResponse(aiohttp.ClientConnectionError("reset")))]
    )
    client = EdookitClient(SESSION, http=fake)

    with pytest.raises(EdookitConnectionError):
        await client.fetch_timetable_html()


EVENTS_URL = f"https://{SCHOOL}.edookit.net/timetable/upcoming"
PAYMENTS_URL = f"https://{SCHOOL}.edookit.net/payments/"
EXAMS_URL = f"https://{SCHOOL}.edookit.net/exams/"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_get_events_parses_the_upcoming_page():
    body = (FIXTURES / "upcoming_events_structure.html").read_text()
    client = EdookitClient(
        SESSION, http=FakeClientSession([("GET", EVENTS_URL, ok(EVENTS_URL, body=body))])
    )

    events = await client.get_events()

    assert [e.id for e in events] == ["5001", "5002", "5003", "5004", "5005"]


@pytest.mark.asyncio
async def test_get_exams_parses_the_exams_page():
    body = (FIXTURES / "exams_structure.html").read_text()
    client = EdookitClient(
        SESSION, http=FakeClientSession([("GET", EXAMS_URL, ok(EXAMS_URL, body=body))])
    )

    assert [e.id for e in await client.get_exams()] == [9003, 9002, 9001]


@pytest.mark.asyncio
async def test_get_payments_parses_the_payments_page():
    body = (FIXTURES / "payments_structure.html").read_text()
    client = EdookitClient(
        SESSION, http=FakeClientSession([("GET", PAYMENTS_URL, ok(PAYMENTS_URL, body=body))])
    )

    payments = await client.get_payments()

    assert payments.remaining == Decimal(321)


@pytest.mark.asyncio
async def test_fetch_page_only_allows_capture_pages():
    url = f"https://{SCHOOL}.edookit.net/assignments/"
    client = EdookitClient(
        SESSION, http=FakeClientSession([("GET", url, ok(url, body="<p>x</p>"))])
    )

    assert await client.fetch_page("assignments") == "<p>x</p>"
    with pytest.raises(ValueError):
        await client.fetch_page("settings")


@pytest.mark.asyncio
async def test_cookies_rotated_by_the_portal_are_kept_and_sent_next_time():
    seen: list[dict] = []

    def first(headers):
        seen.append(headers)
        return ok(TIMETABLE_URL, raw_set_cookies=["PHPSESSID=rotated; path=/"])

    def second(headers):
        seen.append(headers)
        return ok(TIMETABLE_URL)

    session = Session.from_cookies(SCHOOL, edookit={"PHPSESSID": "s"}, plus4u={})
    fake = FakeClientSession([("GET", TIMETABLE_URL, first), ("GET", TIMETABLE_URL, second)])
    client = EdookitClient(session, http=fake)

    await client.fetch_timetable_html()
    await client.fetch_timetable_html()

    assert seen[1]["Cookie"] == "PHPSESSID=rotated"


@pytest.mark.asyncio
async def test_get_timetable_week_parses_lessons_and_periods_from_one_fetch():
    # FakeClientSession consumes each route once, so scripting a single
    # /timetable/ route proves one fetch: a second request would fail.
    html = (Path(__file__).parent / "fixtures" / "timetable_structure.html").read_text()
    fake = FakeClientSession([("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body=html))])

    week = await EdookitClient(SESSION, http=fake).get_timetable_week()

    assert [lesson.code for lesson in week.lessons] == ["Mat", "Tv"]
    assert [period.number for period in week.periods] == [1, 2, 3]


@pytest.mark.asyncio
async def test_get_timetable_week_of_a_holiday_week_has_periods_and_no_lessons():
    html = (
        '<html><body><div title="1&nbsp;08:00&ndash;08:45 " class="period-time"></div>'
        '<div title="2&nbsp;08:55&ndash;09:40 " class="period-time"></div></body></html>'
    )
    fake = FakeClientSession([("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body=html))])

    week = await EdookitClient(SESSION, http=fake).get_timetable_week()

    assert week.lessons == []
    assert [period.number for period in week.periods] == [1, 2]


@pytest.mark.asyncio
async def test_get_timetable_week_still_raises_when_the_page_has_neither_lessons_nor_periods():
    html = "<html><body>not a timetable</body></html>"
    fake = FakeClientSession([("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body=html))])

    with pytest.raises(ParseError):
        await EdookitClient(SESSION, http=fake).get_timetable_week()


INBOX_URL = f"https://{SCHOOL}.edookit.net/overview/updates"
MESSAGE_URL = f"https://{SCHOOL}.edookit.net/messages/detail"
INBOX = (FIXTURES / "inbox_structure.html").read_text()
MESSAGE = (FIXTURES / "message_structure.html").read_text()
LOGIN_PAGE = '<html><form action="/user/login" method="post"></form></html>'


def _recording(seen: list[dict], body: str, url: str = INBOX_URL):
    def answer(headers):
        seen.append(headers)
        return ok(url, body=body)

    return answer


@pytest.mark.asyncio
async def test_get_inbox_parses_the_inbox_page():
    client = EdookitClient(
        SESSION, http=FakeClientSession([("GET", INBOX_URL, ok(INBOX_URL, body=INBOX))])
    )

    messages = await client.get_inbox()

    assert [m.id for m in messages] == [9003, 9002, 9001]


@pytest.mark.asyncio
async def test_inbox_and_message_are_fetched_without_the_ajax_headers():
    seen: list[dict] = []
    fake = FakeClientSession(
        [
            ("GET", INBOX_URL, _recording(seen, INBOX)),
            ("GET", MESSAGE_URL, _recording(seen, MESSAGE, MESSAGE_URL)),
        ]
    )
    client = EdookitClient(SESSION, http=fake)

    await client.get_inbox()
    await client.get_message(9003)

    for headers in seen:
        assert "X-Requested-With" not in headers
        assert "X-CSRF-Token" not in headers
        assert "PHPSESSID=s" in headers["Cookie"]


@pytest.mark.asyncio
async def test_timetable_is_still_fetched_with_the_ajax_headers():
    seen: list[dict] = []
    fake = FakeClientSession([("GET", TIMETABLE_URL, _recording(seen, "tt", TIMETABLE_URL))])

    await EdookitClient(SESSION, http=fake).fetch_timetable_html()

    assert seen[0]["X-Requested-With"] == "XMLHttpRequest"


@pytest.mark.asyncio
async def test_get_message_fetches_the_detail_page():
    fake = FakeClientSession([("GET", MESSAGE_URL, ok(MESSAGE_URL, body=MESSAGE))])

    message = await EdookitClient(SESSION, http=fake).get_message(9003)

    assert (message.id, message.subject) == (9003, "Třídní schůzky")


@pytest.mark.asyncio
async def test_plain_fetch_redirect_counts_as_expired_and_is_renewed(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    fake = FakeClientSession(
        [
            ("GET", INBOX_URL, redirect(f"https://{SCHOOL}.edookit.net/user/login")),
            ("GET", INBOX_URL, ok(INBOX_URL, body=INBOX)),
        ]
    )

    messages = await EdookitClient(SESSION, http=fake).get_inbox()

    assert [m.id for m in messages] == [9003, 9002, 9001]
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_fetch_login_page_counts_as_expired(monkeypatch):
    refresh = AsyncMock(return_value=_renewed("new"))
    monkeypatch.setattr(auth, "refresh", refresh)
    fake = FakeClientSession(
        [
            ("GET", INBOX_URL, ok(INBOX_URL, body=LOGIN_PAGE)),
            ("GET", INBOX_URL, ok(INBOX_URL, body=INBOX)),
        ]
    )

    messages = await EdookitClient(SESSION, http=fake).get_inbox()

    assert [m.id for m in messages] == [9003, 9002, 9001]
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_logged_in_page_mentioning_login_is_not_expired(monkeypatch):
    refresh = AsyncMock()
    monkeypatch.setattr(auth, "refresh", refresh)
    body = INBOX.replace("</body>", '<a href="/user/login?switch=1">x</a></body>')
    fake = FakeClientSession([("GET", INBOX_URL, ok(INBOX_URL, body=body))])

    messages = await EdookitClient(SESSION, http=fake).get_inbox()

    assert len(messages) == 3
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbox_is_no_longer_a_capture_page():
    client = EdookitClient(SESSION, http=FakeClientSession([]))

    with pytest.raises(ValueError):
        await client.fetch_page("inbox")


LISTING_URL = f"https://{SCHOOL}.edookit.net/evaluation/listing"
SUBJECTS_URL = f"https://{SCHOOL}.edookit.net/evaluation/"
OVERALL_URL = f"https://{SCHOOL}.edookit.net/evaluation/overall"
LISTING = (FIXTURES / "evaluation_listing_structure.html").read_text()
SUBJECTS = (FIXTURES / "evaluation_subjects_structure.html").read_text()


def _grades_session(seen: list[dict] | None = None) -> FakeClientSession:
    # The listing route comes first: the subjects URL is a prefix of it.
    if seen is None:
        return FakeClientSession(
            [
                ("GET", LISTING_URL, ok(LISTING_URL, body=LISTING)),
                ("GET", SUBJECTS_URL, ok(SUBJECTS_URL, body=SUBJECTS)),
            ]
        )
    return FakeClientSession(
        [
            ("GET", LISTING_URL, _recording(seen, LISTING, LISTING_URL)),
            ("GET", SUBJECTS_URL, _recording(seen, SUBJECTS, SUBJECTS_URL)),
        ]
    )


@pytest.mark.asyncio
async def test_get_grades_combines_both_pages():
    grades = await EdookitClient(SESSION, http=_grades_session()).get_grades()

    assert grades.term == "1. pololetí 26/27"
    assert [g.id for g in grades.grades] == [9102, 9101, 9100, 9099]
    assert [s.course_id for s in grades.subjects] == [501, 502, 503]
    assert grades.subjects[0].attendance_percent == 100.0


@pytest.mark.asyncio
async def test_grades_pages_are_fetched_without_the_ajax_headers():
    seen: list[dict] = []

    await EdookitClient(SESSION, http=_grades_session(seen)).get_grades()

    assert len(seen) == 2
    for headers in seen:
        assert "X-Requested-With" not in headers
        assert "X-CSRF-Token" not in headers


@pytest.mark.asyncio
async def test_overall_evaluation_is_a_capture_page():
    fake = FakeClientSession([("GET", OVERALL_URL, ok(OVERALL_URL, body="<p>x</p>"))])

    assert await EdookitClient(SESSION, http=fake).fetch_page("evaluation_overall") == "<p>x</p>"


class _ParamsRecorder(FakeClientSession):
    def __init__(self, routes, seen: list) -> None:
        super().__init__(routes)
        self._seen = seen

    def request(self, method, url, **kwargs):
        self._seen.append(kwargs.get("params"))
        return super().request(method, url, **kwargs)


@pytest.mark.asyncio
async def test_another_week_is_reached_by_resetting_then_moving_by_days():
    # The portal keeps the shown week in its session and moves it relative
    # to wherever it is, so every fetch starts from the current week.
    seen: list = []
    fake = _ParamsRecorder(
        [("GET", TIMETABLE_URL, ok(TIMETABLE_URL, body=body)) for body in ("now", "x", "next")],
        seen,
    )
    client = EdookitClient(SESSION, http=fake)

    assert await client.fetch_timetable_html() == "now"
    assert await client.fetch_timetable_html(-2) == "next"

    reset = {"do": "familyTimetable-resetFilter"}
    move = {"familyTimetable-value": -14, "do": "familyTimetable-changeFilter"}
    assert seen == [reset, reset, move]
