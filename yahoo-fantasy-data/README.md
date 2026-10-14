# yahoo-fantasy-data

`yahoo-fantasy-data` archives **public** Yahoo Fantasy Football leagues into immutable, compressed CSV snapshots. It is intended to be a small, reusable data repository suitable for a public GitHub project. It never writes credentials, browser cookies, or private-league data.

Yahoo has two relevant interfaces: the supported Fantasy Sports API, which commonly requires OAuth, and an unofficial read-only public endpoint. This project attempts the anonymous public endpoint first. OAuth is optional and is never needed just to install, run unit tests, or make anonymous attempts. Unofficial endpoints can change or stop providing historical data at any time.

## Install

```bash
cd yahoo-fantasy-data
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

Optional OAuth configuration belongs in a local `.env` (copy `.env.example`) or environment variables:

```text
YAHOO_CLIENT_ID=
YAHOO_CLIENT_SECRET=
YAHOO_REFRESH_TOKEN=
YAHOO_GAME_ID=
```

No value is included in the repository. `YAHOO_GAME_ID` is only an explicit fallback if Yahoo cannot discover a season's NFL game ID.

## Collect data

```bash
python -m yahoo_fantasy_data collect --season 2025 --league 707737 --week 5
python -m yahoo_fantasy_data backfill --season 2025 --league 707737
python -m yahoo_fantasy_data test --season 2025 --league 707737 --week 5
```

`collect` writes each independent collector that succeeds. A failed projection request, for example, does not discard successfully retrieved roster or schedule snapshots. Existing snapshots are skipped by default; use `--overwrite` only when deliberately replacing a file. `backfill` resolves `end_week` from league metadata when available and proceeds sequentially with a small configurable delay.

The `test` command performs no writes. It reports anonymous/public reachability, OAuth requirements, row counts, and small player-projection and roster samples.

## Output layout

```text
data/{league_id}/{season}/
  player_data/player_data_week_{week}.csv.gz
  projection_data/projection_data_week_{week}.csv.gz
  team_data/team_data_week_{week}.csv.gz
  schedule/schedule_week_{week}.csv.gz
  divisions/divisions_week_{week}.csv.gz
  draft/draft_week_{week}.csv.gz
  league_settings/league_settings_week_{week}.csv.gz
  metadata.json
```

Week numbers are intentionally not zero-padded. Every practical dataset carries `season`, `week`, `game_id`, `league_id`, and `league_key` (for example `461.l.707737`). Metadata records collection time, league identifiers, discovered week bounds, and the status/source of each collector.

## Dataset meanings

- `player_data` is the available league player universe, including ownership and actual information Yahoo returns.
- `projection_data` is Yahoo's individual-player projection response for the requested week. It makes an anonymous request with historical-week and projected-stat parameters; it never scrapes HTML or uses cookies. If Yahoo demands authentication, it reports an authentication/projection-unavailable result instead of creating a fake file.
- `team_data` is a historical roster snapshot: one player in one fantasy-team slot. It requests the historical week directly and raises a clear error rather than substituting a current roster.
- `schedule` contains fantasy-team matchup data only, not roster assignments.
- `divisions` contains one row per fantasy team with `team_id`, `team_key`, `team_name`, `division_id`, and `division_name`. Manager contact fields are excluded. Assignments reflect current Yahoo settings and are reused within each backfill run.
- `draft` preserves draft results in a weekly snapshot for a uniform filesystem contract.
- `league_settings` provides readable league, roster-position, and scoring-stat rows rather than one opaque JSON blob.

Yahoo response fields vary by endpoint and season. The normalizers retain extra fields where possible, use snake_case headers, and do not manufacture unavailable fields. Historical projection and roster availability must be validated for each league/season. Compare `team_data` between two weeks to inspect real roster changes; the package does not infer transactions.

## Read compressed files

```python
import pandas as pd

projections = pd.read_csv(
    "data/707737/2025/projection_data/projection_data_week_5.csv.gz"
)
rosters = pd.read_csv(
    "data/707737/2025/team_data/team_data_week_5.csv.gz"
)
```

## Tests

Unit tests mock all Yahoo traffic:

```bash
pytest
```

The optional manual anonymous smoke test for the known public 2025 league is deliberately opt-in:

```bash
YAHOO_INTEGRATION=1 pytest -m integration tests/test_integration_public.py
```

It uses no OAuth or cookies. Run the CLI `test` command first and treat its report as the authoritative statement of which endpoints Yahoo currently permits anonymously.

### Weekly team points for reconciliation

Collection and backfill now also write:

```text
data/<league nickname>/<season>/points recon/points_recon_week_<week>.csv.gz
```

These are gzip-compressed CSVs, readable directly with `pandas.read_csv(path)`.
Each row contains `season`, `week`, league identifiers, `team_id`, `team_key`,
`team_name`, `official_points`, `points_coverage_type`, and `points_week_returned`.
The source is Yahoo's league teams weekly stats endpoint, independent of the
player-score collector. It includes teams without a scheduled matchup.

`official_points` is that team's points **for the requested week**, not a
cumulative season total. Complete weekly snapshots can later be concatenated
and summed per team for season reconciliation. Scores for an ongoing week
reflect the value at collection time and can change with play/stat corrections.
Wrong-week, season-coverage, empty, or nonnumeric team-score responses fail
without saving a snapshot.

Existing files remain skipped with `overwrite=False`. Running backfill on an
older archive adds the missing `points recon` snapshots while retaining the
other existing datasets. The metadata status key is `points_recon`.
Restart a running notebook kernel after updating the collector to load the new
code. Bronze/silver ingestion of this folder is a separate follow-up.

### Player page size and pacing

Actual-player and projection pulls request 200 players per page by default.
The public endpoint was checked with 200-player actual-stat and projection
requests; both returned 200 unique players with populated scores. Override with `YAHOO_PLAYER_PAGE_SIZE=25` (or another positive
integer) if needed. Pagination advances by the number actually returned and
continues until an empty page, so a server-side cap cannot silently truncate
the archive. Repeated player IDs fail collection rather than looping forever.

`YAHOO_REQUEST_DELAY` now also applies **between player/projection page requests**
(default 1.5 seconds through `load_settings`). Previously these loops had no
inter-page delay. For a 1,250-player pool, 200-player pages need 8 requests
including the terminal empty page, compared with 51 using 25-player pages.

HTTP 429/999 responses stop collection/backfill; the notebook batch also stops
rather than moving to the next league. They are not automatically retried by
the HTTP adapter. Completed snapshots remain on disk; retry later with
`overwrite=False` to resume missing snapshots. Restart the notebook kernel to
load these changes. Larger pages and pacing reduce request volume/bursts but
cannot guarantee Yahoo will not block requests.

Within a backfill run, draft results, league settings, and division assignments are each fetched once
and reused for missing weekly snapshots, with the snapshot week updated. These
endpoints expose the original draft/current settings, not historical weekly
settings. The cache is discarded after each league backfill, so subsequent
runs fetch fresh data. Reusing these responses reduces repeated requests across weeks. Existing
snapshots still remain untouched unless overwrite is explicitly enabled.

Team rosters and reconciliation points already retrieve all teams per weekly
request. The schedule matrix is already built once per backfill and reused;
its underlying scoreboards still require separate weekly requests.

### One-time Yahoo account authorization

The refresh token is issued after account consent; it is not shown in Yahoo's
app settings. Put your client ID and secret in the local `.env`, then run from
`yahoo-fantasy-data` with the environment activated:

```bash
python -m yahoo_fantasy_data auth --redirect-uri 'https://localhost/callback'
```

Use the **exact redirect URI registered for your app** in place of the example.
Open the printed link and approve access with your league account. Copy the full
redirected browser address into the terminal prompt, even if the callback page
cannot load. No callback server is needed. The command checks the returned state,
exchanges the code, and saves `YAHOO_REFRESH_TOKEN` in `.env` with owner-only
permissions. Use `--env-file /path/to/.env` to choose another configuration file.
Tokens and authorization codes are not printed. `.env` is ignored by Git.

Restart your notebook kernel after authorization. All collectors now use a shared public-first provider. If a public request fails,
it tries the official OAuth API when credentials are configured. Public rate limits
still stop collection rather than triggering retries through another endpoint.
Division pulls use the same public-first request policy, without manager enrichment.
Existing snapshots remain skipped unless overwritten. Backfill with
`overwrite=True` refreshes all datasets, not just divisions.

For notebook sessions, you can instead keep the token only in memory:

```python
from yahoo_fantasy_data.auth import authorize_in_memory

settings = authorize_in_memory(settings, redirect_uri="YOUR_REGISTERED_REDIRECT_URI")
# The existing collect/backfill calls using settings=settings now use this token.
# If running the batch cell, assign batch_settings = settings instead of reloading it.
```

This does not write a token to `.env`. Keep using the returned settings object;
calling `load_settings()` again does not retain the in-memory token. Sign in
again after restarting the kernel. Saved refresh tokens normally avoid repeated
browser sign-in; memory-only storage is a session preference, not a Yahoo requirement.
