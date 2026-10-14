"""Manual smoke test only: pytest -m integration tests/test_integration_public.py"""
import os

import pytest

from yahoo_fantasy_data.cli import connectivity_report


pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("YAHOO_INTEGRATION") != "1", reason="set YAHOO_INTEGRATION=1 to call Yahoo")
def test_known_public_league_anonymously() -> None:
    report = connectivity_report(2025, "707737", 5)
    assert report["league_type"] == "public"
