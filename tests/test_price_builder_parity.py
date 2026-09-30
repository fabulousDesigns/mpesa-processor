"""Fuzz the Python PriceBuilder port against the original JS logic. Needs `node` on PATH."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.pricing.price_builder import from_device

REF = Path(__file__).parent / "fixtures" / "price_builder_reference.js"
FIELDS = {
    "loanTermDays": "loan_term_days", "depositPercentage": "deposit_percentage",
    "targetSalesPrice": "target_sales_price", "depositAmount": "deposit_amount",
    "loanAmount": "loan_amount", "totalInstallments": "total_installments",
    "dailyPayment": "daily_payment", "totalCollections": "total_collections",
    "totalCustomerPays": "total_customer_pays", "depositPromoDiscount": "deposit_promo_discount",
    "actualDepositRequired": "actual_deposit_required",
}


def _random_device(rng: random.Random) -> dict:
    # TypeORM returns DECIMAL columns as strings from MariaDB, so mix strings in.
    def dec(x):
        return f"{x:.2f}" if rng.random() < 0.6 else x
    rrp = rng.choice([rng.randint(3000, 80000), rng.randint(30, 800) * 50, 12345.67])
    return {
        "rrp": dec(rrp),
        "margin": dec(rng.choice([0, 100, 45.5, rng.uniform(0, 150)])),
        "stockOwnership": rng.choice(["CELLIPAY", "SHOP"]),
        "loanTermDays": rng.choice([365, 180, 90, 270, 0, None, rng.randint(1, 730)]),
        "depositPercentage": dec(rng.choice([30, 25, 20, 0, 100, 33.33, rng.uniform(0, 100)])),
        "depositPromoDiscount": dec(rng.choice([0, 0, 500, 1000, 999999, rng.uniform(0, 3000)])),
    }


def _edge_devices() -> list[dict]:
    # Hand-picked cases around the rounding boundaries.
    return [
        {"rrp": "15000.00", "margin": "100.00", "stockOwnership": "SHOP", "loanTermDays": 365,
         "depositPercentage": "30.00", "depositPromoDiscount": "0.00"},
        {"rrp": 1650, "margin": 0, "stockOwnership": "CELLIPAY", "loanTermDays": 365,
         "depositPercentage": 30, "depositPromoDiscount": 0},          # deposit 495 -> tie at 4.95
        {"rrp": 1750, "margin": 0, "stockOwnership": "CELLIPAY", "loanTermDays": 365,
         "depositPercentage": 30, "depositPromoDiscount": 0},          # deposit 525 -> tie at 5.25? no, .25
        {"rrp": 50000, "margin": 50, "stockOwnership": "SHOP", "loanTermDays": None,
         "depositPercentage": None, "depositPromoDiscount": None},    # all defaults
        {"rrp": 20000, "margin": 80, "stockOwnership": "SHOP", "loanTermDays": "180",
         "depositPercentage": "0.00", "depositPromoDiscount": "0"},   # 0% deposit quirk -> 30%
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_port_matches_typescript_exactly():
    rng = random.Random(20260921)
    devices = _edge_devices() + [_random_device(rng) for _ in range(20000)]
    out = subprocess.run(["node", str(REF)], input=json.dumps(devices),
                         capture_output=True, text=True, check=True)
    expected = json.loads(out.stdout)
    for dev, exp in zip(devices, expected):
        got = from_device(dev)
        for js_key, py_key in FIELDS.items():
            assert getattr(got, py_key) == exp[js_key], (dev, js_key, getattr(got, py_key), exp[js_key])


def test_known_example():
    p = from_device({"rrp": "15000.00", "margin": "100.00", "stockOwnership": "SHOP",
                     "loanTermDays": 365, "depositPercentage": "30.00", "depositPromoDiscount": "0.00"})
    assert p.deposit_amount == 4500
    assert p.daily_payment == 70          # (30000 - 4500) / 365 = 69.86 -> ceil to 5 -> 70
    assert p.total_collections == 25550
