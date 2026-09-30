import pytest
from app.core.msisdn import (masked_matches, msisdn_from_statement_filename,
                             normalize_msisdn, to_users_format)


@pytest.mark.parametrize("raw", ["+254713138481", "254713138481", "0713138481",
                                 "0713 138 481", "713138481", "+254 713-138-481"])
def test_all_formats_normalise(raw):
    assert normalize_msisdn(raw) == "254713138481"


def test_new_01_prefix():
    assert normalize_msisdn("0110026199") == "254110026199"


@pytest.mark.parametrize("raw", ["", None, "12345", "0213138481", "+1 415 236 0599"])
def test_rejects_junk(raw):
    assert normalize_msisdn(raw) is None


def test_users_format():
    assert to_users_format("254713138481") == "+254713138481"


def test_mask():
    assert masked_matches("254110026199", "254110***199")
    assert not masked_matches("254110026198", "254110***199")


def test_filename():
    assert msisdn_from_statement_filename(
        "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf") == "254110026199"
    assert msisdn_from_statement_filename(
        "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199_unlocked.pdf") == "254110026199"
    assert msisdn_from_statement_filename("statement.pdf") is None
