"""Read display names only for enrolled chats; never open message transcripts."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..core import safe_session_name

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 1.0


class SessionNames:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path.home()
        self._next_refresh = 0.0
        self._reported_failures: set[str] = set()
        self._recent_codex: dict[str, float] = {}
        self._index_stamp: tuple[Any, ...] | None = None
        self._index_names: dict[tuple[str, str], str] = {}

    def _unavailable(self, provider: str, error: Exception) -> None:
        if provider not in self._reported_failures:
            logger.debug("%s display metadata unavailable (%s)", provider, type(error).__name__)
            self._reported_failures.add(provider)

    def read(self, owners: dict[str, dict[str, tuple[int, str]]]) -> dict[tuple[str, str], str]:
        now = time.monotonic()
        if now < self._next_refresh:
            return {}
        self._next_refresh = now + REFRESH_SECONDS
        names: dict[tuple[str, str], str] = {}
        for session_id in owners.get("codex", {}):
            self._recent_codex[session_id] = now
        self._recent_codex = dict(sorted(
            ((sid, at) for sid, at in self._recent_codex.items() if now - at < 300),
            key=lambda item: item[1], reverse=True,
        )[:128])
        codex_ids = list(self._recent_codex)
        if codex_ids:
            self._codex_names(codex_ids, names)
            self._codex_index(codex_ids, names)
        for session_id, (pid, _) in list(owners.get("claude", {}).items())[:128]:
            path = self.home / ".claude" / "sessions" / f"{pid}.json"
            try:
                with path.open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    continue
                metadata: Any = json.loads(raw)
                if not isinstance(metadata, dict) or metadata.get("pid") != pid or metadata.get("sessionId") != session_id:
                    continue
                name = safe_session_name(metadata.get("name"))
                if name:
                    names[("claude", session_id)] = name
            except (OSError, UnicodeError, ValueError, RecursionError) as error:
                self._unavailable("Claude", error)
        return names

    def _codex_names(self, session_ids: list[str], names: dict[tuple[str, str], str]) -> None:
        try:
            paths = [p for p in (self.home / ".codex").glob("state_*.sqlite")
                     if p.stem.removeprefix("state_").isdecimal()]
            paths.sort(key=lambda p: int(p.stem.removeprefix("state_")), reverse=True)
            for path in paths[:3]:
                # mode=ro preserves the application's database; WAL stays visible.
                db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
                try:
                    db.execute("PRAGMA query_only=ON")
                    columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
                    if not {"id", "name"}.issubset(columns):
                        continue
                    placeholders = ",".join("?" for _ in session_ids)
                    # title/preview/first_user_message can contain prompt text.
                    # Only the saved user-facing name is read, with a SQL bound.
                    for session_id, value in db.execute(
                        f"SELECT id, substr(name, 1, 161) FROM threads WHERE id IN ({placeholders})", session_ids,
                    ):
                        name = safe_session_name(value)
                        if name:
                            names[("codex", session_id)] = name
                    return
                finally:
                    db.close()
        except (OSError, sqlite3.Error, ValueError) as error:
            self._unavailable("Codex", error)

    def _codex_index(self, session_ids: list[str], names: dict[tuple[str, str], str]) -> None:
        """Saved display-name index covers chats not yet present in SQLite."""
        missing = {sid for sid in session_ids if ("codex", sid) not in names}
        if not missing:
            return
        path = self.home / ".codex" / "session_index.jsonl"
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, tuple(sorted(missing)))
            if stamp == self._index_stamp:
                names.update(self._index_names)
                return
            indexed: dict[tuple[str, str], str] = {}
            # The index contains display metadata only. Never scan transcripts.
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 2 * 1024 * 1024))
                if stream.tell():
                    stream.readline(65537)
                lines = stream.read(2 * 1024 * 1024).splitlines()
                for line in lines:
                    if len(line) > 65536:
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in missing:
                        continue
                    name = safe_session_name(row.get("thread_name"))
                    if name:
                        indexed[("codex", row["id"])] = name
            self._index_stamp, self._index_names = stamp, indexed
            names.update(indexed)
        except OSError as error:
            self._unavailable("Codex index", error)
