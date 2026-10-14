"""Lazy bronze → silver → gold processing for one league/season archive."""
from __future__ import annotations

from functools import cached_property
from pathlib import Path
from datetime import datetime, timezone
import importlib
import json
import re

import pandas as pd

NON_STARTERS = {'BN', 'IR', 'IR+', 'NA', 'RES'}
REPORTS = ('boned_index', 'boned_detail', 'freaky_friday', 'bhole', 'studs_duds',
           'grower_shower', 'managerial_expertise', 'vobl', 'playoff_odds', 'draft_analysis')


def records(frame):
    """JSON-safe records: missing/nonfinite numeric values become null."""
    return json.loads(frame.to_json(orient='records'))


def complete_sum(values):
    return values.sum() if len(values) and values.notna().all() else float('nan')


class ReportProcessor:
    """Inputs are a league/season directory (or metadata.json) and optional week.

    ``data_files`` overrides individual sources with a path or list of paths.
    Official scores load from points recon; official_scores_path overrides them.
    All weekly sources are read through current_week; static snapshots use the
    newest available snapshot at or before it. No future snapshot is substituted.
    """

    sources = {'draft': 'draft', 'settings': 'league_settings', 'player': 'player_data',
               'projection': 'projection_data', 'schedule': 'schedule', 'team_data': 'team_data',
               'divisions': 'divisions', 'points_recon': 'points recon'}

    def __init__(self, data_path, current_week=None, *, data_files=None,
                 official_scores_path=None, tolerance=0.01, simulation_count=10000,
                 random_seed=42, playoff_teams=None, playoff_byes=None, divisions=None,
                 playoff_blend_weeks=10):
        path = Path(data_path)
        self.data_path = path.parent if path.is_file() else path
        metadata_path = path if path.is_file() else path / 'metadata.json'
        self.metadata = json.loads(metadata_path.read_text())
        week = current_week if current_week not in (None, '') else self.metadata.get(
            'current_week', self.metadata.get('last_collected_week'))
        if week is None or isinstance(week, bool) or not str(week).isdigit() or int(week) < 1:
            raise ValueError('A positive current_week or metadata current_week/last_collected_week is required')
        self.current_week = int(week)
        self.data_files = data_files or {}
        self.official_scores_path = official_scores_path
        self.tolerance = tolerance
        if tolerance < 0 or simulation_count < 1:
            raise ValueError('tolerance must be nonnegative and simulation_count positive')
        if isinstance(simulation_count, bool) or not isinstance(simulation_count, int) or simulation_count % 10:
            raise ValueError('simulation_count must be a positive multiple of 10 for equal exponent groups')
        self.simulation_count, self.random_seed = simulation_count, random_seed
        self.playoff_teams, self.playoff_byes = playoff_teams, playoff_byes
        if isinstance(playoff_blend_weeks, bool) or not isinstance(playoff_blend_weeks, int) or playoff_blend_weeks < 1:
            raise ValueError('playoff_blend_weeks must be a positive integer')
        self.playoff_blend_weeks = playoff_blend_weeks
        self._divisions_override = ({str(k): str(v) for k, v in divisions.items()}
                                    if divisions is not None else None)
        self.source_files = {}
        self._gold = {}

    @cached_property
    def team_nicknames(self):
        """Optional display names; a bad file or entry never blocks reporting."""
        try:
            mapping = json.loads((self.data_path / 'team_nicknames.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if not isinstance(mapping, dict):
            return {}
        return {team_id: nickname.strip() for team_id, nickname in mapping.items()
                if isinstance(nickname, str) and nickname.strip()}

    def _team_display_names(self, frame):
        return frame.team_id.astype('string').map(self.team_nicknames).fillna(frame.team_name)

    def read_files(self, table, *, latest=False):
        """Read CSV/CSV.GZ paths, validate snapshot weeks, concatenate once."""
        source = self.data_files.get(table, self.data_path / self.sources.get(table, table))
        paths = [Path(p) for p in source] if isinstance(source, (list, tuple)) else [Path(source)]
        files = []
        for path in paths:
            files.extend(sorted(path.glob('*.csv*')) if path.is_dir() else [path])
        eligible = []
        for path in files:
            match = re.search(r'_week_(\d+)\.csv(?:\.gz)?$', path.name)
            if match and int(match[1]) > self.current_week:
                continue
            frame = pd.read_csv(path, dtype={k: 'string' for k in
                                ('player_id', 'team_id', 'league_id', 'game_id', 'team_key', 'division_id')})
            snapshot = int(match[1]) if match else None
            if table != 'schedule':
                if 'week' not in frame:
                    if snapshot is None:
                        raise ValueError(f'{path}: missing week and snapshot week in filename')
                    frame['week'] = snapshot
                frame['week'] = pd.to_numeric(frame.week, errors='raise').astype(int)
                if snapshot is not None and not frame.week.eq(snapshot).all():
                    raise ValueError(f'{path}: row week does not match filename')
                frame = frame[frame.week <= self.current_week]
                if frame.empty and table != 'draft':
                    continue
                if not frame.empty:
                    snapshot = int(frame.week.max())
            eligible.append((snapshot or 0, path, frame))
        if not eligible:
            raise FileNotFoundError(f'No {table} files through week {self.current_week} in {source}')
        if latest:
            newest = max(x[0] for x in eligible)
            eligible = [x for x in eligible if x[0] == newest]
            if len(eligible) != 1:
                raise ValueError(f'Ambiguous {table} snapshot for week {newest}')
        self.source_files[table] = [str(x[1]) for x in eligible]
        frame = pd.concat([x[2] for x in eligible], ignore_index=True, sort=False)
        if latest and table != 'schedule':
            frame = frame[frame.week == frame.week.max()].reset_index(drop=True)
        for column in ('league_key', 'season'):
            if column in frame:
                values = frame[column].dropna().astype(str).unique()
                expected = self.metadata.get(column)
                if len(values) > 1 or (len(values) and expected is not None and values[0] != str(expected)):
                    raise ValueError(f'{table}: mixed or incorrect {column}: {values}')
        return frame

    @cached_property
    def bronze_draft(self): return self.read_files('draft', latest=True)
    @cached_property
    def bronze_settings(self): return self.read_files('settings', latest=True)
    @cached_property
    def bronze_player(self): return self.read_files('player')
    @cached_property
    def bronze_projection(self): return self.read_files('projection')
    @cached_property
    def bronze_schedule(self): return self.read_files('schedule', latest=True)
    @cached_property
    def bronze_team_data(self): return self.read_files('team_data')
    @cached_property
    def bronze_divisions(self): return self.read_files('divisions', latest=True)
    @cached_property
    def bronze_points_recon(self): return self.read_files('points_recon')

    @staticmethod
    def _unique(frame, keys, name):
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError(f'{name}: null or duplicate join keys {keys}')

    @cached_property
    def divisions(self):
        """Use archived division IDs unless the caller supplies an explicit mapping."""
        if self._divisions_override is not None:
            return self._divisions_override
        try:
            frame = self.silver_divisions
        except FileNotFoundError:
            return {}
        if frame.division_id.isna().all():
            return {}
        if frame.division_id.isna().any():
            raise ValueError('divisions: missing division_id for some teams')
        return dict(zip(frame.team_id.astype(str), frame.division_id.astype(str)))

    @cached_property
    def silver_divisions(self):
        """Team division assignments from the latest snapshot through current_week."""
        frame = self.bronze_divisions.copy()
        self._unique(frame, ['team_id'], 'divisions')
        frame['team_name'] = self._team_display_names(frame)
        return frame[['team_id', 'team_name', 'division_id', 'division_name', 'week']].reset_index(drop=True)

    @cached_property
    def silver_player(self):
        keys = ['player_id', 'week']
        base = self.bronze_player.copy()
        self._unique(base, keys, 'player')
        for name, table, join in [('projection', self.bronze_projection, keys),
                                  ('team', self.bronze_team_data, keys),
                                  ('draft', self.bronze_draft, ['player_id'])]:
            self._unique(table, join, name)
            right = table.rename(columns={c: f'{name}_{c}' for c in table if c not in join})
            base = base.merge(right, on=join, how='left', validate='one_to_one' if name != 'draft' else 'many_to_one')
        base = base.copy()
        # Roster ownership belongs to that week, never to the draft snapshot.
        for c in ('team_id', 'team_name', 'roster_slot', 'is_starting'):
            base[c] = base.get(f'team_{c}', pd.Series(index=base.index, dtype='object'))
        base['team_name'] = self._team_display_names(base)
        base['is_starting'] = base.is_starting.astype(str).str.lower().isin(['true', '1'])
        base['actual_points'] = pd.to_numeric(base.get('fantasy_points_actual'), errors='coerce')
        base['projected_points'] = pd.to_numeric(base.get('projection_projected_points'), errors='coerce')
        # Season coverage mislabeled with a snapshot week is not a weekly score.
        coverage = [c for c in base if re.fullmatch(r'player_points(?:_\d+)?_coverage_type', c)]
        for c in coverage:
            base.loc[base[c].notna() & base[c].ne('week'), 'actual_points'] = float('nan')
        if 'projection_projection_week_returned' in base:
            returned = pd.to_numeric(base['projection_projection_week_returned'], errors='coerce')
            base.loc[returned.notna() & returned.ne(base.week), 'projected_points'] = float('nan')
        base['is_stud'] = (base.actual_points > 2 * base.projected_points) & (base.actual_points >= 20)
        base['is_dud'] = (base.actual_points <= .25 * base.projected_points) & (base.projected_points >= 10)
        base['volatility_known'] = base.actual_points.notna() & base.projected_points.notna()
        from .lineups import eligibility
        position_columns = [c for c in base if 'eligible_positions' in c or c in ('primary_position', 'position', 'display_position')]
        positions = base[position_columns].apply(eligibility, axis=1)
        # Coalesce collector aliases into one public column per concept.
        for column, aliases in {
            'player_name': ('player_name', 'name_full'),
            'position': ('primary_position', 'position', 'display_position'),
            'nfl_team': ('nfl_team', 'editorial_team_abbr'),
            'bye_week': ('bye_week', 'bye_weeks_week'),
        }.items():
            values = pd.Series(pd.NA, index=base.index, dtype='object')
            for alias in aliases:
                if alias in base:
                    values = values.fillna(base[alias])
            base[column] = values
        base['bye_week'] = pd.to_numeric(base.bye_week, errors='coerce').astype('Int64')
        base['eligible_positions'] = positions.map(sorted)
        columns = [
            'player_id', 'player_name', 'week', 'position', 'eligible_positions',
            'nfl_team', 'bye_week', 'team_id', 'team_name', 'roster_slot', 'is_starting',
            'actual_points', 'projected_points', 'draft_round', 'draft_pick', 'draft_team_id',
            'is_stud', 'is_dud', 'volatility_known',
        ]
        from .lineups import add_player_lineup_columns
        return add_player_lineup_columns(base.reindex(columns=columns), self.lineup_slots,
                                         self.bronze_team_data)

    @cached_property
    def teams(self):
        t = self.bronze_team_data.sort_values('week').drop_duplicates('team_id', keep='last')
        t = t[['team_id', 'team_name']].copy()
        schedule_ids = self.bronze_schedule.team_key.astype('string').str.rsplit('.t.').str[-1]
        t = pd.DataFrame({'team_id': schedule_ids}).drop_duplicates().merge(t, on='team_id', how='outer', validate='one_to_one')
        t['team_name'] = t.team_name.fillna(t.team_id.map(lambda value: f'Team {value}'))
        t['team_name'] = self._team_display_names(t)
        t['manager'] = t.team_name  # Collector does not archive manager names.
        return t.sort_values('team_id').reset_index(drop=True)

    @cached_property
    def silver_schedule(self):
        raw = self.bronze_schedule
        weeks = [c for c in raw if re.fullmatch(r'week_\d+', c)]
        frame = raw.melt(id_vars='team_key', value_vars=weeks, var_name='week', value_name='opponent_key')
        frame['team_id'] = frame.team_key.astype('string').str.rsplit('.t.').str[-1]
        frame['opponent_id'] = frame.opponent_key.astype('string').str.rsplit('.t.').str[-1]
        frame['week'] = frame.week.str.removeprefix('week_').astype(int)
        self._unique(frame, ['team_id', 'week'], 'schedule')
        return frame[['team_id', 'week', 'opponent_id']]

    @cached_property
    def silver_reconciliation(self):
        players = self.silver_player
        starters = players[players.is_starting & players.team_id.notna()]
        totals = starters.groupby(['team_id', 'week']).agg(
            calculated_points=('actual_points', complete_sum),
            starter_count=('player_id', 'size'), missing_points=('actual_points', lambda s: s.isna().sum()))
        weeks = range(int(self.metadata.get('start_week', 1)), self.current_week + 1)
        expected = pd.MultiIndex.from_product([self.teams.team_id, weeks], names=['team_id', 'week'])
        out = totals.reindex(expected).reset_index()
        # Players in roster files but absent from the player universe must not disappear silently.
        roster = self.bronze_team_data
        roster = roster[roster.is_starting.astype(str).str.lower().isin(['true', '1'])]
        counts = roster.groupby(['team_id', 'week']).size().rename('roster_starter_count')
        out = out.merge(counts, on=['team_id', 'week'], how='left')
        out['official_points'] = float('nan')
        official = None
        if self.official_scores_path:
            official = pd.read_csv(self.official_scores_path, dtype={'team_id': 'string'})
        elif 'points_recon' in self.data_files or (self.data_path / self.sources['points_recon']).is_dir():
            try:
                official = self.bronze_points_recon
            except FileNotFoundError as exc:
                # Older archives may have no snapshots through the selected week.
                # Explicit overrides and unreadable files should still fail.
                if 'points_recon' in self.data_files or exc.filename is not None:
                    raise
        if official is not None:
            self._unique(official, ['team_id', 'week'], 'official scores')
            out = out.drop(columns='official_points').merge(
                official[['team_id', 'week', 'official_points']], on=['team_id', 'week'], how='left', validate='one_to_one')
        out['difference'] = out.calculated_points - out.official_points
        out['status'] = 'unverified'
        complete = out.calculated_points.notna() & out.starter_count.eq(out.roster_starter_count)
        out.loc[~complete, 'status'] = 'missing_player_points_or_roster'
        out.loc[complete & out.official_points.notna(), 'status'] = 'mismatch'
        out.loc[complete & out.difference.abs().le(self.tolerance), 'status'] = 'matched'
        out.loc[~complete, 'calculated_points'] = float('nan')
        return out

    @cached_property
    def silver_season_reconciliation(self):
        out = self.silver_reconciliation.groupby('team_id').agg(
            calculated_points=('calculated_points', complete_sum),
            official_points=('official_points', complete_sum),
            matched_weeks=('status', lambda s: s.eq('matched').sum()),
            issue_weeks=('status', lambda s: s.ne('matched').sum())).reset_index()
        out['difference'] = out.calculated_points - out.official_points
        out['status'] = out.issue_weeks.map(lambda n: 'matched' if n == 0 else 'incomplete_or_mismatch')
        return out

    def validate_reconciliation(self):
        issues = self.silver_reconciliation.query("status != 'matched'")
        if not issues.empty:
            raise ValueError(f'{len(issues)} team-weeks could not be reconciled; inspect silver_reconciliation')

    @cached_property
    def silver_team_week(self):
        out = self.silver_reconciliation.rename(columns={'calculated_points': 'actual_points'})
        out = out.merge(self.silver_schedule, on=['team_id', 'week'], how='left', validate='one_to_one')
        opponent = out[['team_id', 'week', 'actual_points']].rename(
            columns={'team_id': 'opponent_id', 'actual_points': 'opponent_points'})
        out = out.merge(opponent, on=['opponent_id', 'week'], how='left', validate='many_to_one')
        out['win'] = (out.actual_points > out.opponent_points).astype(float)
        out.loc[out.actual_points.eq(out.opponent_points), 'win'] = .5
        out.loc[out.actual_points.isna() | out.opponent_points.isna(), 'win'] = float('nan')
        return out

    @cached_property
    def lineup_slots(self):
        s = self.bronze_settings
        return [(r.position, i + 1) for r in s[s.setting_type.eq('roster_position')].itertuples()
                if r.position not in NON_STARTERS for i in range(int(r.count))]

    @cached_property
    def silver_lineups(self):
        from .lineups import build_lineups
        return build_lineups(self)

    def _gold_table(self, name):
        if name not in self._gold:
            self._gold[name] = importlib.import_module(f'.{name}', __package__).build(self)
        return self._gold[name]

    @property
    def gold_tables(self):
        return {name: self._gold_table(name) for name in REPORTS}

    @cached_property
    def playoff_odds_history(self):
        """Recalculate each outlook using only snapshots available that week."""
        history = []
        for week in range(1, self.current_week + 1):
            snapshot = self if week == self.current_week else type(self)(
                self.data_path, week, data_files=self.data_files,
                official_scores_path=self.official_scores_path, tolerance=self.tolerance,
                simulation_count=self.simulation_count, random_seed=self.random_seed,
                playoff_teams=self.playoff_teams, playoff_byes=self.playoff_byes,
                playoff_blend_weeks=self.playoff_blend_weeks,
                divisions=self._divisions_override,
            )
            try:
                table = snapshot.gold_playoff_odds
                history.append({'week': week, 'rows': records(table),
                                'unavailable_reason': table.attrs.get('unavailable_reason')})
            except FileNotFoundError:
                history.append({'week': week, 'rows': [],
                                'unavailable_reason': 'Required archived snapshots are missing for this week.'})
        return history

    def to_dict(self):
        data = {'meta': {'title': 'The Fantasy League Weekly Report™',
                        'league_name': self.metadata.get('league_name'), 'season': self.metadata.get('season'),
                        'current_week': self.current_week, 'generated_at': datetime.now(timezone.utc).isoformat(),
                        'provider': self.metadata.get('provider', 'yahoo'),
                        'intro': 'Season-to-date fantasy analytics. Unavailable measurements are shown as —. '
                                 + ' '.join(self.metadata.get('notes', []))},
                'teams': records(self.teams)}
        for name, table in self.gold_tables.items():
            module = importlib.import_module(f'.{name}', __package__)
            data[name] = module.payload(self, table)
        data['data_quality'] = {'weekly_reconciliation': records(self.silver_reconciliation),
                                'season_reconciliation': records(self.silver_season_reconciliation),
                                'sources': self.source_files,
                                'notes': self.metadata.get('notes', [])}
        # Round-trip converts nested numpy scalars and NaN as well as table values.
        return json.loads(pd.Series({'payload': data}).to_json())['payload']

    def to_json(self, path=None):
        result = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)
        if path is not None:
            Path(path).write_text(result, encoding='utf-8')
        return result

    def write_html(self, output_path, template_path=None, *, template='original'):
        """Export with original or PC wording; template_path overrides the built-in file."""
        templates = {'original': 'fantasy_weekly_report_dynamic.html',
                     'pc': 'fantasy_weekly_report_pc.html'}
        if template not in templates:
            raise ValueError("template must be 'original' or 'pc'")
        template = Path(template_path) if template_path else Path(__file__).resolve().parents[1] / 'report_template' / templates[template]
        html = template.read_text(encoding='utf-8')
        provider_name = {'sleeper': 'Sleeper', 'espn': 'ESPN'}.get(getattr(self, 'metadata', {}).get('provider'))
        if provider_name:
            # Display wording only; retain legacy JSON keys for compatibility.
            html = html.replace('Yahoo', provider_name)
        pattern = r'(<script\s+id="report-data"\s+type="application/json">).*?(</script>)'
        safe_json = self.to_json().replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        html, count = re.subn(pattern, lambda m: m[1] + '\n' + safe_json + '\n' + m[2], html, flags=re.S)
        if count != 1:
            raise ValueError('Template must contain exactly one report-data JSON script')
        Path(output_path).write_text(html, encoding='utf-8')
        return Path(output_path)


# Each gold property is a DataFrame; report modules handle nested HTML serialization.
for _name in REPORTS:
    setattr(ReportProcessor, f'gold_{_name}', property(lambda self, name=_name: self._gold_table(name)))
