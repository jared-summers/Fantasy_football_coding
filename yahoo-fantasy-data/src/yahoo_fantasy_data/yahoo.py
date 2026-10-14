"""HTTP access and snapshot orchestration."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings, load_settings, storage_league_name
from .errors import (
    YahooAPIError,
    YahooAuthenticationError,
    YahooNotFoundError,
    YahooPrivateLeagueError,
    YahooRateLimitError,
)
from .utils import first_value

PUBLIC_BASE_URL = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2"
OFFICIAL_BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
USER_AGENT = "yahoo-fantasy-data/0.1 (public data archiver; contact: repository owner)"


class YahooHTTPClient:
    """Small, polite client shared by providers."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get(self, path: str, *, params: dict[str, Any] | None = None, token: str | None = None, official: bool = False) -> dict[str, Any]:
        base = OFFICIAL_BASE_URL if official else PUBLIC_BASE_URL
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self.session.get(f"{base}/{path.lstrip('/')}", params=params, headers=headers, timeout=self.settings.timeout)
        except requests.RequestException as error:
            raise YahooAPIError(f"Yahoo request failed for {path}: {error}") from error
        if response.status_code in (401, 403):
            raise YahooAuthenticationError(f"Yahoo requires authentication for {path}.")
        if response.status_code == 404:
            raise YahooNotFoundError(f"Yahoo resource was not found: {path}")
        if response.status_code in (429, 999):
            raise YahooRateLimitError(f"Yahoo rate limited or denied the request (HTTP {response.status_code}); retry later.")
        if response.status_code >= 400:
            raise YahooAPIError(f"Yahoo returned HTTP {response.status_code} for {path}: {response.text[:240]}")
        try:
            return response.json()
        except ValueError as error:
            raise YahooAPIError(f"Yahoo returned non-JSON data for {path}.") from error


def get_game_id(season: int, *, provider: Any | None = None, settings: Settings | None = None) -> str:
    """Discover the NFL game id for a season, with an explicit environment fallback."""
    active_settings = settings or load_settings()
    if active_settings.game_id:
        return active_settings.game_id
    if provider is None:
        from .providers.fallback import FallbackYahooProvider
        provider = FallbackYahooProvider(YahooHTTPClient(active_settings))
    payload = provider.games(season)
    for candidate in (item for item in _walk_values(payload) if isinstance(item, dict)):
        season_value = candidate.get("season")
        game_key = candidate.get("game_key") or candidate.get("game_id")
        code = candidate.get("code") or candidate.get("game_code")
        if str(season_value) == str(season) and str(code).lower() in {"nfl", "football"} and game_key:
            return str(game_key).split(".")[0]
    game_id = first_value(payload, "game_id")
    if game_id is not None:
        return str(game_id)
    raise YahooNotFoundError(f"Yahoo did not expose an NFL game id for season {season}; supply YAHOO_GAME_ID.")


def _walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def league_key(game_id: str, league_id: str) -> str:
    return f"{game_id}.l.{league_id}"


def _context(season: int, league_id: str, settings: Settings | None = None) -> tuple[Settings, Any, str, str]:
    active_settings = settings or load_settings()
    from .providers.fallback import FallbackYahooProvider
    public = FallbackYahooProvider(YahooHTTPClient(active_settings))
    game_id = get_game_id(season, provider=public, settings=active_settings)
    return active_settings, public, game_id, league_key(game_id, str(league_id))


def league_metadata(season: int, league_id: str, settings: Settings | None = None) -> tuple[dict[str, Any], Settings, Any, str, str]:
    active_settings, public, game_id, key = _context(season, league_id, settings)
    payload = public.league(key)
    private = first_value(payload, "is_private")
    # Yahoo can report ``league_type=private`` for a league whose website
    # setting says "Make League Publicly Viewable: Yes". That field is not a
    # reliable anonymous-access signal. Only an explicit is_private flag stops
    # collection; individual collectors report endpoint authentication needs.
    if str(private).lower() in {"1", "true"}:
        raise YahooPrivateLeagueError(f"League {key} is private and will not be archived.")
    return payload, active_settings, public, game_id, key


def _snapshot_path(settings: Settings, league_id: str, season: int, data_type: str, week: int) -> Path:
    filename = f"{data_type.replace(' ', '_')}_week_{week}.csv.gz"
    return settings.data_dir / storage_league_name(settings.league_nickname, league_id) / str(season) / data_type / filename


def write_snapshot(frame: pd.DataFrame, path: Path, overwrite: bool) -> str:
    if path.exists() and not overwrite:
        return "skipped_existing"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression="gzip")
    return "written"


def _metadata_path(settings: Settings, league_id: str, season: int) -> Path:
    return settings.data_dir / storage_league_name(settings.league_nickname, league_id) / str(season) / "metadata.json"


def update_metadata(settings: Settings, season: int, league_id: str, game_id: str, key: str, payload: Any, week: int, statuses: dict[str, str]) -> None:
    path = _metadata_path(settings, league_id, season)
    path.parent.mkdir(parents=True, exist_ok=True)
    old: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    end_week = first_value(payload, "end_week") or old.get("end_week")
    metadata = {
        **old,
        "season": season, "game_id": game_id, "league_id": str(league_id), "league_key": key,
        "league_name": first_value(payload, "name", old.get("league_name")),
        "league_type": "public", "start_week": first_value(payload, "start_week", old.get("start_week", 1)),
        "current_week": first_value(payload, "current_week", old.get("current_week")),
        "is_finished": first_value(payload, "is_finished", old.get("is_finished")),
        "end_week": end_week, "last_collected_week": max(week, int(old.get("last_collected_week", 0))),
        "last_updated": datetime.now(UTC).isoformat(),
        "sources": {**old.get("sources", {}), **statuses},
    }
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def collect_week(season: int, league_id: str, week: int, overwrite: bool = False, *, settings: Settings | None = None, league_nickname: str | None = None, _metadata_context: tuple[Any, Any, str, str] | None = None, _schedule_matrix: pd.DataFrame | None = None, _static_frames: dict[str, pd.DataFrame] | None = None) -> dict[str, str]:
    """Collect independent datasets, retaining successes when another endpoint fails."""
    if week < 1:
        raise ValueError("week must be at least 1")
    active_settings = settings or load_settings()
    if league_nickname is not None:
        active_settings = replace(active_settings, league_nickname=league_nickname)
    if _metadata_context is None:
        payload, active_settings, _public, game_id, key = league_metadata(season, league_id, active_settings)
    else:
        payload, _public, game_id, key = _metadata_context
    from .collectors import divisions, draft, league_settings, players, points_recon, projections, schedule, teams
    jobs: dict[str, tuple[str, Callable[..., pd.DataFrame]]] = {
        "player_data": ("player_data", players.get_player_data),
        "projection_data": ("projection_data", projections.get_projection_data),
        "team_data": ("team_data", teams.get_team_data),
        "points_recon": ("points recon", points_recon.get_points_recon),
        "schedule": ("schedule", schedule.get_schedule),
        "divisions": ("divisions", divisions.get_divisions),
        "draft": ("draft", draft.get_draft_data),
        "league_settings": ("league_settings", league_settings.get_league_settings),
    }
    statuses: dict[str, str] = {}
    for name, (folder, collector) in jobs.items():
        path = _snapshot_path(active_settings, league_id, season, folder, week)
        if path.exists() and not overwrite:
            statuses[name] = "skipped_existing"
            continue
        try:
            if _static_frames is not None and name in _static_frames:
                frame = _static_frames[name].copy()
                frame["week"] = week
            else:
                frame = (
                    schedule.get_schedule_matrix(
                        season, str(league_id), week,
                        int(first_value(payload, "end_week", week)),
                        settings=active_settings, game_id=game_id, provider=_public,
                    )
                    if name == "schedule" and _schedule_matrix is None
                    else (_schedule_matrix if name == "schedule" else collector(season, str(league_id), week, settings=active_settings, game_id=game_id, provider=_public))
                )
                # These endpoints return current league settings/original draft,
                # not historical weekly data. Reuse only within this backfill.
                if _static_frames is not None and name in {"draft", "league_settings", "divisions"}:
                    _static_frames[name] = frame.copy()
            statuses[name] = write_snapshot(frame, path, overwrite=True)
        except YahooRateLimitError:
            raise  # Stop backfill instead of issuing more requests while blocked.
        except YahooAuthenticationError:
            statuses[name] = "authentication_required"
        except Exception as error:  # each collector is deliberately isolated
            statuses[name] = f"failed: {type(error).__name__}: {error}"
        time.sleep(active_settings.request_delay)
    update_metadata(active_settings, season, str(league_id), game_id, key, payload, week, statuses)
    return statuses


def backfill_season(season: int, league_id: str, start_week: int = 1, end_week: int | None = None, overwrite: bool = False, *, settings: Settings | None = None, league_nickname: str | None = None, refresh_latest: bool = False) -> dict[int, dict[str, str]]:
    active_settings = settings or load_settings()
    if league_nickname is not None:
        active_settings = replace(active_settings, league_nickname=league_nickname)
    payload, active_settings, public, game_id, key = league_metadata(season, league_id, active_settings)
    resolved_end = end_week or int(first_value(payload, "end_week", start_week))
    if resolved_end < start_week:
        raise ValueError("end_week must not precede start_week")
    from .collectors.schedule import get_schedule_matrix
    schedule_matrix: pd.DataFrame | None = None
    schedule_missing = overwrite or refresh_latest or any(
        not _snapshot_path(active_settings, league_id, season, "schedule", week).exists()
        for week in range(start_week, resolved_end + 1)
    )
    if schedule_missing:
        schedule_matrix = get_schedule_matrix(
            season, str(league_id), start_week, int(first_value(payload, "end_week", resolved_end)),
            settings=active_settings, game_id=game_id, provider=public,
        )
    context = (payload, public, game_id, key)
    static_frames: dict[str, pd.DataFrame] = {}
    return {
        week: collect_week(
            season, league_id, week, overwrite or (refresh_latest and week == resolved_end), settings=active_settings,
            league_nickname=league_nickname, _metadata_context=context,
            _schedule_matrix=schedule_matrix, _static_frames=static_frames,
        )
        for week in range(start_week, resolved_end + 1)
    }
