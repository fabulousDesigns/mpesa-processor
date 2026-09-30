from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import deps
from app.api.routes import qualification as q
from app.core.config import get_settings
from app.main import app

PDF = Path(__file__).parent / "private" / "MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="private statement fixture not present")
H = {"X-Internal-Token": "t0k3n"}


def test_full_flow_over_http(db_engine, monkeypatch):
    monkeypatch.setenv("CODE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("INTERNAL_API_TOKEN", "t0k3n")
    get_settings.cache_clear()
    monkeypatch.setattr(q, "fetch_attachment", lambda e: PDF.read_bytes())
    app.dependency_overrides[deps.db] = lambda: db_engine
    with db_engine.begin() as c:
        c.execute(text("INSERT INTO users VALUES ('u1','Bernard','Maina','+254110026199')"))
        c.execute(text("INSERT INTO shops (id, name, region) VALUES ('s1','Kinoo','Nairobi')"))
        c.execute(text("INSERT INTO devices (id, shopId, rrp, creditMultiplier) VALUES ('d1','s1',15000,1),('d2','s1',90000,3)"))
        c.execute(text("INSERT INTO stmt_inbound_emails (id, ses_message_id, s3_bucket, s3_key, received_at, msisdn) "
                       "VALUES ('e1','m1','b','k',UTC_TIMESTAMP(3),'254110026199')"))
    api = TestClient(app)
    try:
        assert api.post("/v1/qualification/requests", json={"userId": "u1"}).status_code == 401
        rid = api.post("/v1/qualification/requests", headers=H,
                       json={"userId": "u1", "shopId": "s1", "consentVersion": "v1"}).json()["requestId"]
        r = api.post(f"/v1/qualification/requests/{rid}/code", headers=H, json={"code": "123456"}).json()
        assert r["status"] == "SCORED" and r["assessment"]["affordableDaily"] == "999.44"
        devs = api.get(f"/v1/qualification/requests/{rid}/devices?qualifiedOnly=true", headers=H).json()
        assert [d["deviceId"] for d in devs] == ["d1"]
        assert api.get("/v1/qualification/customers/u1/latest", headers=H).json()["confidence"] == "MEDIUM"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()