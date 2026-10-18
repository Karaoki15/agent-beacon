"""Confirm per-chat presentation once, before aggregation, history and delivery."""

from __future__ import annotations

from typing import Any


class StateConfirmation:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.version = 0
        self._rows: dict[tuple[str, str], tuple[dict[str, Any], tuple[Any, ...] | None, float]] = {}

    @staticmethod
    def _key(row: dict[str, Any], now: float) -> tuple[Any, ...]:
        deadline = row.get("_question_deadline")
        return row["state"], bool(row["state"] == "working" and deadline and deadline > now)

    def select(self, key: tuple[str, str], row: dict[str, Any], now: float,
               *, healthy: bool, commit: bool) -> dict[str, Any]:
        previous = self._rows.get(key)
        # Closing, observer loss and generation changes invalidate old evidence.
        if (self.seconds == 0 or not healthy or not row["active"] or previous is None
                or previous[0]["generation"] != row["generation"]):
            if commit:
                self._rows[key] = (dict(row), None, now)
            return row
        stable, pending, since = previous
        target = self._key(row, now)
        if target == self._key(stable, now):
            if commit:
                self._rows[key] = (dict(row), None, now)
            return row
        if commit:
            if pending != target:
                pending, since = target, now
            if now - since >= self.seconds:
                self._rows[key] = (dict(row), None, now)
                self.version += 1
                return row
            self._rows[key] = (stable, pending, since)
        # Metadata and sequence remain current. Only presentation waits.
        result = dict(row)
        for field in ("state", "_question_deadline", "_initial_idle", "_unconfirmed_reason"):
            result[field] = stable.get(field)
        return result

    def retain(self, keys: set[tuple[str, str]]) -> None:
        self._rows = {key: value for key, value in self._rows.items() if key in keys}

    def remaining(self, now: float) -> float | None:
        deadlines = [max(0.0, since + self.seconds - now)
                     for _, pending, since in self._rows.values() if pending is not None]
        return min(deadlines) if deadlines else None
