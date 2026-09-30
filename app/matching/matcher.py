"""Pairs an agent's code with the customer's Safaricom email. Order doesn't matter:

  code first  -> request parks as AWAITING_EMAIL, the email's arrival triggers match()
  email first -> email sits UNCLAIMED, the code submission triggers match()

Candidates are UNCLAIMED emails for the customer's number that landed after the request
opened (minus a small lookback). The code is tried on each; the one it opens is claimed
atomically. Nothing is held locked while PDFs are being decrypted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time, timedelta
from typing import Callable

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.msisdn import normalize_msisdn
from app.db.repository import new_id
from app.matching.codes import clean_code, decrypt_code, encrypt_code
from app.parsing.errors import NotAStatement, WrongCode
from app.parsing.pdf_open import try_open
from app.scoring.pipeline import PipelineResult, score_statement

log = logging.getLogger(__name__)

# Given an email row (dict), return the ENCRYPTED attachment bytes (from S3 in production).
FetchAttachment = Callable[[dict], bytes]

LIVE = ("AWAITING_CODE", "AWAITING_EMAIL", "WRONG_CODE", "OPENED", "PARSED")
WAITING_FOR_MATCH = ("AWAITING_EMAIL",)


@dataclass(frozen=True)
class MatchOutcome:
    request_id: str
    status: str          # AWAITING_EMAIL | WRONG_CODE | LOCKED_OUT | SCORED | FAILED | ...
    message: str         # what the agent sees
    scoring: PipelineResult | None = None


def mask_of(msisdn: str) -> str:
    return f"{msisdn[:6]}***{msisdn[-3:]}"


# ── 1. agent opens a qualification request for a customer ───────────────────
def create_request(engine: Engine, *, user_id: str, agent_id: str | None, shop_id: str | None,
                   consent_version: str) -> str:
    s = get_settings()
    with engine.begin() as c:
        phone = c.execute(text("SELECT phoneNumber FROM users WHERE id = :u"), {"u": user_id}).scalar()
        msisdn = normalize_msisdn(phone)
        if not msisdn:
            raise ValueError("customer has no valid phone number")
        rid = new_id()
        try:
            c.execute(text("""
                INSERT INTO stmt_requests (id, user_id, agent_id, shop_id, msisdn, status, consent_at,
                                           consent_version, expires_at)
                VALUES (:id, :u, :a, :s, :m, 'AWAITING_CODE', UTC_TIMESTAMP(3), :cv,
                        UTC_TIMESTAMP(3) + INTERVAL :ttl MINUTE)
            """), {"id": rid, "u": user_id, "a": agent_id, "s": shop_id, "m": msisdn,
                   "cv": consent_version, "ttl": s.request_ttl_minutes})
            return rid
        except IntegrityError:
            pass
    # The customer already has a live request (unique index). Resume it instead of failing.
    with engine.connect() as c:
        return c.execute(text("SELECT id FROM stmt_requests WHERE live_user_id = :u"), {"u": user_id}).scalar_one()


# ── 2. agent submits the code from the customer's SMS ────────────────────────
def submit_code(engine: Engine, request_id: str, raw_code: str, fetch: FetchAttachment,
                sms_requested_time: time | None = None) -> MatchOutcome:
    code = clean_code(raw_code)
    with engine.begin() as c:
        r = c.execute(text("""
            UPDATE stmt_requests
            SET code_ciphertext = :ct, code_submitted_at = UTC_TIMESTAMP(3), sms_requested_time = :t,
                status = 'AWAITING_EMAIL', failure_reason = NULL
            WHERE id = :id AND status IN ('AWAITING_CODE','AWAITING_EMAIL','WRONG_CODE')
              AND expires_at > UTC_TIMESTAMP(3)
        """), {"id": request_id, "ct": encrypt_code(code), "t": sms_requested_time})
        if r.rowcount == 0:
            st = c.execute(text("SELECT status FROM stmt_requests WHERE id = :id"), {"id": request_id}).scalar()
            return MatchOutcome(request_id, st or "UNKNOWN", "This request is closed. Start a new one.")
    return match(engine, request_id, fetch)


# ── 3. a Safaricom email was stored (called by the ingestion worker) ─────────
def on_email_stored(engine: Engine, email_id: str, fetch: FetchAttachment) -> list[MatchOutcome]:
    with engine.connect() as c:
        e = c.execute(text("SELECT msisdn, msisdn_masked FROM stmt_inbound_emails WHERE id = :id"),
                      {"id": email_id}).one()
        ids = c.execute(text("""
            SELECT id FROM stmt_requests
            WHERE status = 'AWAITING_EMAIL' AND code_ciphertext IS NOT NULL
              AND expires_at > UTC_TIMESTAMP(3)
              AND (msisdn = :m OR (:m IS NULL AND CONCAT(LEFT(msisdn,6),'***',RIGHT(msisdn,3)) = :mask))
            ORDER BY created_at
        """), {"m": e.msisdn, "mask": e.msisdn_masked}).scalars().all()
    return [match(engine, rid, fetch) for rid in ids]


# ── the core ─────────────────────────────────────────────────────────────────
def _candidates(c, req, lookback_min: int) -> list[dict]:
    rows = c.execute(text("""
        SELECT id, s3_bucket, s3_key, attachment_s3_key, attachment_filename, requested_time, received_at
        FROM stmt_inbound_emails
        WHERE status = 'UNCLAIMED'
          AND received_at >= :since
          AND (msisdn = :m OR (msisdn IS NULL AND msisdn_masked = :mask))
        ORDER BY received_at DESC
        LIMIT 10
    """), {"m": req.msisdn, "mask": mask_of(req.msisdn),
           "since": req.created_at - timedelta(minutes=lookback_min)}).mappings().all()
    rows = [dict(r) for r in rows]
    target = _secs(req.sms_requested_time)
    if target is not None:
        # The SMS "requested at" time equals the subject's time on the right email. Try that one first.
        rows.sort(key=lambda r: abs(_secs(r["requested_time"]) - target) if r["requested_time"] is not None else 10**9)
    return rows


def _secs(t) -> int | None:
    """MariaDB TIME comes back from PyMySQL as timedelta; our own inputs are datetime.time."""
    if t is None:
        return None
    if isinstance(t, timedelta):
        return int(t.total_seconds())
    return t.hour * 3600 + t.minute * 60 + t.second


def match(engine: Engine, request_id: str, fetch: FetchAttachment) -> MatchOutcome:
    s = get_settings()
    with engine.connect() as c:
        req = c.execute(text("""
            SELECT id, msisdn, status, code_ciphertext, code_attempts, max_code_attempts,
                   sms_requested_time, created_at, expires_at
            FROM stmt_requests WHERE id = :id
        """), {"id": request_id}).one()
        if req.status not in WAITING_FOR_MATCH or req.code_ciphertext is None:
            return MatchOutcome(request_id, req.status, "Nothing to match.")
        candidates = _candidates(c, req, s.email_lookback_minutes)

    if not candidates:
        return MatchOutcome(request_id, "AWAITING_EMAIL", "Waiting for Safaricom's email. This usually takes seconds.")

    code = decrypt_code(req.code_ciphertext)
    for email in candidates:
        try:
            decrypted = try_open(fetch(email), code)
        except WrongCode:
            continue
        except NotAStatement as e:
            with engine.begin() as c:
                c.execute(text("UPDATE stmt_inbound_emails SET status='UNPARSEABLE', status_reason=:r "
                               "WHERE id=:id AND status='UNCLAIMED'"), {"id": email["id"], "r": str(e)[:255]})
            continue

        # Opened. Claim the email and move the request on, together, or not at all.
        claim = _claim(engine, email["id"], request_id)
        if claim == "TAKEN":
            continue  # another match grabbed this email a millisecond earlier
        if claim == "REQUEST_CLOSED":
            return MatchOutcome(request_id, "CANCELLED", "This request is closed.")

        result = score_statement(engine, request_id, decrypted, settings=s)
        msg = ("Statement scored." if result.status == "SCORED"
               else f"We opened the statement but couldn't score it: {result.reason}")
        return MatchOutcome(request_id, result.status, msg, result)

    # Candidates existed and the code opened none of them.
    with engine.begin() as c:
        c.execute(text("""
            UPDATE stmt_requests
            SET code_attempts = code_attempts + 1,
                code_ciphertext = NULL,
                status = IF(code_attempts + 1 >= max_code_attempts, 'LOCKED_OUT', 'WRONG_CODE'),
                failure_reason = 'code did not open the statement'
            WHERE id = :id AND status = 'AWAITING_EMAIL'
        """), {"id": request_id})
        st, left = c.execute(text("SELECT status, max_code_attempts - code_attempts FROM stmt_requests "
                                  "WHERE id = :id"), {"id": request_id}).one()
    if st == "LOCKED_OUT":
        return MatchOutcome(request_id, st, "Too many wrong codes. Ask the customer to request a new statement.")
    return MatchOutcome(request_id, st, f"That code didn't open the statement. {left} tries left.")


class _Abort(Exception):
    pass


def _claim(engine: Engine, email_id: str, request_id: str) -> str:
    try:
        with engine.begin() as c:
            if not c.execute(text("""
                UPDATE stmt_inbound_emails
                SET status = 'CLAIMED', claimed_by_request_id = :r, claimed_at = UTC_TIMESTAMP(3)
                WHERE id = :e AND status = 'UNCLAIMED'
            """), {"e": email_id, "r": request_id}).rowcount:
                return "TAKEN"
            if not c.execute(text("""
                UPDATE stmt_requests SET status = 'OPENED', email_id = :e, code_ciphertext = NULL
                WHERE id = :r AND status = 'AWAITING_EMAIL'
            """), {"e": email_id, "r": request_id}).rowcount:
                raise _Abort  # request cancelled/expired meanwhile: roll back so the email stays free
        return "OK"
    except _Abort:
        return "REQUEST_CLOSED"


# ── housekeeping (run every few minutes) ─────────────────────────────────────
def expire_stale(engine: Engine) -> tuple[int, int]:
    s = get_settings()
    with engine.begin() as c:
        req = c.execute(text("""
            UPDATE stmt_requests SET status = 'EXPIRED', code_ciphertext = NULL
            WHERE status IN ('AWAITING_CODE','AWAITING_EMAIL','WRONG_CODE') AND expires_at <= UTC_TIMESTAMP(3)
        """)).rowcount
        em = c.execute(text("""
            UPDATE stmt_inbound_emails SET status = 'EXPIRED'
            WHERE status = 'UNCLAIMED' AND received_at < UTC_TIMESTAMP(3) - INTERVAL :h HOUR
        """), {"h": s.unclaimed_email_ttl_hours}).rowcount
    return req, em