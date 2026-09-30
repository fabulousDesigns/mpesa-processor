import pytest
from app.parsing.details_parser import clean_details, parse_details

CASES = [
    # (details, txn_type, name, number, account)
    ("Customer Transfer to - 254794***965 STEPHANIA MWAKOI", "P2P_SEND", "STEPHANIA MWAKOI", "254794***965", None),
    ("Customer Transfer to - 0110***585 JOHN MBURU", "P2P_SEND", "JOHN MBURU", "0110***585", None),
    ("Customer Transfer of Funds Charge", "CHARGE", None, None, None),
    ("Pay Bill Charge", "CHARGE", None, None, None),
    ("Pay Bill Online to 4005495 - TINGG Acc. Glovo", "PAYBILL", "TINGG", "4005495", "Glovo"),
    ("Pay Bill Online to 400200 - Co-operative Bank Money Transfer Acc. 60944", "PAYBILL",
     "Co-operative Bank Money Transfer", "400200", "60944"),
    ("Merchant Payment Online to 4342487 - PUREVISTA WATERS LIMITED KINOO", "TILL",
     "PUREVISTA WATERS LIMITED KINOO", "4342487", None),
    ("Merchant Payment to 4748597 - FAIMO MINI MART", "TILL", "FAIMO MINI MART", "4748597", None),
    ("Customer Payment to Small Business to - 254726***245 JOSEPH GACHARA", "POCHI", "JOSEPH GACHARA", "254726***245", None),
    ("Business Payment from 300600 - Equity Bulk Account via API. Original conversation ID is EQX76CBE6195F74.",
     "B2C", "Equity Bulk Account", "300600", None),
    ("Business Payment from 4009051 - CELLIPAY LIMITED", "B2C", "CELLIPAY LIMITED", "4009051", None),
    ("Funds received from - 254713***163 Samuel Macharia", "P2P_RECEIVE", "Samuel Macharia", "254713***163", None),
    ("Card Pay Bill Online to 903470 - M-PESA GlobalPay Acc. NETFLIX INTERNATIONAL BV Amsterdam NL", "CARD",
     "NETFLIX INTERNATIONAL BV Amsterdam NL", "903470", None),
    ("Pay Utility Reversal by TINGG\\AKINUTHIA", "REVERSAL", "TINGG\\AKINUTHIA", None, None),
    ("Airtime Purchase", "AIRTIME", None, None, None),
    ("Customer Bundle Purchase to 244441SAFARICOM POSTPAID BUNDLES by - 0110***199 BERNARD MAINA", "BUNDLE",
     "SAFARICOM POSTPAID BUNDLES", "244441", None),
    # Not in the sample statement, but common on customers' statements:
    ("Customer Withdrawal At Agent Till 123456 - JANE SHOP KINOO", "AGENT_WITHDRAWAL", "JANE SHOP KINOO", "123456", None),
    ("Deposit of Funds at Agent Till 123456 - JANE SHOP KINOO", "AGENT_DEPOSIT", "JANE SHOP KINOO", "123456", None),
    ("OverDraft of Credit Party", "FULIZA_DRAW", None, None, None),
    ("OD Loan Repayment to 232323 - M-PESA Overdraw", "FULIZA_REPAY", None, None, None),
    ("M-Shwari Loan Repayment", "MSHWARI", "Loan Repayment", None, None),
]


@pytest.mark.parametrize("details,txn_type,name,number,acc", CASES)
def test_parse_details(details, txn_type, name, number, acc):
    info = parse_details(details)
    assert (info.txn_type, info.counterparty_name, info.counterparty_number, info.account_ref) == \
           (txn_type, name, number, acc)


def test_unknown_is_other_not_crash():
    assert parse_details("Something Safaricom invents next year").txn_type == "OTHER"


def test_clean_details_hyphen_wrap():
    assert clean_details("Pay Bill Online to 400200 - Co-\noperative Bank") == \
        "Pay Bill Online to 400200 - Co-operative Bank"
    assert clean_details("Customer Transfer to -\n254794***965 STEPHANIA\nMWAKOI") == \
        "Customer Transfer to - 254794***965 STEPHANIA MWAKOI"
