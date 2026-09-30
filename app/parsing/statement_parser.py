"""Decrypted M-PESA statement PDF -> ParsedStatement (header, summary, every row).

Safety net: the parsed rows must add up EXACTLY to Safaricom's own page-1 summary.
If they don't, `reconciled` is False and the scoring step must refuse to score.
Better to tell the agent "we couldn't read this one" than to score garbage.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pdfplumber

from app.core.msisdn import normalize_msisdn
from app.parsing.details_parser import DetailsInfo, clean_details, parse_details
from app.parsing.errors import NotAStatement, UnknownLayout

PARSER_VERSION = "1.0.0"

_RECEIPT = re.compile(r"^[A-Z0-9]{10}$")
_TS_FMT = "%Y-%m-%d %H:%M:%S"
# Column names as Safaricom prints them. We find columns by NAME, not position,
# so a reordered layout still parses and a renamed one fails loudly.
_COLS = {
    "receipt": ("receipt no.", "receipt no", "receipt"),
    "time": ("completion time",),
    "details": ("details",),
    "status": ("transaction status",),
    "paid_in": ("paid in",),
    "withdrawn": ("withdrawn", "withdrawal", "paid out"),
    "balance": ("balance",),
}


@dataclass(frozen=True)
class ParsedTxn:
    line_no: int
    receipt_no: str
    completed_at: datetime
    details: str
    txn_status: str
    paid_in: Decimal
    withdrawn: Decimal          # always positive
    balance: Decimal | None
    info: DetailsInfo

    @property
    def direction(self) -> str:
        return "IN" if self.paid_in > 0 else "OUT"


@dataclass
class ParsedStatement:
    customer_name: str
    msisdn: str
    statement_email: str | None
    period_start: date
    period_end: date
    request_date: date | None
    verification_code: str | None
    page_count: int
    summary: dict[str, tuple[Decimal, Decimal]]   # 'SEND MONEY' -> (paid_in, paid_out)
    summary_paid_in: Decimal
    summary_paid_out: Decimal
    txns: list[ParsedTxn] = field(default_factory=list)
    skipped_rows: list[list] = field(default_factory=list)
    zero_amount_rows: list[list] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    @property
    def parsed_paid_in(self) -> Decimal:
        return sum((t.paid_in for t in self.txns), Decimal("0"))

    @property
    def parsed_paid_out(self) -> Decimal:
        return sum((t.withdrawn for t in self.txns), Decimal("0"))

    @property
    def reconciled(self) -> bool:
        return (self.parsed_paid_in == self.summary_paid_in
                and self.parsed_paid_out == self.summary_paid_out
                and not self.skipped_rows)

    @property
    def window_days(self) -> Decimal:
        """Days the statement covers. 21 Aug -> 21 Sep = 31. Never below 1."""
        return Decimal(max(1, (self.period_end - self.period_start).days))

    @property
    def first_txn_at(self) -> datetime | None:
        return min((t.completed_at for t in self.txns), default=None)

    @property
    def last_txn_at(self) -> datetime | None:
        return max((t.completed_at for t in self.txns), default=None)

    @property
    def unclassified_count(self) -> int:
        return sum(1 for t in self.txns if t.info.txn_type == "OTHER")


def _money(s: str | None) -> Decimal:
    s = (s or "").replace(",", "").strip()
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise UnknownLayout(f"bad amount {s!r}") from e


def _date(s: str) -> date:
    return datetime.strptime(s.strip(), "%d %b %Y").date()


def _header(text: str) -> dict:
    def grab(label: str) -> str | None:
        m = re.search(rf"{label}:\s*(.+)", text)
        return m.group(1).strip() if m else None

    name, mobile = grab("Customer Name"), grab("Mobile Number")
    period = grab("Statement Period")
    if not (name and mobile and period) or "M-PESA STATEMENT" not in text.upper():
        raise NotAStatement("page 1 is missing the M-PESA statement header")
    m = re.match(r"(\d{1,2} \w{3} \d{4})\s*-\s*(\d{1,2} \w{3} \d{4})", period)
    if not m:
        raise UnknownLayout(f"unreadable statement period {period!r}")
    msisdn = normalize_msisdn(mobile)
    if not msisdn:
        raise UnknownLayout(f"unreadable mobile number {mobile!r}")
    req = grab("Request Date")
    return {
        "customer_name": re.sub(r"\s+", " ", name).upper(),
        "msisdn": msisdn,
        "statement_email": grab("Email Address"),
        "period_start": _date(m.group(1)),
        "period_end": _date(m.group(2)),
        "request_date": _date(req) if req else None,
    }


def _summary(tables: list[list[list]]) -> tuple[dict, Decimal, Decimal]:
    for t in tables:
        if t and t[0] and (t[0][0] or "").strip().upper() == "TRANSACTION TYPE":
            rows, total = {}, None
            for r in t[1:]:
                label = (r[0] or "").strip().rstrip(":").upper()
                pin, pout = _money(r[1]), _money(r[2])
                if label == "TOTAL":
                    total = (pin, pout)
                elif label:
                    rows[label] = (pin, pout)
            if total is None:
                raise UnknownLayout("summary table has no TOTAL row")
            return rows, total[0], total[1]
    raise UnknownLayout("no summary table on page 1")


def _column_map(header_row: list) -> dict[str, int] | None:
    names = [(c or "").strip().lower() for c in header_row]
    found = {}
    for key, options in _COLS.items():
        for i, n in enumerate(names):
            if n in options:
                found[key] = i
                break
    return found if len(found) == len(_COLS) else None


def _verification_code(tables: list[list[list]]) -> str | None:
    for t in tables:
        if t and t[0] and (t[0][0] or "").strip().lower() == "statement verification code":
            for r in t[1:]:
                v = (r[0] or "").strip()
                if re.fullmatch(r"[A-Z0-9]{6,12}", v):
                    return v
    return None


def parse_statement(pdf_bytes: bytes) -> ParsedStatement:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            raise NotAStatement("empty PDF")
        page1 = pdf.pages[0]
        head = _header(page1.extract_text() or "")
        p1_tables = page1.extract_tables()
        summary, s_in, s_out = _summary(p1_tables)

        st = ParsedStatement(**head, verification_code=_verification_code(p1_tables),
                             page_count=len(pdf.pages), summary=summary,
                             summary_paid_in=s_in, summary_paid_out=s_out)
        line_no = 0
        saw_detail_table = False
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                cols = _column_map(table[0])
                if cols is None:
                    continue  # summary / verification boxes, not transactions
                saw_detail_table = True
                for row in table[1:]:
                    receipt = (row[cols["receipt"]] or "").strip()
                    ts = (row[cols["time"]] or "").strip()
                    if not receipt and not ts:
                        extra = clean_details(row[cols["details"]])
                        if extra and st.txns:
                            # Details wrapped onto the next page: glue it to the row it belongs to.
                            last = st.txns[-1]
                            merged = clean_details(last.details + "\n" + extra)
                            st.txns[-1] = replace(last, details=merged, info=parse_details(merged))
                        continue
                    if not _RECEIPT.match(receipt):
                        st.skipped_rows.append(row)  # a real row we couldn't read -> breaks reconciliation
                        continue
                    try:
                        completed_at = datetime.strptime(ts, _TS_FMT)
                    except ValueError:
                        st.skipped_rows.append(row)
                        continue
                    details = clean_details(row[cols["details"]])
                    paid_in = abs(_money(row[cols["paid_in"]]))
                    withdrawn = abs(_money(row[cols["withdrawn"]]))
                    bal_raw = (row[cols["balance"]] or "").strip()
                    if paid_in == 0 and withdrawn == 0:
                        st.zero_amount_rows.append(row)  # e.g. failed attempts; no effect on totals
                        continue
                    line_no += 1
                    st.txns.append(ParsedTxn(
                        line_no=line_no, receipt_no=receipt, completed_at=completed_at,
                        details=details, txn_status=(row[cols["status"]] or "").strip(),
                        paid_in=paid_in, withdrawn=withdrawn,
                        balance=_money(bal_raw) if bal_raw else None,
                        info=parse_details(details),
                    ))
        if not saw_detail_table:
            raise UnknownLayout("no DETAILED STATEMENT table found")
        return st
