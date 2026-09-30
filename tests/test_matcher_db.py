from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.config import get_settings
from app.matching import matcher as m

PDF = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="private statement fixture not present")
fetch = lambda email: PDF.read_bytes()  # noqa: E731  (S3 in production)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def seed(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO users VALUES ('u1','Bernard','Maina','+254110026199')"))
        c.execute(text("INSERT INTO shops (id, name, region) VALUES ('s1','Kinoo','Nairobi')"))
        c.execute(text("INSERT INTO devices (id, shopId, rrp) VALUES ('d1','s1',15000)"))
    return m.create_request(engine, user_id="u1", agent_id=None, shop_id="s1", consent_version="v1")


def land_email(engine, eid="e1"):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO stmt_inbound_emails (id, ses_message_id, s3_bucket, s3_key, received_at, msisdn)
                          VALUES (:id, :id, 'b', 'k', UTC_TIMESTAMP(3), '254110026199')"""), {"id": eid})
    return m.on_email_stored(engine, eid, fetch)


def test_code_first_then_email(db_engine):
    rid = seed(db_engine)
    assert m.create_request(db_engine, user_id="u1", agent_id=None, shop_id="s1", consent_version="v1") == rid
    assert m.submit_code(db_engine, rid, "123 456", fetch).status == "AWAITING_EMAIL"   # parked
    [out] = land_email(db_engine)
    assert out.status == "SCORED" and out.scoring.qualified_count == 1
    with db_engine.connect() as c:
        assert c.execute(text("SELECT code_ciphertext FROM stmt_requests")).scalar() is None   # code wiped


def test_email_first_wrong_code_then_lockout(db_engine):
    rid = seed(db_engine)
    land_email(db_engine)
    out = m.submit_code(db_engine, rid, "000000", fetch)
    assert out.status == "WRONG_CODE" and "4 tries left" in out.message
    for _ in range(4):
        out = m.submit_code(db_engine, rid, "000000", fetch)
    assert out.status == "LOCKED_OUT"
    assert m.submit_code(db_engine, rid, "123456", fetch).status == "LOCKED_OUT"