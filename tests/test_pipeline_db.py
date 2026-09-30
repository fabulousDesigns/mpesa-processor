"""End to end on real MariaDB: decrypted statement in -> every stmt_ table filled."""
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.core.names import name_matches
from app.scoring.pipeline import score_statement

FIXTURE = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"
needs_pdf = pytest.mark.skipif(not FIXTURE.exists(), reason="private statement fixture not present")


def _pdf() -> bytes:
    from app.parsing.pdf_open import try_open
    return try_open(FIXTURE.read_bytes(), "123456")


def _seed(engine, first="Bernard", last="Maina", phone="+254110026199", msisdn="254110026199", req="r1"):
    with engine.begin() as c:
        c.execute(text("INSERT INTO users VALUES ('u1', :f, :l, :p)"), {"f": first, "l": last, "p": phone})
        c.execute(text("INSERT INTO shops (id, name, region) VALUES ('s1','Kinoo Shop','Nairobi'), "
                       "('s2','Other Region','Mombasa')"))
        c.execute(text("""INSERT INTO devices (id, brand, model, shopId, status, rrp, creditMultiplier) VALUES
            ('d-cheap','Tecno','Spark','s1','AVAILABLE',15000,1.0),
            ('d-mid','Samsung','A15','s1','AVAILABLE',40000,3.0),
            ('d-dear','Samsung','S24','s1','AVAILABLE',90000,3.0),
            ('d-sold','Tecno','Spark','s1','SOLD',15000,1.0),
            ('d-far','Tecno','Spark','s2','AVAILABLE',15000,1.0)"""))
        c.execute(text("""INSERT INTO stmt_inbound_emails (id, ses_message_id, s3_bucket, s3_key, received_at, msisdn, status)
                          VALUES ('e1','m1','b','k',NOW(3),:m,'CLAIMED')"""), {"m": msisdn})
        c.execute(text("""INSERT INTO stmt_requests (id, user_id, shop_id, msisdn, status, email_id, consent_at,
                          consent_version, expires_at)
                          VALUES (:r,'u1','s1',:m,'OPENED','e1',NOW(3),'v1',NOW(3) + INTERVAL 30 MINUTE)"""),
                  {"r": req, "m": msisdn})


@needs_pdf
def test_scores_and_stores_everything(db_engine):
    _seed(db_engine)
    res = score_statement(db_engine, "r1", _pdf(), settings=Settings())
    assert res.status == "SCORED" and res.affordable_daily == Decimal("999.44") and res.confidence == "MEDIUM"

    with db_engine.connect() as c:
        one = lambda q: c.execute(text(q)).scalar()  # noqa: E731
        assert one("SELECT status FROM stmt_requests WHERE id='r1'") == "SCORED"
        assert one("SELECT reconciled FROM stmt_statements") == 1
        assert one("SELECT COUNT(*) FROM stmt_transactions") == 278
        assert one("SELECT SUM(withdrawn) FROM stmt_transactions") == Decimal("161108.68")
        assert one("SELECT affordable_daily FROM stmt_assessments") == Decimal("999.44")
        # pool = same region, AVAILABLE only: cheap, mid, dear (not sold, not the Mombasa one)
        devs = dict(c.execute(text("SELECT device_id, qualified FROM stmt_assessment_devices")).all())
        assert devs == {"d-cheap": 1, "d-mid": 1, "d-dear": 0}   # dear: 420 x 3 = 1260 > 999.44
        m = c.execute(text("SELECT name, location_text, county, txn_count, customer_count FROM stmt_merchants "
                           "WHERE merchant_key = 'TILL:4342487'")).one()
        assert m == ("PUREVISTA WATERS LIMITED KINOO", "KINOO", "Kiambu", 5, 1)
        # people never land in the merchant registry
        assert one("SELECT COUNT(*) FROM stmt_merchants WHERE name LIKE '%STEPHANIA%'") == 0


@needs_pdf
def test_rescoring_same_customer_does_not_double_count_customers(db_engine):
    _seed(db_engine)
    score_statement(db_engine, "r1", _pdf(), settings=Settings())
    with db_engine.begin() as c:
        c.execute(text("INSERT INTO stmt_inbound_emails (id, ses_message_id, s3_bucket, s3_key, received_at, "
                       "msisdn, status) VALUES ('e2','m2','b','k',NOW(3),'254110026199','CLAIMED')"))
        c.execute(text("INSERT INTO stmt_requests (id, user_id, shop_id, msisdn, status, email_id, consent_at, "
                       "consent_version, expires_at) VALUES ('r2','u1','s1','254110026199','OPENED','e2',NOW(3),"
                       "'v1',NOW(3))"))
    assert score_statement(db_engine, "r2", _pdf(), settings=Settings()).status == "SCORED"
    with db_engine.connect() as c:
        assert c.execute(text("SELECT txn_count, customer_count FROM stmt_merchants "
                              "WHERE merchant_key='TILL:4342487'")).one() == (10, 1)


@needs_pdf
def test_manual_location_is_never_overwritten(db_engine):
    _seed(db_engine)
    with db_engine.begin() as c:
        c.execute(text("INSERT INTO stmt_merchants (id, merchant_key, kind, number, name, location_text, county, "
                       "location_source) VALUES ('mx','TILL:4748597','TILL','4748597','FAIMO MINI MART',"
                       "'KINOO STAGE','Kiambu','MANUAL')"))
    score_statement(db_engine, "r1", _pdf(), settings=Settings())
    with db_engine.connect() as c:
        assert c.execute(text("SELECT location_text, location_source FROM stmt_merchants WHERE id='mx'")).one() \
            == ("KINOO STAGE", "MANUAL")


@needs_pdf
def test_name_mismatch_stores_nothing(db_engine):
    _seed(db_engine, first="Nancy", last="Wanja")
    res = score_statement(db_engine, "r1", _pdf(), settings=Settings())
    assert res.status == "FAILED" and res.reason.startswith("NAME_MISMATCH")
    with db_engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM stmt_statements")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM stmt_transactions")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM stmt_merchants")).scalar() == 0


@needs_pdf
def test_number_mismatch_stores_nothing(db_engine):
    _seed(db_engine, phone="+254713138481", msisdn="254713138481")
    res = score_statement(db_engine, "r1", _pdf(), settings=Settings())
    assert res.status == "FAILED" and res.reason.startswith("MSISDN_MISMATCH")


@needs_pdf
def test_already_scored_request_is_left_alone(db_engine):
    _seed(db_engine)
    score_statement(db_engine, "r1", _pdf(), settings=Settings())
    again = score_statement(db_engine, "r1", _pdf(), settings=Settings())
    assert again.status == "SCORED"
    with db_engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM stmt_statements")).scalar() == 1


def test_shop_scope_limits_pool(db_engine):
    from app.db.repository import load_device_pool
    _seed(db_engine)
    with db_engine.begin() as c:
        c.execute(text("INSERT INTO shops (id, name, region) VALUES ('s3','Neighbour','Nairobi')"))
        c.execute(text("INSERT INTO devices (id, shopId, status, rrp) VALUES ('d-next','s3','AVAILABLE',15000)"))
        region = {d["id"] for d in load_device_pool(c, "s1", "REGION")}
        shop = {d["id"] for d in load_device_pool(c, "s1", "SHOP")}
    assert region == {"d-cheap", "d-mid", "d-dear", "d-next"}
    assert shop == {"d-cheap", "d-mid", "d-dear"}


@pytest.mark.parametrize("stmt,first,last,ok", [
    ("BERNARD MBURU MAINA", "Bernard", "Maina", True),
    ("BERNARD MBURU MAINA", "Maina", "Bernard", True),      # registered the other way round
    ("BERNARD MBURU MAINA", "Bernard", "Mburu Maina", True),
    ("BERNARD MBURU MAINA", "Bernard", "Kamau", False),
    ("BERNARD MBURU MAINA", "", "Maina", False),
])
def test_name_rule(stmt, first, last, ok):
    assert name_matches(stmt, first, last) is ok