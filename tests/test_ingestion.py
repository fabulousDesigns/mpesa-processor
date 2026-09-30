import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from app.ingestion.email_parser import parse_email
from app.ingestion.ses_event import Rejected, _check_trust, unwrap

PDF = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"


def test_parse_real_shape_email():
    m = EmailMessage()
    m["From"] = "M-PESA STATEMENTS <m-pesastatements@safaricom.co.ke>"
    m["Subject"] = ("[WARNING: UNSCANNABLE EXTRACTION FAILED]M-PESA Statement requested at 17:00:18 "
                    "for 254110***199 for period 21 Aug 2026 - 21 Sep 2026")
    m["Message-ID"] = "<abc@safaricom.co.ke>"
    m.set_content("Dear BERNARD")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                     filename="MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf")
    p = parse_email(m.as_bytes(), "fallback")
    assert (p.message_id, p.msisdn, p.msisdn_masked, str(p.requested_time), str(p.period_start)) == \
        ("abc@safaricom.co.ke", "254110026199", "254110***199", "17:00:18", "2026-08-21")
    assert p.attachment == b"%PDF-1.4 fake"


def _n(dkim="PASS", source="m-pesastatements@safaricom.co.ke"):
    ok = {"status": "PASS"}
    return {"mail": {"source": source}, "receipt": {"spfVerdict": ok, "dkimVerdict": {"status": dkim},
                                                    "virusVerdict": ok, "spamVerdict": ok}}


def test_trust_checks():
    _check_trust(_n())
    _check_trust(_n(source="prvs=71897fcba=m-pesastatements@safaricom.co.ke"))   # BATV-tagged, real Safaricom
    with pytest.raises(Rejected):
        _check_trust(_n(dkim="FAIL"))
    with pytest.raises(Rejected):
        _check_trust(_n(source="scammer@gmail.com"))
    with pytest.raises(Rejected):
        _check_trust(_n(source="prvs=abc=m-pesastatements@safaricom.co.ke.evil.com"))


def test_unwrap_both_sns_modes():
    inner = {"notificationType": "Received"}
    assert unwrap(json.dumps(inner)) == inner
    assert unwrap(json.dumps({"Type": "Notification", "Message": json.dumps(inner)})) == inner