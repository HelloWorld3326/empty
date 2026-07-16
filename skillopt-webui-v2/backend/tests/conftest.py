import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402

FAKE_REPO = Path(__file__).resolve().parent / "fixtures" / "fake_skillopt"


@pytest.fixture()
def settings(tmp_path):
    return Settings(env={
        "SKILLOPT_REPO": str(FAKE_REPO),
        "SKILLOPT_DATA_DIR": str(tmp_path / "data"),
        "SKILLOPT_MAX_CONCURRENT_JOBS": "1",
    })


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
    app.state.jobs.close()


def wait_for_status(client, job_id, statuses, timeout=20.0):
    """轮询任务直到进入目标状态集合。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()["data"]
        if data["status"] in statuses:
            return data
        time.sleep(0.1)
    raise AssertionError(
        f"job {job_id} did not reach {statuses} in {timeout}s; last={data['status']}")
