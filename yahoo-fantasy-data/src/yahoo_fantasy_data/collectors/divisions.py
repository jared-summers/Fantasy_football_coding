"""Current division definitions and fantasy-team assignments."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..errors import YahooAPIError
from ..utils import entity_objects, first_value, stable_frame


def get_divisions(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    definitions = {
        str(item["division_id"]): item.get("name")
        for item in entity_objects(provider.settings(key), "division_id")
    }
    teams = entity_objects(provider.teams(key), "team_key")
    if not teams:
        raise YahooAPIError("Yahoo returned no teams for division assignments; snapshot not saved.")
    records = []
    for team in teams:
        division_id = team.get("division_id")
        records.append({
            **context_row(season, league_id, week, game_id),
            "team_id": team.get("team_id") or str(team["team_key"]).split(".")[-1],
            "team_key": team["team_key"],
            "team_name": first_value(team, "name"),
            "division_id": division_id,
            "division_name": definitions.get(str(division_id)) if division_id is not None else None,
        })
    return stable_frame(records, ["division_id", "team_id"])
