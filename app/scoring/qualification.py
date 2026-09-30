"""Assessment + device pool -> which phones this customer qualifies for.

    required_daily = PriceBuilder daily payment * device.creditMultiplier
    qualified      = required_daily <= affordable_daily

Bigger multiplier = stricter. Default multiplier on devices is 1.0.
Every device is evaluated and returned (qualified or not), so the agent can also see
"almost" phones and how far off they are.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from app.pricing.price_builder import from_device
from app.scoring.assessment import CENT

ZERO = Decimal("0")


@dataclass(frozen=True)
class DeviceResult:
    device_id: str
    shop_id: str | None
    brand: str | None
    model: str | None
    daily_payment: Decimal
    credit_multiplier: Decimal
    required_daily: Decimal
    deposit_required: Decimal
    qualified: bool
    headroom_daily: Decimal         # affordable - required. Negative = how far off.
    deposit_above_avg_balance: bool  # heads-up only: can they actually pay the deposit today?


def _dec(x: Any, default: str = "0") -> Decimal:
    if x is None or x == "":
        return Decimal(default)
    return Decimal(str(x))


def evaluate_pool(affordable_daily: Decimal, devices: Iterable[dict],
                  avg_balance: Decimal | None = None) -> list[DeviceResult]:
    """`devices` are rows from the devices table (dicts with DB column names)."""
    out = []
    for d in devices:
        p = from_device(d)
        daily = _dec(p.daily_payment)
        mult = _dec(d.get("creditMultiplier"), "1.0")
        if mult <= 0:
            mult = Decimal("1.0")   # a 0 multiplier would make every phone "free". Never trust it.
        required = (daily * mult).quantize(CENT)
        deposit = _dec(p.actual_deposit_required).quantize(CENT)
        out.append(DeviceResult(
            device_id=d["id"], shop_id=d.get("shopId"), brand=d.get("brand"), model=d.get("model"),
            daily_payment=daily.quantize(CENT), credit_multiplier=mult, required_daily=required,
            deposit_required=deposit, qualified=required <= affordable_daily,
            headroom_daily=(affordable_daily - required).quantize(CENT),
            deposit_above_avg_balance=avg_balance is not None and deposit > avg_balance,
        ))
    # Qualified first (priciest first, the best phone they can have), then nearest misses.
    return sorted(out, key=lambda r: (not r.qualified, -r.required_daily if r.qualified else -r.headroom_daily))