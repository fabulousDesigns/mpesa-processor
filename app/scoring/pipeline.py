"""Decrypted statement -> everything stored, in ONE transaction.

Called by the matcher right after a code opens a PDF. Either all of it lands
(statement, rows, merchants, assessment, device snapshot, request = SCORED) or none
of it does and the request is marked FAILED with a reason the agent can act on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine

from app.classify.classifier import classify_statement
from app.core.config import Settings, get_settings
from app.core.names import name_matches
from app.db import repository as repo
from app.parsing.errors import StatementError
from app.parsing.statement_parser import parse_statement
from app.scoring.assessment import NotScorable, assess
from app.scoring.qualification import evaluate_pool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    request_id: str
    status: str                     # SCORED | FAILED
    reason: str | None = None
    statement_id: str | None = None
    assessment_id: str | None = None
    affordable_daily: Decimal | None = None
    confidence: str | None = None
    qualified_count: int = 0


def _fail(engine: Engine, request_id: str, reason: str) -> PipelineResult:
    with engine.begin() as conn:
        repo.set_request_status(conn, request_id, "FAILED", reason)
    log.warning("request %s failed: %s", request_id, reason)
    return PipelineResult(request_id=request_id, status="FAILED", reason=reason)


def _fail_in(conn, request_id: str, reason: str) -> PipelineResult:
    repo.set_request_status(conn, request_id, "FAILED", reason)
    log.warning("request %s failed: %s", request_id, reason)
    return PipelineResult(request_id=request_id, status="FAILED", reason=reason)


def score_statement(engine: Engine, request_id: str, decrypted_pdf: bytes,
                    decrypted_s3_key: str | None = None, settings: Settings | None = None) -> PipelineResult:
    settings = settings or get_settings()

    # Parsing is CPU work: do it BEFORE opening the transaction so no row lock is held for seconds.
    try:
        st = parse_statement(decrypted_pdf)
    except StatementError as e:
        return _fail(engine, request_id, f"{e.code}: {e}")

    with engine.begin() as conn:
        req = repo.load_request_for_update(conn, request_id)
        if req is None:
            raise ValueError(f"unknown request {request_id}")
        if req.status not in ("OPENED",):
            log.info("request %s is %s, skipping (already handled)", request_id, req.status)
            return PipelineResult(request_id=request_id, status=req.status)

        customer = repo.load_customer(conn, req.user_id)
        msisdn_ok = st.msisdn == req.msisdn
        name_ok = bool(customer) and name_matches(st.customer_name, customer.first_name, customer.last_name)

        # Identity first. If this isn't provably the customer's statement we store NOTHING from it:
        # it may be someone else's financial data, and they never consented.
        if not msisdn_ok:
            return _fail_in(conn, req.id, "MSISDN_MISMATCH: statement number is not the customer's")
        if not name_ok:
            return _fail_in(conn, req.id, "NAME_MISMATCH: statement name does not match KYC name")

        rows = classify_statement(st)
        merchant_ids = repo.upsert_merchants(conn, st.msisdn, rows)
        statement_id = repo.insert_statement(conn, request_id=req.id, email_id=req.email_id, st=st,
                                             name_matched=name_ok, msisdn_matched=msisdn_ok,
                                             decrypted_s3_key=decrypted_s3_key)
        repo.insert_transactions(conn, statement_id, rows, merchant_ids)

        try:
            a = assess(st, rows, settings.assessment_config())
        except NotScorable as e:
            # Keep the stored rows (they're the customer's, identity checked) for manual review.
            repo.set_request_status(conn, req.id, "FAILED", f"NOT_SCORABLE: {e}")
            return PipelineResult(request_id=req.id, status="FAILED", reason=f"NOT_SCORABLE: {e}",
                                  statement_id=statement_id)

        pool = repo.load_device_pool(conn, req.shop_id, settings.device_pool_scope) if req.shop_id else []
        results = evaluate_pool(a.affordable_daily, pool, a.avg_balance)
        assessment_id = repo.insert_assessment(conn, statement_id=statement_id, request_id=req.id,
                                               user_id=req.user_id, a=a)
        repo.insert_device_results(conn, assessment_id, results)
        repo.set_request_status(conn, req.id, "SCORED")

    return PipelineResult(request_id=request_id, status="SCORED", statement_id=statement_id,
                          assessment_id=assessment_id, affordable_daily=a.affordable_daily,
                          confidence=a.confidence, qualified_count=sum(r.qualified for r in results))