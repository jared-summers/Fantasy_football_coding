"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
from typing import Any

from .collectors.divisions import get_divisions
from .collectors.players import get_player_data
from .collectors.projections import get_projection_data
from .collectors.schedule import get_schedule
from .collectors.teams import get_team_data
from .config import load_settings
from .errors import YahooAPIError
from .yahoo import _context, backfill_season, collect_week, league_metadata


def _args() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m yahoo_fantasy_data")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="Authorize your Yahoo account and save a refresh token")
    auth.add_argument("--env-file", default=".env")
    auth.add_argument("--redirect-uri", required=True, help="Exact redirect URI registered in Yahoo's app settings")
    for name in ("collect", "test"):
        command = commands.add_parser(name)
        command.add_argument("--season", type=int, required=True)
        command.add_argument("--league", required=True)
        command.add_argument("--week", type=int, required=True)
        command.add_argument("--overwrite", action="store_true")
        command.add_argument("--nickname", help="Local data-folder nickname (collect only)")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--season", type=int, required=True)
    backfill.add_argument("--league", required=True)
    backfill.add_argument("--start-week", type=int, default=1)
    backfill.add_argument("--end-week", type=int)
    backfill.add_argument("--overwrite", action="store_true")
    backfill.add_argument("--nickname", help="Local data-folder nickname")
    return parser


def connectivity_report(season: int, league_id: str, week: int) -> dict[str, Any]:
    settings = load_settings()
    metadata, _settings, public, game_id, key = league_metadata(season, league_id, settings)
    report: dict[str, Any] = {
        "league": key, "league_type": "public", "official_api": {"oauth_configured": settings.oauth_configured},
        "provider_policy": "public_first_with_optional_oauth_fallback",
    }
    checks = {
        "divisions": lambda: get_divisions(season, league_id, week, settings=settings, game_id=game_id, provider=public),
        "players": lambda: get_player_data(season, league_id, week, settings=settings, game_id=game_id, provider=public),
        "historical_projections": lambda: get_projection_data(season, league_id, week, settings=settings, game_id=game_id, provider=public),
        "historical_team_rosters": lambda: get_team_data(season, league_id, week, settings=settings, game_id=game_id, provider=public),
        "schedule": lambda: get_schedule(season, league_id, week, settings=settings, game_id=game_id, provider=public),
    }
    for name, check in checks.items():
        try:
            frame = check()
            report[name] = {"available": True, "week_requested": week, "records_returned": len(frame), "sample": frame.head(5).to_dict("records")}
        except YahooAPIError as error:
            report[name] = {"available": False, "week_requested": week, "error": str(error)}
    for name, method in {"draft": public.draft, "league_settings": public.settings}.items():
        try:
            method(key)
            report[name] = {"available": True}
        except YahooAPIError as error:
            report[name] = {"available": False, "error": str(error)}
    return report


def main() -> None:
    args = _args().parse_args()
    try:
        if args.command == "auth":
            from .auth import authorize
            authorize(args.env_file, args.redirect_uri)
        elif args.command == "collect":
            print(json.dumps(collect_week(args.season, args.league, args.week, args.overwrite, league_nickname=args.nickname), indent=2))
        elif args.command == "backfill":
            print(json.dumps(backfill_season(args.season, args.league, args.start_week, args.end_week, args.overwrite, league_nickname=args.nickname), indent=2))
        else:
            print(json.dumps(connectivity_report(args.season, args.league, args.week), indent=2, default=str))
    except YahooAPIError as error:
        raise SystemExit(f"Yahoo request rejected: {error}") from error
