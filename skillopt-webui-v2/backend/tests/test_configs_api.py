def test_health(client):
    data = client.get("/api/system/health").json()
    assert data["code"] == 0
    assert data["data"]["repo_ok"] is True


def test_list_configs(client):
    data = client.get("/api/configs").json()["data"]
    assert "configs/demo/default.yaml" in data["configs"]
    assert not any("_base_" in c for c in data["configs"])


def test_get_config_merges_base_and_splits_fields(client):
    data = client.get("/api/configs/configs/demo/default.yaml").json()["data"]
    # _base_ 继承已合并
    assert data["values"]["train"]["num_epochs"] == 2
    assert data["values"]["env"]["name"] == "demo"
    # 元数据未覆盖的 benchmark 透传键进 extra
    assert data["extra"]["env"]["custom_passthrough_key"] == 7
    # schema 附带字段元数据
    sections = {s["name"] for s in data["schema"]["sections"]}
    assert sections == {"model", "train", "gradient", "optimizer", "evaluation", "env"}


def test_get_config_not_found(client):
    resp = client.get("/api/configs/configs/nope/default.yaml")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


def test_config_path_escape_rejected(client):
    resp = client.get("/api/configs/../../etc/passwd")
    assert resp.status_code in (400, 404)


def test_backends_preflight(client):
    data = client.get("/api/system/backends").json()["data"]
    assert all(r["ready"] for r in data["results"])  # fake_exec 后端无预检项
