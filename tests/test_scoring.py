from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.classify.classifier import classify_statement
from app.parsing.details_parser import parse_details
from app.parsing.statement_parser import ParsedStatement, ParsedTxn
from app.scoring.assessment import AssessmentConfig, NotScorable, assess
from app.scoring.qualification import evaluate_pool

D = Decimal


def make_statement(rows: list[tuple[str, str, str]], days: int = 30, balance: str = "5000") -> ParsedStatement:
    """rows = (details, paid_in, withdrawn). Summary is set to match, so it reconciles."""
    start = date(2026, 8, 1)
    txns = []
    for i, (details, pin, out) in enumerate(rows, 1):
        txns.append(ParsedTxn(line_no=i, receipt_no=f"R{i:09d}",
                              completed_at=datetime(2026, 8, 1, 9) + timedelta(days=i % days),
                              details=details, txn_status="Completed", paid_in=D(pin), withdrawn=D(out),
                              balance=D(balance), info=parse_details(details)))
    return ParsedStatement(
        customer_name="JANE WANJIKU DOE", msisdn="254712345678", statement_email=None,
        period_start=start, period_end=start + timedelta(days=days), request_date=None,
        verification_code=None, page_count=1, summary={},
        summary_paid_in=sum((t.paid_in for t in txns), D(0)),
        summary_paid_out=sum((t.withdrawn for t in txns), D(0)), txns=txns)


SALARY = ("Business Payment from 4009051 - ACME LIMITED", "30000", "0")
CASH_IN = ("Deposit of Funds at Agent Till 123456 - SHOP", "1000", "0")
SHOP = ("Merchant Payment to 4748597 - FAIMO MINI MART", "0", "300")
GLOVO = ("Pay Bill Online to 4005495 - TINGG Acc. Glovo", "0", "1000")
RENT = ("Pay Bill Online to 555555 - SUNRISE APARTMENTS Acc. A4", "0", "9000")


def score(rows, days=30, **cfg):
    st = make_statement(rows, days)
    return assess(st, classify_statement(st), AssessmentConfig(**cfg) if cfg else AssessmentConfig())


def test_basic_maths():
    a = score([SALARY, CASH_IN, CASH_IN, RENT] + [SHOP] * 10 + [GLOVO] * 3, days=30)
    # funding 32000, must pay 9000 + 3000 = 12000 -> left 20000/30 = 666.67
    assert a.left_daily == D("666.67")
    assert a.affordable_daily == D("600.00")       # * 0.9
    assert a.wants_daily == D("100.00")            # reported, never subtracted
    assert a.confidence == "HIGH" and a.flags == []


def test_one_week_statement_still_scores_but_is_low_confidence():
    a = score([SALARY, CASH_IN, CASH_IN, SHOP], days=7)
    assert a.affordable_daily > 0
    assert a.confidence == "LOW" and "SHORT_WINDOW_7D" in a.flags


def test_three_week_statement_is_medium():
    a = score([SALARY, CASH_IN, CASH_IN, SHOP], days=21)
    assert a.confidence == "MEDIUM" and "SHORT_WINDOW_21D" in a.flags


def test_borrowed_money_is_not_income():
    base = score([SALARY, CASH_IN, CASH_IN, RENT])
    with_loan = score([SALARY, CASH_IN, CASH_IN, RENT,
                       ("Business Payment from 222222 - TALA KENYA via API.", "20000", "0")])
    assert with_loan.left_daily == base.left_daily
    assert "LOAN_DEPENDENT" in with_loan.flags and with_loan.confidence == "LOW"


def test_betting_winnings_are_not_income():
    base = score([SALARY, CASH_IN, CASH_IN])
    lucky = score([SALARY, CASH_IN, CASH_IN, ("Business Payment from 100100 - BETIKA LIMITED", "50000", "0")])
    assert lucky.left_daily == base.left_daily and lucky.has_betting


def test_heavy_betting_is_low():
    a = score([SALARY, CASH_IN, CASH_IN] + [("Pay Bill Online to 290290 - SPORTPESA Acc. 1", "0", "500")] * 10)
    assert "HEAVY_BETTING" in a.flags and a.confidence == "LOW"


def test_unknown_outflows_count_as_needs():
    known = score([SALARY, CASH_IN, CASH_IN, SHOP])
    unknown = score([SALARY, CASH_IN, CASH_IN, ("Pay Bill Online to 999999 - MYSTERY LTD Acc. 1", "0", "300")])
    assert known.left_daily == unknown.left_daily
    assert "HIGH_UNKNOWN_SHARE" in unknown.flags


def test_spending_more_than_earning_gives_zero_not_negative():
    a = score([CASH_IN, CASH_IN, CASH_IN, RENT])
    assert a.left_daily < 0 and a.affordable_daily == 0 and "NO_HEADROOM" in a.flags


def test_unreconciled_statement_is_refused():
    st = make_statement([SALARY])
    st.summary_paid_in += 1
    with pytest.raises(NotScorable):
        assess(st, classify_statement(st))


def test_lumpy_income():
    a = score([SALARY, SHOP])
    assert "LUMPY_INCOME" in a.flags and a.confidence == "MEDIUM"


def _dev(rrp, mult, id_="d"):
    return dict(id=id_, rrp=rrp, margin=100, stockOwnership="SHOP", loanTermDays=365,
                depositPercentage=30, depositPromoDiscount=0, creditMultiplier=mult)


def test_multiplier_shrinks_pool():
    # rrp 15000 -> daily 70
    assert [r.qualified for r in evaluate_pool(D("100"), [_dev(15000, 1)])] == [True]
    assert [r.qualified for r in evaluate_pool(D("100"), [_dev(15000, 2)])] == [False]   # needs 140
    assert [r.qualified for r in evaluate_pool(D("100"), [_dev(15000, "0.5")])] == [True]  # needs 35


def test_zero_multiplier_is_treated_as_one():
    r = evaluate_pool(D("50"), [_dev(15000, 0)])[0]
    assert r.credit_multiplier == 1 and not r.qualified


def test_pool_order_best_phone_first_then_nearest_miss():
    res = evaluate_pool(D("200"), [_dev(15000, 1, "cheap"), _dev(40000, 1, "mid"),
                                   _dev(90000, 1, "dear"), _dev(50000, 1, "near")])
    assert [r.device_id for r in res] == ["mid", "cheap", "near", "dear"]


FIXTURE = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="private statement fixture not present")
def test_real_statement():
    from app.parsing.pdf_open import try_open
    from app.parsing.statement_parser import parse_statement
    st = parse_statement(try_open(FIXTURE.read_bytes(), "123456"))
    a = assess(st, classify_statement(st))
    assert a.left_daily == D("1110.48")
    assert a.affordable_daily == D("999.44")
    assert a.confidence == "MEDIUM" and a.flags == ["SELF_TRANSFER_HEAVY"]