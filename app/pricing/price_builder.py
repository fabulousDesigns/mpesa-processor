"""
Python port of the customer-facing half of Nest's PriceBuilder.calculatePricing / fromDevice.

Why this exists: the scoring engine decides "this customer qualifies for this device"
by comparing the device's dailyPayment to what the customer can afford. If our daily
number differs from what Nest writes to sales.dailyPaymentAmount at step 9, the agent
sees a phone qualify and then the sale shows a different daily. So this file must match
the TypeScript exactly, rounding quirks included. It uses float maths on purpose,
because JS numbers are IEEE doubles and so are Python floats, which makes results
bit-for-bit identical. tests/test_price_builder_parity.py fuzzes it against the
original TS.

Commission / payout / profit fields are deliberately NOT ported: scoring never needs
them, and fewer ported lines means fewer places to drift.

If PriceBuilder.ts changes, this file must change in the same PR.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

StockOwnership = Literal["CELLIPAY", "SHOP"]

DEFAULT_LOAN_TERM_DAYS = 365
DEFAULT_DEPOSIT_PERCENTAGE = 30


def _js_round(x: float) -> float:
    """JS Math.round: nearest integer, exact .5 goes UP (toward +infinity).
    Python's round() is banker's rounding, so it can't be used here."""
    f = math.floor(x)
    return float(f + 1) if (x - f) >= 0.5 else float(f)


def _js_number(v: Any) -> float:
    """JS Number(v) for the value types we actually get from MariaDB/TypeORM."""
    if v is None:
        return 0.0  # Number(null) === 0
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0
        try:
            return float(s)
        except ValueError:
            return math.nan
    return math.nan


def _js_or_default(v: Any, default: float) -> float:
    """JS `Number(v) || default`: 0, NaN, null, undefined and '' all fall back.
    Note the quirk this reproduces: a device with depositPercentage 0 gets 30%."""
    n = _js_number(v)
    return default if (n == 0 or math.isnan(n)) else n


@dataclass(frozen=True)
class CustomerPricing:
    rrp: float
    margin: float
    stock_ownership: StockOwnership
    loan_term_days: int
    deposit_percentage: float
    target_sales_price: float
    deposit_amount: float
    loan_amount: float
    total_installments: float
    daily_payment: float
    total_collections: float
    total_customer_pays: float
    deposit_promo_discount: float
    actual_deposit_required: float


def calculate_pricing(
    rrp: float,
    margin: float,
    stock_ownership: StockOwnership,
    loan_term_days: float = DEFAULT_LOAN_TERM_DAYS,
    deposit_percentage: float = DEFAULT_DEPOSIT_PERCENTAGE,
    deposit_promo_discount: float = 0,
) -> CustomerPricing:
    term_days = max(1, int(_js_round(loan_term_days)))
    dep_pct = max(0.0, min(100.0, deposit_percentage))

    target_sales_price = rrp * (1 + margin / 100)
    deposit_raw = rrp * (dep_pct / 100)
    deposit_amount = _js_round(deposit_raw / 100) * 100
    loan_amount = rrp - deposit_amount
    total_installments = target_sales_price - deposit_amount
    daily_payment_raw = total_installments / term_days
    daily_payment = math.ceil(daily_payment_raw / 5) * 5
    total_collections = daily_payment * term_days

    promo = min(max(0.0, deposit_promo_discount), deposit_amount)
    actual_deposit_required = deposit_amount - promo

    return CustomerPricing(
        rrp=rrp,
        margin=margin,
        stock_ownership=stock_ownership,
        loan_term_days=term_days,
        deposit_percentage=dep_pct,
        target_sales_price=target_sales_price,
        deposit_amount=deposit_amount,
        loan_amount=loan_amount,
        total_installments=total_installments,
        daily_payment=float(daily_payment),
        total_collections=float(total_collections),
        total_customer_pays=deposit_amount + total_collections,
        deposit_promo_discount=promo,
        actual_deposit_required=actual_deposit_required,
    )


def from_device(device: dict[str, Any]) -> CustomerPricing:
    """Mirror of PriceBuilder.fromDevice. Pass a devices row as a dict (DB column names)."""
    return calculate_pricing(
        rrp=_js_number(device["rrp"]),
        margin=_js_number(device["margin"]),
        stock_ownership=device["stockOwnership"],
        loan_term_days=_js_or_default(device.get("loanTermDays"), DEFAULT_LOAN_TERM_DAYS),
        deposit_percentage=_js_or_default(device.get("depositPercentage"), DEFAULT_DEPOSIT_PERCENTAGE),
        deposit_promo_discount=_js_or_default(device.get("depositPromoDiscount"), 0),
    )
