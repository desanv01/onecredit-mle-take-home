"""Parse registry deliveries into comparable company snapshots."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

_MALAYSIA_TIME = timezone(timedelta(hours=8))
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class InvalidEvent(ValueError):
    """A permanent problem with the registry payload."""


@dataclass(frozen=True, slots=True)
class CompanyEvent:
    registration_number: str
    event_at: datetime
    active: bool
    body_hash: str
    company_name: str | None = None
    industry: str | None = None
    state: str | None = None
    nature_of_business: str | None = None

    @property
    def order_key(self) -> tuple[datetime, bool, str]:
        return self.event_at, not self.active, self.body_hash


def delivery_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise InvalidEvent(f"invalid {field}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidEvent(f"invalid {field}") from error
    return value.strip() or None


def normalize_registration_number(value: object) -> str:
    """Normalize a registry or portal identifier to the same database key."""
    registration = _text(value, "registrationNumber")
    if not registration:
        raise InvalidEvent("missing registrationNumber")
    return registration.upper()


def _event_time(value: object) -> datetime:
    try:
        # Numeric timestamps in the registry feed are Unix milliseconds.
        if type(value) is int:
            return _UNIX_EPOCH + timedelta(milliseconds=value)
        if isinstance(value, str) and len(value) >= 16 and value[10] in "T ":
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=_MALAYSIA_TIME)
            return timestamp.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise InvalidEvent("invalid updatedAt") from error
    raise InvalidEvent("invalid updatedAt")


def parse_event(body: bytes) -> CompanyEvent:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        raise InvalidEvent("invalid JSON") from error
    if not isinstance(payload, dict):
        raise InvalidEvent("event must be an object")

    event_type = payload.get("type")
    if event_type not in ("company-upserted", "company-struck-off"):
        raise InvalidEvent("unsupported event type")
    registration = normalize_registration_number(payload.get("registrationNumber"))
    event_at = _event_time(payload.get("updatedAt"))
    body_hash = delivery_hash(body)
    if event_type == "company-struck-off":
        return CompanyEvent(registration, event_at, False, body_hash)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise InvalidEvent("upsert data must be an object")
    state = _text(data.get("state"), "state")
    return CompanyEvent(
        registration_number=registration,
        event_at=event_at,
        active=True,
        body_hash=body_hash,
        company_name=_text(data.get("companyName"), "companyName"),
        industry=_text(data.get("industry"), "industry"),
        state=state.title() if state else None,
        nature_of_business=_text(data.get("natureOfBusiness"), "natureOfBusiness"),
    )
