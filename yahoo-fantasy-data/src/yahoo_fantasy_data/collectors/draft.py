"""Original draft results, emitted as an immutable weekly snapshot."""
from __future__ import annotations

from typing import Any
import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..utils import entity_objects, first_value, flatten, stable_frame


def get_draft_data(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    payload = provider.draft(key)
    records: list[dict[str, Any]] = []
    for pick in entity_objects(payload, "player_key"):
        team_key = pick.get("team_key")
        records.append({
            **context_row(season, league_id, week, game_id), **flatten(pick),
            "pick": pick.get("pick") or pick.get("pick_number"), "round": pick.get("round"),
            "team_id": pick.get("team_id") or (str(team_key).split(".")[-1] if team_key else None), "team_key": team_key,
            "team_name": first_value(pick.get("team", {}), "name"),
            "player_id": pick.get("player_id") or str(pick["player_key"]).split(".")[-1], "player_key": pick["player_key"],
            "player_name": first_value(pick.get("name", {}), "full") or first_value(pick, "name"),
            "position": pick.get("primary_position"), "nfl_team": pick.get("editorial_team_abbr"),
            "cost": pick.get("cost"), "keeper_status": pick.get("keeper_status"),
        })
    return stable_frame(records, ["pick"])
