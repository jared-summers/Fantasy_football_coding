import pandas as pd
import pytest

from yahoo_fantasy_data.collectors.points_recon import get_points_recon
from yahoo_fantasy_data.config import Settings
from yahoo_fantasy_data.errors import YahooAPIError
from yahoo_fantasy_data.providers.public import PublicYahooProvider
from yahoo_fantasy_data.yahoo import _snapshot_path, collect_week


class Provider:
    def team_points(self, key, week):
        return {'teams': {'0': {'team_key': f'{key}.t.1', 'team_id': '1', 'name': 'A',
                               'team_points': [{'coverage_type': 'week', 'week': week}, {'total': 0}]},
                          '1': {'team_key': f'{key}.t.2', 'name': 'B',
                                'team_points': {'coverage_type': 'week', 'week': week, 'total': '111.72'}}}}


def test_weekly_totals_include_zero_and_teams_without_matchups():
    frame = get_points_recon(2025, '1', 5, game_id='461', provider=Provider())
    assert frame.team_id.tolist() == ['1', '2']
    assert frame.official_points.tolist() == [0, 111.72]
    assert frame.week.tolist() == [5, 5]
    assert frame.points_week_returned.tolist() == [5, 5]


@pytest.mark.parametrize('points', [
    {'coverage_type': 'season', 'total': 1000},
    {'coverage_type': 'week', 'week': 4, 'total': 20},
    {'coverage_type': 'week', 'week': 5, 'total': None},
    {'coverage_type': 'week', 'week': 5, 'total': 'NaN'},
])
def test_invalid_points_fail_before_writing(points):
    class BadProvider:
        def team_points(self, *args):
            return {'team': {'team_key': '461.l.1.t.1', 'team_points': points}}
    with pytest.raises(YahooAPIError, match='snapshot not saved'):
        get_points_recon(2025, '1', 5, game_id='461', provider=BadProvider())


def test_team_endpoint_places_week_on_stats():
    class Client:
        def get(self, path, *, params):
            assert path == 'league/461.l.1/teams/stats;type=week;week=5'
            assert params == {'format': 'json'}
            return {}
    PublicYahooProvider(Client()).team_points('461.l.1', 5)


def test_collection_adds_missing_recon_and_skips_existing_files(tmp_path):
    settings = Settings(data_dir=tmp_path, league_nickname='Test', request_delay=0)
    # Existing datasets must survive adding the new dataset to an old archive.
    existing = []
    for name in ('player_data', 'projection_data', 'team_data', 'schedule', 'draft', 'league_settings'):
        path = _snapshot_path(settings, '1', 2025, name, 5)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'keep existing file')
        existing.append(path)
    kwargs = dict(settings=settings, _metadata_context=({'end_week': 17}, Provider(), '461', '461.l.1'))
    statuses = collect_week(2025, '1', 5, **kwargs)
    path = tmp_path / 'Test/2025/points recon/points_recon_week_5.csv.gz'
    assert statuses['points_recon'] == 'written'
    assert pd.read_csv(path).official_points.tolist() == [0, 111.72]
    assert all(p.read_bytes() == b'keep existing file' for p in existing)
    original = path.read_bytes()
    assert collect_week(2025, '1', 5, **kwargs)['points_recon'] == 'skipped_existing'
    assert path.read_bytes() == original
