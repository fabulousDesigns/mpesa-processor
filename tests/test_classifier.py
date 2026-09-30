from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.classify.classifier import Classifier, classify_statement
from app.classify.locations import location_from_name
from app.parsing.details_parser import parse_details
from app.parsing.statement_parser import ParsedTxn

C = Classifier("254110026199", "BERNARD MBURU MAINA")


def txn(details: str, paid_in="0", out="0") -> ParsedTxn:
    return ParsedTxn(line_no=1, receipt_no="ABCDEFGHIJ", completed_at=datetime(2026, 9, 1, 12),
                     details=details, txn_status="Completed", paid_in=Decimal(paid_in),
                     withdrawn=Decimal(out), balance=None, info=parse_details(details))


@pytest.mark.parametrize("details,paid_in,out,bucket,category", [
    ("Pay Bill Online to 4005495 - TINGG Acc. Glovo", "0", "900", "WANT", "FOOD_DELIVERY"),
    ("Merchant Payment to 7813023 - HIRAM KABATA BETH", "0", "50", "NEED", "SMALL_TRADERS"),  # BETH is not BET
    ("Pay Bill Online to 290290 - SPORTPESA Acc. 0712", "0", "100", "WANT", "BETTING"),
    ("Business Payment from 123456 - BETIKA LIMITED", "5000", "0", "INCOME", "BETTING_WINNINGS"),
    ("Business Payment from 4009051 - CELLIPAY LIMITED", "60000", "0", "INCOME", "BUSINESS_PAYMENT_IN"),
    ("Business Payment from 300600 - Equity Bulk Account via API. Original conversation ID is X.",
     "5000", "0", "SELF_TRANSFER", "BANK_TO_MPESA"),
    ("Business Payment from 222222 - TALA KENYA via API.", "3000", "0", "BORROWED", "LOAN_APP_DISBURSEMENT"),
    ("Pay Bill Online to 222222 - TALA Acc. 0712", "0", "3300", "COMMITTED", "LOAN_REPAYMENT"),
    ("Pay Bill Online to 111111 - M-KOPA KENYA Acc. 123", "0", "50", "COMMITTED", "ASSET_FINANCE_REPAYMENT"),
    ("OverDraft of Credit Party", "200", "0", "BORROWED", "FULIZA"),
    ("OD Loan Repayment to 232323 - M-PESA Overdraw", "0", "210", "COMMITTED", "LOAN_REPAYMENT"),
    ("Customer Transfer to - 254110***199 BERNARD MAINA", "0", "500", "SELF_TRANSFER", "OWN_LINE"),
    ("Customer Transfer to - 254725***348 JANE MBURU", "0", "500", "COMMITTED", "SENT_TO_PEOPLE"),
    ("Customer Withdrawal At Agent Till 123456 - JANE SHOP KINOO", "0", "1000", "NEED", "CASH_WITHDRAWAL"),
    ("Deposit of Funds at Agent Till 123456 - JANE SHOP KINOO", "2000", "0", "INCOME", "CASH_DEPOSIT"),
    ("Pay Bill Online to 888880 - KPLC PREPAID Acc. 1234", "0", "500", "NEED", "ELECTRICITY"),
    ("Pay Bill Online to 999999 - SOME RANDOM BILLER Acc. 1", "0", "500", "UNKNOWN", "OTHER_BILLS"),
    ("Something Safaricom invents next year", "0", "10", "UNKNOWN", "UNCLASSIFIED"),
])
def test_rules(details, paid_in, out, bucket, category):
    c = C.classify(txn(details, paid_in, out))
    assert (c.bucket, c.category) == (bucket, category), c.rule_id


def test_people_are_never_merchants():
    assert C.classify(txn("Customer Transfer to - 254725***348 JANE MBURU", out="500")).merchant is None


def test_aggregator_paybill_keys_on_account():
    m = C.classify(txn("Pay Bill Online to 4005495 - TINGG Acc. Glovo", out="900")).merchant
    assert m.merchant_key == "PAYBILL:4005495:GLOVO"


def test_bank_paybill_does_not_leak_account_number():
    m = C.classify(txn("Pay Bill Online to 542542 - IM BANK C2B Acc. 458192", out="100")).merchant
    assert m.merchant_key == "PAYBILL:542542" and "458192" not in m.name


def test_card_descriptor_collapses():
    a = C.classify(txn("Card Pay Bill Online to 903470 - M-PESA GlobalPay Acc. ANTHROPIC +14152360599 US", out="1")).merchant
    b = C.classify(txn("Card Pay Bill Online to 903470 - M-PESA GlobalPay Acc. ANTHROPIC* CLAUDE SUB +14152360599 US", out="1")).merchant
    assert a.merchant_key == b.merchant_key == "CARD:ANTHROPIC"


@pytest.mark.parametrize("name,text,county", [
    ("PUREVISTA WATERS LIMITED KINOO", "KINOO", "Kiambu"),
    ("NAIVAS SPUR MALL", "SPUR MALL", "Kiambu"),
    ("TOTALENERGIES UTHIRU 87 SHOP", "UTHIRU", None),
    ("FAIMO MINI MART", None, None),
])
def test_locations(name, text, county):
    assert location_from_name(name) == (text, county)


FIXTURE = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="private statement fixture not present")
def test_real_statement_totals():
    from app.parsing.pdf_open import try_open
    from app.parsing.statement_parser import parse_statement
    cl = classify_statement(parse_statement(try_open(FIXTURE.read_bytes(), "123456")))
    by = {}
    for c in cl:
        by[c.bucket] = by.get(c.bucket, 0) + (c.txn.paid_in or c.txn.withdrawn)
    assert by["NEED"] == Decimal("19820.00")
    assert by["WANT"] == Decimal("37533.68")
    assert by["COMMITTED"] == Decimal("102290.00")
    assert by["FEE"] == Decimal("1465.00")
    assert by["INCOME"] == Decimal("61000.00")
    assert by["SELF_TRANSFER"] == Decimal("97000.00")
    assert by["REFUND"] == Decimal("1995.50")
    assert "UNKNOWN" not in by