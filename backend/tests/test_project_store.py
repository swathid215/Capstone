import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.project_store import ProjectStore
from api.server import app, _analysis_simulation_completed


def test_api_health_alias_route():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_project_store_round_trip(tmp_path):
    db_path = tmp_path / "uidt_projects.db"
    store = ProjectStore(str(db_path))

    project = store.create_project(name="Test Project")
    assert project["name"] == "Test Project"
    assert project["id"]

    listed = store.list_projects()
    assert len(listed) == 1
    assert listed[0]["name"] == "Test Project"

    loaded = store.get_project(project["id"])
    assert loaded["name"] == "Test Project"

    updated = store.update_project(project["id"], {"name": "Renamed Project"})
    assert updated["name"] == "Renamed Project"

    deleted = store.delete_project(project["id"])
    assert deleted is True
    assert store.get_project(project["id"]) is None


def test_analysis_simulation_completion_controls_project_replacement():
    assert _analysis_simulation_completed({
        "simulation": {"status": "batch_complete"},
        "route4_error": "Route 4 unavailable",
    })
    assert _analysis_simulation_completed({
        "simulation": {
            "status": "complete",
            "avg_travel_time_before_min": 4.1,
            "avg_travel_time_after_min": 4.4,
        },
        "route4_error": "Route 4 unavailable",
    })
    assert not _analysis_simulation_completed({
        "simulation": {"status": "complete", "routes": [{"id": 1}]},
        "route4_error": "Modified-network simulation failed",
    })
    assert not _analysis_simulation_completed({"simulation": {"status": "failed"}})
