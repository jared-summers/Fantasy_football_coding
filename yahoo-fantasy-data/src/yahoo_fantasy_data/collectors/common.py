from __future__ import annotations

from typing import Any

from ..config import Settings
from ..utils import entity_objects, first_value, flatten, stable_frame, stat_columns
from ..yahoo import league_key


def context_row(season: int, league_id: str, week: int, game_id: str) -> dict[str, Any]:
    return {"season": season, "week": week, "game_id": game_id, "league_id": str(league_id), "league_key": league_key(game_id, str(league_id))}


def resolve(season: int, league_id: str, week: int, settings: Settings | None, game_id: str | None, provider: Any | None):
    if game_id and provider:
        return settings, provider, game_id, league_key(game_id, league_id)
    from ..yahoo import _context
    return (*_context(season, league_id, settings),)  # settings, provider, game id, key


def player_rows(payload: Any, base: dict[str, Any], projected: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player in entity_objects(payload, "player_key"):
        flat = flatten(player)
        row = {**base, **flat}
        row["player_id"] = player.get("player_id") or str(player["player_key"]).split(".")[-1]
        row["player_key"] = player["player_key"]
        row["player_name"] = first_value(player.get("name", {}), "full") or first_value(player, "name")
        row["nfl_team"] = player.get("editorial_team_abbr") or player.get("nfl_team")
        row["position"] = player.get("primary_position") or player.get("position")
        eligible = player.get("eligible_positions")
        row["eligible_positions"] = ",".join(map(str, eligible)) if isinstance(eligible, list) else eligible
        row["status"] = player.get("status")
        row["injury_status"] = player.get("injury_note") or player.get("injury_status")
        row["bye_week"] = first_value(player, "bye_weeks") or player.get("bye_week")
        row["percent_owned"] = first_value(player, "percent_owned")
        row["percent_started"] = first_value(player, "percent_started")
        if projected:
            # Yahoo includes actual and projected structures in one response.
            # Read only the explicit weekly projection fields.
            projected_points = player.get("player_projected_points", {})
            projected_stats = player.get("player_projected_stats", {})
            projection_week = first_value(projected_points, "week")
            projection_coverage = first_value(projected_points, "coverage_type")
            row["projection_week_returned"] = projection_week
            row.update(stat_columns(projected_stats, "projected_"))
            row["projected_points"] = (
                first_value(projected_points, "total")
                if projection_coverage == "week" and str(projection_week) == str(base["week"])
                else None
            )
        else:
            # Scope both coverage and total to actual points, never projections,
            # ownership percentages, or season-level advanced statistics.
            points = player.get("player_points", {})
            stats = player.get("player_stats", {})
            points_week = first_value(points, "week")
            points_coverage = first_value(points, "coverage_type")
            row["actual_week_returned"] = points_week
            row["actual_coverage_returned"] = points_coverage
            row["actual_stats_week_returned"] = first_value(stats, "week")
            row["actual_stats_coverage_returned"] = first_value(stats, "coverage_type")
            weekly_points = points_coverage == "week" and str(points_week) == str(base["week"])
            weekly_stats = (first_value(stats, "coverage_type") == "week"
                            and str(first_value(stats, "week")) == str(base["week"]))
            if weekly_stats:
                row.update(stat_columns(stats))
            row["fantasy_points_actual"] = first_value(points, "total") if weekly_points else None
        rows.append(row)
    return rows
