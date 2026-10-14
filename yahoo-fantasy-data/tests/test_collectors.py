from __future__ import annotations

from yahoo_fantasy_data.collectors.league_settings import parse_settings
from yahoo_fantasy_data.collectors.players import get_player_data
from yahoo_fantasy_data.collectors.teams import get_team_data


class FakeProvider:
    def __init__(self) -> None:
        self.weeks: list[int] = []

    def players(self, key: str, week: int, start: int, count: int, projected: bool = False):
        self.weeks.append(week)
        if start:
            return {"players": []}
        return {"players": {"0": {"player_key": "461.p.7", "player_id": "7", "name": {"full": "Test Player"}, "primary_position": "QB", "player_points": {"coverage_type": "week", "week": week, "total": 0}}, "count": 1}}

    def teams_roster(self, key: str, week: int):
        self.weeks.append(week)
        return {"teams": {"0": {"team_key": "461.l.1.t.1", "team_id": "1", "name": "A", "roster": {"players": {"0": {"player_key": "461.p.7", "selected_position": {"position": "BN"}, "name": {"full": "Test Player"}}}}}}}


def test_player_pagination_and_week_are_preserved() -> None:
    provider = FakeProvider()
    frame = get_player_data(2025, "1", 5, game_id="461", provider=provider)
    assert len(frame) == 1
    assert frame.loc[0, "week"] == 5
    assert provider.weeks == [5, 5]


def test_roster_slot_parsing_and_historical_week() -> None:
    provider = FakeProvider()
    frame = get_team_data(2025, "1", 10, game_id="461", provider=provider)
    assert frame.loc[0, "roster_slot"] == "BN"
    assert not frame.loc[0, "is_starting"]
    assert provider.weeks == [10]


def test_playoff_settings_are_preserved() -> None:
    rows = parse_settings({"settings": {"playoff_start_week": "15",
                                         "num_playoff_teams": "6",
                                         "num_playoff_byes": 0}}, {"season": 2025})
    league = next(row for row in rows if row["setting_type"] == "league")
    assert league["playoff_start_week"] == "15"
    assert league["num_playoff_teams"] == "6"
    assert league["num_playoff_byes"] == 0


def test_scoring_and_roster_settings_are_tabular() -> None:
    rows = parse_settings({"roster_positions": {"0": {"position": "QB", "count": 1}}, "stat_categories": {"0": {"stat_id": "4", "name": "Passing Yards", "value": "0.04"}}}, {"season": 2025})
    assert any(row["setting_type"] == "roster_position" and row["position"] == "QB" for row in rows)
    assert any(row["setting_type"] == "scoring_stat" and row["stat_id"] == "4" for row in rows)


class PagedProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[int] = []

    def players(self, key: str, week: int, start: int, count: int, projected: bool = False):
        self.weeks.append(week)
        self.starts.append(start)
        if start > count:
            return {"players": []}
        size = count if start == 0 else 1
        return {"players": {str(index): {"player_key": f"461.p.{start + index}", "player_id": str(start + index), "name": {"full": f"Player {start + index}"}, "player_points": {"coverage_type": "week", "week": week, "total": 0}} for index in range(size)}}


def test_player_pagination_requests_the_next_offset() -> None:
    provider = PagedProvider()
    frame = get_player_data(2025, "1", 5, game_id="461", provider=provider)
    assert len(frame) == 201
    assert provider.starts == [0, 200, 201]


def test_actual_points_use_only_matching_weekly_points():
    from yahoo_fantasy_data.collectors.common import player_rows
    player = {
        'player_key': '461.p.7',
        'ownership': {'coverage_type': 'season', 'total': 99},
        'player_projected_points': {'coverage_type': 'week', 'week': 5, 'total': 25},
        'player_points': [{'coverage_type': 'week', 'week': 5}, {'total': 0}],
        'player_stats': [{'coverage_type': 'week', 'week': 5},
                         {'stats': [{'stat_id': '4', 'value': 100}]}],
        'player_advanced_stats': {'coverage_type': 'season', 'stats': [{'stat_id': '4', 'value': 999}]},
    }
    row = player_rows({'player': player}, {'week': 5})[0]
    assert row['fantasy_points_actual'] == 0
    assert row['stat_4'] == 100
    player['player_points'] = {'coverage_type': 'week', 'week': 6, 'total': 30}
    assert player_rows({'player': player}, {'week': 5})[0]['fantasy_points_actual'] is None


def test_player_collector_rejects_season_wrong_week_and_blank_scores():
    import pytest
    from yahoo_fantasy_data.errors import YahooAPIError

    class InvalidProvider:
        def players(self, *args):
            return {'player': {'player_key': '461.p.7', 'player_points': self.points}}

    provider = InvalidProvider()
    for points in ({'coverage_type': 'season', 'total': 200},
                   {'coverage_type': 'week', 'week': 6, 'total': 20},
                   {'coverage_type': 'week', 'week': 5, 'total': ''}, {}):
        provider.points = points
        with pytest.raises(YahooAPIError, match='snapshot not saved'):
            get_player_data(2025, '1', 5, game_id='461', provider=provider)


def test_larger_pages_are_paced_and_server_caps_do_not_truncate(monkeypatch):
    from yahoo_fantasy_data.collectors import players, projections
    from yahoo_fantasy_data.config import Settings

    class CappedProvider:
        def __init__(self):
            self.starts = []

        def players(self, key, week, start, count, projected=False):
            assert count == 100
            self.starts.append(start)
            return {'players': [
                {'player_key': f'461.p.{i}',
                 'player_points': {'coverage_type': 'week', 'week': week, 'total': i},
                 'player_projected_points': {'coverage_type': 'week', 'week': week, 'total': i}}
                for i in range(start, min(start + 2, 5))]}

    delays = []
    monkeypatch.setattr(players.time, 'sleep', delays.append)
    for collector in (players.get_player_data, projections.get_projection_data):
        provider = CappedProvider()
        frame = collector(2025, '1', 5, game_id='461', provider=provider,
                          settings=Settings(player_page_size=100, request_delay=2))
        assert len(frame) == 5
        assert provider.starts == [0, 2, 4, 5]
    assert delays == [2] * 6


def test_division_assignments_join_names_and_keep_unassigned_teams():
    from yahoo_fantasy_data.collectors.divisions import get_divisions

    class DivisionProvider:
        def settings(self, key):
            return {'settings': [{'divisions': {'0': {'division': [
                {'division_id': '1'}, {'name': 'East'}]}, 'count': 1}}]}

        def teams(self, key):
            return {'teams': {'0': {'team': [{'team_key': key + '.t.1'},
                    {'name': 'Team A'}, {'division_id': 1}]},
                '1': {'team': [{'team_key': key + '.t.2'}, {'name': 'Team B'}]}, 'count': 2}}

    frame = get_divisions(2025, '1', 5, game_id='461', provider=DivisionProvider())
    assert frame.team_id.tolist() == ['1', '2']
    assert frame.team_name.tolist() == ['Team A', 'Team B']
    assert frame.loc[0, 'division_name'] == 'East'
    assert frame.loc[[1], 'division_name'].isna().all()
    assert frame.week.tolist() == [5, 5]


def test_league_without_divisions_and_empty_team_response():
    import pytest
    from yahoo_fantasy_data.collectors.divisions import get_divisions
    from yahoo_fantasy_data.errors import YahooAPIError

    class DivisionProvider:
        def settings(self, key):
            return {'settings': {}}

        def teams(self, key):
            return {'team': {'team_key': key + '.t.1', 'name': 'Team A'}}

    provider = DivisionProvider()
    frame = get_divisions(2025, '1', 1, game_id='461', provider=provider)
    assert frame.division_id.isna().all()
    assert frame.division_name.isna().all()
    provider.teams = lambda key: {'teams': []}
    with pytest.raises(YahooAPIError, match='snapshot not saved'):
        get_divisions(2025, '1', 1, game_id='461', provider=provider)


def test_divisions_exclude_manager_contacts():
    from yahoo_fantasy_data.collectors.divisions import get_divisions

    class Provider:
        def settings(self, key):
            return {}

        def teams(self, key):
            return {'teams': [
                {'team_key': key + '.t.1', 'name': 'Team A', 'managers': {
                    '0': {'manager': [{'manager_id': '1'}, {'nickname': 'Alex'},
                                      {'email': 'alex@example.test'}]},
                    '1': {'manager': [{'manager_id': '2'}, {'name': 'Sam'},
                                      {'nickname': 'Coach Sam'}]}, 'count': 2}},
                {'team_key': key + '.t.2', 'name': 'Team B'},
                {'team_key': key + '.t.3', 'managers': {'manager': {
                    'manager_id': '3', 'email': 'pat@example.test'}}},
            ]}

    frame = get_divisions(2025, '1', 1, game_id='461', provider=Provider()).set_index('team_id')
    assert 'manager_name' not in frame.columns
    assert 'manager_email' not in frame.columns
    assert len(frame) == 3


def test_schedule_keeps_unplayed_matchups_without_a_winner():
    from yahoo_fantasy_data.collectors.schedule import get_schedule_matrix

    class ScheduleProvider:
        def scoreboard(self, key, week):
            matchup = {'week': week, 'status': 'postevent' if week == 1 else 'preevent',
                       'is_playoffs': int(week == 4),
                       '0': {'teams': [{'team_key': f'{key}.t.{i}', 'team_id': str(i)}
                                      for i in (1, 2)]}}
            if week == 1:
                matchup['winner_team_key'] = f'{key}.t.1'
            return {'scoreboard': {'week': week, 'matchups': [{'matchup': matchup}]}}

    matrix = get_schedule_matrix(2026, '1', 2, 4, game_id='470', provider=ScheduleProvider())
    assert list(matrix.columns) == ['team_key', 'week_1', 'week_2', 'week_3']
    assert matrix.set_index('team_key').loc['470.l.1.t.1', 'week_2'] == '470.l.1.t.2'
    assert matrix.set_index('team_key').loc['470.l.1.t.2', 'week_3'] == '470.l.1.t.1'
