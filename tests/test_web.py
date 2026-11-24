"""Stage 6: the web page is served, and the page never renders model text as HTML."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from chefscout.server.app import WEB_DIR, create_app


def test_index_and_static_files_are_served(settings):
    api = TestClient(create_app(settings))
    page = api.get("/")
    assert page.status_code == 200 and 'dir="rtl"' in page.text and "Dishly" in page.text
    assert api.get("/static/app.js").status_code == 200
    assert api.get("/static/styles.css").status_code == 200


def test_page_script_never_uses_inner_html():
    script = Path(WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script.replace("never innerHTML", "")
    assert "insertAdjacentHTML" not in script and "eval(" not in script
