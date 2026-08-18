from datetime import UTC, datetime

from sqlalchemy import types


class SafeDateTimeUTC(types.TypeDecorator[datetime]):
    """Ensures datetimes are saved as UTC and returned as timezone-aware UTC."""

    impl = types.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Naive datetime passed; timezone-aware expected.")
            return value.astimezone(UTC)
        return value

    def process_result_value(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
