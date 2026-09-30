"""Raw Safaricom email (.eml bytes) -> what we store in stmt_inbound_emails."""
from __future__ import annotations

import email
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from email import policy

from app.core.msisdn import msisdn_from_statement_filename

SAFARICOM_SENDER = "m-pesastatements@safaricom.co.ke"

_REQ_TIME = re.compile(r"requested at (\d{2}):(\d{2}):(\d{2})", re.I)
_MASK = re.compile(r"for (\d{6}\*{3}\d{3})", re.I)
_PERIOD = re.compile(r"period (\d{1,2} \w{3} \d{4}) - (\d{1,2} \w{3} \d{4})", re.I)


@dataclass
class ParsedEmail:
    message_id: str
    from_address: str | None
    subject: str | None
    attachment_filename: str | None
    attachment: bytes | None
    msisdn: str | None
    msisdn_masked: str | None
    requested_time: time | None
    period_start: date | None
    period_end: date | None


def _d(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d %b %Y").date()
    except ValueError:
        return None


def parse_email(raw: bytes, fallback_message_id: str) -> ParsedEmail:
    msg = email.message_from_bytes(raw, policy=policy.default)
    subject = str(msg.get("Subject") or "")
    sender = email.utils.parseaddr(str(msg.get("From") or ""))[1].lower() or None

    filename, payload = None, None
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        if name.lower().endswith(".pdf") or part.get_content_type() == "application/pdf":
            filename, payload = name, part.get_payload(decode=True)
            break

    t = _REQ_TIME.search(subject)
    mask = _MASK.search(subject)
    per = _PERIOD.search(subject)
    return ParsedEmail(
        message_id=(str(msg.get("Message-ID") or "").strip("<> ") or fallback_message_id)[:255],
        from_address=sender,
        subject=subject[:500] or None,
        attachment_filename=filename,
        attachment=payload,
        msisdn=msisdn_from_statement_filename(filename) if filename else None,
        msisdn_masked=mask.group(1) if mask else None,
        requested_time=time(*map(int, t.groups())) if t else None,
        period_start=_d(per.group(1)) if per else None,
        period_end=_d(per.group(2)) if per else None,
    )