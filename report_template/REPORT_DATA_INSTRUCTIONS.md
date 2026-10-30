# Fantasy Weekly Report — Data Population Instructions

## Purpose

`fantasy_weekly_report_dynamic.html` is a **single reusable report template**. The HTML/CSS/JavaScript should stay essentially unchanged from week to week.

The only part your Python code should populate is the JSON inside:

```html
<script id="report-data" type="application/json">
  ... JSON goes here ...
</script>
```

The JavaScript reads that JSON and automatically builds the report. Team count and completed-week count are data-driven; do not hard-code 12 teams or Week 5 in the Python code.

The report currently supports these sections from the legacy PDFs:

1. Boned Index
2. Boned Index by Manager by Week
3. Freaky Friday
4. Between High Over Low Evaluation (BHOLE)
5. Studs to Duds
6. Grower or Shower
7. Managerial Expertise
8. Value Over Baseline — Expanded
9. Playoff Odds
10. Draft Analysis
   - Historical Points From Rounds
   - Optimal Points From Rounds

---

## Recommended Python workflow

Use four conceptual layers:

```text
Yahoo archived data
      ↓
metric calculation functions
      ↓
report_data Python dict
      ↓
JSON embedded into HTML template
      ↓
League_Week_X.html
```

The report HTML should **not** calculate the fantasy metrics. Python calculates the values; HTML only displays them.

A future Python entry point can look roughly like:

```python
report_data = build_report_data(
    league_id=league_id,
    season=season,
    through_week=week,
)

write_report_html(
    template_path="templates/fantasy_weekly_report_dynamic.html",
    report_data=report_data,
    output_path=f"reports/{league_id}/{season}/week_{week}.html",
)
```

Do not treat this pseudocode as a required package structure. It only shows the intended separation.

---

# 1. Top-level JSON structure

```json
{
  "meta": {},
  "teams": [],
  "boned_index": {},
  "boned_detail": {},
  "freaky_friday": {},
  "bhole": {},
  "studs_duds": {},
  "grower_shower": {},
  "managerial_expertise": {},
  "vobl": {},
  "playoff_odds": {},
  "draft_analysis": {},
  "trades": {}
}
```

Every `team_id` used elsewhere in the JSON must exist once in `teams`.

Use strings for `team_id`. Do not use the manager name as the identifier because manager/team names can change.

---

# 2. `meta`

Required example:

```json
"meta": {
  "title": "The Fantasy League Weekly Report™",
  "season": 2026,
  "league_name": "B League",
  "current_week": 5,
  "author": "Joey Schiazza",
  "generated_at": "2026-09-05",
  "intro": "Weekly fantasy football analytics through Week 5."
}
```

Fields:

| Field | Type | Meaning |
|---|---|---|
| `title` | string | Report title |
| `season` | integer | Fantasy season |
| `league_name` | string | Display name of league |
| `current_week` | integer | Latest completed week included in report |
| `author` | string | Optional author/byline metadata |
| `generated_at` | string | Display date; ISO `YYYY-MM-DD` is recommended |
| `intro` | string | Short report introduction |

The HTML uses `current_week` for display only. Each week-based report should also explicitly supply the weeks it contains.

---

# 3. `teams`

Example:

```json
"teams": [
  {
    "team_id": "1.l.707737.t.1",
    "manager": "Mike",
    "team_name": "The Football Team"
  },
  {
    "team_id": "1.l.707737.t.2",
    "manager": "Joey",
    "team_name": "Another Team"
  }
]
```

The order of this array is the default display order for matrix reports such as Freaky Friday.

The renderer works with any team count. If the league contains 10, 12, or 14 teams, populate 10, 12, or 14 objects here.

---

# 4. `boned_index`

The legacy report shows one Boned Index and rank for each completed report week, plus difference and rank change.

```json
"boned_index": {
  "description": "How opponents perform against each manager compared with everyone else.",
  "weeks": [2, 3, 4, 5],
  "rows": [
    {
      "team_id": "t1",
      "values": {
        "2": -16.5,
        "3": -6.9,
        "4": -2.6,
        "5": -6.4
      },
      "ranks": {
        "2": 3,
        "3": 2,
        "4": 5,
        "5": 5
      },
      "difference": -3.8,
      "rank_delta": 0
    }
  ]
}
```

Important: `values` are **percentage points**, not fractions. Use `-6.4` for `-6.4%`, not `-0.064`.

`weeks` controls how many week/rank column pairs are rendered.

---

# 5. `boned_detail`

One detail table is generated for each item in `managers`.

```json
"boned_detail": {
  "description": "Manager-by-manager weekly detail behind the Boned Index.",
  "managers": [
    {
      "team_id": "t1",
      "rows": [
        {
          "week": 1,
          "opponent": "Adam",
          "opponent_score": 88.56,
          "opponent_games": 4,
          "opponent_avg": 97.96,
          "ratio": 0.90
        }
      ],
      "averages": {
        "opponent_score": 93.40,
        "opponent_avg": 99.83,
        "ratio": 0.94
      }
    }
  ]
}
```

Unlike Boned Index percentage values, `ratio` is stored as a numeric ratio such as `0.94` or `1.13`.

Only include completed weeks in `rows`. Do not create zero rows for future weeks merely to mimic the old spreadsheet.

---

# 6. `freaky_friday`

This section contains two square matrices.

## Schedule swap matrix

```json
"schedule_swap": {
  "matrix": {
    "t1": {"t1": 5, "t2": 3, "t3": 2},
    "t2": {"t1": 4, "t2": 3, "t3": 2},
    "t3": {"t1": 1, "t2": 2, "t3": 4}
  },
  "summary": [
    {
      "team_id": "t1",
      "total": 10,
      "rank": 1,
      "max": 5,
      "avg": 3.33,
      "min": 2
    }
  ]
}
```

Interpretation of:

```json
"t1": {"t2": 3}
```

is:

> Team `t1` would have 3 wins if it had played team `t2`'s schedule.

The HTML dynamically renders an `N × N` matrix using the teams array.

## Head-to-head matrix

```json
"head_to_head": {
  "matrix": {
    "t1": {"t1": 0, "t2": 4, "t3": 2},
    "t2": {"t1": 1, "t2": 0, "t3": 3},
    "t3": {"t1": 3, "t2": 2, "t3": 0}
  }
}
```

Diagonal values should normally be `0`.

---

# 7. `bhole`

```json
"bhole": {
  "description": "How a fixed weekly point adjustment changes the season outcome.",
  "deltas": [-10, -5, -2.5, 0, 2.5, 5, 10],
  "rows": [
    {
      "team_id": "t1",
      "wins": {
        "-10": 2,
        "-5": 3,
        "-2.5": 4,
        "0": 5,
        "2.5": 5,
        "5": 5,
        "10": 5
      },
      "range": 3,
      "avg_width": 17.4
    }
  ]
}
```

`deltas` controls the displayed adjustment columns. The HTML does not assume the seven legacy values; Python may supply another set later if desired.

Keys inside `wins` become strings when serialized to JSON. That is expected.

---

# 8. `studs_duds`

```json
"studs_duds": {
  "description": "Counts of starter over- and under-performance.",
  "rows": [
    {
      "team_id": "t1",
      "studs": 1,
      "studded_on": 0,
      "duds": 2,
      "dudded_on": 0,
      "s2d_diff": -1
    }
  ]
}
```

Use cumulative values through the report week unless the metric is intentionally redesigned later.

---

# 9. `grower_shower`

```json
"grower_shower": {
  "description": "Compares blended expected wins with actual wins.",
  "weeks": [1, 2, 3, 4, 5],
  "weights": {
    "expected_wins": 0.8,
    "actual_vs_opp_avg": 0.075,
    "avg_vs_opp_actual": 0.075,
    "avg_vs_opp_avg": 0.05
  },
  "rows": [
    {
      "team_id": "t1",
      "weekly_probability": {
        "1": 0.61,
        "2": 0.57,
        "3": 0.72,
        "4": 0.87,
        "5": 0.85
      },
      "expected_wins": 3.61,
      "actual_vs_opp_avg": 4,
      "avg_vs_opp_actual": 5,
      "avg_vs_opp_avg": 5,
      "actual_wins": 5,
      "ratio": 1.31,
      "rank": 2
    }
  ]
}
```

`weekly_probability` is stored as fractions between 0 and 1.

`weights` are also fractions. They are displayed as percentages by the HTML.

Only put completed weeks in `weeks` and `weekly_probability`.

---

# 10. `managerial_expertise`

```json
"managerial_expertise": {
  "description": "Lineup efficiency and management metrics.",
  "rows": [
    {
      "team_id": "t1",
      "actual_points": 554.18,
      "optimal_points": 660.22,
      "efficiency": 0.839,
      "using_yahoo_games": 4,
      "using_yahoo_efficiency": 0.873,
      "not_using_yahoo_games": 1,
      "not_using_yahoo_efficiency": 0.699,
      "wins_added": 0,
      "losses_added": 0,
      "net_added": 0,
      "sheep_pct": 0.80,
      "plums": "80%",
      "vobl": 17.82,
      "vobm": -312.3,
      "wobl": 5,
      "wobm": 0,
      "all_studs": 1,
      "studs_benched": 0,
      "all_duds": 2,
      "duds_started": 0
    }
  ]
}
```

Fraction-style fields that the HTML converts to percentages:

- `efficiency`
- `using_yahoo_efficiency`
- `not_using_yahoo_efficiency`
- `sheep_pct`

`plums` is deliberately accepted as display text because the legacy report can contain non-numeric text such as `All Balls`.

Use JSON `null` when an efficiency value is undefined because there were zero games in that category.

---

# 11. `vobl`

The position list is dynamic. `description` must define VOBL (Value Over
Baseline League) and VOBM (Value Over Baseline Market), including how each
comparison is chosen. Each row also contains `market_total` and `market_values`
with the same position keys as `values`; `total` and `values` remain VOBL.
`weekly_baselines` contains one record per week/position with `week`, `position`,
`baseline_player_id`, `baseline_player_name`, `baseline_points`,
`market_player_id`, `market_player_name`, and `market_points`.
Use null for unavailable points or players. Both report templates use one shared
selector for the team values and weekly comparison-player chart.

```json
"vobl": {
  "description": "Value over baseline by lineup slot.",
  "positions": [
    "QB1",
    "WR1",
    "WR2",
    "RB1",
    "RB2",
    "TE1",
    "W/R/T1",
    "DEF1",
    "K1"
  ],
  "rows": [
    {
      "team_id": "t1",
      "total": 17.82,
      "values": {
        "QB1": 10.42,
        "WR1": 37.60,
        "WR2": 15.60,
        "RB1": -0.30,
        "RB2": -6.20,
        "TE1": -24.80,
        "W/R/T1": -2.50,
        "DEF1": -11.00,
        "K1": -1.00
      }
    }
  ]
}
```

Do **not** hard-code the positions in Python. Generate `positions` from the league's starting-roster configuration / the metric design, then populate `values` using those exact keys.

This matters for leagues with different roster formats.

The legacy workbook described baseline as the 12th-best player at each position. That may need to become league-size-aware in the new calculation layer; the HTML does not make that decision.

---

# 12. `playoff_odds`

```json
"playoff_odds": {
  "description": "Monte Carlo outlook for the remainder of the season.",
  "simulation_count": 10000,
  "rows": [
    {
      "team_id": "t1",
      "make_playoffs": 0.978,
      "division": 0.844,
      "wildcard": 0.134,
      "bye": 0.794,
      "last_place": 0.000
    }
  ]
}
```

All odds are fractions from 0 to 1. The HTML converts them to percentages.

The HTML automatically sorts the display by `make_playoffs`, highest first.

---

# 13. `draft_analysis`

Both charts use the same dynamic bucket list.

```json
"draft_analysis": {
  "description": "Points produced by draft-round group.",
  "buckets": ["1-5", "6-10", "11-16", "FA"],
  "historical": [
    {
      "team_id": "t1",
      "values": {
        "1-5": 339.40,
        "6-10": 137.62,
        "11-16": 96.58,
        "FA": 115.60
      }
    }
  ],
  "optimal": [
    {
      "team_id": "t1",
      "values": {
        "1-5": 309.10,
        "6-10": 137.62,
        "11-16": 97.90,
        "FA": 115.60
      }
    }
  ]
}
```

`historical` represents points actually generated by the relevant players/lineups.

`optimal` represents the equivalent values under the optimal-lineup definition.

The HTML scales the bars automatically across however many teams are supplied.

---

# 14. How Python should insert the JSON

Recommended approach: keep one pristine HTML template file and replace the contents of the `report-data` element when generating a report.

A simple implementation can use two explicit markers added around that script block, or parse the HTML with a library. If using string replacement, make the replacement target very specific so unrelated JavaScript is never modified.

Conceptually:

```python
import json
from pathlib import Path

html = Path(template_path).read_text(encoding="utf-8")
json_text = json.dumps(report_data, ensure_ascii=False, separators=(",", ":"))

# Replace only the contents of <script id="report-data" type="application/json">...</script>
# using a safe, targeted helper.

Path(output_path).write_text(html, encoding="utf-8")
```

When serializing:

- use valid JSON, not Python `repr()`
- use `null` for missing values
- use `true` / `false`, not `True` / `False`
- do not emit `NaN` or `Infinity`
- preferably convert NumPy/Pandas scalar types to normal Python `int` / `float` / `str` first

For maximum safety when embedding arbitrary strings in an HTML script element, escape any literal `</script>` sequence in serialized text (for example as `<\/script>`).

---

# 13b. `trades` (optional)

Optional. Omit the key (or supply no trades) and the section shows "No trades detected". `trades` lists one entry per
trade; every trade has exactly two `sides`. Net values are what the side received minus what it gave up, from the
trade week through `meta.current_week`. Use `null` for a metric that could not be calculated.

```json
"trades": {
  "description": "How trades were found and rated.",
  "unconfirmed_moves": 24,
  "trades": [
    {
      "trade_id": "w7_2_8",
      "week": 7,
      "winner": "2",
      "label": "Slight",
      "margin": 12.3,
      "votes": {"2": 2, "8": 1},
      "sides": [
        {
          "team_id": "2",
          "received": [{"player_id": "p1", "name": "Player One", "position": "WR", "points": 113.2, "por": 40.1}],
          "sent": [{"player_id": "p2", "name": "Player Two", "position": "QB", "points": 123.5, "por": 4.8}],
          "raw_net": -10.3, "por_net": 35.3, "lineup_net": 16.3, "wins_net": 0.0,
          "missing_player_weeks": 0
        }
      ]
    }
  ],
  "teams": [
    {"team_id": "2", "trades": 2, "won": 2, "lost": 0, "even": 0,
     "raw_net": 21.6, "por_net": 36.9, "lineup_net": 16.7, "wins_net": 0.0, "unavailable": 0}
  ]
}
```

`winner` is a team id or `null`. `label` is one of Lopsided, Clear, Slight, Split decision, Even. `teams` has one row per team,
including teams with no trades (the table shows only teams with `trades` above zero).

---

# 15. Validation rules before writing the HTML

The Python reporting layer should validate at least the following:

1. Every referenced `team_id` exists in `teams`.
2. `team_id` values are unique.
3. Week numbers do not exceed `meta.current_week` unless intentionally showing projections.
4. Week-driven arrays contain only completed weeks.
5. Freaky Friday matrices contain the expected team keys.
6. Probability fields are between 0 and 1.
7. Boned Index percentage-point fields are not accidentally supplied as fractions.
8. The VOBL position keys agree with `vobl.positions`.
9. Draft row keys agree with `draft_analysis.buckets`.
10. No JSON value is `NaN` or infinite.
11. Every trade has exactly two sides, and `teams` covers every team.

Prefer failing loudly during report generation rather than silently drawing an incomplete report.

---

# 16. Dynamic behavior already handled by the HTML

The template automatically handles:

- dynamic number of teams
- dynamic Boned Index week columns
- dynamic Boned-detail manager cards
- dynamic Freaky Friday `N × N` matrices
- dynamic BHOLE delta columns
- dynamic Grower or Shower week columns
- dynamic VOBL lineup positions
- dynamic playoff rows
- dynamic draft chart bars
- mobile horizontal scrolling for wide tables/matrices
- print styling for PDF export

Therefore the Python builder should not generate HTML rows, table cells, or chart bars. It should only generate the JSON data model.

---

# 17. Suggested first implementation milestone for Codex

Before porting every Excel formula, ask Codex to implement only the **report-data assembly and HTML writer** against a fixed Python dictionary matching this schema.

The first successful milestone should be:

```text
Python dict
   ↓
validate schema
   ↓
embed JSON
   ↓
write standalone HTML
   ↓
open HTML and confirm every section renders
```

Only after that works should individual metric calculations be ported from the legacy workbook one at a time.

A sensible migration sequence is:

```text
1. Basic league/team metadata
2. Boned Index
3. Boned detail
4. Freaky Friday
5. BHOLE
6. Studs/Duds
7. Grower or Shower
8. Managerial Expertise
9. VOBL
10. Playoff Odds
11. Draft Analysis
```

That keeps display/debugging separate from reverse-engineering the old Excel logic.

---

# 18. Source-of-truth principle

The new Python calculations should ultimately be derived from the Yahoo data archive, not from reading values back out of the old Excel workbook.

Use the Excel workbook and old PDFs as **reference implementations** for:

- metric definitions
- formulas / calculation logic
- edge cases
- expected output values
- chart organization

For each metric, compare Python output against one or more known historical league/week reports until the values agree, then consider that metric migrated.


`boned_index.standings` supplies the first section's standings tables. It contains
`description`, `has_divisions`, and `rows` with `team_id`, `wins`, `losses`, `ties`,
`win_pct`, `points_for`, `points_against`, `division_id`, and `division_name`.
Division leagues also supply `division_wins`, `division_losses`, and
`division_ties`. Rows are ordered by win percentage then points for, and the
renderer groups them by division. Unknown results remain null.

Both draft charts display segment values and a total above each bar, plus an
exact-value table for small segments. A missing bucket makes the bar and its
total unavailable; it is not treated as zero. Negative buckets extend below zero.

Section 01 is titled Standings and displays only `boned_index.standings`;
the cumulative scoring-index table and manager-by-week detail are combined in
section 02 (Boned Index / Opponent Scoring Index). The total appears first, then
the manager selector and weekly detail. The menu contains 10 sections, numbered
01 through 10. For Week 1,
the total-index section explains that no comparison baseline exists yet. Standings rows
include `division_rank` and `overall_rank`. Division leagues display those
columns in that order within each division; other leagues show overall rank as
Rank. Both ranks use win percentage then points for, share ranks for exact ties,
and remain null for teams with unavailable ranking inputs.
