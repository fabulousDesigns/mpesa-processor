"""All SQL for the scoring flow. Raw SQL on purpose: you can read exactly what hits MariaDB.

Python only WRITES to stmt_* tables. users / devices / shops are read-only here.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import Connection, bindparam, text

from app.classify.classifier import ClassifiedTxn
from app.parsing.statement_parser import ParsedStatement
from app.scoring.assessment import Assessment
from app.scoring.qualification import DeviceResult


def new_id() -> str:
    return str(uuid.uuid4())


# ── reads from Nest-owned tables ──────────────────────────────────────────────

@dataclass(frozen=True)
class Customer:
    id: str
    first_name: str
    last_name: str
    phone_number: str


def load_customer(conn: Connection, user_id: str) -> Customer | None:
    r = conn.execute(text(
        "SELECT id, firstName, lastName, phoneNumber FROM users WHERE id = :id"), {"id": user_id}).first()
    return Customer(*r) if r else None


_POOL_COLUMNS = """
    d.id, d.brand, d.model, d.shopId, d.rrp, d.margin, d.stockOwnership, d.loanTermDays,
    d.depositPercentage, d.depositPromoDiscount, d.creditMultiplier
"""


def load_device_pool(conn: Connection, shop_id: str, scope: str = "REGION") -> list[dict]:
    """AVAILABLE devices the agent can actually sell from: the shop's region, or just the shop."""
    if scope == "SHOP":
        sql = f"""
            SELECT {_POOL_COLUMNS}
            FROM devices d
            JOIN shops s ON s.id = d.shopId
            WHERE d.status = 'AVAILABLE' AND s.isActive = 1 AND d.shopId = :shop_id"""
    else:
        sql = f"""
            SELECT {_POOL_COLUMNS}
            FROM devices d
            JOIN shops s ON s.id = d.shopId
            WHERE d.status = 'AVAILABLE' AND s.isActive = 1
              AND s.region = (SELECT region FROM shops WHERE id = :shop_id)"""
    return [dict(r._mapping) for r in conn.execute(text(sql), {"shop_id": shop_id})]


# ── writes to stmt_* ──────────────────────────────────────────────────────────

def _customer_seen_merchants(conn: Connection, msisdn: str, merchant_ids: list[str]) -> set[str]:
    """Merchants this customer already paid on an earlier statement, so we don't
    double count them in stmt_merchants.customer_count."""
    if not merchant_ids:
        return set()
    q = text("""
        SELECT DISTINCT t.merchant_id
        FROM stmt_transactions t JOIN stmt_statements s ON s.id = t.statement_id
        WHERE s.msisdn = :msisdn AND t.merchant_id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    return {r[0] for r in conn.execute(q, {"msisdn": msisdn, "ids": merchant_ids})}


def upsert_merchants(conn: Connection, msisdn: str, rows: Iterable[ClassifiedTxn]) -> dict[str, str]:
    """Insert new merchants, refresh existing ones. Returns merchant_key -> id.
    A location someone typed in by hand (MANUAL) is never overwritten by a guess from a name."""
    agg: dict[str, dict] = {}
    for r in rows:
        m = r.merchant
        if not m:
            continue
        a = agg.setdefault(m.merchant_key, {
            "id": new_id(), "merchant_key": m.merchant_key, "kind": m.kind, "number": m.number,
            "name": m.name, "category": m.category, "location_text": m.location_text,
            "county": m.county, "location_source": "NAME" if m.location_text else None,
            "first_seen_at": r.txn.completed_at, "last_seen_at": r.txn.completed_at, "txn_count": 0,
        })
        a["txn_count"] += 1
        a["first_seen_at"] = min(a["first_seen_at"], r.txn.completed_at)
        a["last_seen_at"] = max(a["last_seen_at"], r.txn.completed_at)
    if not agg:
        return {}

    conn.execute(text("""
        INSERT INTO stmt_merchants
          (id, merchant_key, kind, number, name, category, location_text, county, location_source,
           first_seen_at, last_seen_at, txn_count, customer_count)
        VALUES
          (:id, :merchant_key, :kind, :number, :name, :category, :location_text, :county, :location_source,
           :first_seen_at, :last_seen_at, :txn_count, 0)
        ON DUPLICATE KEY UPDATE
          name            = VALUES(name),
          category        = COALESCE(category, VALUES(category)),
          location_text   = IF(location_source = 'MANUAL', location_text, COALESCE(location_text, VALUES(location_text))),
          county          = IF(location_source = 'MANUAL', county, COALESCE(county, VALUES(county))),
          location_source = IF(location_source = 'MANUAL', location_source, COALESCE(location_source, VALUES(location_source))),
          first_seen_at   = LEAST(COALESCE(first_seen_at, VALUES(first_seen_at)), VALUES(first_seen_at)),
          last_seen_at    = GREATEST(COALESCE(last_seen_at, VALUES(last_seen_at)), VALUES(last_seen_at)),
          txn_count       = txn_count + VALUES(txn_count)
    """), list(agg.values()))

    q = text("SELECT merchant_key, id FROM stmt_merchants WHERE merchant_key IN :keys") \
        .bindparams(bindparam("keys", expanding=True))
    ids = {k: i for k, i in conn.execute(q, {"keys": list(agg)})}

    already = _customer_seen_merchants(conn, msisdn, list(ids.values()))
    new_for_customer = [i for i in ids.values() if i not in already]
    if new_for_customer:
        conn.execute(text("UPDATE stmt_merchants SET customer_count = customer_count + 1 WHERE id IN :ids")
                     .bindparams(bindparam("ids", expanding=True)), {"ids": new_for_customer})
    return ids


def insert_statement(conn: Connection, *, request_id: str, email_id: str, st: ParsedStatement,
                     name_matched: bool, msisdn_matched: bool, decrypted_s3_key: str | None) -> str:
    sid = new_id()
    conn.execute(text("""
        INSERT INTO stmt_statements
          (id, request_id, email_id, customer_name, msisdn, statement_email, period_start, period_end,
           request_date, first_txn_at, last_txn_at, window_days, page_count, txn_count,
           summary_paid_in, summary_paid_out, parsed_paid_in, parsed_paid_out, reconciled,
           verification_code, name_matched, msisdn_matched, decrypted_s3_key, parser_version)
        VALUES
          (:id, :request_id, :email_id, :customer_name, :msisdn, :statement_email, :period_start, :period_end,
           :request_date, :first_txn_at, :last_txn_at, :window_days, :page_count, :txn_count,
           :summary_paid_in, :summary_paid_out, :parsed_paid_in, :parsed_paid_out, :reconciled,
           :verification_code, :name_matched, :msisdn_matched, :decrypted_s3_key, :parser_version)
    """), {
        "id": sid, "request_id": request_id, "email_id": email_id, "customer_name": st.customer_name,
        "msisdn": st.msisdn, "statement_email": st.statement_email, "period_start": st.period_start,
        "period_end": st.period_end, "request_date": st.request_date, "first_txn_at": st.first_txn_at,
        "last_txn_at": st.last_txn_at, "window_days": st.window_days, "page_count": st.page_count,
        "txn_count": len(st.txns), "summary_paid_in": st.summary_paid_in,
        "summary_paid_out": st.summary_paid_out, "parsed_paid_in": st.parsed_paid_in,
        "parsed_paid_out": st.parsed_paid_out, "reconciled": st.reconciled,
        "verification_code": st.verification_code, "name_matched": name_matched,
        "msisdn_matched": msisdn_matched, "decrypted_s3_key": decrypted_s3_key,
        "parser_version": st.parser_version,
    })
    return sid


def insert_transactions(conn: Connection, statement_id: str, rows: list[ClassifiedTxn],
                        merchant_ids: dict[str, str]) -> None:
    if not rows:
        return
    conn.execute(text("""
        INSERT INTO stmt_transactions
          (statement_id, line_no, receipt_no, completed_at, details, txn_status, paid_in, withdrawn,
           balance, direction, txn_type, bucket, category, counterparty_name, counterparty_number,
           account_ref, merchant_id, rule_id)
        VALUES
          (:statement_id, :line_no, :receipt_no, :completed_at, :details, :txn_status, :paid_in, :withdrawn,
           :balance, :direction, :txn_type, :bucket, :category, :counterparty_name, :counterparty_number,
           :account_ref, :merchant_id, :rule_id)
    """), [{
        "statement_id": statement_id, "line_no": r.txn.line_no, "receipt_no": r.txn.receipt_no,
        "completed_at": r.txn.completed_at, "details": r.txn.details, "txn_status": r.txn.txn_status,
        "paid_in": r.txn.paid_in, "withdrawn": r.txn.withdrawn, "balance": r.txn.balance,
        "direction": r.txn.direction, "txn_type": r.txn.info.txn_type, "bucket": r.bucket,
        "category": r.category, "counterparty_name": r.txn.info.counterparty_name,
        "counterparty_number": r.txn.info.counterparty_number, "account_ref": r.txn.info.account_ref,
        "merchant_id": merchant_ids.get(r.merchant.merchant_key) if r.merchant else None,
        "rule_id": r.rule_id[:50],
    } for r in rows])


def insert_assessment(conn: Connection, *, statement_id: str, request_id: str, user_id: str,
                      a: Assessment) -> str:
    aid = new_id()
    conn.execute(text("""
        INSERT INTO stmt_assessments
          (id, statement_id, request_id, user_id, window_days, income_total, self_transfer_total,
           income_daily, needs_daily, committed_daily, wants_daily, fees_daily, left_daily,
           affordability_ratio, affordable_daily, min_balance, avg_balance, has_fuliza, has_loan_apps,
           has_betting, confidence, flags, category_breakdown, engine_version)
        VALUES
          (:id, :statement_id, :request_id, :user_id, :window_days, :income_total, :self_transfer_total,
           :income_daily, :needs_daily, :committed_daily, :wants_daily, :fees_daily, :left_daily,
           :affordability_ratio, :affordable_daily, :min_balance, :avg_balance, :has_fuliza, :has_loan_apps,
           :has_betting, :confidence, :flags, :category_breakdown, :engine_version)
    """), {
        "id": aid, "statement_id": statement_id, "request_id": request_id, "user_id": user_id,
        "window_days": a.window_days, "income_total": a.income_total,
        "self_transfer_total": a.self_transfer_total, "income_daily": a.income_daily,
        "needs_daily": a.needs_daily, "committed_daily": a.committed_daily, "wants_daily": a.wants_daily,
        "fees_daily": a.fees_daily, "left_daily": a.left_daily, "affordability_ratio": a.affordability_ratio,
        "affordable_daily": a.affordable_daily, "min_balance": a.min_balance, "avg_balance": a.avg_balance,
        "has_fuliza": a.has_fuliza, "has_loan_apps": a.has_loan_apps, "has_betting": a.has_betting,
        "confidence": a.confidence, "flags": json.dumps(a.flags),
        "category_breakdown": json.dumps(a.category_breakdown), "engine_version": a.engine_version,
    })
    return aid


def insert_device_results(conn: Connection, assessment_id: str, results: list[DeviceResult]) -> None:
    if not results:
        return
    conn.execute(text("""
        INSERT INTO stmt_assessment_devices
          (assessment_id, device_id, shop_id, brand, model, daily_payment, credit_multiplier,
           required_daily, deposit_required, deposit_above_avg_balance, qualified, headroom_daily)
        VALUES
          (:assessment_id, :device_id, :shop_id, :brand, :model, :daily_payment, :credit_multiplier,
           :required_daily, :deposit_required, :deposit_above_avg_balance, :qualified, :headroom_daily)
    """), [{"assessment_id": assessment_id, **{k: getattr(r, k) for k in (
        "device_id", "shop_id", "brand", "model", "daily_payment", "credit_multiplier", "required_daily",
        "deposit_required", "deposit_above_avg_balance", "qualified", "headroom_daily")}} for r in results])


def set_request_status(conn: Connection, request_id: str, status: str, reason: str | None = None) -> None:
    conn.execute(text("""
        UPDATE stmt_requests SET status = :status, failure_reason = :reason WHERE id = :id
    """), {"id": request_id, "status": status, "reason": reason})


@dataclass(frozen=True)
class ScoringRequest:
    id: str
    user_id: str
    shop_id: str | None
    msisdn: str
    email_id: str | None
    status: str


def load_request_for_update(conn: Connection, request_id: str) -> ScoringRequest | None:
    """Row-locks the request so two workers can never score the same one."""
    r = conn.execute(text("""
        SELECT id, user_id, shop_id, msisdn, email_id, status
        FROM stmt_requests WHERE id = :id FOR UPDATE
    """), {"id": request_id}).first()
    return ScoringRequest(*r) if r else None