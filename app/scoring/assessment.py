"""Classified rows -> the numbers the business decides on.

    funding     = income + net money the customer moved in from their own accounts
    must_pay    = needs + committed + fees + unknown outflows (unknown is treated as a need)
    left_daily  = (funding - must_pay) / window_days
    affordable  = max(0, left_daily * affordability_ratio)

Left over is effectively what they currently spend on WANTS: the money a device
payment can realistically replace. Wants are reported but never subtracted.

Excluded from funding on purpose:
  * BORROWED (Fuliza, loan apps): borrowed money is not capacity to repay
  * REFUND: money coming back, not new money
  * BETTING_WINNINGS: too unstable to lend against

Any window length works. Everything is per day, and short windows lower confidence
instead of blocking the customer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.classify.classifier import ClassifiedTxn
from app.parsing.statement_parser import ParsedStatement

ENGINE_VERSION = "1.0.0"
UNSTABLE_INCOME = {"BETTING_WINNINGS"}
CENT = Decimal("0.01")
ZERO = Decimal("0")


class NotScorable(Exception):
    """The statement must not be scored (didn't reconcile, or empty)."""


@dataclass(frozen=True)
class AssessmentConfig:
    affordability_ratio: Decimal = Decimal("0.900")
    short_window_days: int = 30        # below this: MEDIUM at best
    very_short_window_days: int = 14   # below this: LOW
    self_transfer_heavy_share: Decimal = Decimal("0.50")
    unknown_medium_share: Decimal = Decimal("0.20")
    unknown_low_share: Decimal = Decimal("0.40")
    borrowed_low_share: Decimal = Decimal("0.20")
    betting_low_share: Decimal = Decimal("0.10")
    low_balance_threshold: Decimal = Decimal("100")


@dataclass
class Assessment:
    window_days: Decimal
    income_total: Decimal
    self_transfer_total: Decimal       # NET in from own accounts (never negative)
    funding_total: Decimal
    income_daily: Decimal              # funding per day (what they actually have to live on)
    needs_daily: Decimal
    committed_daily: Decimal
    wants_daily: Decimal
    fees_daily: Decimal
    unknown_out_daily: Decimal
    left_daily: Decimal
    affordability_ratio: Decimal
    affordable_daily: Decimal
    min_balance: Decimal | None
    avg_balance: Decimal | None
    has_fuliza: bool
    has_loan_apps: bool
    has_betting: bool
    confidence: str
    flags: list[str] = field(default_factory=list)
    category_breakdown: dict = field(default_factory=dict)
    engine_version: str = ENGINE_VERSION


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _share(part: Decimal, whole: Decimal) -> Decimal:
    return part / whole if whole > 0 else ZERO


_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def assess(st: ParsedStatement, rows: list[ClassifiedTxn],
           cfg: AssessmentConfig = AssessmentConfig()) -> Assessment:
    if not st.reconciled:
        raise NotScorable("statement did not reconcile with Safaricom's summary")
    if not rows:
        raise NotScorable("statement has no transactions")

    days = st.window_days
    tin: dict[str, Decimal] = defaultdict(lambda: ZERO)    # bucket -> money in
    tout: dict[str, Decimal] = defaultdict(lambda: ZERO)   # bucket -> money out
    cat: dict[tuple[str, str], dict] = {}
    unstable_in = ZERO
    funding_days: set = set()

    for r in rows:
        amt = r.txn.paid_in if r.txn.direction == "IN" else r.txn.withdrawn
        (tin if r.txn.direction == "IN" else tout)[r.bucket] += amt
        k = (r.bucket, r.category)
        c = cat.setdefault(k, {"bucket": r.bucket, "category": r.category, "in": ZERO, "out": ZERO, "count": 0})
        c["in" if r.txn.direction == "IN" else "out"] += amt
        c["count"] += 1
        if r.category in UNSTABLE_INCOME:
            unstable_in += amt
        if r.txn.direction == "IN" and r.bucket in ("INCOME", "SELF_TRANSFER") and r.category not in UNSTABLE_INCOME:
            funding_days.add(r.txn.completed_at.date())

    income = tin["INCOME"] - unstable_in
    net_self_in = max(ZERO, tin["SELF_TRANSFER"] - tout["SELF_TRANSFER"])
    funding = income + net_self_in
    needs, committed, wants = tout["NEED"], tout["COMMITTED"], tout["WANT"]
    fees, unknown_out = tout["FEE"], tout["UNKNOWN"]
    must_pay = needs + committed + fees + unknown_out
    left_daily = (funding - must_pay) / days
    affordable = max(ZERO, left_daily * cfg.affordability_ratio)

    balances = [r.txn.balance for r in rows if r.txn.balance is not None]
    total_out = sum(tout.values(), ZERO)
    cats_present = {r.category for r in rows}

    # ── confidence: start HIGH, each finding can only pull it down ─────────
    conf, flags = "HIGH", []

    def cap(level: str, flag: str):
        nonlocal conf
        flags.append(flag)
        if _RANK[level] < _RANK[conf]:
            conf = level

    if days < cfg.very_short_window_days:
        cap("LOW", f"SHORT_WINDOW_{int(days)}D")
    elif days < cfg.short_window_days:
        cap("MEDIUM", f"SHORT_WINDOW_{int(days)}D")

    if _share(net_self_in, funding) > cfg.self_transfer_heavy_share:
        cap("MEDIUM", "SELF_TRANSFER_HEAVY")   # can't see the real income behind bank top-ups

    unk = _share(unknown_out, total_out)
    if unk > cfg.unknown_low_share:
        cap("LOW", "HIGH_UNKNOWN_SHARE")
    elif unk > cfg.unknown_medium_share:
        cap("MEDIUM", "HIGH_UNKNOWN_SHARE")

    borrowed = tin["BORROWED"]
    if borrowed > 0:
        flags.append("BORROWS")
        if _share(borrowed, funding + borrowed) > cfg.borrowed_low_share:
            cap("LOW", "LOAN_DEPENDENT")

    betting_out = sum((c["out"] for (b, k), c in cat.items() if k == "BETTING"), ZERO)
    if betting_out > 0 or unstable_in > 0:
        flags.append("BETTING")
        if _share(betting_out, total_out) > cfg.betting_low_share:
            cap("LOW", "HEAVY_BETTING")

    if "ASSET_FINANCE_REPAYMENT" in cats_present:
        flags.append("EXISTING_ASSET_FINANCE")
    if len(funding_days) < 3:
        cap("MEDIUM", "LUMPY_INCOME")
    if balances and min(balances) < cfg.low_balance_threshold:
        flags.append("RUNS_NEAR_ZERO")
    if left_daily <= 0:
        flags.append("NO_HEADROOM")

    breakdown = {
        "by_category": [
            {**c, "in": str(_q(c["in"])), "out": str(_q(c["out"])),
             "out_daily": str(_q(c["out"] / days)), "in_daily": str(_q(c["in"] / days))}
            for c in sorted(cat.values(), key=lambda c: -(c["out"] + c["in"]))
        ],
        "left_daily_income_only": str(_q((income - must_pay) / days)),  # the harsh view, for the record
        "borrowed_total": str(_q(borrowed)),
        "refund_total": str(_q(tin["REFUND"])),
        "funding_days": len(funding_days),
    }

    return Assessment(
        window_days=days, income_total=_q(income), self_transfer_total=_q(net_self_in),
        funding_total=_q(funding), income_daily=_q(funding / days),
        needs_daily=_q(needs / days), committed_daily=_q(committed / days),
        wants_daily=_q(wants / days), fees_daily=_q(fees / days),
        unknown_out_daily=_q(unknown_out / days), left_daily=_q(left_daily),
        affordability_ratio=cfg.affordability_ratio, affordable_daily=_q(affordable),
        min_balance=min(balances) if balances else None,
        avg_balance=_q(sum(balances, ZERO) / len(balances)) if balances else None,
        has_fuliza="FULIZA" in cats_present,
        has_loan_apps=bool(cats_present & {"LOAN_APP_DISBURSEMENT", "LOAN_REPAYMENT", "MOBILE_LOAN",
                                           "ASSET_FINANCE_REPAYMENT"}),
        has_betting="BETTING" in flags,
        confidence=conf, flags=flags, category_breakdown=breakdown,
    )