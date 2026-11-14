"""Logging for !exchange.

Everything goes to the `bot.exchange` logger, which main.py writes to
logs/exchange.log. Lines that belong to a session carry its ID and are also
saved to the exchange_log_entries table, so support can read a whole session
with !exchange log after the file has rotated.

Records are put on a thread-safe queue when they are logged, because commands
run on several event loops, and `flush_exchange_logs()` writes them in batches.
"""

from __future__ import annotations

import logging
import queue
import secrets
from datetime import datetime, timedelta
from typing import Any, MutableMapping

from sqlalchemy import delete, select

from database.models import ExchangeLogEntry, ExchangeSession, PendingExchangeDelivery
from database.session import get_async_session

LOGGER_NAME = "bot.exchange"
exchange_logger = logging.getLogger(LOGGER_NAME)

MAX_QUEUED_RECORDS = 10_000
FLUSH_BATCH_SIZE = 500
RETENTION = timedelta(days=60)


def new_session_id() -> str:
    return f"EX-{secrets.token_hex(3).upper()}"


class SessionLogAdapter(logging.LoggerAdapter):
    """Puts the session ID, user and channel on every line."""

    def __init__(self, session_id: str, user_login: str = "", channel_name: str = "", logger: logging.Logger | None = None):
        super().__init__(logger or exchange_logger, {"exchange_session_id": session_id})
        self.session_id = session_id
        self.user_login = user_login
        self.channel_name = channel_name

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]):
        extra = dict(kwargs.get("extra") or {})
        extra["exchange_session_id"] = self.session_id
        kwargs["extra"] = extra
        return f"{self.session_id} [{self.user_login}@{self.channel_name}] {msg}", kwargs


class ExchangeDBLogHandler(logging.Handler):
    """Queues records that carry a session ID so they can be saved to the database."""

    def __init__(self, max_queued: int = MAX_QUEUED_RECORDS):
        super().__init__(logging.DEBUG)
        self.queue: queue.Queue[dict] = queue.Queue()
        self.max_queued = max_queued
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        session_id = getattr(record, "exchange_session_id", None)
        if not session_id:
            return
        try:
            exc_text = None
            if record.exc_info:
                exc_text = logging.Formatter().formatException(record.exc_info)
            entry = {
                "session_id": session_id,
                "timestamp": datetime.fromtimestamp(record.created),
                "level": record.levelname,
                "message": record.getMessage(),
                "exc_text": exc_text,
            }
            while self.queue.qsize() >= self.max_queued:
                try:
                    self.queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    break
            self.queue.put_nowait(entry)
        except Exception:
            self.handleError(record)

    def take(self, limit: int) -> list[dict]:
        out = []
        while len(out) < limit:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return out

    def put_back(self, entries: list[dict]) -> None:
        # Order matters less than not losing them; they keep their timestamps
        for entry in entries:
            if self.queue.qsize() >= self.max_queued:
                self.dropped += 1
                continue
            self.queue.put_nowait(entry)


db_log_handler = ExchangeDBLogHandler()


def install_db_log_handler() -> None:
    if db_log_handler not in exchange_logger.handlers:
        exchange_logger.addHandler(db_log_handler)


async def flush_exchange_logs(handler: ExchangeDBLogHandler | None = None) -> int:
    """Write queued lines to the database. Returns how many were written."""
    handler = handler or db_log_handler
    if handler.dropped:
        dropped, handler.dropped = handler.dropped, 0
        # No session ID, so this only goes to the file
        exchange_logger.warning(f"Dropped {dropped} exchange log lines because the database queue was full")
    written = 0
    while True:
        batch = handler.take(FLUSH_BATCH_SIZE)
        if not batch:
            return written
        try:
            async with get_async_session() as session:
                session.add_all([ExchangeLogEntry(**entry) for entry in batch])
        except Exception:
            handler.put_back(batch)
            exchange_logger.warning("Could not save exchange log lines to the database, will retry", exc_info=True)
            return written
        written += len(batch)


async def purge_old_exchanges(now: datetime | None = None, retention: timedelta = RETENTION) -> tuple[int, int]:
    """Delete sessions created before the retention window, and their log lines.

    Sessions that still owe a user something are kept until it is settled.
    Returns (sessions deleted, log lines deleted).
    """
    cutoff = (now or datetime.now()) - retention
    async with get_async_session() as session:
        owed = select(PendingExchangeDelivery.session_id).where(PendingExchangeDelivery.session_id.is_not(None))
        result = await session.execute(
            select(ExchangeSession.id).where(
                ExchangeSession.created_at < cutoff,
                ExchangeSession.id.not_in(owed),
            )
        )
        ids = [row[0] for row in result.all()]
        if not ids:
            return 0, 0
        lines = 0
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            r = await session.execute(delete(ExchangeLogEntry).where(ExchangeLogEntry.session_id.in_(chunk)))
            lines += r.rowcount or 0
            await session.execute(delete(ExchangeSession).where(ExchangeSession.id.in_(chunk)))
    exchange_logger.info(f"Purged {len(ids)} exchange sessions ({lines:,} log lines) older than {cutoff:%Y-%m-%d}")
    return len(ids), lines
