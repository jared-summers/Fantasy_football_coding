"""Historical fantasy-team roster snapshots, one row per rostered player."""
from __future__ import annotations

from typing import Any
import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..errors import YahooHistoricalRosterUnavailableError
from ..utils import entity_objects, first_value, flatten, stable_frame


def get_team_data(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    payload = provider.teams_roster(key, week)
    teams = entity_objects(payload, "team_key")
    records: list[dict[str, Any]] = []
    for team in teams:
        team_id = team.get("team_id") or str(team["team_key"]).split(".")[-1]
        team_name = first_value(team.get("name", {}), "") or team.get("name")
        for player in entity_objects(team, "player_key"):
            selected = player.get("selected_position", {})
            slot = selected.get("position") if isinstance(selected, dict) else None
            is_starting = selected.get("is_starting") if isinstance(selected, dict) else None
            if is_starting is None:
                is_starting = slot not in {None, "BN", "IR", "IR+"}
            eligible = player.get("eligible_positions")
            records.append({
                **context_row(season, league_id, week, game_id), **flatten(player),
                "team_id": team_id, "team_key": team["team_key"], "team_name": team_name,
                "player_id": player.get("player_id") or str(player["player_key"]).split(".")[-1],
                "player_key": player["player_key"],
                "player_name": first_value(player.get("name", {}), "full") or first_value(player, "name"),
                "nfl_team": player.get("editorial_team_abbr"), "primary_position": player.get("primary_position"),
                "eligible_positions": ",".join(map(str, eligible)) if isinstance(eligible, list) else eligible,
                "roster_slot": slot, "is_starting": is_starting,
                "player_status": player.get("status"), "injury_status": player.get("injury_note") or player.get("injury_status"),
                "bye_week": first_value(player, "bye_weeks") or player.get("bye_week"),
            })
    if not records:
        raise YahooHistoricalRosterUnavailableError(f"Yahoo returned no historical roster rows for week {week}.")
    return stable_frame(records, ["team_id", "roster_slot", "player_id"])
