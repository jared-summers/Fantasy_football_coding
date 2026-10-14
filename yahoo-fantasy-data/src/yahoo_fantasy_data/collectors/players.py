"""Complete league player-universe collector with conservative pagination."""
from __future__ import annotations

import time
from typing import Any
import pandas as pd

from .common import context_row, player_rows, resolve
from ..config import Settings
from ..errors import YahooAPIError
from ..utils import stable_frame


def get_player_data(season: int, league_id: str, week: int, *, settings: Settings | None = None, game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    base = context_row(season, league_id, week, game_id)
    records: list[dict[str, Any]] = []
    start, page_size = 0, settings.player_page_size if settings is not None else 200
    if page_size < 1:
        raise ValueError("player_page_size must be positive")
    seen = set()
    while True:
        payload = provider.players(key, week, start, page_size)
        page = player_rows(payload, base)
        for row in page:
            for prefix in ("actual", "actual_stats"):
                coverage = row.get(f"{prefix}_coverage_returned")
                returned_week = row.get(f"{prefix}_week_returned")
                if (coverage is not None or returned_week is not None) and (
                    coverage != "week" or str(returned_week) != str(week)
                ):
                    raise YahooAPIError(
                        f"Player {row['player_id']}: requested week {week}, but Yahoo returned "
                        f"{prefix} coverage={coverage!r}, week={returned_week!r}; snapshot not saved."
                    )
        if not page:
            break
        ids = [row["player_id"] for row in page]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            raise YahooAPIError("Yahoo repeated player IDs during pagination; snapshot not saved.")
        seen.update(ids)
        records.extend(page)
        # Advance by the actual response size in case Yahoo caps a larger request.
        # Only an empty page proves completion under a silently capped endpoint.
        start += len(page)
        if settings is not None:
            time.sleep(settings.request_delay)
    frame = stable_frame(records, ["player_id"])
    if frame.empty or pd.to_numeric(frame["fantasy_points_actual"], errors="coerce").notna().sum() == 0:
        raise YahooAPIError(f"Yahoo returned no numeric actual player scores for week {week}; snapshot not saved.")
    return frame
