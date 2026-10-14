"""Fantasy matchup schedule; intentionally separate from roster snapshots."""
from __future__ import annotations

import time
from typing import Any
import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..utils import first_value, flatten, stable_frame, walk


def get_schedule(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    payload = provider.scoreboard(key, week)
    records: list[dict[str, Any]] = []
    # Yahoo omits matchup_id in its public scoreboard response. Matchups are
    # identifiable by their week/status fields (teams may be in a numbered
    # wrapper), and the full team rows
    # contain team_id plus the weekly actual and projected point objects.
    matchups = (
        item for item in walk(payload)
        if "week" in item and "status" in item
    )
    for matchup_number, matchup in enumerate(matchups, start=1):
        teams = [
            item for item in walk(matchup)
            if "team_key" in item and ("team_id" in item or "team_points" in item)
        ]
        if len(teams) < 2:
            continue
        for index, team in enumerate(teams[:2]):
            opponent = teams[1 - index]
            records.append({
                **context_row(season, league_id, week, game_id), **flatten(matchup),
                "matchup_id": matchup.get("matchup_id") or f"{week}-{matchup_number}",
                "team_id": team.get("team_id") or str(team["team_key"]).split(".")[-1],
                "team_key": team["team_key"], "team_name": first_value(team.get("name", {}), "") or team.get("name"),
                "opponent_team_id": opponent.get("team_id") or str(opponent["team_key"]).split(".")[-1],
                "opponent_team_key": opponent["team_key"], "opponent_team_name": first_value(opponent.get("name", {}), "") or opponent.get("name"),
                "is_home": bool(index == 0), "is_playoffs": matchup.get("is_playoffs"), "is_consolation": matchup.get("is_consolation"),
                "team_actual_points": first_value(team.get("team_points", {}), "total"),
                "opponent_actual_points": first_value(opponent.get("team_points", {}), "total"),
                "team_projected_points": first_value(team.get("team_projected_points", {}), "total"),
                "opponent_projected_points": first_value(opponent.get("team_projected_points", {}), "total"),
                "winner_team_id": matchup.get("winner_team_key"), "is_tied": matchup.get("is_tied"), "status": matchup.get("status"),
            })
    return stable_frame(records, ["week", "team_id"])


def get_schedule_matrix(season: int, league_id: str, snapshot_week: int, end_week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    """Return a full regular-season opponent matrix for one schedule snapshot."""
    frames: list[pd.DataFrame] = []
    for schedule_week in range(1, end_week + 1):
        frames.append(get_schedule(season, league_id, schedule_week, settings=settings, game_id=game_id, provider=provider))
        if settings is not None:
            time.sleep(settings.request_delay)
    rows = pd.concat(frames, ignore_index=True)
    playoffs = rows["is_playoffs"].astype(str).str.lower().isin({"1", "true"})
    regular = rows.loc[~playoffs].copy()
    regular["week"] = pd.to_numeric(regular["week"], errors="raise").astype(int)
    regular = regular.drop_duplicates(["team_key", "week"], keep="last")
    matrix = regular.pivot(index="team_key", columns="week", values="opponent_team_key").sort_index()
    matrix.columns = [f"week_{int(week)}" for week in matrix.columns]
    matrix.index.name = "team_key"
    return matrix.reset_index()
