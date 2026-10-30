import json
import re
from pathlib import Path

import pandas as pd
import pytest

from report_code import ReportProcessor
from report_code.lineups import optimize


@pytest.fixture
def archive(tmp_path):
    (tmp_path/'metadata.json').write_text(json.dumps({'current_week':2,'last_collected_week':3,
        'start_week':1,'season':2025,'league_key':'l','league_name':'Test </script><script>bad()</script>'}))
    def write(name, week, rows):
        d=tmp_path/name; d.mkdir(exist_ok=True)
        pd.DataFrame(rows).to_csv(d/f'{name}_week_{week}.csv.gz',index=False)
    for week in (1,2,3):
        players=[]; projections=[]; rosters=[]
        for pid,team,pos,slot,score in [('1','1','RB','RB',10),('2','1','WR','W/R/T',8),
                ('3','1','RB','BN',20),('4','2','RB','RB',12),('5','2','WR','W/R/T',14),
                ('6','2','WR','BN',3),('7',None,'RB',None,7),('8',None,'WR',None,5)]:
            players.append(dict(player_id=pid,week=week,primary_position=pos,player_name='Player '+pid,
                                fantasy_points_actual=score+week,player_points_coverage_type='week'))
            projections.append(dict(player_id=pid,week=week,projected_points=int(pid)+week))
            if team:
                rosters.append(dict(player_id=pid,week=week,team_id=team,team_name='Team '+team,
                                    roster_slot=slot,is_starting=slot!='BN'))
        write('player_data',week,players); write('projection_data',week,projections); write('team_data',week,rosters)
        write('draft',week,[dict(player_id=str(i),week=week,team_id='1' if i<4 else '2',round=i) for i in range(1,7)])
        write('league_settings',week,[dict(week=week,setting_type='league',playoff_start_week=4,num_playoff_teams=1,num_playoff_byes=0),
            dict(week=week,setting_type='roster_position',position='RB',count=1),
            dict(week=week,setting_type='roster_position',position='W/R/T',count=1)])
        write('schedule',week,[{'team_key':'l.t.1','week_1':'l.t.2','week_2':'l.t.2','week_3':'l.t.2'},
                              {'team_key':'l.t.2','week_1':'l.t.1','week_2':'l.t.1','week_3':'l.t.1'}])
    official=tmp_path/'scores.csv'
    pd.DataFrame([dict(team_id=t,week=w,official_points=s+2*w) for t,s in [('1',18),('2',26)] for w in (1,2,3)]).to_csv(official,index=False)
    return tmp_path


def test_team_nicknames_override_display_names(archive):
    (archive/'team_nicknames.json').write_text(json.dumps({'1': ' Joe ', '99': 'Unused'}))
    processor = ReportProcessor(archive/'metadata.json')
    teams = processor.teams.set_index('team_id')
    assert teams.loc['1', ['team_name', 'manager']].tolist() == ['Joe', 'Joe']
    assert teams.loc['2', 'team_name'] == 'Team 2'
    players = processor.silver_player
    assert players.loc[players.team_id.eq('1'), 'team_name'].eq('Joe').all()
    assert players.loc[players.team_id.isna(), 'team_name'].isna().all()
    assert processor.bronze_team_data.query("team_id == '1'").team_name.eq('Team 1').all()
    processor.bronze_divisions = pd.DataFrame([
        dict(team_id='1', team_name='Team 1', division_id='1', division_name='East', week=2)])
    assert processor.silver_divisions.iloc[0].team_name == 'Joe'


@pytest.mark.parametrize('contents', [None, '{bad', '[]', 'null', '42',
    '{"1": null, "2": "  "}', '{"1": 12, "2": {}}', b'\xff'])
def test_team_nicknames_fallback(archive, contents):
    path = archive/'team_nicknames.json'
    if isinstance(contents, bytes):
        path.write_bytes(contents)
    elif contents is not None:
        path.write_text(contents)
    assert ReportProcessor(archive).teams.team_name.tolist() == ['Team 1', 'Team 2']


def test_team_nicknames_invalid_entry_and_unreadable_file(archive):
    path = archive/'team_nicknames.json'
    path.write_text('{"1": "Joe", "2": false}')
    assert ReportProcessor(archive).teams.team_name.tolist() == ['Joe', 'Team 2']
    path.unlink()
    path.mkdir()
    assert ReportProcessor(archive).teams.team_name.tolist() == ['Team 1', 'Team 2']


def test_metadata_week_snapshot_selection_and_joins(archive):
    r=ReportProcessor(archive)
    assert r.current_week==2
    assert len(r.silver_player)==16
    assert r.bronze_player.week.max()==2
    assert r.bronze_settings.week.unique().tolist()==[2]
    player=r.silver_player.query("player_id == '1'")
    assert player.projected_points.tolist()==[2,3]
    assert player.draft_round.tolist()==[1,1]
    assert ReportProcessor(archive/'metadata.json',1).bronze_draft.week.unique().tolist()==[1]


def test_silver_player_compact_schema_and_aliases(archive):
    processor = ReportProcessor(archive)
    processor.bronze_player['bye_week'] = None
    processor.bronze_player['bye_weeks_week'] = 7
    processor.bronze_player['editorial_team_abbr'] = 'BUF'
    table = processor.silver_player
    assert table.columns.tolist() == [
        'player_id', 'player_name', 'week', 'position', 'eligible_positions',
        'nfl_team', 'bye_week', 'team_id', 'team_name', 'roster_slot', 'is_starting',
        'actual_points', 'projected_points', 'draft_round', 'draft_pick', 'draft_team_id',
        'is_stud', 'is_dud', 'volatility_known', 'rank_rb', 'rank_w_r_t', 'is_optimal',
    ]
    assert table.bye_week.eq(7).all()
    assert table.nfl_team.eq('BUF').all()
    assert table.iloc[0].position == 'RB'
    assert table.iloc[0].eligible_positions == ['RB']
    assert table.draft_pick.isna().all()
    assert table.loc[table.player_id.eq('7'), 'team_id'].isna().all()
    assert optimize(table.iloc[:3], [('RB', 1), ('W/R/T', 1)]) == [2, 0]


def test_player_ranks_and_optimal_flags(archive):
    processor = ReportProcessor(archive)
    table = processor.silver_player
    week = table.query('week == 1').set_index('player_id')
    assert week.loc[['3', '1'], 'rank_rb'].tolist() == [1, 2]
    assert pd.isna(week.loc['2', 'rank_rb'])
    assert week.loc[['3', '1', '2'], 'rank_w_r_t'].tolist() == [1, 2, 3]
    assert week.loc['4', 'rank_rb'] == 1  # Separate fantasy team.
    assert week.loc[['7', '8'], 'rank_w_r_t'].tolist() == [1, 2]  # FA pool.
    assert set(week.index[week.is_optimal]) == {'1', '3', '4', '5', '7', '8'}
    for row in processor.silver_lineups.itertuples():
        group = table[table.team_id.eq(row.team_id) & table.week.eq(row.week)]
        assert set(group.index[group.is_optimal]) == set(row.optimal_player_indices)
        assert group.loc[group.is_optimal, 'actual_points'].sum() == row.optimal_points


def test_player_ranks_ties_reserves_and_missing_data(archive):
    processor = ReportProcessor(archive, 1)
    processor.bronze_player.loc[processor.bronze_player.player_id.eq('3'), 'fantasy_points_actual'] = 11
    table = processor.silver_player.set_index('player_id')
    assert table.loc[['1', '3'], 'rank_rb'].tolist() == [1, 1]
    assert table.loc['2', 'rank_w_r_t'] == 3

    processor = ReportProcessor(archive, 1)
    processor.bronze_team_data.loc[processor.bronze_team_data.player_id.eq('3'), 'roster_slot'] = 'IR'
    table = processor.silver_player.set_index('player_id')
    assert pd.isna(table.loc['3', 'rank_rb'])
    assert not table.loc['3', 'is_optimal']
    assert table.loc[['1', '2'], 'is_optimal'].all()

    processor = ReportProcessor(archive, 1)
    processor.bronze_player.loc[processor.bronze_player.player_id.isin(['3', '7']), 'fantasy_points_actual'] = None
    table = processor.silver_player
    assert table.loc[table.team_id.eq('1') | table.team_id.isna(), 'is_optimal'].isna().all()
    assert table.loc[table.player_id.eq('3'), 'rank_rb'].isna().all()
    assert table.loc[table.team_id.eq('2'), 'is_optimal'].notna().all()

    processor = ReportProcessor(archive, 1)
    processor.bronze_player.drop(processor.bronze_player.index[processor.bronze_player.player_id.eq('3')], inplace=True)
    assert processor.silver_player.loc[lambda f: f.team_id.eq('1'), 'is_optimal'].isna().all()


def test_optimal_flags_respect_repeated_slots_and_multi_position():
    from report_code.lineups import add_player_lineup_columns
    players = pd.DataFrame([
        dict(player_id=str(i), week=1, team_id=None, roster_slot=None,
             eligible_positions=positions, actual_points=points)
        for i, positions, points in [(1, ['RB', 'WR'], 30), (2, ['RB'], 20),
                                     (3, ['RB'], 10), (4, ['WR'], 5)]
    ])
    slots = [('RB', 1), ('RB', 2), ('WR', 1), ('W/R/T', 1)]
    table = add_player_lineup_columns(players, slots, players.iloc[:0])
    assert table.is_optimal.all()
    assert table.rank_rb.tolist()[:3] == [1, 2, 3]
    incomplete = add_player_lineup_columns(players.iloc[:3], slots, players.iloc[:0])
    assert incomplete.is_optimal.isna().all()


def test_division_snapshot_selection(archive):
    directory = archive / 'divisions'
    directory.mkdir()
    for week in (1, 2, 3):
        pd.DataFrame([
            dict(team_id='1', team_name='Team 1', division_id='01',
                 division_name=f'East {week}', week=week),
            dict(team_id='2', team_name='Team 2', division_id=None,
                 division_name=None, week=week),
        ]).to_csv(directory / f'divisions_week_{week}.csv.gz', index=False)
    processor = ReportProcessor(archive)
    table = processor.silver_divisions
    assert table.week.tolist() == [2, 2]
    assert table.division_id.iloc[0] == '01'
    assert pd.isna(table.division_id.iloc[1])
    assert table.division_name.iloc[0] == 'East 2'
    assert len(processor.source_files['divisions']) == 1
    assert ReportProcessor(archive, 1).silver_divisions.week.tolist() == [1, 1]
    (directory / 'divisions_week_2.csv.gz').unlink()
    assert ReportProcessor(archive).silver_divisions.week.tolist() == [1, 1]
    override = ReportProcessor(archive, data_files={'divisions': directory / 'divisions_week_1.csv.gz'})
    assert override.silver_divisions.week.tolist() == [1, 1]
    (directory / 'divisions_week_1.csv.gz').unlink()
    with pytest.raises(FileNotFoundError, match='No divisions files through week 2'):
        _ = ReportProcessor(archive).silver_divisions


def test_division_duplicate_teams_are_rejected(archive):
    processor = ReportProcessor(archive)
    processor.bronze_divisions = pd.DataFrame({'team_id': ['1', '1']})
    with pytest.raises(ValueError, match='duplicate join keys'):
        _ = processor.silver_divisions


def test_reconciliation_and_missing_scores(archive):
    r=ReportProcessor(archive,official_scores_path=archive/'scores.csv')
    r.validate_reconciliation()
    assert r.silver_season_reconciliation.calculated_points.tolist()==[42,58]
    assert r.silver_reconciliation.status.eq('matched').all()
    r=ReportProcessor(archive)
    assert r.silver_reconciliation.status.eq('unverified').all()
    with pytest.raises(ValueError,match='could not be reconciled'): r.validate_reconciliation()
    r=ReportProcessor(archive,official_scores_path=archive/'scores.csv')
    r.bronze_player.loc[0,'fantasy_points_actual']=None
    assert r.silver_reconciliation.iloc[0].status=='missing_player_points_or_roster'
    assert pd.isna(r.silver_season_reconciliation.iloc[0].calculated_points)


def test_archived_official_scores_reconcile_and_respect_week(archive):
    folder = archive / 'points recon'
    folder.mkdir()
    scores = pd.read_csv(archive / 'scores.csv')
    for week, frame in scores.groupby('week'):
        frame.to_csv(folder / f'points_recon_week_{week}.csv.gz', index=False)
    processor = ReportProcessor(archive)
    assert processor.bronze_points_recon.week.tolist() == [1, 1, 2, 2]
    assert processor.bronze_points_recon.team_id.tolist() == ['1', '2', '1', '2']
    processor.validate_reconciliation()
    assert processor.silver_season_reconciliation.official_points.tolist() == [42, 58]
    assert len(processor.source_files['points_recon']) == 2
    assert ReportProcessor(archive, 1).silver_reconciliation.official_points.tolist() == [20, 28]

    scores.loc[0, 'official_points'] += 1
    scores.to_csv(archive / 'override.csv', index=False)
    override = ReportProcessor(archive, official_scores_path=archive / 'override.csv')
    assert override.silver_reconciliation.iloc[0].status == 'mismatch'
    assert 'points_recon' not in override.source_files

    (folder / 'points_recon_week_1.csv.gz').unlink()
    partial = ReportProcessor(archive).silver_reconciliation
    assert partial.query('week == 1').official_points.isna().all()
    assert partial.query('week == 1').status.eq('unverified').all()
    assert partial.query('week == 2').status.eq('matched').all()
    assert ReportProcessor(archive, 1).silver_reconciliation.status.eq('unverified').all()


def test_official_score_source_override_and_duplicate_keys(archive):
    processor = ReportProcessor(archive, data_files={'points_recon': archive / 'scores.csv'})
    processor.validate_reconciliation()
    processor = ReportProcessor(archive, data_files={'points_recon': [archive / 'scores.csv'] * 2})
    with pytest.raises(ValueError, match='duplicate join keys'):
        _ = processor.silver_reconciliation
    processor = ReportProcessor(archive, data_files={'points_recon': archive / 'missing.csv'})
    with pytest.raises(FileNotFoundError):
        _ = processor.silver_reconciliation


def test_missing_player_and_duplicate_roster_are_detected(archive):
    r=ReportProcessor(archive)
    r.bronze_player.drop(index=0,inplace=True)
    assert r.silver_reconciliation.iloc[0].status=='missing_player_points_or_roster'
    r=ReportProcessor(archive)
    r.bronze_projection.loc[len(r.bronze_projection)]=r.bronze_projection.iloc[0]
    with pytest.raises(ValueError,match='duplicate join keys'): _=r.silver_player


def test_weekly_coverage_and_official_mismatch(archive):
    r=ReportProcessor(archive)
    r.bronze_player.loc[0,'player_points_coverage_type']='season'
    assert pd.isna(r.silver_player.iloc[0].actual_points)
    scores=pd.read_csv(archive/'scores.csv'); scores.loc[0,'official_points']+=1
    scores.to_csv(archive/'scores.csv',index=False)
    r=ReportProcessor(archive,official_scores_path=archive/'scores.csv')
    assert r.silver_reconciliation.iloc[0].status=='mismatch'


def test_flex_optimizer_does_not_reuse_players():
    players=pd.DataFrame([dict(primary_position='RB',actual_points=10),
                          dict(primary_position='WR',actual_points=20),
                          dict(primary_position='RB',actual_points=9)])
    selected=optimize(players,[('W/R/T',1),('RB',1)])
    assert selected==[1,0]
    future=pd.DataFrame([dict(primary_position='WR',eligible_positions_to_add_0_position='QB',actual_points=20)])
    assert optimize(future,[('QB',1)]) is None
    players.loc[2,'actual_points']=None
    assert optimize(players,[('W/R/T',1),('RB',1)]) is None


def test_gold_calculations_and_round_trip(archive,tmp_path):
    r=ReportProcessor(archive,simulation_count=100,official_scores_path=archive/'scores.csv')
    payload=r.to_dict()
    assert len(r.gold_tables)==11
    assert all(isinstance(t,pd.DataFrame) for t in r.gold_tables.values())
    assert r.gold_managerial_expertise.iloc[0].actual_points==42
    assert r.gold_managerial_expertise.iloc[0].optimal_points==66
    assert payload['freaky_friday']['schedule_swap']['matrix']['1']['1']==0
    assert payload['freaky_friday']['schedule_swap']['matrix']['1']['2']==0
    assert payload['freaky_friday']['head_to_head']['matrix']['2']['1']==2
    assert sum(payload['draft_analysis']['historical'][0]['values'].values())==42
    assert sum(payload['draft_analysis']['optimal'][0]['values'].values())==66
    assert sum(x['make_playoffs'] for x in payload['playoff_odds']['rows'])==pytest.approx(1)
    assert r.gold_bhole.iloc[0]['range']==2
    json.loads(r.to_json(),parse_constant=lambda v: pytest.fail(v))
    output=r.write_html(tmp_path/'report.html').read_text()
    embedded=re.search(r'<script id="report-data" type="application/json">(.*?)</script>',output,re.S)[1]
    assert json.loads(embedded)['meta']['league_name']==r.metadata['league_name']
    assert '<script>bad()' not in output


def test_html_template_selection(tmp_path, monkeypatch):
    report = ReportProcessor.__new__(ReportProcessor)
    payload = {'meta': {'league_name': '</script><script>bad()</script>'}}
    monkeypatch.setattr(report, 'to_json', lambda: json.dumps(payload))
    original = report.write_html(tmp_path/'original.html').read_text()
    assert original == report.write_html(tmp_path/'explicit.html', template='original').read_text()
    pc = report.write_html(tmp_path/'pc.html', template='pc').read_text()
    assert "'Grower or Shower'" in original
    assert "'Actual vs Expected Wins'" in pc
    assert "'Grower or Shower'" not in pc
    assert "'Decision efficiency ratio'" in pc
    embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', pc, re.S)[1]
    assert json.loads(embedded) == payload
    assert '<script>bad()' not in pc
    custom = tmp_path/'custom.html'
    custom.write_text('<script id="report-data" type="application/json">{}</script>')
    assert report.write_html(tmp_path/'custom-output.html', custom).read_text().startswith('<script')
    with pytest.raises(ValueError, match='template must'):
        report.write_html(tmp_path/'invalid.html', template='unknown')


def test_draft_ownership_and_repeatable_simulation(archive):
    r=ReportProcessor(archive,simulation_count=100)
    r.bronze_draft.loc[r.bronze_draft.player_id.eq('1'),'team_id']='2'
    assert r.gold_draft_analysis.iloc[0]['values']['FA']==23
    a=ReportProcessor(archive,simulation_count=100).gold_playoff_odds
    b=ReportProcessor(archive,simulation_count=100).gold_playoff_odds
    pd.testing.assert_frame_equal(a,b)


def test_missing_whole_week_never_becomes_zero(archive):
    (archive/'player_data/player_data_week_2.csv.gz').unlink()
    r=ReportProcessor(archive)
    assert r.silver_reconciliation.query('week == 2').calculated_points.isna().all()
    assert r.gold_managerial_expertise.actual_points.isna().all()
    assert r.gold_draft_analysis['values'].map(lambda d: all(pd.isna(v) for v in d.values())).all()


def test_future_only_and_bad_weeks(archive):
    with pytest.raises(ValueError): ReportProcessor(archive,0)
    with pytest.raises(ValueError): ReportProcessor(archive,1.5)
    with pytest.raises(FileNotFoundError):
        _=ReportProcessor(archive,1,data_files={'draft':archive/'draft/draft_week_3.csv.gz'}).bronze_draft


def test_wrong_projection_week_is_unavailable(archive):
    r=ReportProcessor(archive)
    r.bronze_projection['projection_week_returned']=r.bronze_projection.week
    r.bronze_projection.loc[0,'projection_week_returned']=9
    assert pd.isna(r.silver_player.iloc[0].projected_points)


def test_division_winners_and_wildcard_probabilities(archive):
    r=ReportProcessor(archive,simulation_count=100,playoff_teams=2,playoff_byes=1,
                      divisions={'1':'East','2':'West'})
    odds=r.gold_playoff_odds
    assert odds.make_playoffs.eq(1).all()
    assert odds.division.eq(1).all()
    assert odds.wildcard.eq(0).all()
    assert odds.bye.sum()==pytest.approx(1)
    assert odds.last_place.sum()==pytest.approx(1)
    r=ReportProcessor(archive,divisions={'1':'East'})
    with pytest.raises(ValueError,match='every team_id'): _=r.gold_playoff_odds


def test_team_missing_from_all_rosters_still_reconciled(archive):
    r=ReportProcessor(archive)
    r.bronze_team_data.drop(r.bronze_team_data.index[r.bronze_team_data.team_id.eq('2')],inplace=True)
    assert len(r.teams)==2
    assert r.silver_reconciliation.query("team_id == '2'").calculated_points.isna().all()


def test_boned_index_known_opponent_baselines(archive):
    r=ReportProcessor(archive)
    r.teams=pd.DataFrame({'team_id':['1','2','3','4'],'team_name':['A','B','C','D'],'manager':['A','B','C','D']})
    r.silver_team_week=pd.DataFrame([
        dict(team_id='1',week=1,actual_points=10,opponent_id='2',opponent_points=20),
        dict(team_id='2',week=1,actual_points=20,opponent_id='1',opponent_points=10),
        dict(team_id='3',week=1,actual_points=30,opponent_id='4',opponent_points=40),
        dict(team_id='4',week=1,actual_points=40,opponent_id='3',opponent_points=30),
        dict(team_id='1',week=2,actual_points=20,opponent_id='3',opponent_points=10),
        dict(team_id='3',week=2,actual_points=10,opponent_id='1',opponent_points=20),
        dict(team_id='2',week=2,actual_points=30,opponent_id='4',opponent_points=20),
        dict(team_id='4',week=2,actual_points=20,opponent_id='2',opponent_points=30)])
    assert r.gold_boned_index.query("team_id == '1'").value.iloc[0]==pytest.approx(-50)
    assert r.gold_boned_detail.query("team_id == '1'").opponent_avg.tolist()==[30,30]


def test_stud_dud_thresholds_and_bench_exclusion(archive):
    r=ReportProcessor(archive,1)
    r.bronze_player['fantasy_points_actual']=r.bronze_player.fantasy_points_actual.astype(float)
    for pid,actual,projected in [('1',20,10),('2',20,9),('3',30,10),('4',2.5,10),('5',0,9)]:
        r.bronze_player.loc[r.bronze_player.player_id.eq(pid),'fantasy_points_actual']=actual
        r.bronze_projection.loc[r.bronze_projection.player_id.eq(pid),'projected_points']=projected
    out=r.gold_studs_duds.set_index('team_id')
    assert out.loc['1','studs']==1
    assert out.loc['2','duds']==1
    assert out.loc['1','dudded_on']==1
    assert out.loc['1','s2d_diff']==2


def test_archived_divisions_feed_playoff_odds(archive):
    folder = archive / 'divisions'
    folder.mkdir()
    for week in (1, 2, 3):
        pd.DataFrame([dict(week=week, team_id=t, team_name='Team '+t,
                           division_id=t if week == 2 else 'same', division_name='Division '+t)
                      for t in ('1', '2')]).to_csv(folder / f'divisions_week_{week}.csv.gz', index=False)
    r = ReportProcessor(archive, playoff_teams=2, playoff_byes=1, simulation_count=100)
    assert r.divisions == {'1': '1', '2': '2'}
    assert r.gold_playoff_odds.division.eq(1).all()
    assert r.gold_playoff_odds.wildcard.eq(0).all()
    assert r.gold_playoff_odds.bye.sum() == pytest.approx(1)
    assert ReportProcessor(archive, divisions={}).divisions == {}
    assert ReportProcessor(archive, divisions={'1': 'East', '2': 'East'}).divisions == {'1': 'East', '2': 'East'}
    frame = pd.read_csv(folder / 'divisions_week_2.csv.gz')
    frame.loc[0, 'division_id'] = float('nan')
    frame.to_csv(folder / 'divisions_week_2.csv.gz', index=False)
    with pytest.raises(ValueError, match='missing division_id'):
        _ = ReportProcessor(archive).divisions


@pytest.mark.parametrize('qualifiers, expected_byes', [(4, 0), (6, 2), (8, 0), (12, 4)])
@pytest.mark.parametrize('override', [None, 0, 1])
def test_playoff_byes_derived_from_bracket(qualifiers, expected_byes, override):
    from types import SimpleNamespace
    from report_code.playoff_odds import build

    teams = [str(i) for i in range(12)]
    processor = SimpleNamespace(
        teams=pd.DataFrame({'team_id': teams}),
        bronze_settings=pd.DataFrame([{'num_playoff_teams': qualifiers,
                                      'playoff_start_week': 3}]),
        playoff_teams=None, playoff_byes=override, divisions={},
        silver_team_week=pd.DataFrame([
            {'team_id': team, 'week': week, 'actual_points': 100 + i + week, 'win': float(i >= 6)}
            for i, team in enumerate(teams) for week in (1, 2)
        ]),
        silver_schedule=pd.DataFrame(columns=['team_id', 'week', 'opponent_id']),
        current_week=2, simulation_count=100, random_seed=42, playoff_blend_weeks=6,
    )
    odds = build(processor)
    assert not odds.attrs.get('unavailable_reason')
    assert odds.make_playoffs.sum() == pytest.approx(qualifiers)
    assert odds.bye.sum() == pytest.approx(expected_byes if override is None else override)


def test_processor_infers_byes_without_archived_bye_count(archive):
    report = ReportProcessor(archive, 2)
    report.bronze_settings.drop(columns='num_playoff_byes', inplace=True)
    assert report.gold_playoff_odds.make_playoffs.notna().all()
    assert report.gold_playoff_odds.bye.eq(0).all()


def test_playoff_history_uses_each_weeks_snapshots(archive):
    report = ReportProcessor(archive, 3, simulation_count=100, playoff_byes=0)
    history = report.playoff_odds_history
    assert [entry['week'] for entry in history] == [1, 2, 3]
    assert history[0]['unavailable_reason']
    expected = ReportProcessor(archive, 2, simulation_count=100, playoff_byes=0)
    assert history[1]['rows'] == json.loads(expected.gold_playoff_odds.to_json(orient='records'))
    assert history[2]['rows'] == json.loads(report.gold_playoff_odds.to_json(orient='records'))
    # A new week-three score must not change the week-two outlook.
    path = archive / 'player_data/player_data_week_3.csv.gz'
    players = pd.read_csv(path)
    players['fantasy_points_actual'] += 1000
    players.to_csv(path, index=False)
    changed = ReportProcessor(archive, 3, simulation_count=100, playoff_byes=0)
    assert changed.playoff_odds_history[1] == history[1]
    assert report.playoff_odds_history is history


def test_playoff_history_retains_missing_weeks(archive):
    (archive / 'league_settings/league_settings_week_1.csv.gz').unlink()
    report = ReportProcessor(archive, 2, simulation_count=100)
    assert report.playoff_odds_history[0]['rows'] == []
    assert report.playoff_odds_history[0]['unavailable_reason']
    assert report.playoff_odds_history[1]['unavailable_reason'] is None


@pytest.mark.parametrize('week, weight', [(2, 1/3), (3, 1/2), (5, 5/6), (6, 1), (10, 1)])
def test_playoff_score_parameters_blend_pooled_scores(week, weight):
    import numpy as np
    from report_code.playoff_odds import score_parameters

    games = pd.DataFrame({'team_id': ['a', 'a', 'b', 'b'],
                          'actual_points': [80., 80., 100., 140.]})
    stats = score_parameters(games, ['b', 'a'], week, blend_weeks=6)
    pooled_std = np.std([80., 80., 100., 140.], ddof=1)
    assert stats.index.tolist() == ['b', 'a']
    assert stats.loc['a', 'mean'] == pytest.approx(weight*80 + (1-weight)*100)
    assert stats.loc['b', 'mean'] == pytest.approx(weight*120 + (1-weight)*100)
    assert stats.loc['a', 'std'] == pytest.approx((1-weight)*pooled_std)
    assert stats.loc['b', 'std'] == pytest.approx(weight*np.std([100., 140.], ddof=1) + (1-weight)*pooled_std)


@pytest.mark.parametrize('window', [6, 8, 10])
def test_configurable_playoff_blending_and_history(archive, window):
    from report_code.playoff_odds import score_parameters
    report = ReportProcessor(archive, 3, playoff_blend_weeks=window, simulation_count=100)
    games = report.silver_team_week
    stats = score_parameters(games, report.teams.team_id, 2, window)
    means = games.groupby('team_id').actual_points.mean()
    assert stats.loc['1', 'mean'] == pytest.approx(2/window*means['1'] + (1-2/window)*games.actual_points.mean())
    expected = ReportProcessor(archive, 2, playoff_blend_weeks=window, simulation_count=100)
    assert report.playoff_odds_history[1]['rows'] == json.loads(expected.gold_playoff_odds.to_json(orient='records'))


@pytest.mark.parametrize('window', [0, -1, 2.5, True, None])
def test_invalid_playoff_blend_window(archive, window):
    with pytest.raises(ValueError, match='playoff_blend_weeks'):
        ReportProcessor(archive, playoff_blend_weeks=window)


def test_exponent_sweep_has_1000_seasons_per_exponent():
    import numpy as np
    from report_code.playoff_odds import simulation_scales
    scales = simulation_scales([16., 25.], 10000)
    assert scales.shape == (10000, 2)
    for i in range(10):
        exponent = (i + 1) * .25
        np.testing.assert_allclose(scales[i*1000:(i+1)*1000, 0], 16**exponent)
        np.testing.assert_allclose(scales[i*1000:(i+1)*1000, 1], 25**exponent)


def test_simulated_scores_clip_both_bounds_without_changing_interior():
    import numpy as np
    from report_code.playoff_odds import simulated_scores
    class FixedDraws:
        def normal(self, means, scales):
            return np.array([[-1000., 60., 115., 200., 9000.]])
    np.testing.assert_array_equal(simulated_scores(FixedDraws(), None, None),
                                  [[60., 60., 115., 200., 200.]])


@pytest.mark.parametrize('count', [1, 99, 100.0, True])
def test_simulations_require_equal_exponent_groups(archive, count):
    with pytest.raises(ValueError, match='multiple of 10'):
        ReportProcessor(archive, simulation_count=count)


def test_default_playoff_roll_in_is_ten_weeks(archive):
    from report_code.playoff_odds import score_parameters
    report = ReportProcessor(archive, 2)
    assert report.playoff_blend_weeks == 10
    games = report.silver_team_week
    stats = score_parameters(games, report.teams.team_id, 2)
    team_mean = games.loc[games.team_id.eq('1'), 'actual_points'].mean()
    assert stats.loc['1', 'mean'] == pytest.approx(.2*team_mean + .8*games.actual_points.mean())


def test_baseline_representatives_and_market_totals(archive):
    from report_code.vobl import build, payload
    report = ReportProcessor(archive, 1)
    table = build(report)
    data = payload(report, table)
    weekly = {r['position']: r for r in data['weekly_baselines']}
    assert {r['week'] for r in data['weekly_baselines']} == {1}
    assert weekly['RB1']['baseline_player_id'] == '4'
    assert weekly['RB1']['baseline_points'] == 13
    assert weekly['W/R/T1']['baseline_player_id'] == '1'
    assert weekly['RB1']['market_player_id'] == '7'
    assert weekly['W/R/T1']['market_player_id'] == '8'
    expertise = report.gold_managerial_expertise.set_index('team_id')
    for row in table.itertuples():
        assert row.total == expertise.loc[row.team_id, 'vobl']
        assert row.market_total == expertise.loc[row.team_id, 'vobm']
    report = ReportProcessor(archive, 1)
    report.bronze_player.loc[report.bronze_player.player_id.eq('7'), 'fantasy_points_actual'] = None
    data = payload(report, build(report))
    assert all(r['market_points'] is None for r in data['weekly_baselines'])
    assert all(r['market_total'] is None for r in data['rows'])


def test_standings_records_divisions_and_missing_scores(archive):
    from report_code.standings import payload
    report = ReportProcessor(archive, 2, divisions={'1': 'East', '2': 'East'})
    games = report.silver_team_week
    games.loc[games.week.eq(2), ['actual_points', 'opponent_points']] = 20
    games.loc[games.week.eq(2), 'win'] = .5
    data = payload(report)
    assert data['has_divisions']
    assert [r['team_id'] for r in data['rows']] == ['2', '1']
    first = data['rows'][0]
    assert (first['wins'], first['losses'], first['ties']) == (1, 0, 1)
    assert (first['division_wins'], first['division_losses'], first['division_ties']) == (1, 0, 1)
    assert first['points_for'] == 48
    assert first['points_against'] == 40
    assert first['win_pct'] == .75
    report.divisions = {'1': 'East', '2': 'West'}
    assert all(r['division_wins'] == 0 for r in payload(report)['rows'])
    games.loc[games.team_id.eq('2') & games.week.eq(1), ['actual_points', 'win']] = float('nan')
    row = next(r for r in payload(report)['rows'] if r['team_id'] == '2')
    assert row['wins'] is None and row['points_for'] is None
    assert row['division_wins'] == 0
    assert not payload(ReportProcessor(archive, 1))['has_divisions']


def test_standings_division_and_overall_ranks(archive):
    from report_code.standings import payload
    report = ReportProcessor(archive, 1, divisions={'1': 'East', '2': 'West'})
    rows = {r['team_id']: r for r in payload(report)['rows']}
    assert (rows['1']['division_rank'], rows['1']['overall_rank']) == (1, 2)
    assert (rows['2']['division_rank'], rows['2']['overall_rank']) == (1, 1)
    report.divisions = {'1': 'East', '2': 'East'}
    rows = {r['team_id']: r for r in payload(report)['rows']}
    assert rows['1']['division_rank'] == 2
    report.silver_team_week[['actual_points', 'opponent_points']] = 20
    report.silver_team_week['win'] = .5
    assert all(r['overall_rank'] == r['division_rank'] == 1 for r in payload(report)['rows'])
    report.silver_team_week.loc[0, 'win'] = float('nan')
    assert payload(report)['rows'][-1]['overall_rank'] is None
