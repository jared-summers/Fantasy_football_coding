"""Independent Yahoo weekly team totals for player-to-team reconciliation."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .common import context_row, resolve
from ..config import Settings
from ..errors import YahooAPIError
from ..utils import entity_objects, first_value, stable_frame


def get_points_recon(season: int, league_id: str, week: int, *, settings: Settings | None = None,
                     game_id: str | None = None, provider: Any | None = None) -> pd.DataFrame:
    """Fetch every team's own weekly total, including teams without a matchup.

    Totals are Yahoo's reported values at collection time, not a sum of player
    scores or a current-season cumulative total labeled with a historical week.
    """
    settings, provider, game_id, key = resolve(season, league_id, week, settings, game_id, provider)
    payload = provider.team_points(key, week)
    records = []
    for team in entity_objects(payload, 'team_key'):
        points = team.get('team_points', {})
        coverage = first_value(points, 'coverage_type')
        returned_week = first_value(points, 'week')
        if coverage != 'week' or str(returned_week) != str(week):
            raise YahooAPIError(
                f"Team {team['team_key']}: requested week {week}, returned "
                f"coverage={coverage!r}, week={returned_week!r}; snapshot not saved."
            )
        try:
            total = float(first_value(points, 'total'))
        except (TypeError, ValueError) as exc:
            raise YahooAPIError(f"Team {team['team_key']}: missing numeric weekly points; snapshot not saved.") from exc
        if not math.isfinite(total):
            raise YahooAPIError(f"Team {team['team_key']}: nonfinite weekly points; snapshot not saved.")
        records.append({
            **context_row(season, league_id, week, game_id),
            'team_id': str(team.get('team_id') or str(team['team_key']).split('.')[-1]),
            'team_key': team['team_key'],
            'team_name': team.get('name'),
            'official_points': total,
            'points_coverage_type': coverage,
            'points_week_returned': int(returned_week),
        })
    if not records:
        raise YahooAPIError(f"Yahoo returned no team points for week {week}; snapshot not saved.")
    frame = stable_frame(records, ['team_id'])
    if frame.duplicated(['team_id', 'week']).any():
        raise YahooAPIError('Duplicate team/week point records; snapshot not saved.')
    return frame
