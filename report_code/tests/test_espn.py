"""ESPN collector tests use a fake league shaped like espn-api's objects (no network)."""
import json
import sys
from types import SimpleNamespace as NS

import pytest

from report_code import ReportProcessor, espn, publish
from yahoo_fantasy_data.config import Settings

# QB, 2 RB, 2 WR, TE, 2 W/R/T flex, D/ST; no kicker. Two bench players per team.
LINEUP = [('QB', 'QB'), ('RB', 'RB'), ('RB', 'RB'), ('WR', 'WR'), ('WR', 'WR'), ('TE', 'TE'),
          ('RB/WR/TE', 'RB'), ('RB/WR/TE', 'WR'), ('D/ST', 'D/ST'), ('BE', 'RB'), ('BE', 'WR')]
EXPECTED_SLOTS = ['QB1', 'RB1', 'RB2', 'WR1', 'WR2', 'TE1', 'W/R/T1', 'W/R/T2', 'DEF1']
PAIRS = {1: [(1, 2), (3, 4)], 2: [(1, 3), (2, 4)], 3: [(1, 4), (2, 3)],
         4: [(1, 2), (3, 4)], 5: [(1, 3), (2, 4)]}


def points(team, week, index):
    return float(5 + (team * 7 + week * 3 + index * 5) % 17)


class FakeLeague:
    def __init__(self, divisions=True, current_week=4):
        self.settings = NS(name='Test ESPN', reg_season_count=5, playoff_team_count=2,
                           matchup_periods={str(w): [w] for w in range(1, 6)},
                           scoring_format=[{'abbr': 'PY', 'id': 3, 'points': 0.04}])
        self.current_week = current_week
        self.teams = [NS(team_id=t, team_name=f'Team {t}', division_id=(t + 1) // 2 if divisions else 0,
                         division_name=f'Div {(t + 1) // 2}' if divisions else '', schedule=[])
                      for t in range(1, 5)]
        by_id = {t.team_id: t for t in self.teams}
        for week, pairs in PAIRS.items():
            for a, b in pairs:
                by_id[a].schedule.append(by_id[b])
                by_id[b].schedule.append(by_id[a])
        self.draft = [NS(team=by_id[t], playerId=t * 100 + i, round_num=1 + i // 4, round_pick=t)
                      for t in range(1, 5) for i in (0, 4)]
        self.empty_slot_team = None
        self.activity = []     # transaction log: objects with .date and .actions

    def lineup(self, team, week):
        rows = []
        for index, (slot, position) in enumerate(LINEUP):
            if self.empty_slot_team == team and index == 6:
                continue
            rows.append(NS(playerId=team * 100 + index, name=f'P{team}-{index}', position=position,
                           slot_position=slot, points=points(team, week, index),
                           projected_points=points(team, week, index) - 2, proTeam='XXX'))
        return rows

    def box_scores(self, week):
        boxes = []
        for a, b in PAIRS[week]:
            totals = []
            for team in (a, b):
                totals.append(sum(p.points for p in self.lineup(team, week) if p.slot_position not in ('BE', 'IR')))
            boxes.append(NS(home_team=self.teams[a - 1], away_team=self.teams[b - 1],
                            home_score=totals[0], away_score=totals[1],
                            home_lineup=self.lineup(a, week), away_lineup=self.lineup(b, week)))
        return boxes

    def recent_activity(self, size=25, offset=0):
        return self.activity[offset:offset + size]

    def free_agents(self, week=None, size=50, position=None):
        assert position != 'K', 'a league without kickers must not request kickers'
        ids = {'QB': 901, 'RB': 902, 'WR': 903, 'TE': 904, 'D/ST': 905}
        return [NS(playerId=ids[position], name=f'FA {position}', position=position, proTeam='YYY',
                   stats={week: {'points': 12.0, 'projected_points': 9.0}}),
                NS(playerId=ids[position] + 50, name=f'FA unscored {position}', position=position,
                   proTeam='YYY', stats={})]


def collect(tmp_path, league=None, end=3):
    espn.backfill_season(2026, '123', 1, end, settings=Settings(data_dir=tmp_path),
                         league_nickname='Test', league=league or FakeLeague())
    return tmp_path / 'Test' / '2026'


def test_end_to_end_without_kicker_and_two_flex(tmp_path):
    root = collect(tmp_path)
    report = ReportProcessor(root, 3, simulation_count=10)
    report.validate_reconciliation()
    assert report.silver_reconciliation.status.eq('matched').all()
    assert [f'{s}{n}' for s, n in report.lineup_slots] == EXPECTED_SLOTS
    assert report.divisions == {'1': '1', '2': '1', '3': '2', '4': '2'}
    assert report.silver_lineups.optimal_points.notna().all()
    assert report.gold_playoff_odds.make_playoffs.notna().all()
    data = report.to_dict()
    assert data['vobl']['positions'] == EXPECTED_SLOTS
    assert all('K1' not in row['values'] for row in data['vobl']['rows'])
    assert data['meta']['provider'] == 'espn'
    report.write_html(tmp_path / 'report.html')
    html = (tmp_path / 'report.html').read_text()
    assert 'ESPN games' in html and 'Yahoo games' not in html


def test_free_agents_and_draft(tmp_path):
    root = collect(tmp_path)
    report = ReportProcessor(root, 3, simulation_count=10)
    players = report.silver_player
    free = players[players.team_id.isna() & players.week.eq(1)]
    # The scored free agent of each position is kept; the unscored one is dropped.
    assert sorted(free.player_id) == ['901', '902', '903', '904', '905']
    assert free.actual_points.eq(12.0).all()
    assert players.query("player_id == '100' and week == 1").draft_team_id.astype(str).iloc[0] == '1'
    assert report.bronze_draft.query("player_id == '104'")['pick'].iloc[0] == 5  # round 2, pick 1 of 4 teams


def test_single_division_means_no_divisions(tmp_path):
    root = collect(tmp_path, FakeLeague(divisions=False))
    assert ReportProcessor(root, 3, simulation_count=10).divisions == {}


def test_empty_starter_slot_scores_zero(tmp_path):
    league = FakeLeague()
    league.empty_slot_team = 1
    root = collect(tmp_path, league)
    report = ReportProcessor(root, 3, simulation_count=10)
    report.validate_reconciliation()
    assert report.silver_reconciliation.status.eq('matched').all()
    assert report.silver_lineups.query("team_id == '1'").optimal_points.isna().all()


def test_incomplete_week_and_unsupported_matchup_periods(tmp_path):
    with pytest.raises(ValueError, match='completed regular-season weeks'):
        collect(tmp_path, FakeLeague(current_week=3), end=3)
    league = FakeLeague()
    league.settings.matchup_periods['2'] = [2, 3]
    with pytest.raises(ValueError, match='span several NFL weeks'):
        espn.league_metadata(2026, '123', league)


def test_archive_collision_and_snapshot_reuse(tmp_path):
    root = collect(tmp_path)
    first = root / 'team_data/team_data_week_1.csv.gz'
    before = first.stat().st_mtime_ns
    statuses = espn.backfill_season(2026, '123', 1, 3, settings=Settings(data_dir=tmp_path),
                                    league_nickname='Test', league=FakeLeague())
    assert set(statuses[1].values()) == {'skipped'} and set(statuses[3].values()) == {'written'}
    assert first.stat().st_mtime_ns == before
    path = root / 'metadata.json'
    metadata = json.loads(path.read_text())
    metadata['provider'] = 'yahoo'
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='different league/provider'):
        collect(tmp_path)


def test_provider_validation_and_week_selection(tmp_path, monkeypatch):
    path = tmp_path / 'runs.json'
    run = dict(provider='espn', league_id='123', year=2026, nickname='Test')
    path.write_text(json.dumps([run]))
    assert publish.load_runs(path) == [run]
    monkeypatch.setattr(espn, 'connect', lambda year, league_id: FakeLeague())
    assert publish.resolve_week(run, tmp_path, backfill=True, settings=None) == 3


def test_publish_routes_espn(tmp_path, monkeypatch):
    from yahoo_fantasy_data import yahoo
    monkeypatch.setattr(espn, 'connect', lambda year, league_id: FakeLeague())
    monkeypatch.setattr(yahoo, 'backfill_season', lambda *a, **kw: pytest.fail('Yahoo must not collect ESPN leagues'))
    config = tmp_path / 'runs.json'
    config.write_text(json.dumps([dict(provider='espn', year=2026, league_id='123', nickname='Test',
                                       template='both', strict=True)]))
    monkeypatch.setattr(sys, 'argv', ['publish', '--config', str(config), '--data-dir', str(tmp_path / 'data'),
                                      '--docs-dir', str(tmp_path / 'docs'), '--backfill'])
    publish.main()
    assert len(list((tmp_path / 'docs').glob('*.html'))) == 2
    assert len(list((tmp_path / 'docs').rglob('*.csv.gz'))) == 2


def test_credentials_are_required(monkeypatch):
    monkeypatch.delenv('ESPN_S2', raising=False)
    monkeypatch.delenv('ESPN_SWID', raising=False)
    with pytest.raises(RuntimeError, match='ESPN_S2'):
        espn.connect(2026, '123')
