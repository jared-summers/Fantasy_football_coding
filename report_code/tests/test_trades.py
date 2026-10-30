"""Trade report card tests, built on the fake ESPN league from test_espn."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import warnings

import pandas as pd
import pytest

from report_code import ReportProcessor, trades
from report_code.tests.test_espn import FakeLeague, collect, points


class TradeLeague(FakeLeague):
    """Teams 1 and 2 swap their first RB (lineup index 1) from week 2 on."""
    swap = True
    one_way = False

    def lineup(self, team, week):
        rows = super().lineup(team, week)
        if self.swap and week >= 2 and team in (1, 2):
            other = 3 - team
            for row in rows:
                if row.playerId == team * 100 + 1:
                    row.playerId, row.name = other * 100 + 1, f'P{other}-1'
                    row.points = points(other, week, 1)
                    row.projected_points = row.points - 2
        if self.one_way and week >= 2 and team in (3, 4):
            for row in rows:
                if team == 3 and row.playerId == 301:   # team 3 simply loses him...
                    row.playerId, row.name, row.points = 391, 'P3-new', 6.0
                if team == 4 and row.playerId == 401:   # ...and team 4 simply gains him
                    row.playerId, row.name = 301, 'P3-1'
                    row.points = points(3, week, 1)
        return rows


def report(tmp_path, league):
    root = collect(tmp_path, league)
    return ReportProcessor(root, 3, simulation_count=10)


def test_swap_is_detected_as_one_trade(tmp_path):
    found, unconfirmed = trades.infer(report(tmp_path, TradeLeague()))
    assert unconfirmed == 0
    assert len(found) == 1
    assert found[0]['week'] == 2 and found[0]['teams'] == ('1', '2')
    assert found[0]['sent'] == {'1': ['101'], '2': ['201']}


def test_raw_points_and_replacement_metrics(tmp_path):
    table = trades.build(report(tmp_path, TradeLeague())).set_index('team_id')
    got = sum(points(2, w, 1) for w in (2, 3))     # what team 1 received
    gave = sum(points(1, w, 1) for w in (2, 3))
    assert table.loc['1', 'raw_received'] == pytest.approx(got)
    assert table.loc['1', 'raw_sent'] == pytest.approx(gave)
    assert table.loc['1', 'raw_net'] == pytest.approx(got - gave)
    assert table.loc['2', 'raw_net'] == pytest.approx(gave - got)
    for team in ('1', '2'):
        assert 0 <= table.loc[team, 'por_received'] <= table.loc[team, 'raw_received'] + 1e-9
        assert table.loc[team, 'por_net'] == pytest.approx(table.loc[team, 'por_received'] - table.loc[team, 'por_sent'])
    assert table.loc['1', 'received'][0]['player_id'] == '201'
    assert table.loc['1', 'sent'][0]['name'] == 'P1-1'


def test_lineup_impact_is_bounded_by_the_players_involved(tmp_path):
    table = trades.build(report(tmp_path, TradeLeague())).set_index('team_id')
    for team in ('1', '2'):
        impact = table.loc[team, 'lineup_net']
        assert pd.notna(impact) and pd.notna(table.loc[team, 'wins_net'])
        # One player each way can move a best lineup by at most that player's points.
        assert abs(impact) <= table.loc[team, 'raw_received'] + table.loc[team, 'raw_sent'] + 1e-9
        assert abs(table.loc[team, 'wins_net']) <= 2   # two weeks after the trade


def test_payload_structure_and_verdict(tmp_path):
    r = report(tmp_path, TradeLeague())
    data = r.to_dict()['trades']
    assert len(data['trades']) == 1 and data['unconfirmed_moves'] == 0
    trade = data['trades'][0]
    assert trade['week'] == 2 and [s['team_id'] for s in trade['sides']] == ['1', '2']
    assert trade['label'] in ('Lopsided', 'Clear', 'Slight', 'Even')
    assert (trade['winner'] is None) == (trade['label'] == 'Even')
    rows = {t['team_id']: t for t in data['teams']}
    assert set(rows) == {'1', '2', '3', '4'}
    assert rows['1']['trades'] == rows['2']['trades'] == 1 and rows['3']['trades'] == 0
    assert rows['1']['won'] + rows['1']['lost'] + rows['1']['even'] == 1
    assert rows['1']['raw_net'] == pytest.approx(-rows['2']['raw_net'])


def test_verdict_rules():
    a = dict(team_id='1', raw_net=60.0, por_net=40.0, lineup_net=10.0)
    b = dict(team_id='2', raw_net=-60.0, por_net=-40.0, lineup_net=-10.0)
    result = trades.verdict(a, b)
    assert result['winner'] == '1' and result['votes'] == {'1': 3, '2': 0}
    assert result['label'] == 'Clear' and result['margin'] == 40.0   # median of 60, 40 and 10
    # Majority winner with tiny gaps is still a (slight) win, never contradicted by the label.
    small = trades.verdict(dict(a, lineup_net=-30.0, por_net=2.0, raw_net=2.0),
                           dict(b, lineup_net=30.0, por_net=-2.0, raw_net=-2.0))
    assert small['winner'] == '1' and small['label'] == 'Slight'
    # Tied votes with real gaps are a split decision; tied votes with no gap are even.
    tie = trades.verdict(dict(a, lineup_net=float('nan'), por_net=-40.0), dict(b, lineup_net=float('nan'), por_net=40.0))
    assert tie['winner'] is None and tie['label'] == 'Split decision'
    even = trades.verdict(dict(a, raw_net=0.2, por_net=0.0, lineup_net=0.1), dict(b, raw_net=-0.2, por_net=0.0, lineup_net=-0.1))
    assert even['winner'] is None and even['label'] == 'Even'


def test_missing_position_is_filled_by_a_replacement_player(tmp_path):
    """Team 1 has no other QB, so without the trade it would have no legal lineup."""
    league = TradeLeague()
    league.swap = False
    original = league.lineup

    def lineup(team, week):
        rows = original(team, week)
        if week >= 2 and team == 1:       # QB 100 was dropped; trade for team 2's QB, giving bench RB 109
            rows = [r for r in rows if r.playerId != 109]
            qb = rows[0]
            qb.playerId, qb.name, qb.points = 200, 'P2-0', points(2, week, 0)
        if week >= 2 and team == 2:       # gets bench RB 109, replaces his QB with a pickup
            rows[0].playerId, rows[0].name, rows[0].points = 291, 'P2-new', 8.0
            rows.append(NS(playerId=109, name='P1-9', position='RB', slot_position='BE',
                           points=points(1, week, 9), projected_points=1.0, proTeam='XXX'))
        return rows
    league.lineup = lineup
    r = report(tmp_path, league)
    found, _ = trades.infer(r)
    assert [(t['teams'], t['sent']) for t in found] == [(('1', '2'), {'1': ['109'], '2': ['200']})]
    table = trades.build(r).set_index('team_id')
    assert table.lineup_net.notna().all() and table.wins_net.notna().all()
    assert table.loc['1', 'lineup_net'] > 0          # a real QB beats the replacement-level fill-in


def test_one_way_move_is_counted_but_not_rated(tmp_path):
    league = TradeLeague()
    league.swap, league.one_way = False, True
    r = report(tmp_path / 'log', league)
    found, unconfirmed = trades.infer(r)
    assert found == [] and unconfirmed == 1
    # With a readable (empty) log, that move is a waiver claim, so nothing is left unconfirmed.
    data = r.to_dict()['trades']
    assert data['trades'] == [] and data['unconfirmed_moves'] == 0 and data['source'] == 'transaction log'
    # Without a log, it stays an unconfirmed move.
    blind = TradeLeague()
    blind.swap, blind.one_way = False, True
    blind.recent_activity = lambda size=25, offset=0: (_ for _ in ()).throw(RuntimeError('no log'))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        data = report(tmp_path / 'blind', blind).to_dict()['trades']
    assert data['trades'] == [] and data['unconfirmed_moves'] == 1 and data['source'] == 'roster snapshots'


def test_league_without_trades(tmp_path):
    r = report(tmp_path, FakeLeague())
    data = r.to_dict()['trades']
    assert data['trades'] == [] and data['unconfirmed_moves'] == 0
    assert [t['trades'] for t in data['teams']] == [0, 0, 0, 0]


# ---- transaction log ---------------------------------------------------------------------------------

def stamp(day):
    return int(datetime(2026, 10, day, 15, tzinfo=timezone.utc).timestamp() * 1000)


def player(pid, name):
    return NS(playerId=pid, name=name)


def activity(league, day, *moves, legacy=False):
    """Append a logged trade; each move is (sending team id, receiving team id, player or player name).

    ESPN's real format logs each player twice: TRADE_SENT for the sender and TRADE_RECEIVED for the receiver.
    legacy=True writes the single 'TRADED' line (receiver only) instead.
    """
    actions = []
    for sender, receiver, who in moves:
        if legacy:
            actions.append((league.teams[receiver - 1], 'TRADED', who, 0))
        else:
            actions.append((league.teams[sender - 1], 'TRADE_SENT', who, 0))
            actions.append((league.teams[receiver - 1], 'TRADE_RECEIVED', who, 0))
    league.activity.append(NS(date=stamp(day), actions=actions))


def swap_log(league, reverse=False, names_only=False, legacy=False):
    """The log entry for the week-2 swap of teams 1 and 2 (team 1 gets 201, team 2 gets 101)."""
    a, b = (player(201, 'P2-1'), player(101, 'P1-1'))
    if names_only:
        a, b = a.name, b.name
    moves = [(2, 1, a), (1, 2, b)]
    activity(league, 14, *(reversed(moves) if reverse else moves), legacy=legacy)


def noise(league, count):
    for index in range(count):
        league.activity.append(NS(date=stamp(1) + index, actions=[(league.teams[0], 'FA ADDED', f'FA {index}', 0)]))


def log_report(tmp_path, league):
    r = report(tmp_path, league)
    return r, trades.find_trades(r)


def test_logged_trade_is_confirmed_against_rosters(tmp_path):
    league = TradeLeague()
    swap_log(league)
    r, (found, unconfirmed, unrated, source) = log_report(tmp_path, league)
    assert source == 'transaction log' and unconfirmed == 0 and unrated == []
    assert len(found) == 1
    assert found[0]['origin'] == 'log' and found[0]['date'] == '2026-10-14' and found[0]['week'] == 2
    assert found[0]['sent'] == {'1': ['101'], '2': ['201']}
    data = r.to_dict()['trades']
    assert data['source'] == 'transaction log' and data['trades'][0]['date'] == '2026-10-14'
    assert data['trades'][0]['origin'] == 'log' and 'transaction log' in data['description']
    snapshot = pd.read_csv(tmp_path / 'Test' / '2026' / 'trades' / 'trades_week_3.csv.gz')
    assert sorted(snapshot.role) == ['received', 'received', 'sent', 'sent']
    assert set(snapshot.kind) == {'trade'}


def test_log_direction_and_name_only_entries_do_not_matter(tmp_path):
    for options in (dict(reverse=True), dict(names_only=True), dict(reverse=True, names_only=True), dict(legacy=True)):
        league = TradeLeague()
        swap_log(league, **options)
        _, (found, _, unrated, source) = log_report(tmp_path / str(sorted(options)), league)
        assert source == 'transaction log' and unrated == [], options
        assert [(t['origin'], t['sent']) for t in found] == [('log', {'1': ['101'], '2': ['201']})], options


def test_picks_and_three_team_trades_are_listed_not_rated(tmp_path):
    league = TradeLeague()
    league.swap, league.one_way = False, True                           # team 3's 301 really moves to team 4
    activity(league, 10, (3, 4, player(301, 'P3-1')))                   # a player for a draft pick
    activity(league, 12, (2, 1, 'P2-9'), (3, 2, 'P3-9'), (1, 3, 'P1-9'))   # three teams
    r, (found, unconfirmed, unrated, source) = log_report(tmp_path, league)
    assert found == [] and source == 'transaction log' and unconfirmed == 0
    assert sorted(u['reason'] for u in unrated) == ['One-way player trade (draft picks are not valued)', 'Three or more teams']
    payload = r.to_dict()['trades']
    assert payload['trades'] == [] and len(payload['unrated']) == 2
    one_way = next(u for u in payload['unrated'] if u['reason'].startswith('One-way'))
    assert one_way['players'] == {'3': [], '4': ['P3-1']}               # who received what comes from the log


def test_uneven_multi_player_trade_is_rated(tmp_path):
    """Two players for one, like a real consolidation trade; a bench-only swap leaves lineups alone."""
    league = TradeLeague()
    league.swap = False
    original = league.lineup

    def bench(team, week):
        rows = original(team, week)
        if week >= 2 and team == 1:
            rows = [r for r in rows if r.playerId not in (109, 110)] + [NS(
                playerId=209, name='P2-9', position='RB', slot_position='BE', points=points(2, week, 9),
                projected_points=1.0, proTeam='XXX')]
        if week >= 2 and team == 2:
            rows = [r for r in rows if r.playerId != 209] + [NS(
                playerId=pid, name=f'P1-{pid - 100}', position=pos, slot_position='BE',
                points=points(1, week, pid - 100), projected_points=1.0, proTeam='XXX')
                for pid, pos in ((109, 'RB'), (110, 'WR'))]
        return rows
    league.lineup = bench
    activity(league, 14, (1, 2, player(109, 'P1-9')), (1, 2, player(110, 'P1-10')), (2, 1, player(209, 'P2-9')))
    r, (found, _, unrated, source) = log_report(tmp_path, league)
    assert unrated == [] and source == 'transaction log'
    assert [(t['origin'], t['sent']) for t in found] == [('log', {'1': ['109', '110'], '2': ['209']})]
    table = trades.build(r).set_index('team_id')
    assert table.loc['1', 'raw_received'] == pytest.approx(sum(points(2, w, 9) for w in (2, 3)))
    assert table.loc['1', 'raw_sent'] == pytest.approx(sum(points(1, w, 9) + points(1, w, 10) for w in (2, 3)))
    assert table.lineup_net.notna().all()
    assert len(r.to_dict()['trades']['trades'][0]['sides'][0]['sent']) == 2


def test_empty_log_still_infers_a_visible_swap(tmp_path):
    league = TradeLeague()                                  # roster swap, but the log is empty
    _, (found, _, _, source) = log_report(tmp_path, league)
    assert source == 'transaction log'                      # the log was read: it simply has no trades
    assert [t['origin'] for t in found] == ['roster']
    snapshot = tmp_path / 'Test' / '2026' / 'trades' / 'trades_week_3.csv.gz'
    assert pd.read_csv(snapshot).kind.tolist() == ['none']


def test_unreadable_log_falls_back_to_rosters(tmp_path):
    league = TradeLeague()

    def broken(size=25, offset=0):
        raise RuntimeError('ESPN said no')
    league.recent_activity = broken
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        r, (found, unconfirmed, unrated, source) = log_report(tmp_path, league)
    assert any('transaction log' in str(w.message) for w in caught)
    assert source == 'roster snapshots' and [t['origin'] for t in found] == ['roster']
    assert not (tmp_path / 'Test' / '2026' / 'trades').exists()


def test_log_paging_and_old_library_without_offset(tmp_path):
    league = TradeLeague()
    noise(league, 250)
    swap_log(league)                                        # the trade sits on the last page
    from report_code import espn
    assert any('TRADE' in a.actions[0][1] for a in espn.activity_pages(league))
    legacy = TradeLeague()
    noise(legacy, 250)
    swap_log(legacy)
    legacy.recent_activity = lambda size=25: legacy.activity[:size]      # no offset argument
    assert any('TRADE' in a.actions[0][1] for a in espn.activity_pages(legacy))


def test_only_the_latest_week_is_recollected(tmp_path):
    from yahoo_fantasy_data.config import Settings
    from report_code import espn
    league = TradeLeague()
    swap_log(league)
    collect(tmp_path, league)
    statuses = espn.backfill_season(2026, '123', 1, 3, settings=Settings(data_dir=tmp_path),
                                    league_nickname='Test', league=league)
    assert statuses[3]['trades'] == 'written' and 'trades' not in statuses[1]
    assert set(statuses[1].values()) == {'skipped'}


class ChainLeague(TradeLeague):
    """Player 101 goes team 1 -> team 2 in week 2, then team 2 -> team 3 in week 3 (for 301)."""

    def lineup(self, team, week):
        rows = super().lineup(team, week)
        if week >= 3 and team == 2:
            for row in rows:
                if row.playerId == 101:
                    row.playerId, row.name, row.points = 301, 'P3-1', points(3, week, 1)
        if week >= 3 and team == 3:
            for row in rows:
                if row.playerId == 301:
                    row.playerId, row.name, row.points = 101, 'P1-1', points(1, week, 1)
        return rows


def test_player_traded_twice_belongs_to_the_right_trade_each_time(tmp_path):
    league = ChainLeague()
    swap_log(league)                                                            # 101 and 201 swap on Oct 14
    activity(league, 21, (2, 3, player(101, 'P1-1')), (3, 2, player(301, 'P3-1')))   # then 101 moves on
    _, (found, _, unrated, source) = log_report(tmp_path, league)
    assert unrated == [] and source == 'transaction log'
    summary = sorted((t['week'], t['teams'], t['sent'], t['date']) for t in found)
    assert summary == [(2, ('1', '2'), {'1': ['101'], '2': ['201']}, '2026-10-14'),
                       (3, ('2', '3'), {'2': ['101'], '3': ['301']}, '2026-10-21')]
