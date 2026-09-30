"""Turn the free-text Details column into structure: what kind of transaction, and who
was on the other side. This is MECHANICS only (paybill vs till vs send money). What it
MEANS (groceries, salary, betting) is the classifier's job in the next step.

Every rule has an id so a stored row tells you which rule matched it.
Add new patterns at the TOP of RULES only if they are more specific than existing ones.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DetailsInfo:
    txn_type: str
    counterparty_name: str | None = None
    counterparty_number: str | None = None
    account_ref: str | None = None
    rule_id: str = "none"


_NUM = r"(?P<num>\d{3,10})"
_PHONE = r"(?P<num>(?:254|0)\d{2,3}\*{3}\d{3})"
_NAME = r"(?P<name>.+?)"

# (rule_id, txn_type, regex). Order matters: first match wins.
RULES: list[tuple[str, str, re.Pattern]] = [
    ("charge", "CHARGE", re.compile(r"^(Customer Transfer of Funds Charge|Pay Bill Charge|Pay Merchant Charge|"
                                    r"Withdrawal Charge|Customer Send Money to Unregistered User Charge|"
                                    r".*\bCharge)$", re.I)),
    ("reversal_by", "REVERSAL", re.compile(r"^.*\bReversal by (?P<name>.+)$", re.I)),
    ("reversal", "REVERSAL", re.compile(r"^.*\bReversal\b.*$", re.I)),
    ("card_paybill", "CARD", re.compile(rf"^Card Pay Bill Online to {_NUM} - M-PESA GlobalPay Acc\.\s*(?P<acc>.+)$", re.I)),
    ("paybill", "PAYBILL", re.compile(rf"^Pay Bill(?: Online| Fuliza M-Pesa)? to {_NUM} - {_NAME}(?: Acc\.\s*(?P<acc>.*))?$", re.I)),
    ("till", "TILL", re.compile(rf"^Merchant Payment(?: Online| Fuliza M-Pesa)? to {_NUM} - {_NAME}$", re.I)),
    ("pochi", "POCHI", re.compile(rf"^Customer Payment to Small Business to - {_PHONE}\s+{_NAME}$", re.I)),
    ("p2p_send", "P2P_SEND", re.compile(rf"^Customer (?:Transfer|Send Money)(?: Fuliza M-Pesa)? to - {_PHONE}\s+{_NAME}$", re.I)),
    ("p2p_unreg", "P2P_SEND", re.compile(rf"^Customer Send Money to Unregistered User to - {_PHONE}\s*{_NAME}?$", re.I)),
    ("p2p_receive", "P2P_RECEIVE", re.compile(rf"^Funds received from - {_PHONE}\s+{_NAME}$", re.I)),
    ("b2c", "B2C", re.compile(rf"^Business Payment from {_NUM} - {_NAME}(?: via API\..*)?$", re.I)),
    ("salary", "B2C", re.compile(rf"^Salary Payment from {_NUM} - {_NAME}$", re.I)),
    ("promo", "B2C", re.compile(rf"^Promotion Payment from {_NUM} - {_NAME}$", re.I)),
    ("agent_withdraw", "AGENT_WITHDRAWAL", re.compile(rf"^Customer Withdrawal At Agent Till {_NUM} - {_NAME}$", re.I)),
    ("agent_deposit", "AGENT_DEPOSIT", re.compile(rf"^Deposit of Funds at Agent Till {_NUM} - {_NAME}$", re.I)),
    ("fuliza_draw", "FULIZA_DRAW", re.compile(r"^OverDraft of Credit Party.*$", re.I)),
    ("fuliza_repay", "FULIZA_REPAY", re.compile(r"^OD Loan Repayment to .*$", re.I)),
    ("mshwari", "MSHWARI", re.compile(r"^M-Shwari (?P<name>Deposit|Withdraw|Loan Repayment|Loan Disburse).*$", re.I)),
    ("kcb_mpesa", "KCB_MPESA", re.compile(r"^KCB M-PESA (?P<name>.+)$", re.I)),
    ("bundle", "BUNDLE", re.compile(r"^Customer Bundle Purchase(?: with Fuliza)? to (?P<num>\d+)\s*(?P<name>.+?)(?: by - .*)?$", re.I)),
    ("airtime", "AIRTIME", re.compile(r"^(?:Airtime Purchase|Recharge for Customer|Buy Bundles Online).*$", re.I)),
]


def clean_details(raw: str) -> str:
    """Join the PDF's wrapped lines. 'Co-\\noperative' must become 'Co-operative', not 'Co- operative'."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    out = ""
    for ln in lines:
        if not out:
            out = ln
        elif out.endswith("-") and not out.endswith(" -") and ln[:1].islower():
            out += ln
        else:
            out += " " + ln
    return re.sub(r"\s+", " ", out).strip()


def parse_details(details: str) -> DetailsInfo:
    for rule_id, txn_type, rx in RULES:
        m = rx.match(details)
        if not m:
            continue
        g = m.groupdict()
        name = (g.get("name") or "").strip() or None
        acc = (g.get("acc") or "").strip() or None
        if txn_type == "CARD":
            name, acc = acc, None  # for card payments the "account" is the real merchant
        return DetailsInfo(txn_type=txn_type, counterparty_name=name,
                           counterparty_number=g.get("num"), account_ref=acc, rule_id=rule_id)
    return DetailsInfo(txn_type="OTHER", rule_id="none")
