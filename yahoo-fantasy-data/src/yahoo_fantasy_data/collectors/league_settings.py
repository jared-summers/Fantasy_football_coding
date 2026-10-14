"""League, roster, and scoring configuration normalizer."""
from __future__ import annotations

from typing import Any
import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..utils import first_value, stable_frame, walk


def parse_settings(payload: Any, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce readable rows for league fields, roster slots, and scoring rules."""
    rows: list[dict[str, Any]] = []
    top = {
        "league_name": first_value(payload, "name"), "league_type": first_value(payload, "league_type"),
        "num_teams": first_value(payload, "num_teams"), "scoring_type": first_value(payload, "scoring_type"),
        "draft_type": first_value(payload, "draft_type"), "waiver_type": first_value(payload, "waiver_type"),
        "waiver_time": first_value(payload, "waiver_time"), "trade_end_date": first_value(payload, "trade_end_date"),
        "playoff_start_week": first_value(payload, "playoff_start_week"),
        "uses_playoff": first_value(payload, "uses_playoff"),
        "num_playoff_teams": first_value(payload, "num_playoff_teams"),
        "num_playoff_byes": first_value(payload, "num_playoff_byes"),
    }
    rows.append({**base, "setting_type": "league", **top})
    for item in walk(payload):
        if "position" in item and ("count" in item or "position_type" in item):
            rows.append({**base, "setting_type": "roster_position", "position": item.get("position"), "count": item.get("count")})
        if "stat_id" in item and ("value" in item or "stat_value" in item):
            rows.append({**base, "setting_type": "scoring_stat", "stat_id": item.get("stat_id"), "stat_name": item.get("name") or item.get("display_name"), "value": item.get("value") or item.get("stat_value")})
    return rows


def get_league_settings(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    return stable_frame(parse_settings(provider.settings(key), context_row(season, league_id, week, game_id)), ["setting_type", "position", "stat_id"])
