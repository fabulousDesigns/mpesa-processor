"""ParsedTxn -> ClassifiedTxn: bucket (how it counts), category (what it is), merchant.

Pure function of the statement plus who the customer is. No DB, no network, so it's
fast and every decision is reproducible from the stored rows.

People are never merchants: P2P counterparties are NOT put in the merchant registry.
The registry is for businesses (tills, paybills, pochi la biashara, card merchants).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.classify import rules
from app.classify.locations import location_from_name
from app.core.msisdn import masked_matches
from app.parsing.statement_parser import ParsedStatement, ParsedTxn

CLASSIFIER_VERSION = "1.0.0"


@dataclass(frozen=True)
class MerchantRef:
    merchant_key: str          # stable identity across ALL customers' statements
    kind: str                  # TILL | PAYBILL | POCHI | CARD_MERCHANT
    number: str | None
    name: str
    category: str
    location_text: str | None
    county: str | None


@dataclass(frozen=True)
class ClassifiedTxn:
    txn: ParsedTxn
    bucket: str
    category: str
    rule_id: str               # "<details rule>/<meaning rule>", e.g. "paybill/kw:FOOD_DELIVERY"
    merchant: MerchantRef | None


@lru_cache(maxsize=512)
def _word_rx(words: tuple[str, ...]) -> re.Pattern:
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Z0-9])(?:{alt})(?![A-Z0-9])")


def _has_any(text: str | None, words: list[str]) -> bool:
    return bool(text) and bool(_word_rx(tuple(words)).search(text.upper()))


def _keyword_category(*texts: str | None) -> tuple[str, str] | None:
    blob = " | ".join(t for t in texts if t)
    for category, bucket, words in rules.MERCHANT_RULES:
        if _has_any(blob, words):
            return category, bucket
    return None


def _norm_name(name: str) -> str:
    n = name.upper().split("*")[0]                     # "ANTHROPIC* CLAUDE SUB" -> "ANTHROPIC"
    n = re.sub(r"\+\d{6,}", " ", n)                    # strip phone numbers on card merchants
    n = re.sub(r"\b(US|NL|GB|IE|KE)\s*$", " ", n)       # trailing country codes
    return re.sub(r"\s+", " ", n).strip(" *")


def _merchant(kind: str, number: str | None, name: str | None, category: str,
              account: str | None = None) -> MerchantRef | None:
    if not name and not number:
        return None
    # Aggregator paybills (TINGG, Pesapal...) front many merchants. The real business is in the
    # account field ("Acc. Glovo"), so it becomes part of the identity. For every other paybill the
    # account is the customer's own account number: never part of the key.
    if kind == "PAYBILL" and account and _has_any(name, rules.PAYMENT_GATEWAY_WORDS):
        clean = _norm_name(account)
        loc, county = location_from_name(account)
        return MerchantRef(merchant_key=f"PAYBILL:{number}:{clean}"[:191], kind=kind, number=number,
                           name=clean, category=category, location_text=loc, county=county)
    clean = _norm_name(name or number or "")
    if kind == "POCHI":
        key = f"POCHI:{number}:{clean}"         # masked number + name, the best identity we get
    elif kind == "CARD_MERCHANT":
        key = f"CARD:{clean}"
    else:
        key = f"{kind}:{number}"                # till / paybill numbers are unique on their own
    loc, county = location_from_name(name)
    return MerchantRef(merchant_key=key[:191], kind=kind, number=number, name=clean,
                       category=category, location_text=loc, county=county)


class Classifier:
    def __init__(self, customer_msisdn: str, customer_name: str):
        self.msisdn = customer_msisdn
        # Surname + one other name is enough to spot the customer sending to themselves.
        self.name_tokens = {t for t in customer_name.upper().split() if len(t) > 2}

    def _is_self(self, number: str | None, name: str | None) -> bool:
        if not number:
            return False
        full = number if number.startswith("254") else "254" + number[1:]
        name_hit = len(self.name_tokens & set((name or "").upper().split())) >= 2
        return masked_matches(self.msisdn, full) and name_hit

    def classify(self, t: ParsedTxn) -> ClassifiedTxn:
        i = t.info
        d_rule = i.rule_id

        def out(bucket, category, rule, merchant=None):
            return ClassifiedTxn(txn=t, bucket=bucket, category=category,
                                 rule_id=f"{d_rule}/{rule}", merchant=merchant)

        tt = i.txn_type
        if tt == "CHARGE":
            return out("FEE", "MPESA_FEES", "charge")
        if tt == "REVERSAL":
            return out("REFUND", "REVERSAL", "reversal")
        if tt in ("AIRTIME", "BUNDLE"):
            return out("NEED", "AIRTIME_DATA", "airtime")
        if tt == "FULIZA_DRAW":
            return out("BORROWED", "FULIZA", "fuliza")
        if tt == "FULIZA_REPAY":
            return out("COMMITTED", "LOAN_REPAYMENT", "fuliza_repay")
        if tt in ("MSHWARI", "KCB_MPESA"):
            what = (i.counterparty_name or "").upper()
            if "DISBURSE" in what or ("LOAN" in what and t.direction == "IN"):
                return out("BORROWED", "MOBILE_LOAN", "mobile_loan_in")
            if "REPAY" in what:
                return out("COMMITTED", "LOAN_REPAYMENT", "mobile_loan_repay")
            return out("SELF_TRANSFER", "MOBILE_SAVINGS", "mobile_savings")
        if tt == "AGENT_DEPOSIT":
            # For small traders, cash deposited at an agent IS their takings.
            return out("INCOME", "CASH_DEPOSIT", "agent_deposit")
        if tt == "AGENT_WITHDRAWAL":
            return out("NEED", "CASH_WITHDRAWAL", "agent_withdrawal")
        if tt == "P2P_RECEIVE":
            if self._is_self(i.counterparty_number, i.counterparty_name):
                return out("SELF_TRANSFER", "OWN_LINE", "self_in")
            return out("INCOME", "RECEIVED_FROM_PEOPLE", "p2p_in")
        if tt == "P2P_SEND":
            if self._is_self(i.counterparty_number, i.counterparty_name):
                return out("SELF_TRANSFER", "OWN_LINE", "self_out")
            return out("COMMITTED", "SENT_TO_PEOPLE", "p2p_out")
        if tt == "B2C":
            return self._b2c(t, out)
        if tt in ("PAYBILL", "TILL", "POCHI", "CARD"):
            return self._merchant_payment(t, out)
        return out("UNKNOWN", "UNCLASSIFIED", "fallback")

    def _b2c(self, t, out):
        name = t.info.counterparty_name
        if _has_any(name, rules.B2C_LOAN_DISBURSERS):
            return out("BORROWED", "LOAN_APP_DISBURSEMENT", "b2c_loan")
        if _has_any(name, rules.B2C_BETTING):
            return out("INCOME", "BETTING_WINNINGS", "b2c_betting")  # assessment excludes as unstable
        if _has_any(name, rules.PAYMENT_GATEWAY_WORDS):
            return out("REFUND", "MERCHANT_REFUND", "b2c_gateway")
        if _has_any(name, rules.BANK_WORDS):
            return out("SELF_TRANSFER", "BANK_TO_MPESA", "b2c_bank")
        return out("INCOME", "BUSINESS_PAYMENT_IN", "b2c_business")  # employers, clients, etc.

    def _merchant_payment(self, t, out):
        i = t.info
        kind = {"PAYBILL": "PAYBILL", "TILL": "TILL", "POCHI": "POCHI", "CARD": "CARD_MERCHANT"}[i.txn_type]
        hit = _keyword_category(i.counterparty_name, i.account_ref)
        if hit:
            category, bucket = hit
            rule = f"kw:{category}"
        elif kind == "CARD_MERCHANT":
            category, bucket, rule = "ONLINE_SHOPPING", "WANT", "card_default"
        elif kind == "PAYBILL":
            category, bucket, rule = "OTHER_BILLS", "UNKNOWN", "paybill_default"
        else:
            # Tills and pochi with no telling name: overwhelmingly corner shops, boda, mama mboga.
            category, bucket, rule = "SMALL_TRADERS", "NEED", f"{kind.lower()}_default"
        if t.direction == "IN":
            return out("REFUND", "MERCHANT_REFUND", "merchant_in")
        return out(bucket, category, rule,
                   _merchant(kind, i.counterparty_number, i.counterparty_name, category, i.account_ref))


def classify_statement(st: ParsedStatement) -> list[ClassifiedTxn]:
    c = Classifier(st.msisdn, st.customer_name)
    return [c.classify(t) for t in st.txns]