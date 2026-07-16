import os

from conftest import wait_for_status


def _create(client, **kwargs):
    body = {"base_config": "configs/demo/default.yaml", **kwargs}
    resp = client.post("/api/jobs", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["job_id"]


def test_full_training_lifecycle(client):
    job_id = _create(client, name="demo-run")
    data = wait_for_status(client, job_id, {"succeeded", "failed"})
    assert data["status"] == "succeeded", data

    # 进度从日志标记解析出来
    assert data["progress"]["total_steps"] == 4
    assert data["progress"]["step"] == 4
    assert data["progress"]["stage_index"] == 6

    # 增量日志
    logs = client.get(f"/api/jobs/{job_id}/logs").json()["data"]
    assert "[1/6 ROLLOUT]" in logs["content"]
    assert "[DONE] training complete" in logs["content"]
    tail = client.get(
        f"/api/jobs/{job_id}/logs", params={"offset": logs["next_offset"]}
    ).json()["data"]
    assert tail["content"] == ""

    # 产物
    results = client.get(f"/api/jobs/{job_id}/results").json()["data"]
    assert results["best_skill"].startswith("# best skill")
    assert len(results["skills"]) == 4
    assert results["history"][-1]["step"] == 4

    # 产物下载 + 路径穿越防护
    dl = client.get(f"/api/jobs/{job_id}/artifacts/best_skill.md")
    assert dl.status_code == 200 and b"best skill" in dl.content
    bad = client.get(f"/api/jobs/{job_id}/artifacts/../job.json")
    assert bad.status_code in (400, 404)


def _wait_for_log(client, job_id, marker, timeout=20.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        content = client.get(f"/api/jobs/{job_id}/logs").json()["data"]["content"]
        if marker in content:
            return
        time.sleep(0.1)
    raise AssertionError(f"marker {marker!r} not seen in logs within {timeout}s")


def test_stop_and_resume(client):
    os.environ["FAKE_TRAIN_STEP_SLEEP"] = "3"
    try:
        job_id = _create(client, name="slow-run")
        wait_for_status(client, job_id, {"running"})
        # 等第 1 步完成（runtime_state.json 已落盘）再停止，验证真实续训
        _wait_for_log(client, job_id, "[STEP 2/")
        resp = client.post(f"/api/jobs/{job_id}/stop")
        assert resp.status_code == 200
        data = wait_for_status(client, job_id, {"stopped"})
        assert data["status"] == "stopped"
    finally:
        del os.environ["FAKE_TRAIN_STEP_SLEEP"]

    # 相同 out_root 续训（假训练器读 runtime_state.json 恢复）
    resp = client.post(f"/api/jobs/{job_id}/resume")
    assert resp.status_code == 200
    data = wait_for_status(client, job_id, {"succeeded"})
    assert data["resume_count"] == 1
    logs = client.get(f"/api/jobs/{job_id}/logs").json()["data"]["content"]
    assert "[RESUME] resuming from step" in logs


def test_failure_then_resume_succeeds(client):
    os.environ["FAKE_TRAIN_FAIL"] = "1"
    try:
        job_id = _create(client, name="fail-run")
        data = wait_for_status(client, job_id, {"failed"})
        assert data["exit_code"] == 3
    finally:
        del os.environ["FAKE_TRAIN_FAIL"]

    client.post(f"/api/jobs/{job_id}/resume")
    data = wait_for_status(client, job_id, {"succeeded"})
    assert data["status"] == "succeeded"


def test_concurrency_queueing(client):
    os.environ["FAKE_TRAIN_STEP_SLEEP"] = "5"
    try:
        first = _create(client, name="first")
        wait_for_status(client, first, {"running"})
        second = _create(client, name="second")
        data = client.get(f"/api/jobs/{second}").json()["data"]
        assert data["status"] == "queued"  # 并发上限 1，第二个排队
        client.post(f"/api/jobs/{first}/stop")
        wait_for_status(client, second, {"running", "succeeded"})
    finally:
        del os.environ["FAKE_TRAIN_STEP_SLEEP"]
        client.post(f"/api/jobs/{second}/stop")


def test_state_conflicts(client):
    job_id = _create(client)
    wait_for_status(client, job_id, {"succeeded"})
    # 已结束任务不能 stop，但可以 resume
    assert client.post(f"/api/jobs/{job_id}/stop").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200


def test_unknown_config_rejected(client):
    resp = client.post("/api/jobs", json={"base_config": "configs/nope.yaml"})
    assert resp.status_code == 404


def test_overrides_reach_trainer(client):
    job_id = _create(client, overrides={"train.num_epochs": 1,
                                        "env.out_root": "/tmp/evil"})
    data = wait_for_status(client, job_id, {"succeeded"})
    # num_epochs=1 → 2 步；out_root 用户覆盖被服务端强制忽略
    assert data["progress"]["total_steps"] == 2
    results = client.get(f"/api/jobs/{job_id}/results").json()["data"]
    assert results["best_skill"] is not None
