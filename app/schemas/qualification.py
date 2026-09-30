from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StartRequest(Camel):
    user_id: str
    agent_id: str | None = None
    shop_id: str | None = None
    consent_version: str = Field(min_length=1, max_length=20)


class SubmitCode(Camel):
    code: str = Field(min_length=6, max_length=12)
    sms_requested_at: time | None = None     # "17:00:18" from the SMS, optional tie-breaker


class AssessmentOut(Camel):
    assessment_id: str
    window_days: Decimal
    income_daily: Decimal
    needs_daily: Decimal
    committed_daily: Decimal
    wants_daily: Decimal
    left_daily: Decimal
    affordable_daily: Decimal
    confidence: str
    flags: list[str]
    has_fuliza: bool
    has_loan_apps: bool
    has_betting: bool
    created_at: datetime


class RequestOut(Camel):
    request_id: str
    status: str
    message: str
    inbox: str
    attempts_left: int
    expires_at: datetime
    failure_reason: str | None = None
    assessment: AssessmentOut | None = None


class DeviceOut(Camel):
    device_id: str
    shop_id: str | None
    brand: str | None
    model: str | None
    daily_payment: Decimal
    credit_multiplier: Decimal
    required_daily: Decimal
    deposit_required: Decimal
    deposit_above_avg_balance: bool
    qualified: bool
    headroom_daily: Decimal