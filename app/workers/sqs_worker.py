"""Long-polls the statements queue. Run it as its own process:

    python -m app.workers.sqs_worker

Success -> message deleted. Any exception -> message left alone, SQS retries it after the
visibility timeout, and after maxReceiveCount it lands in the DLQ for a human to look at.
"""
from __future__ import annotations

import logging
import signal
import time

from app.core.aws import sqs
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import get_engine
from app.ingestion.s3_store import fetch_attachment
from app.ingestion.ses_event import store_email, unwrap
from app.matching.matcher import expire_stale, on_email_stored

log = logging.getLogger("sqs_worker")
_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("shutdown requested, finishing current batch")


def handle(body: str) -> None:
    engine = get_engine()
    email_id = store_email(engine, unwrap(body))
    if email_id:
        for out in on_email_stored(engine, email_id, fetch_attachment):
            log.info("request %s -> %s", out.request_id, out.status)


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = sqs()
    log.info("polling %s", s.statements_queue_url)
    last_sweep = 0.0
    while _running:
        if time.monotonic() - last_sweep > 60:   # expire stale requests / unclaimed emails every minute
            try:
                reqs, emails = expire_stale(get_engine())
                if reqs or emails:
                    log.info("expired %s requests, %s emails", reqs, emails)
            except Exception:  # noqa: BLE001
                log.exception("expiry sweep failed")
            last_sweep = time.monotonic()
        resp = client.receive_message(QueueUrl=s.statements_queue_url, MaxNumberOfMessages=5,
                                      WaitTimeSeconds=20, VisibilityTimeout=120)
        for m in resp.get("Messages", []):
            try:
                handle(m["Body"])
                client.delete_message(QueueUrl=s.statements_queue_url, ReceiptHandle=m["ReceiptHandle"])
            except Exception:  # noqa: BLE001
                log.exception("message %s failed, SQS will retry", m.get("MessageId"))


if __name__ == "__main__":
    main()