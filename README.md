# pyedookit

[![PyPI](https://img.shields.io/pypi/v/pyedookit)](https://pypi.org/project/pyedookit/)
[![CI](https://github.com/TFoxik/pyedookit/actions/workflows/ci.yml/badge.svg)](https://github.com/TFoxik/pyedookit/actions/workflows/ci.yml)

Unofficial async Python client for the [Edookit](https://edookit.com) parent
portal (`https://<school>.edookit.net`) used by Czech schools. It powers the
[ha-edookit](https://github.com/TFoxik/ha-edookit) Home Assistant integration
and works standalone.

Edookit's REST API only works if a school administrator enables it, so this
library signs in like a parent does (Plus4U OIDC, no browser) and reads the
portal's pages. Edookit doesn't support it, and a change to the portal's
markup can break a parser until it's updated.

## Install

```bash
pip install pyedookit
```

Requires Python 3.12+.

## Usage

```python
import asyncio
from pyedookit import EdookitClient, login

async def main():
    session = await login("zs-example", "parent@example.com", "hunter2")
    client = EdookitClient(session)
    for lesson in await client.get_timetable():
        print(lesson.start, lesson.subject, lesson.room)

asyncio.run(main())
```

| Call | Returns |
|---|---|
| `get_timetable(week_offset=0)` | one week's `Lesson`s, times in `PORTAL_TZ` (Europe/Prague); `is_changed` for a substitution or other change, `is_cancelled` for a lesson that won't take place ("Zrušeno", or "Událost" when an event replaces it) |
| `get_timetable_week(week_offset=0)` | a `TimetableWeek`: `lessons`, the bell-schedule `periods`, and the week's `events` (days off such as "Ředitelské volno", or timed ones, as `SchoolEvent`s, one per day as drawn, so a multi-day event repeats under the same `id`); offset `1` is next week, `-1` last week |
| `get_events()` | upcoming `SchoolEvent`s; all-day events have `date` start/end, end inclusive |
| `get_payments()` | `Payments` totals and requests, amounts as `Decimal` |
| `get_inbox()` | the `MessageSummary`s in Schránka, newest first: subject, sender, sent time, preview, `unread` |
| `get_message(id)` | one `Message` in full; fetching it marks the message read on the portal |
| `get_grades()` | `Grades` of the current term: `grades` newest first (mark, weight, topic, teacher, entered date), `subjects` with attendance, and `average(course_id)`, weighted |
| `get_exams()` | the `Exam`s on Písemky, zkoušení (upcoming, today's and recent, in page order): subject, kind, start/end, title, description, who announced it and when, `unread` |
| `get_assignments()` | the `Assignment`s (homework) on Domácí úkoly (today's, upcoming and recent, in page order): subject, `due`, title, description, who set it and when, `unread`, and `done` as marked on the portal |
| `fetch_page(name)` | raw HTML of a page without a parser yet (`CAPTURE_PAGES`) |

The inbox, message and grades pages are requested the way a browser's first page
load is, without the AJAX headers the other calls send: to an AJAX request
the portal answers with the inbox's first-run guide and its sample items.
`Message.body` is the message's HTML reduced to a JSON-ready node tree of
p, br, strong, em, u, ul, ol, li and http(s) links; everything else, scripts
and event handlers included, is dropped. Render it as nodes, never as HTML.

`content_fingerprint(html)` tells you when such a page's content changed.

### Sessions

The password is only used by `login()`. Store `session.to_json()` instead,
and treat it like a password: it holds the Plus4U sign-in. Its cookie is set for 30
days from `login()` (`session.plus4u_expires_at` tells you when, or is `None`
if that's unknown or already past). Load it with
`Session.from_json()`; sessions stored by 0.3.x still load.

`EdookitClient` renews the short-lived edookit session by itself and
retries once. Pass `on_session_update` to persist each renewed session:

    client = EdookitClient(session, on_session_update=lambda s: save(s.to_json()))

Only when Plus4U answers that the user has to sign in again do calls raise
`AuthError`, and you need `login()` again. A renewal that fails for any other
reason (Plus4U or the portal erroring, a maintenance page) raises
`EdookitConnectionError`, so a passing outage doesn't cost the user a login.

| Exception | Meaning | What to do |
|---|---|---|
| `SessionExpired` | still expired right after a renewal | retry later |
| `AuthError` | bad credentials, or Plus4U wants a new login | `login()` |
| `EdookitConnectionError` | network error, 30 s timeout, or a failed renewal | retry later |
| `ParseError` | the portal's markup changed | the parser needs updating |

All of them derive from `EdookitError`. `login()`, `refresh()` and
`EdookitClient` accept an `http: aiohttp.ClientSession` to reuse. Create it
with `cookie_jar=aiohttp.DummyCookieJar()`: cookies are handled per host by
hand, and a real jar would resend them.

## Limitations

- **Timetable changes carry no reason.** A changed lesson is recognised by
  its orange border only; what changed (teacher, room, subject) has to be
  read from its fields. A timed event spanning several days hasn't been
  seen.
- **Another week is two requests.** The portal stores the shown week in the
  session and moves it relative to where it is, so `week_offset` other than
  `0` first resets to the current week.
- **Unpaid payment requests are unverified.** Only paid ones have been seen.
  A request without the "accepted" marker is reported as unpaid.
- **Grades: term marks and non-numeric marks are unverified.** Neither has
  been seen. A mark other than 1–5 is kept as text and left out of
  `average()`; the current term is chosen by its heading ("2. pololetí" over
  "1. pololetí").
- **Exams: only the main page is read.** Exams older than the "recent"
  grid are only in the portal's archive, which isn't fetched. Only recent
  exams have been seen; upcoming and today's are assumed to use the same
  rows.
- **Assignments: only the main page is read.** Homework older than the
  "recent" grid is only in the portal's archive, which isn't fetched. A
  filled today's grid, attachments and a deadline without a time (such a
  row is skipped) haven't been seen.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy src
```

To release, bump `version` in `pyproject.toml` and push a `vX.Y.Z` tag. The
publish workflow checks that the tag matches the version, then tests, builds
and uploads to PyPI with trusted publishing.
