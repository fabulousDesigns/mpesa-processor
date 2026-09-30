"""SES receipt notification -> store the email. Arrives via SES (S3 action + SNS topic) -> SQS.

Security: anyone can email our inbox, including a doctored "statement". We only accept
mail that SES itself verified came from Safaricom (SPF + DKIM pass, no virus/spam).
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import Engine, text

from app.db.repository import new_id
from app.ingestion import s3_store
from app.ingestion.email_parser import SAFARICOM_SENDER, parse_email

log = logging.getLogger(__name__)


class Rejected(Exception):
    """Not a trustworthy Safaricom statement email. Dropped, never stored."""


def unwrap(sqs_body: str) -> dict:
    """Works with SNS raw message delivery on or off."""
    body = json.loads(sqs_body)
    if body.get("Type") == "Notification" and "Message" in body:
        body = json.loads(body["Message"])
    return body


_BATV = re.compile(r"^(?:prvs|btv1)=[^=]+=", re.I)   # prvs=71897fcba=user@domain -> user@domain


def _senders(n: dict) -> set[str]:
    """Envelope sender (BATV tag stripped) plus the From: header address(es)."""
    mail = n.get("mail", {})
    out = {_BATV.sub("", (mail.get("source") or "").strip().lower())}
    for raw in mail.get("commonHeaders", {}).get("from", []) or []:
        out.add(email.utils.parseaddr(raw)[1].strip().lower())
    return {s for s in out if s}


def _check_trust(n: dict) -> None:
    r = n.get("receipt", {})
    for k in ("spfVerdict", "dkimVerdict", "virusVerdict", "spamVerdict"):
        if r.get(k, {}).get("status") != "PASS":
            raise Rejected(f"{k}={r.get(k, {}).get('status')}")
    senders = _senders(n)
    if SAFARICOM_SENDER not in senders:
        raise Rejected(f"sender {sorted(senders)!r}")


def store_email(engine: Engine, notification: dict) -> str | None:
    """Returns the stmt_inbound_emails id (new or already stored), or None if rejected."""
    if notification.get("notificationType") != "Received":
        return None
    mail, action = notification["mail"], notification["receipt"]["action"]
    try:
        _check_trust(notification)
    except Rejected as e:
        log.warning("dropping email %s: %s", mail.get("messageId"), e)
        return None

    bucket, key = action["bucketName"], action["objectKey"]
    with engine.connect() as c:
        existing = c.execute(text("SELECT id FROM stmt_inbound_emails WHERE ses_message_id = :m"),
                             {"m": mail["messageId"]}).scalar()
    if existing:
        return existing  # SQS/SNS redelivery

    parsed = parse_email(s3_store.get_object(bucket, key), mail["messageId"])
    eid = new_id()
    att_key = s3_store.put_attachment(eid, parsed.attachment) if parsed.attachment else None
    received = datetime.fromisoformat(mail["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
    status, reason = ("UNCLAIMED", None) if (parsed.attachment and (parsed.msisdn or parsed.msisdn_masked)) \
        else ("UNPARSEABLE", "no statement PDF or no phone number")

    with engine.begin() as c:
        c.execute(text("""
            INSERT IGNORE INTO stmt_inbound_emails
              (id, ses_message_id, s3_bucket, s3_key, from_address, subject, received_at,
               attachment_filename, attachment_s3_key, attachment_sha256, msisdn, msisdn_masked,
               requested_time, period_start, period_end, status, status_reason)
            VALUES
              (:id, :mid, :b, :k, :f, :s, :rcv, :fn, :ak, :sha, :m, :mask, :rt, :ps, :pe, :st, :why)
        """), {"id": eid, "mid": mail["messageId"], "b": bucket, "k": key, "f": parsed.from_address,
               "s": parsed.subject, "rcv": received.replace(tzinfo=None), "fn": parsed.attachment_filename,
               "ak": att_key, "sha": hashlib.sha256(parsed.attachment).hexdigest() if parsed.attachment else None,
               "m": parsed.msisdn, "mask": parsed.msisdn_masked, "rt": parsed.requested_time,
               "ps": parsed.period_start, "pe": parsed.period_end, "st": status, "why": reason})
        # INSERT IGNORE + a racing duplicate delivery: return whichever row won.
        return c.execute(text("SELECT id FROM stmt_inbound_emails WHERE ses_message_id = :m"),
                         {"m": mail["messageId"]}).scalar()