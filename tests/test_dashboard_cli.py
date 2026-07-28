from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marketsearch.cli import app
from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import content_hash
from marketsearch.store import Store

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Same shape as the fixture in tests/test_cli.py: a tmp dir holding a
    real config.yaml copied from tests/fixtures/."""
    shutil.copy(
        Path(__file__).parent / "fixtures" / "config.yaml",
        tmp_path / "config.yaml",
    )
    return tmp_path


def seed(db: Path) -> None:
    listing = RawListing(
        listing_id="1", title="2021 Bobcat T770", price_cents=3_950_000,
        location="Peoria, IL", url="https://example.com/1",
        thumbnail_url=None, seller_name="Dale",
    )
    with Store(db) as store:
        store.initialize()
        store.upsert_listing(listing, "fp", watchlist_name="track-loaders",
                             model_name="bobcat-t770")
        detail = ListingDetail(listing_id="1", description="runs strong",
                               structured_fields={}, photo_urls=[],
                               distance_miles=None)
        store.save_detail(detail, content_hash(detail))
        store.save_extraction(
            listing_id="1", attributes={"core": {"engine_hours": 2200}},
            verdict="match", confidence=0.72, reasoning="low hours",
            unknowns=[], model="claude-opus-5",
            input_tokens=1, output_tokens=1, cost_cents=0.1,
        )
        store.set_stage("1", "matched")


def run_dashboard(project: Path, db: Path, out: Path, *extra: str):
    return runner.invoke(app, [
        "dashboard", "--db", str(db), "--config", str(project / "config.yaml"),
        "--out", str(out), "--no-open", *extra,
    ])


def test_dashboard_writes_a_page(project: Path):
    db, out = project / "d.db", project / "dash.html"
    seed(db)
    result = run_dashboard(project, db, out)
    assert result.exit_code == 0, result.output
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert 'data-listing-id="1"' in html


def test_dashboard_reports_an_empty_database_without_crashing(project: Path):
    db, out = project / "empty.db", project / "dash.html"
    with Store(db) as store:
        store.initialize()
    result = run_dashboard(project, db, out)
    assert result.exit_code == 0, result.output
    assert "nothing judged yet" in out.read_text(encoding="utf-8").lower()


def test_life_hours_flag_is_passed_through(project: Path):
    db, out = project / "l.db", project / "dash.html"
    seed(db)
    result = run_dashboard(project, db, out, "--life-hours", "9000")
    assert result.exit_code == 0, result.output
    assert 'value="9000"' in out.read_text(encoding="utf-8")


def test_dashboard_appears_in_help():
    result = runner.invoke(app, ["--help"])
    assert "dashboard" in result.stdout
