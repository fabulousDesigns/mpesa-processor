import hmac

from fastapi import Header, HTTPException
from sqlalchemy import Engine

from app.core.config import get_settings
from app.db.engine import get_engine


def db() -> Engine:
    return get_engine()


def internal_only(x_internal_token: str = Header(default="")) -> None:
    expected = get_settings().internal_api_token
    if not expected or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")