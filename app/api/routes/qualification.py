"""Internal API for Nest. Every route needs the X-Internal-Token header.

  POST /v1/qualification/requests                   agent starts qualification for a customer
  POST /v1/qualification/requests/{id}/code         agent submits the 6-digit code
  GET  /v1/qualification/requests/{id}              poll status (and the score once SCORED)
  GET  /v1/qualification/requests/{id}/devices      the device snapshot (?qualifiedOnly=true)
  POST /v1/qualification/requests/{id}/cancel       agent abandons it
  GET  /v1/qualification/customers/{userId}/latest  reuse a recent score instead of a new statement
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine, text

from app.api.deps import db, internal_only
from app.core.config import get_settings
from app.ingestion.s3_store import fetch_attachment
from app.matching import matcher
from app.matching.codes import InvalidCode
from app.schemas.qualification import AssessmentOut, DeviceOut, RequestOut, StartRequest, SubmitCode

router = APIRouter(prefix="/v1/qualification", tags=["qualification"], dependencies=[Depends(internal_only)])

MESSAGES = {
    "AWAITING_CODE": "Ask the customer to request their M-PESA statement to {inbox}, then enter the code from the SMS.",
    "AWAITING_EMAIL": "Waiting for Safaricom's email. This usually takes seconds.",
    "WRONG_CODE": "That code didn't open the statement. Check the SMS and try again.",
    "LOCKED_OUT": "Too many wrong codes. Start a new request and ask for a new statement.",
    "OPENED": "Statement received, scoring now.",
    "PARSED": "Statement received, scoring now.",
    "SCORED": "Done.",
    "FAILED": "We couldn't score this statement.",
    "EXPIRED": "This request timed out. Start a new one.",
    "CANCELLED": "This request was cancelled.",
}

_ASSESSMENT_SQL = """
    SELECT id AS assessment_id, window_days, income_daily, needs_daily, committed_daily, wants_daily,
           left_daily, affordable_daily, confidence, flags, has_fuliza, has_loan_apps, has_betting, created_at
    FROM stmt_assessments
"""


def _assessment(row) -> AssessmentOut | None:
    if not row:
        return None
    d = dict(row._mapping)
    d["flags"] = json.loads(d["flags"] or "[]")
    return AssessmentOut(**d)


def _request_out(engine: Engine, request_id: str) -> RequestOut:
    with engine.connect() as c:
        r = c.execute(text("""
            SELECT id, status, failure_reason, max_code_attempts - code_attempts AS left_, expires_at
            FROM stmt_requests WHERE id = :id
        """), {"id": request_id}).first()
        if not r:
            raise HTTPException(404, "request not found")
        a = c.execute(text(_ASSESSMENT_SQL + " WHERE request_id = :id"), {"id": request_id}).first()
    return RequestOut(request_id=r.id, status=r.status,
                      message=MESSAGES.get(r.status, r.status).format(inbox=get_settings().statement_inbox),
                      inbox=get_settings().statement_inbox, attempts_left=max(0, r.left_),
                      expires_at=r.expires_at, failure_reason=r.failure_reason, assessment=_assessment(a))


@router.post("/requests", response_model=RequestOut, response_model_by_alias=True, status_code=201)
def start(body: StartRequest, engine: Engine = Depends(db)):
    try:
        rid = matcher.create_request(engine, user_id=body.user_id, agent_id=body.agent_id,
                                     shop_id=body.shop_id, consent_version=body.consent_version)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _request_out(engine, rid)


@router.post("/requests/{request_id}/code", response_model=RequestOut, response_model_by_alias=True)
def submit_code(request_id: str, body: SubmitCode, engine: Engine = Depends(db)):
    try:
        out = matcher.submit_code(engine, request_id, body.code, fetch_attachment, body.sms_requested_at)
    except InvalidCode as e:
        raise HTTPException(422, str(e))
    res = _request_out(engine, request_id)
    return res.model_copy(update={"message": out.message})   # the matcher's message is the precise one


@router.get("/requests/{request_id}", response_model=RequestOut, response_model_by_alias=True)
def status(request_id: str, engine: Engine = Depends(db)):
    return _request_out(engine, request_id)


@router.get("/requests/{request_id}/devices", response_model=list[DeviceOut], response_model_by_alias=True)
def devices(request_id: str, qualified_only: bool = Query(False, alias="qualifiedOnly"), engine: Engine = Depends(db)):
    with engine.connect() as c:
        aid = c.execute(text("SELECT id FROM stmt_assessments WHERE request_id = :id"), {"id": request_id}).scalar()
        if not aid:
            raise HTTPException(409, "not scored yet")
        rows = c.execute(text(f"""
            SELECT device_id, shop_id, brand, model, daily_payment, credit_multiplier, required_daily,
                   deposit_required, deposit_above_avg_balance, qualified, headroom_daily
            FROM stmt_assessment_devices WHERE assessment_id = :a {"AND qualified = 1" if qualified_only else ""}
            ORDER BY qualified DESC,
                     CASE WHEN qualified = 1 THEN -required_daily ELSE -headroom_daily END
        """), {"a": aid}).mappings().all()
    return [DeviceOut(**r) for r in rows]


@router.post("/requests/{request_id}/cancel", response_model=RequestOut, response_model_by_alias=True)
def cancel(request_id: str, engine: Engine = Depends(db)):
    with engine.begin() as c:
        c.execute(text("""
            UPDATE stmt_requests SET status = 'CANCELLED', code_ciphertext = NULL
            WHERE id = :id AND status IN ('AWAITING_CODE','AWAITING_EMAIL','WRONG_CODE')
        """), {"id": request_id})
    return _request_out(engine, request_id)


@router.get("/customers/{user_id}/latest", response_model=AssessmentOut | None, response_model_by_alias=True)
def latest_for_customer(user_id: str, engine: Engine = Depends(db)):
    with engine.connect() as c:
        row = c.execute(text(_ASSESSMENT_SQL + """
            WHERE user_id = :u AND created_at >= UTC_TIMESTAMP(3) - INTERVAL :d DAY
            ORDER BY created_at DESC LIMIT 1
        """), {"u": user_id, "d": get_settings().reuse_assessment_days}).first()
    return _assessment(row)