from yahoo_fantasy_data.utils import flatten, normalize_numbered
from yahoo_fantasy_data.yahoo import league_key


def test_league_key_generation() -> None:
    assert league_key("461", "707737") == "461.l.707737"


def test_normalizes_yahoo_numbered_dictionary() -> None:
    assert normalize_numbered({"0": {"x": 1}, "1": {"x": 2}, "count": 2}) == [{"x": 1}, {"x": 2}]


def test_flatten_uses_snake_case() -> None:
    assert flatten({"Display Name": "A", "nested": {"Stat-ID": 4}}) == {"display_name": "A", "nested_stat_id": 4}
