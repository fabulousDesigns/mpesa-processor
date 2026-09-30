"""Scoring Lab: read-only views for the directors' test page (via Nest).

  GET /v1/lab/shops                          shops with AVAILABLE stock
  GET /v1/lab/requests/{id}/report           everything we learned from the statement
  GET /v1/lab/requests/{id}/pool             live device pool for ANY shop (?shopId=&scope=SHOP|REGION)
  GET /v1/lab/history                        every request, newest first (?scoredOnly=&limit=&offset=)
"""
import json
from collections import Counter, defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine, text

from app.api.deps import db, internal_only
from app.db.repository import load_device_pool
from app.scoring.qualification import evaluate_pool

router = APIRouter(prefix="/v1/lab", tags=["lab"], dependencies=[Depends(internal_only)])


def _f(x) -> float | None:
    return None if x is None else float(x)


@router.get("/shops")
def shops(engine: Engine = Depends(db)):
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT s.id, s.name, s.region, s.county, COUNT(d.id) AS available
            FROM shops s JOIN devices d ON d.shopId = s.id AND d.status = 'AVAILABLE'
            WHERE s.isActive = 1
            GROUP BY s.id, s.name, s.region, s.county
            ORDER BY available DESC, s.name
        """)).mappings().all()
    return [dict(r) for r in rows]


def _assessment_row(c, request_id: str):
    a = c.execute(text("""
        SELECT a.*, s.customer_name, s.msisdn, s.period_start, s.period_end, s.txn_count, s.page_count,
               s.reconciled, s.verification_code, s.first_txn_at, s.last_txn_at
        FROM stmt_assessments a JOIN stmt_statements s ON s.id = a.statement_id
        WHERE a.request_id = :id
    """), {"id": request_id}).mappings().first()
    if not a:
        raise HTTPException(409, "not scored yet")
    return a


@router.get("/requests/{request_id}/report")
def report(request_id: str, engine: Engine = Depends(db)):
    with engine.connect() as c:
        a = _assessment_row(c, request_id)
        txns = c.execute(text("""
            SELECT t.line_no, t.receipt_no, t.completed_at, t.details, t.paid_in, t.withdrawn, t.balance,
                   t.direction, t.txn_type, t.bucket, t.category, t.counterparty_name, t.counterparty_number,
                   t.account_ref, m.id AS merchant_id, m.name AS merchant_name, m.kind AS merchant_kind,
                   m.location_text, m.county
            FROM stmt_transactions t LEFT JOIN stmt_merchants m ON m.id = t.merchant_id
            WHERE t.statement_id = :sid ORDER BY t.completed_at, t.line_no
        """), {"sid": a["statement_id"]}).mappings().all()

    buckets = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    merchants: dict[str, dict] = {}
    places = defaultdict(lambda: {"total": 0.0, "count": 0, "merchants": set()})
    people = defaultdict(lambda: {"total": 0.0, "count": 0, "number": None})
    daily = defaultdict(lambda: {"in": 0.0, "out": 0.0, "balance": None})
    hours = [0.0] * 24
    out_rows = []

    for t in txns:
        amt_in, amt_out = float(t["paid_in"]), float(t["withdrawn"])
        b = buckets[t["bucket"]]
        b["in"] += amt_in
        b["out"] += amt_out
        b["count"] += 1
        day = t["completed_at"].date().isoformat()
        daily[day]["in"] += amt_in
        daily[day]["out"] += amt_out
        if t["balance"] is not None:
            daily[day]["balance"] = float(t["balance"])   # rows are in time order: last one wins
        if amt_out and t["bucket"] not in ("SELF_TRANSFER", "FEE"):
            hours[t["completed_at"].hour] += amt_out
        if t["merchant_id"] and amt_out:
            m = merchants.setdefault(t["merchant_id"], {
                "name": t["merchant_name"], "kind": t["merchant_kind"], "category": t["category"],
                "location": t["location_text"], "county": t["county"], "total": 0.0, "count": 0,
                "hours": Counter(), "last_seen": None})
            m["total"] += amt_out
            m["count"] += 1
            m["hours"][t["completed_at"].hour] += 1
            m["last_seen"] = t["completed_at"].isoformat()
            if t["location_text"]:
                p = places[t["location_text"]]
                p["total"] += amt_out
                p["count"] += 1
                p["merchants"].add(t["merchant_name"])
        if t["category"] == "SENT_TO_PEOPLE" and t["counterparty_name"]:
            p = people[t["counterparty_name"].upper()]
            p["total"] += amt_out
            p["count"] += 1
            p["number"] = t["counterparty_number"]
        out_rows.append({
            "time": t["completed_at"].isoformat(), "receipt": t["receipt_no"], "details": t["details"],
            "in": amt_in, "out": amt_out, "balance": _f(t["balance"]), "bucket": t["bucket"],
            "category": t["category"], "type": t["txn_type"], "merchant": t["merchant_name"],
            "location": t["location_text"],
        })

    top_merchants = sorted(merchants.values(), key=lambda m: -m["total"])[:20]
    for m in top_merchants:
        m["usual_hour"] = m.pop("hours").most_common(1)[0][0]

    return {
        "statement": {
            "customerName": a["customer_name"], "msisdn": a["msisdn"],
            "periodStart": a["period_start"].isoformat(), "periodEnd": a["period_end"].isoformat(),
            "windowDays": _f(a["window_days"]), "txnCount": a["txn_count"], "pages": a["page_count"],
            "reconciled": bool(a["reconciled"]), "verificationCode": a["verification_code"],
        },
        "assessment": {
            "incomeTotal": _f(a["income_total"]), "selfTransferTotal": _f(a["self_transfer_total"]),
            "incomeDaily": _f(a["income_daily"]), "needsDaily": _f(a["needs_daily"]),
            "committedDaily": _f(a["committed_daily"]), "wantsDaily": _f(a["wants_daily"]),
            "feesDaily": _f(a["fees_daily"]), "leftDaily": _f(a["left_daily"]),
            "affordabilityRatio": _f(a["affordability_ratio"]), "affordableDaily": _f(a["affordable_daily"]),
            "minBalance": _f(a["min_balance"]), "avgBalance": _f(a["avg_balance"]),
            "hasFuliza": bool(a["has_fuliza"]), "hasLoanApps": bool(a["has_loan_apps"]),
            "hasBetting": bool(a["has_betting"]), "confidence": a["confidence"],
            "flags": json.loads(a["flags"] or "[]"),
            "breakdown": json.loads(a["category_breakdown"] or "{}"),
            "scoredAt": a["created_at"].isoformat(),
        },
        "buckets": [{"bucket": k, **v} for k, v in sorted(buckets.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))],
        "merchants": top_merchants,
        "places": sorted([{"place": k, "total": v["total"], "count": v["count"], "merchants": sorted(v["merchants"])}
                          for k, v in places.items()], key=lambda p: -p["total"]),
        "people": sorted([{"name": k, **v} for k, v in people.items()], key=lambda p: -p["total"])[:15],
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "hours": [{"hour": h, "out": round(v, 2)} for h, v in enumerate(hours)],
        "transactions": out_rows,
    }


@router.get("/requests/{request_id}/pool")
def pool(request_id: str, shop_id: str = Query(alias="shopId"),
         scope: str = Query("SHOP", pattern="^(SHOP|REGION)$"), engine: Engine = Depends(db)):
    with engine.connect() as c:
        a = _assessment_row(c, request_id)
        devices = load_device_pool(c, shop_id, scope)
    results = evaluate_pool(Decimal(a["affordable_daily"]), devices,
                            Decimal(a["avg_balance"]) if a["avg_balance"] is not None else None)
    return [{"deviceId": r.device_id, "shopId": r.shop_id, "brand": r.brand, "model": r.model,
             "dailyPayment": float(r.daily_payment), "creditMultiplier": float(r.credit_multiplier),
             "requiredDaily": float(r.required_daily), "depositRequired": float(r.deposit_required),
             "depositAboveAvgBalance": r.deposit_above_avg_balance, "qualified": r.qualified,
             "headroomDaily": float(r.headroom_daily)} for r in results]


def _utc(dt):
    """Stored as naive UTC. The Z makes browsers convert it to EAT on their own."""
    return dt.isoformat() + "Z" if dt else None


@router.get("/history")
def history(scored_only: bool = Query(False, alias="scoredOnly"), limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), engine: Engine = Depends(db)):
    where = "WHERE a.id IS NOT NULL" if scored_only else ""
    with engine.connect() as c:
        total = c.execute(text(f"""
            SELECT COUNT(*) FROM stmt_requests r LEFT JOIN stmt_assessments a ON a.request_id = r.id {where}
        """)).scalar()
        rows = c.execute(text(f"""
            SELECT r.id, r.status, r.failure_reason, r.created_at, r.code_submitted_at, r.msisdn, r.shop_id,
                   sh.name AS shop_name, u.firstName, u.lastName,
                   a.affordable_daily, a.left_daily, a.confidence, a.window_days, a.flags, a.created_at AS scored_at,
                   s.txn_count, s.customer_name
            FROM stmt_requests r
            LEFT JOIN users u ON u.id = r.user_id
            LEFT JOIN shops sh ON sh.id = r.shop_id
            LEFT JOIN stmt_assessments a ON a.request_id = r.id
            LEFT JOIN stmt_statements s ON s.request_id = r.id
            {where}
            ORDER BY r.created_at DESC
            LIMIT :limit OFFSET :offset
        """), {"limit": limit, "offset": offset}).mappings().all()
    items = []
    for r in rows:
        secs = ((r["scored_at"] - r["code_submitted_at"]).total_seconds()
                if r["scored_at"] and r["code_submitted_at"] else None)
        items.append({
            "requestId": r["id"], "status": r["status"], "failureReason": r["failure_reason"],
            "createdAt": _utc(r["created_at"]), "scoredAt": _utc(r["scored_at"]),
            "secondsToScore": round(secs, 1) if secs is not None and secs >= 0 else None,
            "customerName": r["customer_name"] or " ".join(x for x in (r["firstName"], r["lastName"]) if x),
            "msisdn": r["msisdn"], "shopId": r["shop_id"], "shopName": r["shop_name"],
            "affordableDaily": _f(r["affordable_daily"]), "leftDaily": _f(r["left_daily"]),
            "confidence": r["confidence"], "windowDays": _f(r["window_days"]), "txnCount": r["txn_count"],
            "flags": json.loads(r["flags"]) if r["flags"] else [],
        })
    return {"total": total, "items": items}