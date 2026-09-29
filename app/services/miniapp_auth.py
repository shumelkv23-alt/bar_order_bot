"""Server-side verification of Telegram Mini App launch data."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    pass


@dataclass(frozen=True)
class TelegramGuest:
    telegram_id: int
    display_name: str
    username: str | None
    language: str


def verify_init_data(raw: str, bot_token: str, *, now: datetime | None = None) -> TelegramGuest:
    if not raw or len(raw) > 8192 or not bot_token:
        raise InvalidInitData("Missing launch data")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("Duplicate fields")
        received_hash = data.pop("hash")
        if len(received_hash) != 64:
            raise ValueError("Invalid hash")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected):
            raise ValueError("Invalid signature")
        auth_date = int(data["auth_date"])
        timestamp = int((now or datetime.now(UTC)).timestamp())
        if auth_date > timestamp + 60 or timestamp - auth_date > 86400:
            raise ValueError("Expired launch data")
        user = json.loads(data["user"])
        telegram_id = int(user["id"])
        if telegram_id <= 0 or telegram_id >= 2**52 or user.get("is_bot"):
            raise ValueError("Invalid user")
        display_name = (
            " ".join(
                part for part in [user.get("first_name", ""), user.get("last_name", "")] if part
            ).strip()[:160]
            or "Гость"
        )
        language = "en" if str(user.get("language_code", "")).lower().startswith("en") else "ru"
        username = str(user["username"])[:64] if user.get("username") else None
        return TelegramGuest(telegram_id, display_name, username, language)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidInitData("Invalid Telegram launch data") from exc
