"""Build the report/download site from local archives, optionally backfilling first."""
import argparse
import json
from pathlib import Path

from . import ReportProcessor


def report_templates(template):
    """Expand a run's wording choice into individual HTML templates."""
    if template not in ('original', 'pc', 'both'):
        raise ValueError('template must be original, pc, or both')
    return ('original', 'pc') if template == 'both' else (template,)


def export_silver(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('player', 'schedule'):
        # Fixed gzip header makes identical exports byte-for-byte reproducible.
        with (directory / f'silver_{name}.csv.gz').open('wb') as stream:
            getattr(report, f'silver_{name}').to_csv(
                stream, index=False, compression={'method': 'gzip', 'mtime': 0})


def load_runs(path):
    """Validate every run before collecting data or writing reports."""
    from yahoo_fantasy_data.config import storage_league_name
    runs = json.loads(Path(path).read_text())
    if not isinstance(runs, list) or not runs:
        raise ValueError('Run configuration must be a nonempty JSON list')
    destinations = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError('Each run must be a JSON object')
        unknown = set(run) - {'league_id', 'year', 'nickname', 'week', 'enabled',
                              'overwrite', 'strict', 'template', 'provider'}
        if unknown:
            raise ValueError(f'Unknown run fields: {sorted(unknown)}')
        if run.get('provider', 'yahoo') not in ('yahoo', 'sleeper', 'espn'):
            raise ValueError('provider must be yahoo, sleeper, or espn')
        if type(run.get('year')) is not int or not 2000 <= run['year'] <= 2100:
            raise ValueError('Each run needs an integer year between 2000 and 2100')
        if not isinstance(run.get('league_id'), (str, int)) or isinstance(run['league_id'], bool) or not str(run['league_id']).isdigit():
            raise ValueError('Each run needs a numeric league_id (prefer a JSON string)')
        if not isinstance(run.get('nickname'), str) or not run['nickname'].strip():
            raise ValueError('Each run needs a nonempty nickname')
        if run.get('week') is not None and (type(run['week']) is not int or not 1 <= run['week'] <= 18):
            raise ValueError('Optional week must be an integer from 1 to 18')
        for option in ('enabled', 'overwrite', 'strict'):
            if option in run and type(run[option]) is not bool:
                raise ValueError(f'{option} must be a JSON boolean')
        report_templates(run.get('template', 'original'))
        folder = storage_league_name(run['nickname'], str(run['league_id']))
        destination = (folder, run['year'])
        if run.get('enabled', True):
            if destination in destinations:
                raise ValueError(f'Duplicate league/year output: {destination}')
            destinations.add(destination)
    return [run for run in runs if run.get('enabled', True)]


def resolve_week(run, data_path, *, backfill, settings):
    """Default to completed weeks, capped at the regular-season end."""
    if run.get('week') is not None:
        return run['week']
    from yahoo_fantasy_data.utils import first_value
    if backfill and run.get('provider') == 'sleeper':
        from .sleeper import league_metadata
        metadata, _ = league_metadata(run['year'], str(run['league_id']))
    elif backfill and run.get('provider') == 'espn':
        from .espn import league_metadata
        metadata, _ = league_metadata(run['year'], str(run['league_id']))
    elif backfill:
        from yahoo_fantasy_data.yahoo import league_metadata
        metadata, _, provider, _, key = league_metadata(run['year'], str(run['league_id']), settings)
    else:
        metadata = json.loads((Path(data_path) / 'metadata.json').read_text())
    current = first_value(metadata, 'current_week')
    if current is None:
        raise ValueError(f"{run['nickname']}: current_week metadata is missing; refresh with backfill or configure week explicitly")
    current = int(current)
    if current < 0:
        raise ValueError('current_week must be nonnegative')
    finished = str(first_value(metadata, 'is_finished')).lower() in ('1', 'true')
    completed = current if finished else max(0, current - 1)
    if completed == 0:
        return 0

    playoff_start = first_value(metadata, 'playoff_start_week')
    use_playoff = first_value(metadata, 'uses_playoff')
    if playoff_start is None and str(use_playoff).lower() not in ('0', 'false'):
        if backfill:
            league_settings = provider.settings(key)
            playoff_start = first_value(league_settings, 'playoff_start_week')
            use_playoff = first_value(league_settings, 'uses_playoff')
        else:
            import pandas as pd
            snapshots = list((Path(data_path) / 'league_settings').glob('league_settings_week_*.csv.gz'))
            if snapshots:
                latest = max(snapshots, key=lambda path: int(path.name.split('_week_')[1].split('.')[0]))
                frame = pd.read_csv(latest)
                for column in ('playoff_start_week', 'uses_playoff'):
                    if column in frame and not frame[column].dropna().empty:
                        value = int(frame[column].dropna().iloc[0])
                        if column == 'playoff_start_week':
                            playoff_start = value
                        else:
                            use_playoff = value
    if str(use_playoff).lower() in ('0', 'false'):
        end = first_value(metadata, 'end_week')
        if end is None:
            raise ValueError('end_week is missing for a league without playoffs')
        regular_end = int(end)
    elif playoff_start is not None and 2 <= int(playoff_start) <= 19:
        regular_end = int(playoff_start) - 1
    else:
        raise ValueError(f"{run['nickname']}: playoff_start_week is missing or invalid; cannot determine the regular-season end")
    if not 1 <= regular_end <= 18:
        raise ValueError('Regular-season end must be between 1 and 18')
    return min(completed, regular_end)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('report_runs.json'))
    parser.add_argument('--week', type=int, help='Override the inferred/configured week for all selected runs')
    parser.add_argument('--leagues', nargs='+', help='Filter configured runs by nickname')
    parser.add_argument('--data-dir', type=Path, default=Path('yahoo-fantasy-data/data'))
    parser.add_argument('--docs-dir', type=Path, default=Path('docs'))
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--overwrite', action='store_true', help='Refresh snapshots for all selected runs')
    parser.add_argument('--strict', action='store_true', help='Require official score reconciliation for all runs')
    parser.add_argument('--template', choices=['original', 'pc', 'both'], help='Override configured report wording; both writes two HTML reports')
    args = parser.parse_args()
    if args.week is not None and not 1 <= args.week <= 18:
        parser.error('--week must be between 1 and 18')
    try:
        runs = load_runs(args.config)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.leagues:
        unknown = set(args.leagues) - {run['nickname'] for run in runs}
        if unknown:
            parser.error(f'Unknown or disabled nicknames: {sorted(unknown)}')
        runs = [run for run in runs if run['nickname'] in args.leagues]
    if not runs:
        parser.error('No enabled runs configured')
    from yahoo_fantasy_data.config import load_settings, storage_league_name
    from yahoo_fantasy_data.yahoo import backfill_season
    settings = load_settings(args.data_dir)
    args.docs_dir.mkdir(parents=True, exist_ok=True)
    for run in runs:
        name, year = run['nickname'], run['year']
        folder = storage_league_name(name, str(run['league_id']))
        data_path = args.data_dir / folder / str(year)
        week = args.week if args.week is not None else resolve_week(
            run, data_path, backfill=args.backfill, settings=settings)
        if week == 0:
            print(f'Skipping {name}, {year}: no completed weeks yet', flush=True)
            continue
        print(f'Building {name}, {year}, week {week}', flush=True)
        if args.backfill:
            collect = backfill_season
            if run.get('provider') == 'sleeper':
                from .sleeper import backfill_season as collect
            elif run.get('provider') == 'espn':
                from .espn import backfill_season as collect
            statuses = collect(year, str(run['league_id']), 1, week,
                                       args.overwrite or run.get('overwrite', False),
                                       settings=settings, league_nickname=name,
                                       refresh_latest=True)
            failures = {week: results for week, results in statuses.items()
                        if any(value.startswith('failed:') or value == 'authentication_required'
                               for value in results.values())}
            if failures:
                raise RuntimeError(f'Incomplete backfill for {name}: {failures}')
        report = ReportProcessor(data_path, week)
        if args.strict or run.get('strict', False):
            report.validate_reconciliation()
        templates = report_templates(args.template or run.get('template', 'original'))
        for template in templates:
            suffix = '_pc' if template == 'pc' else ''
            report.write_html(args.docs_dir / f'{folder}_{year}_week{week}{suffix}.html', template=template)
        export_silver(report, args.docs_dir / 'data' / folder / str(year))
        print(f'Saved {name} report and silver tables', flush=True)
    print(f'Reports and data saved in {args.docs_dir}', flush=True)


if __name__ == '__main__':
    main()
