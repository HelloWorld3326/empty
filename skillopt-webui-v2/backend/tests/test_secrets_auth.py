from fastapi.testclient import TestClient

from conftest import FAKE_REPO
from app.main import create_app
from app.settings import Settings


def test_secrets_roundtrip_masked(client):
    resp = client.put("/api/settings/secrets", json={"entries": {
        "AZURE_OPENAI_API_KEY": "sk-plain-secret",
        "AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com/",
    }})
    assert resp.status_code == 200
    data = client.get("/api/settings/secrets").json()["data"]
    entries = {e["key"]: e["value"] for e in data["entries"]}
    assert entries["AZURE_OPENAI_API_KEY"] == "******"          # 密钥永不回显明文
    assert entries["AZURE_OPENAI_ENDPOINT"] == "https://x.openai.azure.com/"

    # 掩码原样提交 = 不修改；null = 删除
    client.put("/api/settings/secrets", json={"entries": {
        "AZURE_OPENAI_API_KEY": "******",
        "AZURE_OPENAI_ENDPOINT": None,
    }})
    data = client.get("/api/settings/secrets").json()["data"]
    entries = {e["key"]: e["value"] for e in data["entries"]}
    assert entries.get("AZURE_OPENAI_API_KEY") == "******"
    assert "AZURE_OPENAI_ENDPOINT" not in entries


def test_secrets_invalid_key_rejected(client):
    resp = client.put("/api/settings/secrets",
                      json={"entries": {"BAD KEY!": "x"}})
    assert resp.status_code == 400


def test_jwt_auth_mode(tmp_path):
    import jwt as pyjwt

    settings = Settings(env={
        "SKILLOPT_REPO": str(FAKE_REPO),
        "SKILLOPT_DATA_DIR": str(tmp_path / "data"),
        "SKILLOPT_AUTH_MODE": "jwt",
        "SKILLOPT_JWT_SECRET": "test-secret",
    })
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/configs").status_code == 401
        assert client.get(
            "/api/configs",
            headers={"Authorization": "Bearer not-a-token"},
        ).status_code == 401
        token = pyjwt.encode({"sub": "user-1", "aud": "authenticated"},
                             "test-secret", algorithm="HS256")
        resp = client.get("/api/configs",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        # health 端点不要求认证（供探活）
        assert client.get("/api/system/health").status_code == 200
    app.state.jobs.close()
