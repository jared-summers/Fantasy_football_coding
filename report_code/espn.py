"""ESPN fantasy football (via the ``espn-api`` package) translated into the shared report archive schema.

Private leagues authenticate with two browser cookies, supplied through the
ESPN_S2 and ESPN_SWID environment variables (GitHub Actions secrets in CI).
Weekly rosters, starters, scores and projections come from historical box
scores. Lineup slots are read from the league's own box scores, so leagues
with different slots (no kicker, two flex spots, ...) need no configuration.

Trades come from ESPN's transaction log (``league.recent_activity``) and are saved
as a ``trades`` snapshot; if the log cannot be read the report infers trades from
rosters instead. Run ``python -m report_code.espn LEAGUE_ID YEAR WEEK`` to print a
sanity check of what ESPN returns for one week before publishing anything.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
import sys
import warnings

import pandas as pd

from yahoo_fantasy_data.config import storage_league_name

# ESPN lineup slot -> the slot names used by lineups.py / the report.
SLOTS = {'RB/WR/TE': 'W/R/T', 'FLEX': 'W/R/T', 'WR/RB': 'W/R', 'RB/WR': 'W/R', 'WR/TE': 'W/T',
         'OP': 'Q/W/R/T', 'D/ST': 'DEF', 'BE': 'BN', 'IR': 'IR'}
POSITIONS = {'D/ST': 'DEF', 'DST': 'DEF', 'PK': 'K'}
BENCH = {'BN', 'IR'}
FLEX_POSITIONS = {'W/R/T': ('RB', 'WR', 'TE'), 'W/R': ('RB', 'WR'), 'W/T': ('WR', 'TE'),
                  'Q/W/R/T': ('QB', 'RB', 'WR', 'TE')}
FREE_AGENT_API_POSITION = {'DEF': 'D/ST'}
FREE_AGENT_SIZE = {'RB': 100, 'WR': 100, 'QB': 50, 'TE': 50, 'DEF': 40, 'K': 40}
NOTES = [
    'ESPN only lists players who are currently unrostered, so the free-agent pool for a past week omits anyone picked up since. Market baselines (VOBM) are most accurate for weeks collected soon after they were played.',
    'ESPN projections are the values stored with each historical box score and may differ from what was shown before kickoff.',
    'Player names, positions, team names, divisions and league settings reflect collection time; weekly ownership and starters come from historical box scores.',
]


def connect(year, league_id):
    """Open the league with cookies from the environment."""
    s2, swid = os.environ.get('ESPN_S2'), os.environ.get('ESPN_SWID')
    if not s2 or not swid:
        raise RuntimeError('Private ESPN leagues need the ESPN_S2 and ESPN_SWID environment variables '
                           '(GitHub Actions secrets). SWID includes its curly braces.')
    try:
        from espn_api.football import League
    except ImportError as error:
        raise RuntimeError('The espn-api package is required: pip install espn-api') from error
    try:
        return League(league_id=int(league_id), year=int(year), espn_s2=s2, swid=swid)
    except Exception as error:
        if type(error).__name__ == 'ESPNAccessDenied':
            raise RuntimeError('ESPN denied access: ESPN_S2 / ESPN_SWID are wrong or expired. '
                               'Copy fresh cookie values from your browser and update the secrets.') from error
        raise


def team_key(league_id, team_id):
    return f'espn.l.{league_id}.t.{team_id}'


def league_metadata(year, league_id, league=None):
    league = league or connect(year, league_id)
    settings = league.settings
    regular_end = int(settings.reg_season_count)
    # Each regular-season matchup period must be exactly one NFL week.
    for period, weeks in (getattr(settings, 'matchup_periods', None) or {}).items():
        if int(period) <= regular_end and [int(w) for w in weeks] != [int(period)]:
            raise ValueError('ESPN matchup periods that span several NFL weeks are not supported')
    current = int(league.current_week)
    finished = int(year) < date.today().year or current > regular_end
    return dict(provider='espn', league_id=str(league_id), league_key=f'espn.l.{league_id}',
                league_name=settings.name, season=int(year), current_week=current,
                is_finished=finished, start_week=1, end_week=18, uses_playoff=1,
                playoff_start_week=regular_end + 1, notes=NOTES), league


def _position(player):
    return POSITIONS.get(player.position, player.position)


def _week_stat(player, week, key):
    value = (getattr(player, 'stats', None) or {}).get(week, {})
    value = value.get(key) if isinstance(value, dict) else None
    return None if value is None else float(value)


def lineup_slots(sides):
    """League slots: the most starters seen in each slot by any team this week."""
    slots = Counter()
    for _, _, lineup in sides:
        counts = Counter(SLOTS.get(p.slot_position, p.slot_position) for p in lineup)
        for slot, n in counts.items():
            if slot not in BENCH:
                slots[slot] = max(slots[slot], n)
    return slots


def free_agent_positions(slots):
    wanted = []
    for slot in slots:
        for position in FLEX_POSITIONS.get(slot, (slot,)):
            if position in FREE_AGENT_SIZE and position not in wanted:
                wanted.append(position)
    return wanted


def free_agents(league, week, positions, rostered):
    rows = {}
    for position in positions:
        found = league.free_agents(week=week, size=FREE_AGENT_SIZE[position],
                                   position=FREE_AGENT_API_POSITION.get(position, position))
        for player in found:
            pid = str(player.playerId)
            points = _week_stat(player, week, 'points')
            # Rostered that week, or no score: leave out (a missing score would void the baselines).
            if pid in rostered or pid in rows or points is None:
                continue
            rows[pid] = dict(player_id=pid, player_name=player.name, position=_position(player),
                             eligible_positions=_position(player), nfl_team=getattr(player, 'proTeam', None),
                             fantasy_points_actual=points,
                             projected=_week_stat(player, week, 'projected_points'))
    if positions and not rows:
        warnings.warn(f'ESPN returned no scored free agents for week {week}; market baselines will be unavailable')
    return rows


def draft_picks(league):
    picks, count = {}, len(league.teams)
    for pick in getattr(league, 'draft', None) or []:
        team = getattr(pick.team, 'team_id', None)
        if team is None:
            continue
        pid = str(pick.playerId)
        picks[pid] = dict(player_id=pid, round=pick.round_num,
                          pick=(pick.round_num - 1) * count + pick.round_pick, team_id=team)
    return picks


TRADE_COLUMNS = ['trade_id', 'trade_date', 'team_id', 'role', 'player_id', 'player_name', 'kind']


def activity_pages(league, page=100, max_pages=60):
    """Every logged transaction, newest first, paging through espn-api's recent_activity."""
    seen, found, offset = set(), [], 0
    for _ in range(max_pages):
        try:
            batch = list(league.recent_activity(size=page, offset=offset))
        except TypeError:    # older espn-api without paging: take one large page
            return list(league.recent_activity(size=page * 10))
        fresh = []
        for activity in batch:
            key = (activity.date, tuple((getattr(a[0], 'team_id', a[0]), str(a[1]), str(a[2])) for a in activity.actions))
            if key not in seen:
                seen.add(key)
                fresh.append(activity)
        found += fresh
        if len(batch) < page or not fresh:
            break
        offset += page
    return found


def _player_fields(player):
    if isinstance(player, str):
        return None, player
    return getattr(player, 'playerId', None), getattr(player, 'name', str(player))


def _day(stamp):
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    return str(stamp)[:10]


def trade_log(league):
    """Rows describing every trade in ESPN's transaction log, or None when the log cannot be read.

    ESPN logs every moved player twice: once for the team that sent him (TRADE_SENT) and once for the team
    that received him (TRADE_RECEIVED). Each row keeps that role ('sent', 'received', or 'moved' when the
    label does not say). The report still confirms the week and direction against the weekly rosters.
    An empty list means the log was read and the league has no trades yet.
    """
    try:
        activities = activity_pages(league)
    except Exception as error:
        warnings.warn(f'Could not read the ESPN transaction log ({error}); trades will be inferred from rosters')
        return None
    rows = []
    for activity in activities:
        moves = [a for a in activity.actions if 'TRADE' in str(a[1]).upper()]
        if not moves:
            continue
        label = '|'.join(sorted(str(_player_fields(a[2])[1]) for a in moves))
        trade_id = f"t{activity.date}_{hashlib.md5(label.encode()).hexdigest()[:6]}"
        for action in moves:
            pid, name = _player_fields(action[2])
            text = str(action[1]).upper()
            role = 'sent' if 'SENT' in text else 'received' if 'RECEIVED' in text else 'moved'
            rows.append(dict(trade_id=trade_id, trade_date=_day(activity.date),
                             team_id=getattr(action[0], 'team_id', action[0]), role=role,
                             player_id=None if pid is None else str(pid), player_name=name, kind='trade'))
    return rows


def write_table(path, table):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    table.to_csv(temporary, index=False, compression={'method': 'gzip', 'mtime': 0})
    temporary.replace(path)


def backfill_season(season, league_id, start_week, end_week, overwrite=False, *,
                    settings, league_nickname, refresh_latest=True, league=None):
    year = season
    metadata, league = league_metadata(year, league_id, league)
    completed = metadata['current_week'] if metadata['is_finished'] else metadata['current_week'] - 1
    regular_end = metadata['playoff_start_week'] - 1
    if not 1 <= start_week <= end_week <= min(completed, regular_end):
        raise ValueError('ESPN collection requires completed regular-season weeks')
    root = settings.data_dir / storage_league_name(league_nickname, league_id) / str(year)
    old = {}
    if (root / 'metadata.json').exists():
        old = json.loads((root / 'metadata.json').read_text())
        if old.get('provider') != 'espn' or str(old.get('league_id')) != str(league_id):
            raise ValueError('Archive destination already belongs to a different league/provider')

    names = {t.team_id: t.team_name for t in league.teams}
    key = lambda team: team_key(league_id, team)
    schedule = {}
    for team in league.teams:
        row = {'team_key': key(team.team_id)}
        for week in range(1, regular_end + 1):
            opponent = team.schedule[week - 1] if week - 1 < len(team.schedule) else None
            row[f'week_{week}'] = key(opponent.team_id) if opponent is not None and hasattr(opponent, 'team_id') else None
        schedule[team.team_id] = row
    picks = draft_picks(league)
    division_ids = {getattr(t, 'division_id', None) for t in league.teams} - {None}
    has_divisions = len(division_ids) > 1

    statuses = {}
    for week in range(start_week, end_week + 1):
        folders = ['draft', 'league_settings', 'player_data', 'projection_data',
                   'schedule', 'team_data', 'divisions', 'points recon']
        paths = {f: root / f / f"{f.replace(' ', '_')}_week_{week}.csv.gz" for f in folders}
        if not overwrite and not (refresh_latest and week == end_week) and all(p.exists() for p in paths.values()):
            statuses[week] = {f: 'skipped' for f in folders}
            continue
        sides = []
        for box in league.box_scores(week):
            if not box.home_team or not box.away_team:
                raise ValueError(f'ESPN week {week}: byes and non head-to-head matchups are not supported')
            sides += [(box.home_team, box.home_score, box.home_lineup),
                      (box.away_team, box.away_score, box.away_lineup)]
        if sorted(t.team_id for t, _, _ in sides) != sorted(names):
            raise ValueError(f'ESPN week {week}: box scores do not cover every team exactly once')
        slots = lineup_slots(sides)
        team_rows, player_rows, projection_rows, official, rostered = [], [], [], [], set()
        for team, score, lineup in sides:
            filled = Counter()
            for p in lineup:
                pid, slot = str(p.playerId), SLOTS.get(p.slot_position, p.slot_position)
                if pid in rostered:
                    raise ValueError(f'ESPN week {week}: player {pid} appears on two rosters')
                rostered.add(pid)
                starting = slot not in BENCH
                filled[slot] += starting
                team_rows.append(dict(player_id=pid, team_id=team.team_id, team_name=names[team.team_id],
                                      roster_slot=slot, is_starting=starting, roster_eligibility_known=True))
                player_rows.append(dict(player_id=pid, player_name=p.name, position=_position(p),
                                        eligible_positions=_position(p), nfl_team=getattr(p, 'proTeam', None),
                                        fantasy_points_actual=p.points))
                projection_rows.append(dict(player_id=pid, projected_points=p.projected_points,
                                            projection_week_returned=week))
            # An empty starter slot scores zero and stays visible in reconciliation.
            for slot, count in slots.items():
                for index in range(count - filled[slot]):
                    empty = f'empty-{team.team_id}-{slot}-{index}'
                    team_rows.append(dict(player_id=empty, team_id=team.team_id, team_name=names[team.team_id],
                                          roster_slot=slot, is_starting=True, roster_eligibility_known=False))
                    player_rows.append(dict(player_id=empty, player_name='(empty slot)', position=None,
                                            eligible_positions='', nfl_team=None, fantasy_points_actual=0.0))
                    projection_rows.append(dict(player_id=empty, projected_points=None,
                                                projection_week_returned=week))
            official.append(dict(team_id=team.team_id, official_points=score))
        for pid, row in free_agents(league, week, free_agent_positions(slots), rostered).items():
            projection_rows.append(dict(player_id=pid, projected_points=row.pop('projected'),
                                        projection_week_returned=week))
            player_rows.append(row)

        config = [dict(setting_type='league', num_playoff_teams=league.settings.playoff_team_count,
                       playoff_start_week=metadata['playoff_start_week'], uses_playoff=1)]
        config += [dict(setting_type='roster_position', position=p, count=n) for p, n in slots.items()]
        for item in getattr(league.settings, 'scoring_format', None) or []:
            if isinstance(item, dict):
                config.append(dict(setting_type='scoring_stat', stat_id=item.get('abbr', item.get('id')),
                                   value=item.get('points')))
        divisions = [dict(team_id=t.team_id, team_name=names[t.team_id],
                          division_id=t.division_id if has_divisions else None,
                          division_name=t.division_name if has_divisions else None) for t in league.teams]
        tables = {'draft': pd.DataFrame(list(picks.values()), columns=['player_id', 'round', 'pick', 'team_id']),
                  'league_settings': pd.DataFrame(config), 'player_data': pd.DataFrame(player_rows),
                  'projection_data': pd.DataFrame(projection_rows), 'team_data': pd.DataFrame(team_rows),
                  'points recon': pd.DataFrame(official), 'divisions': pd.DataFrame(divisions),
                  'schedule': pd.DataFrame(schedule.values())}
        base = dict(season=year, league_id=str(league_id), league_key=metadata['league_key'], week=week)
        for folder, table in tables.items():
            for column, value in base.items():
                table[column] = value
            path = paths[folder]
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            table.to_csv(temporary, index=False, compression={'method': 'gzip', 'mtime': 0})
            temporary.replace(path)
        statuses[week] = {f: 'written' for f in folders}
    # The log is a full history, so each run saves one fresh snapshot at the latest week. It sits outside the
    # per-week skip check on purpose: collecting it never re-collects (and so never changes) older weeks.
    log = trade_log(league)
    if log is not None:
        table = pd.DataFrame(log or [dict(kind='none')], columns=TRADE_COLUMNS)
        for column, value in dict(season=year, league_id=str(league_id), league_key=metadata['league_key'],
                                  week=end_week).items():
            table[column] = value
        write_table(root / 'trades' / f'trades_week_{end_week}.csv.gz', table)
        statuses.setdefault(end_week, {})['trades'] = 'written'
    root.mkdir(parents=True, exist_ok=True)
    metadata['last_collected_week'] = max(end_week, old.get('last_collected_week', 0))
    (root / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return statuses


def probe(league, week):
    """Print what ESPN returns for one week, to check the collector before publishing."""
    sides = []
    for box in league.box_scores(week):
        sides += [(box.home_team, box.home_score, box.home_lineup),
                  (box.away_team, box.away_score, box.away_lineup)]
    slots = lineup_slots(sides)
    print(f'League: {league.settings.name} | teams: {len(league.teams)} | '
          f'regular season weeks: {league.settings.reg_season_count} | current week: {league.current_week}')
    print('Lineup slots found:', dict(slots))
    for team, score, lineup in sides:
        starters = sum(p.points for p in lineup if SLOTS.get(p.slot_position, p.slot_position) not in BENCH)
        flag = '' if abs(starters - score) < 0.01 else '   <-- starters do not add up to the official score'
        print(f'  {team.team_name:<28} official {score:>7.2f}  starters {starters:>7.2f}{flag}')
    rostered = {str(p.playerId) for _, _, lineup in sides for p in lineup}
    found = free_agents(league, week, free_agent_positions(slots), rostered)
    projected = sum(row['projected'] is not None for row in found.values())
    print(f'Free agents with week-{week} points: {len(found)} ({projected} with projections)')
    print(f"Draft picks found: {len(draft_picks(league))}")
    try:
        activities = activity_pages(league)
    except Exception as error:
        print(f'Transaction log: could not be read ({type(error).__name__}: {error})')
        return
    trades = [a for a in activities if any('TRADE' in str(x[1]).upper() for x in a.actions)]
    print(f'Transaction log: {len(activities)} transactions read, {len(trades)} of them trades')
    kinds = Counter(str(x[1]) for a in activities for x in a.actions)
    print('  action labels seen:', dict(kinds))
    for activity in trades[:3]:
        print(f'  trade on {_day(activity.date)}:')
        for team, action, player, *rest in activity.actions:
            pid, name = _player_fields(player)
            print(f"    team {getattr(team, 'team_id', team)} | {action} | {name} | player id {pid}")
    if trades:
        sample = trades[0]
        print('  activity attributes:', sorted(x for x in dir(sample) if not x.startswith('_')))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit('usage: python -m report_code.espn LEAGUE_ID YEAR WEEK')
    probe(connect(sys.argv[2], sys.argv[1]), int(sys.argv[3]))
