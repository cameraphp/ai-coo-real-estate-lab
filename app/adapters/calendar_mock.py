"""Deterministic local availability adapter. It never books or creates events."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.adapters.tools import PreparedToolCall, ToolContext
from app.domain.models import AvailabilityResult, AvailabilitySlot

CANDIDATE_START_HOURS = (9, 11, 14, 16)
BUSINESS_DAY_END = time(17, 0)


class MockCalendarAdapter:
    def find_availability(
        self, context: ToolContext, requested_date: date, duration_minutes: int
    ) -> PreparedToolCall[AvailabilityResult]:
        slots: list[AvailabilitySlot] = []
        if requested_date.weekday() < 5:
            day_end = datetime.combine(requested_date, BUSINESS_DAY_END, tzinfo=UTC)
            for hour in CANDIDATE_START_HOURS:
                start = datetime.combine(requested_date, time(hour, 0), tzinfo=UTC)
                end = start + timedelta(minutes=duration_minutes)
                if end <= day_end:
                    slots.append(AvailabilitySlot(start=start, end=end))
        result = AvailabilityResult(
            requested_date=requested_date,
            duration_minutes=duration_minutes,
            slots=slots,
        )
        return PreparedToolCall(result=result)
