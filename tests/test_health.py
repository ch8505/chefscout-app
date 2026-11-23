from __future__ import annotations

from fastapi.testclient import TestClient

from chefscout import __version__
from chefscout.server.app import create_app


def test_health_reports_status_without_leaking_the_key(settings):
    client = TestClient(create_app(settings))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "version": __version__,
        "model": "claude-haiku-5-5",
        "api_key_configured": False,
    }
