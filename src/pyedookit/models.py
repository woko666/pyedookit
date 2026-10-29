from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

# The portal renders wall-clock times with no zone; every Edookit school is
# Czech, so parsed times are pinned to Prague rather than left naive.
PORTAL_TZ = ZoneInfo("Europe/Prague")


@dataclass(frozen=True, slots=True)
class Lesson:
    start: datetime
    end: datetime
    subject: str
    teacher: str | None = None
    room: str | None = None
    group: str | None = None
    is_changed: bool = False
    is_cancelled: bool = False
    note: str | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class Period:
    number: int
    start: time
    end: time


@dataclass(slots=True)
class TimetableWeek:
    lessons: list[Lesson]
    periods: list[Period]
    # Days off and other school events drawn across the week grid
    # ("Ředitelské volno", "Podzimní prázdniny", ...).
    events: list[SchoolEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Term:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class SchoolEvent:
    id: str
    title: str
    start: datetime | date
    end: datetime | date
    description: str | None = None
    created_by: str | None = None

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


@dataclass(frozen=True, slots=True)
class MessageSummary:
    id: int
    subject: str
    sender: str
    sent: datetime
    preview: str
    unread: bool


# A sanitised message body, ready to serialise as JSON: `{"text": str}`, or
# `{"tag": str, "children": [...]}` with tag one of p, br, strong, em, u, ul,
# ol, li, a; an `a` also has `"href"`, always http(s).
type BodyNode = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str
    sender: str
    recipients: str
    sent: datetime
    body: list[BodyNode]
    body_text: str


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    title: str
    amount: Decimal
    currency: str
    is_paid: bool


@dataclass(frozen=True, slots=True)
class Payments:
    paid: Decimal
    remaining: Decimal
    overpaid: Decimal
    currency: str
    requests: list[PaymentRequest]


_NUMERIC_MARKS = {"1", "2", "3", "4", "5"}


def _short(code: str | None, name: str | None) -> str | None:
    """The portal's subject abbreviation: "Aj 2 - I.B" → "Aj", "Aj2- 1.A" → "Aj2"."""
    return code.split()[0].rstrip("-") if code and code.strip() else name


@dataclass(frozen=True, slots=True)
class Grade:
    id: int
    mark: str
    weight: float | None
    category: str
    topic: str
    teacher: str
    entered: date
    course_id: int | None = None
    subject: str | None = None
    course_code: str | None = None

    @property
    def value(self) -> int | None:
        return int(self.mark) if self.mark in _NUMERIC_MARKS else None

    @property
    def short(self) -> str | None:
        return _short(self.course_code, self.subject)


@dataclass(frozen=True, slots=True)
class Subject:
    course_id: int
    name: str
    code: str
    teacher: str
    attendance_percent: float | None

    @property
    def short(self) -> str:
        return _short(self.code, self.name) or self.name


@dataclass(slots=True)
class Grades:
    """One term's grades, newest first, and the child's subjects."""

    term: str | None
    grades: list[Grade]
    subjects: list[Subject]

    def for_subject(self, course_id: int) -> list[Grade]:
        return [grade for grade in self.grades if grade.course_id == course_id]

    def average(self, course_id: int) -> float | None:
        """Weighted average of the numeric marks; a grade without a weight counts once."""
        total = weights = 0.0
        for grade in self.for_subject(course_id):
            if (value := grade.value) is None:
                continue
            weight = grade.weight if grade.weight is not None else 1.0
            total += value * weight
            weights += weight
        return total / weights if weights else None


@dataclass(frozen=True, slots=True)
class Exam:
    """A test or oral exam announced on Písemky, zkoušení (`/exams/`)."""

    id: int
    title: str
    start: datetime
    end: datetime
    subject: str
    # "Malá písemka" and "Písemná práce" on a real portal.
    kind: str
    category: str
    created_by: str
    created: datetime
    unread: bool
    description: str | None = None
    course_id: int | None = None
    course_code: str | None = None

    @property
    def short(self) -> str:
        return _short(self.course_code, self.subject) or self.subject
