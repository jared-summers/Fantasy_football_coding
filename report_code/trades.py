"""Trade report card: find trades and rate each one three ways.

Trades come from the league's transaction log when the archive has one (ESPN), and otherwise are inferred
from weekly rosters. The log says that a trade happened, between which teams, with which players, and when.
The weekly rosters then confirm the week each player arrived and who sent him, so the report never depends
on which direction the log describes. A trade is rated only when two teams each sent at least one player,
because draft picks are not valued. Anything else is listed as not rated.

Without a log, a trade is two teams that each sent at least one player to the other between consecutive
weekly roster snapshots. One-way moves cannot be told apart from a drop followed by a waiver claim, so they
are counted but never rated.

Each side is scored from the first week on its new roster through the report week:

1. raw points      points of the players received minus the players sent away
2. points over replacement
                   weekly points above a replacement-level player at the same
                   positions (never below zero), received minus sent
3. lineup impact   the team's best legal lineup with the trade minus the same
                   roster without it (received players removed, sent players
                   kept), plus the wins that difference is estimated to swing.
                   A replacement-level free agent is always available for each
                   slot, in both versions, so a missing starter is not a hole.
"""
import re

import pandas as pd

from . import vobl
from .common import NAN, total, win
from .lineups import eligible, optimize, roster_pool
from .processor import records

MARGINS = ((50, 'Lopsided'), (20, 'Clear'), (5, 'Slight'))
POSITIONS = ('QB', 'RB', 'WR', 'TE', 'K', 'DEF', 'DL', 'LB', 'DB')
COLUMNS = ['trade_id', 'week', 'date', 'origin', 'team_id', 'partner_id', 'received', 'sent',
           'raw_received', 'raw_sent', 'raw_net', 'por_received', 'por_sent', 'por_net',
           'lineup_net', 'wins_net', 'missing_player_weeks']
METHODS = (
    'Each side is scored from its first week on the new roster through the report week, as what it received '
    'minus what it gave up. Raw points counts the players’ fantasy points. Points over replacement counts '
    'weekly points above a replacement-level player at the same positions, never below zero. Lineup impact '
    'compares the team’s best possible lineup with and without the trade (a replacement-level free agent is '
    'available for any slot in both versions), and estimates the wins that difference swung. The winner is '
    'the side ahead on more metrics; the margin label comes from the typical gap across the three metrics, in '
    'points. This is hindsight: injuries, roster needs and later moves are not considered.')
DESCRIPTIONS = {
    'transaction log': 'Trades come from the league’s transaction log, with the week each player arrived and who '
                       'sent him confirmed against the weekly rosters. A trade is rated when two teams each sent at '
                       'least one player, because draft picks are not valued. Others are listed as not rated. ',
    'roster snapshots': 'Trades are inferred from weekly rosters: two teams that each sent at least one player to '
                        'the other between consecutive weeks. One-way moves look the same as waiver claims, so '
                        'they are not rated. '}


def ownership(players):
    owned = players[players.team_id.notna() & ~players.player_id.astype(str).str.startswith('empty-')]
    return owned.pivot(index='player_id', columns='week', values='team_id')


def infer(processor):
    """(trades, unconfirmed_move_count) inferred from consecutive weekly rosters."""
    own = ownership(processor.silver_player)
    trades, one_way = [], 0
    for week in sorted(own.columns):
        nxt = week + 1
        if nxt not in own.columns or nxt > processor.current_week:
            continue
        before, after = own[week], own[nxt]
        moved = before.notna() & after.notna() & (before != after)
        groups = {}
        for pid in own.index[moved]:
            a, b = str(before[pid]), str(after[pid])
            groups.setdefault(tuple(sorted((a, b))), []).append((pid, a))
        for pair, moves in groups.items():
            sent = {team: [pid for pid, origin in moves if origin == team] for team in pair}
            if all(sent.values()):
                trades.append(dict(trade_id=f'w{nxt}_{pair[0]}_{pair[1]}', week=int(nxt), teams=pair,
                                   sent=sent, date=None, origin='roster'))
            else:
                one_way += len(moves)
    return trades, one_way


def _team(value):
    if pd.isna(value):
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def _arrival(own, pair, pid, logged_team):
    """(first week on the new team, sender, receiver) for one traded player, from the weekly rosters."""
    if pid not in own.index:
        return None
    row, weeks = own.loc[pid], sorted(own.columns)
    for week in weeks:
        nxt = week + 1
        if nxt in own.columns and pd.notna(row[week]) and pd.notna(row[nxt]) \
                and {str(row[week]), str(row[nxt])} == set(pair):
            return nxt, str(row[week]), str(row[nxt])
    # No handover is visible (a trade before the first snapshot): trust the log's receiver if the roster agrees.
    first = next((w for w in weeks if pd.notna(row[w])), None)
    if first is not None and str(row[first]) in pair and str(row[first]) == logged_team:
        return first, next(t for t in pair if t != logged_team), logged_team
    return None


def logged(processor, own):
    """(rated trades, unrated trades, unresolved count) from the archived transaction log, or None."""
    try:
        log = processor.bronze_trades
    except FileNotFoundError:
        return None
    log = log[log.kind.eq('trade')] if 'kind' in log else log.iloc[:0]
    players = processor.silver_player.sort_values('week').drop_duplicates('player_id', keep='last')
    by_name = {}
    for pid, name in zip(players.player_id, players.player_name):
        by_name.setdefault(str(name).strip().lower(), []).append(str(pid))
    names = players.set_index('player_id').player_name
    rated, unrated, unresolved = [], [], 0
    for trade_id, group in log.groupby('trade_id', sort=False):
        group = group.assign(team=[_team(t) for t in group.team_id])
        teams = sorted(set(group.team.dropna()))
        found, moved = {}, {team: [] for team in teams}      # player id -> team the log names for him
        for row in group.itertuples():
            if row.team is not None:
                moved[row.team].append(str(names.get(str(row.player_id), row.player_name))
                                       if pd.isna(row.player_name) else str(row.player_name))
            pid = None if pd.isna(row.player_id) else str(row.player_id)
            if pid is None:
                pid = next((c for c in by_name.get(str(row.player_name).strip().lower(), []) if c in own.index
                            and set(own.loc[c].dropna().astype(str)) & set(teams)), None)
            if pid is not None:
                found[pid] = row.team
        date = None if group.trade_date.isna().all() else str(group.trade_date.iloc[0])[:10]
        if len(teams) != 2:
            unrated.append(dict(trade_id=trade_id, date=date, teams=teams, players=moved,
                                reason='One-way player trade (draft picks are not valued)' if len(teams) < 2 else 'Three or more teams'))
            continue
        arrivals = {pid: _arrival(own, tuple(teams), pid, team) for pid, team in found.items()}
        arrivals = {pid: a for pid, a in arrivals.items() if a and a[0] <= processor.current_week}
        sent = {team: [pid for pid, a in arrivals.items() if a[1] == team] for team in teams}
        if not arrivals:
            unresolved += 1
        elif all(sent.values()):
            rated.append(dict(trade_id=trade_id, week=min(a[0] for a in arrivals.values()), teams=tuple(teams),
                              sent=sent, date=date, origin='log'))
        else:
            unrated.append(dict(trade_id=trade_id, date=date, teams=teams, players=moved,
                                reason='One-way player trade (draft picks are not valued)'))
    return rated, unrated, unresolved


def find_trades(processor):
    """(trades, unconfirmed move count, unrated trades, source) for the report."""
    inferred, one_way = infer(processor)
    log = logged(processor, ownership(processor.silver_player))
    if log is None:
        return inferred, one_way, [], 'roster snapshots'
    rated, unrated, _ = log
    # A swap the rosters show but the log lacks (or the log failed to describe) is kept and labelled.
    known = [(set(t['teams']), {p for ids in t['sent'].values() for p in ids}) for t in rated]
    extra = [t for t in inferred if not any(t['teams'] == tuple(sorted(k[0])) and
                                            k[1] & {p for ids in t['sent'].values() for p in ids} for k in known)]
    return sorted(rated + extra, key=lambda t: (t['week'], t['trade_id'])), 0, unrated, 'transaction log'


def replacement_level(processor):
    """(level, slot_baseline): replacement points by player positions, and by lineup slot, for a week."""
    frame = vobl.weekly(processor).drop_duplicates(['week', 'position'])
    by_week, by_slot = {}, {}
    for week, group in frame.groupby('week'):
        by_week[int(week)] = [(re.sub(r'\d+$', '', label), value) for label, value in zip(group.position, group.baseline_points)]
        by_slot[int(week)] = dict(zip(group.position, group.baseline_points))
    cache = {}

    def level(week, positions):
        """Weakest baseline among the lineup slots a player is eligible for."""
        key = (week, frozenset(positions))
        if key not in cache:
            values = [v for slot, v in by_week.get(week, []) if pd.notna(v) and eligible(set(positions), slot)]
            cache[key] = min(values) if values else NAN
        return cache[key]

    def slot_baseline(week, label):
        value = by_slot.get(week, {}).get(label, NAN)
        return 0.0 if pd.isna(value) else float(value)
    return level, slot_baseline


def replacement_players(slots, week, slot_baseline):
    """One replacement-level free agent per lineup slot, eligible only where that slot allows."""
    rows = [dict(player_id=f'replacement-{slot}{number}', player_name='Replacement level', week=week,
                 eligible_positions=sorted(p for p in POSITIONS if eligible({p}, slot)),
                 actual_points=slot_baseline(week, f'{slot}{number}'))
            for slot, number in slots]
    return pd.DataFrame(rows)


def player_value(pid, start, end, points, positions, level):
    """(raw points, points over replacement, weeks without data) for one player."""
    raw, por, missing, por_known = 0.0, 0.0, 0, True
    for week in range(start, end + 1):
        value = points.loc[pid, week] if pid in points.index and week in points.columns else NAN
        if pd.isna(value):
            missing, value = missing + 1, 0.0
        base = level(week, positions)
        raw += value
        if pd.isna(base):
            por_known = False
        else:
            por += max(0.0, value - base)
    return raw, por if por_known else NAN, missing


def lineup_impacts(processor, groups, archived, team, received, sent, start, end, slot_baseline):
    """Weekly best-lineup difference (with trade minus without), and weeks missing a sent player."""
    slots, players = processor.lineup_slots, processor.silver_player
    impacts, missing = {}, 0
    for week in range(start, end + 1):
        group = groups.get((team, week))
        if group is None:
            impacts[week] = NAN
            continue
        pool, incomplete = roster_pool(group, archived.get((team, week), group.iloc[:0]))
        if incomplete:
            impacts[week] = NAN
            continue
        counterfactual = pool[~pool.player_id.isin(received)]
        extra = players[players.week.eq(week) & players.player_id.isin(sent)
                        & ~players.player_id.isin(counterfactual.player_id)]
        missing += len(set(sent)) - len(extra) - int(counterfactual.player_id.isin(sent).sum())
        free = replacement_players(slots, week, slot_baseline)
        counterfactual = pd.concat([counterfactual, extra, free], ignore_index=True)
        pool = pd.concat([pool, free], ignore_index=True)
        with_trade, without = optimize(pool, slots), optimize(counterfactual, slots)
        if with_trade is None or without is None:
            impacts[week] = NAN
        else:
            impacts[week] = total(pool.loc[with_trade, 'actual_points']) - total(counterfactual.loc[without, 'actual_points'])
    return impacts, max(missing, 0)


def estimated_wins(processor, team, partner, impacts, partner_impacts):
    """Wins the trade is estimated to have added: actual results minus results without its lineup impact."""
    week_table = processor.silver_team_week.set_index(['team_id', 'week'])
    swing = 0.0
    for week, impact in impacts.items():
        if pd.isna(impact):
            return NAN
        if (team, week) not in week_table.index:
            continue
        row = week_table.loc[(team, week)]
        if pd.isna(row.actual_points) or pd.isna(row.opponent_points):
            continue
        opponent_impact = partner_impacts.get(week, 0.0) if str(row.opponent_id) == partner else 0.0
        if pd.isna(opponent_impact):
            return NAN
        swing += row.win - win(row.actual_points - impact, row.opponent_points - opponent_impact)
    return swing


def build(processor):
    players = processor.silver_player
    trades, unconfirmed, unrated, source = find_trades(processor)
    info = players.sort_values('week').drop_duplicates('player_id', keep='last').set_index('player_id')
    points = players.pivot(index='player_id', columns='week', values='actual_points')
    level, slot_baseline = replacement_level(processor) if trades else (None, None)
    groups = {key: group for key, group in players[players.team_id.notna()].groupby(['team_id', 'week'])}
    archived = {key: group for key, group in processor.bronze_team_data.groupby(['team_id', 'week'])}
    end = processor.current_week
    rows = []
    for trade in trades:
        start, impacts, sides = trade['week'], {}, {}
        for team in trade['teams']:
            partner = trade['teams'][1 - trade['teams'].index(team)]
            received, sent = trade['sent'][partner], trade['sent'][team]
            impacts[team], missing = lineup_impacts(processor, groups, archived, team, received, sent, start, end, slot_baseline)
            sides[team] = dict(partner=partner, received=received, sent=sent, missing=missing)
        for team, side in sides.items():
            def detail(ids):
                out = []
                for pid in ids:
                    raw, por, absent = player_value(pid, start, end, points, info.loc[pid, 'eligible_positions'], level)
                    side['missing'] += absent
                    out.append(dict(player_id=str(pid), name=info.loc[pid, 'player_name'],
                                    position=info.loc[pid, 'position'], points=raw, por=por))
                return out
            received, sent = detail(side['received']), detail(side['sent'])
            raw_in, raw_out = sum(p['points'] for p in received), sum(p['points'] for p in sent)
            por_in, por_out = total(p['por'] for p in received), total(p['por'] for p in sent)
            rows.append(dict(
                trade_id=trade['trade_id'], week=start, date=trade['date'], origin=trade['origin'], team_id=team, partner_id=side['partner'],
                received=received, sent=sent, raw_received=raw_in, raw_sent=raw_out, raw_net=raw_in - raw_out,
                por_received=por_in, por_sent=por_out, por_net=por_in - por_out,
                lineup_net=total(impacts[team].values()),
                wins_net=estimated_wins(processor, team, side['partner'], impacts[team], impacts[side['partner']]),
                missing_player_weeks=side['missing']))
    table = pd.DataFrame(rows, columns=COLUMNS)
    table.attrs.update(unconfirmed_moves=unconfirmed, unrated=unrated, source=source)
    return table


def verdict(a, b):
    """Winner, margin and label for one trade from its two side rows."""
    votes = {a['team_id']: 0, b['team_id']: 0}
    gaps = []
    for metric in ('raw_net', 'por_net', 'lineup_net'):
        if pd.notna(a[metric]) and pd.notna(b[metric]):
            gaps.append(abs(a[metric] - b[metric]) / 2)
            if abs(a[metric] - b[metric]) >= 1:
                votes[a['team_id'] if a[metric] > b[metric] else b['team_id']] += 1
    margin = float(pd.Series(gaps).median()) if gaps else 0.0
    winner = max(votes, key=votes.get) if votes[a['team_id']] != votes[b['team_id']] else None
    size = next((name for floor, name in MARGINS if margin >= floor), None)
    label = (size or 'Slight') if winner else ('Split decision' if size else 'Even')
    return dict(winner=winner, votes=votes, margin=round(margin, 2), label=label)


def payload(processor, table):
    trades, record = [], {team: dict(team_id=team, trades=0, won=0, lost=0, even=0, raw_net=0.0, por_net=0.0,
                                     lineup_net=0.0, wins_net=0.0, unavailable=0) for team in processor.teams.team_id}
    for trade_id, group in table.groupby('trade_id', sort=False):
        a, b = (row for _, row in group.iterrows())
        result = verdict(a, b)
        trades.append(dict(trade_id=trade_id, week=int(a['week']), date=a['date'], origin=a['origin'], winner=result['winner'], votes=result['votes'],
                           margin=result['margin'], label=result['label'], sides=[row_dict(a), row_dict(b)]))
        for row in (a, b):
            summary = record[row['team_id']]
            summary['trades'] += 1
            summary['won' if result['winner'] == row['team_id'] else 'even' if result['winner'] is None else 'lost'] += 1
            for metric in ('raw_net', 'por_net', 'lineup_net', 'wins_net'):
                if pd.isna(row[metric]):
                    summary['unavailable'] += 1
                else:
                    summary[metric] += float(row[metric])
    source = table.attrs.get('source', 'roster snapshots')
    return {'description': DESCRIPTIONS[source] + METHODS, 'source': source, 'trades': trades,
            'teams': records(pd.DataFrame(record.values())),
            'unconfirmed_moves': int(table.attrs.get('unconfirmed_moves', 0)),
            'unrated': [dict(u, players={team: list(map(str, ps)) for team, ps in u['players'].items()})
                        for u in table.attrs.get('unrated', [])]}


def row_dict(row):
    keys = ('team_id', 'received', 'sent', 'raw_net', 'por_net', 'lineup_net', 'wins_net', 'missing_player_weeks')
    return {key: row[key] for key in keys}
