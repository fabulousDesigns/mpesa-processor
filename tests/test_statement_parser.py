"""Runs against a real statement kept in tests/private/ (gitignored, never committed:
it's a real person's financial data). Skipped when the file isn't there."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsing.errors import WrongCode
from app.parsing.pdf_open import try_open
from app.parsing.statement_parser import parse_statement

FIXTURE = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="private statement fixture not present")


@pytest.fixture(scope="module")
def statement():
    return parse_statement(try_open(FIXTURE.read_bytes(), "123456"))


def test_wrong_code_raises():
    with pytest.raises(WrongCode):
        try_open(FIXTURE.read_bytes(), "000000")


def test_header(statement):
    assert statement.customer_name == "BERNARD MBURU MAINA"
    assert statement.msisdn == "254110026199"
    assert str(statement.period_start) == "2026-08-21" and str(statement.period_end) == "2026-09-21"
    assert statement.window_days == 31
    assert statement.verification_code == "FHDTZ6ZZ"


def test_reconciles_to_the_cent(statement):
    assert len(statement.txns) == 278
    assert statement.parsed_paid_in == statement.summary_paid_in == Decimal("159995.50")
    assert statement.parsed_paid_out == statement.summary_paid_out == Decimal("161108.68")
    assert statement.reconciled
    assert statement.unclassified_count == 0


def test_one_missing_row_breaks_reconciliation(statement):
    tampered = replace(statement, txns=statement.txns[1:])
    assert not tampered.reconciled


def test_line_numbers_are_unique_and_ordered(statement):
    assert [t.line_no for t in statement.txns] == list(range(1, 279))
