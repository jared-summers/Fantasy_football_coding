from pathlib import Path

import pandas as pd

from yahoo_fantasy_data.config import Settings
from yahoo_fantasy_data.yahoo import _snapshot_path, write_snapshot


def test_exact_snapshot_filename_and_gzip_round_trip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    path = _snapshot_path(settings, "707737", 2025, "player_data", 12)
    assert path.name == "player_data_week_12.csv.gz"
    frame = pd.DataFrame({"player_id": [1], "player_name": ["A"]})
    assert write_snapshot(frame, path, overwrite=False) == "written"
    assert pd.read_csv(path).to_dict("records") == [{"player_id": 1, "player_name": "A"}]


def test_existing_snapshot_is_immutable_by_default(tmp_path: Path) -> None:
    path = tmp_path / "x.csv.gz"
    write_snapshot(pd.DataFrame({"value": [1]}), path, overwrite=False)
    assert write_snapshot(pd.DataFrame({"value": [2]}), path, overwrite=False) == "skipped_existing"
    assert pd.read_csv(path).loc[0, "value"] == 1


def test_invalid_actual_response_never_replaces_existing_snapshot(tmp_path, monkeypatch):
    from yahoo_fantasy_data.yahoo import collect_week
    from yahoo_fantasy_data.collectors import divisions, draft, league_settings, projections, schedule, teams

    class SeasonProvider:
        def players(self, *args):
            return {'player': {'player_key': '461.p.7',
                               'player_points': {'coverage_type': 'season', 'total': 200}}}

    settings = Settings(data_dir=tmp_path, request_delay=0)
    path = _snapshot_path(settings, '1', 2025, 'player_data', 5)
    write_snapshot(pd.DataFrame({'fantasy_points_actual': [22.5]}), path, overwrite=False)
    original = path.read_bytes()
    for module, name in [(divisions, 'get_divisions'), (draft, 'get_draft_data'), (league_settings, 'get_league_settings'),
                         (projections, 'get_projection_data'), (teams, 'get_team_data'),
                         (schedule, 'get_schedule_matrix')]:
        monkeypatch.setattr(module, name, lambda *args, **kwargs: pd.DataFrame({'test': [1]}))
    kwargs = dict(settings=settings, _metadata_context=({'end_week': 17}, SeasonProvider(), '461', '461.l.1'))
    statuses = collect_week(2025, '1', 5, overwrite=True, **kwargs)
    assert statuses['player_data'].startswith('failed: YahooAPIError:')
    assert path.read_bytes() == original
    statuses = collect_week(2025, '1', 6, overwrite=False, **kwargs)
    assert statuses['player_data'].startswith('failed: YahooAPIError:')
    assert not _snapshot_path(settings, '1', 2025, 'player_data', 6).exists()


def test_rate_limit_stops_collection_before_next_dataset(tmp_path, monkeypatch):
    import pytest
    from yahoo_fantasy_data.collectors import players, projections
    from yahoo_fantasy_data.errors import YahooRateLimitError
    from yahoo_fantasy_data.yahoo import collect_week

    def blocked(*args, **kwargs):
        raise YahooRateLimitError('Yahoo returned HTTP 999')

    monkeypatch.setattr(players, 'get_player_data', blocked)
    monkeypatch.setattr(projections, 'get_projection_data', lambda *a, **k: pytest.fail('Must stop after 999'))
    with pytest.raises(YahooRateLimitError):
        collect_week(2025, '1', 5, settings=Settings(data_dir=tmp_path),
                     _metadata_context=({'end_week': 17}, object(), '461', '461.l.1'))
    assert not list(tmp_path.rglob('*.csv.gz'))


def test_backfill_reuses_static_endpoints_only_within_one_run(tmp_path, monkeypatch):
    from collections import Counter
    from yahoo_fantasy_data import yahoo
    from yahoo_fantasy_data.collectors import divisions, draft, league_settings, players, projections, points_recon, teams, schedule

    calls = Counter()
    settings = Settings(data_dir=tmp_path, request_delay=0)
    monkeypatch.setattr(yahoo, 'league_metadata', lambda season, league, active:
                        ({'end_week': 3}, active, object(), '461', f'461.l.{league}'))
    def collector(name):
        def fetch(season, league, week, **kwargs):
            calls[name] += 1
            return pd.DataFrame({'season': [season], 'week': [week], 'league_id': [league]})
        return fetch
    for module, function in [(divisions, 'get_divisions'), (draft, 'get_draft_data'), (league_settings, 'get_league_settings'),
                             (players, 'get_player_data'), (projections, 'get_projection_data'),
                             (points_recon, 'get_points_recon'), (teams, 'get_team_data')]:
        monkeypatch.setattr(module, function, collector(function))
    def matrix(*args, **kwargs):
        calls['schedule'] += 1
        return pd.DataFrame({'team_key': ['461.l.1.t.1']})
    monkeypatch.setattr(schedule, 'get_schedule_matrix', matrix)
    yahoo.backfill_season(2025, '1', 1, 3, settings=settings)
    assert calls['get_divisions'] == calls['get_draft_data'] == calls['get_league_settings'] == calls['schedule'] == 1
    assert calls['get_player_data'] == calls['get_projection_data'] == 3
    for week in (1, 2, 3):
        for folder in ('draft', 'league_settings', 'divisions'):
            assert pd.read_csv(_snapshot_path(settings, '1', 2025, folder, week)).week.tolist() == [week]
    before = calls.copy()
    yahoo.backfill_season(2025, '1', 1, 3, settings=settings)
    assert calls == before  # Existing snapshots are skipped.
    yahoo.backfill_season(2025, '2', 1, 3, settings=settings)
    assert calls['get_divisions'] == calls['get_draft_data'] == calls['get_league_settings'] == 2
    assert pd.read_csv(_snapshot_path(settings, '2', 2025, 'draft', 3)).league_id.tolist() == [2]


def test_backfill_refreshes_latest_week_and_collects_future_schedule(tmp_path, monkeypatch):
    from yahoo_fantasy_data import yahoo
    from yahoo_fantasy_data.collectors import schedule

    settings = Settings(data_dir=tmp_path, request_delay=0)
    monkeypatch.setattr(yahoo, 'league_metadata', lambda season, league, active:
                        ({'end_week': 17}, active, object(), '470', '470.l.1'))
    for week in (1, 2):
        write_snapshot(pd.DataFrame({'team_key': ['470.l.1.t.1']}),
                       _snapshot_path(settings, '1', 2026, 'schedule', week), False)
    schedule_calls = []
    def matrix(season, league, snapshot_week, end_week, **kwargs):
        schedule_calls.append((snapshot_week, end_week))
        return pd.DataFrame({'team_key': ['470.l.1.t.1'], 'week_14': ['470.l.1.t.2']})
    monkeypatch.setattr(schedule, 'get_schedule_matrix', matrix)
    collected = []
    def collect(season, league, week, overwrite, **kwargs):
        collected.append((week, overwrite))
        assert 'week_14' in kwargs['_schedule_matrix']
        return {'player_data': 'written' if overwrite else 'skipped_existing'}
    monkeypatch.setattr(yahoo, 'collect_week', collect)
    yahoo.backfill_season(2026, '1', 1, 2, settings=settings, refresh_latest=True)
    assert collected == [(1, False), (2, True)]
    assert schedule_calls == [(1, 17)]
