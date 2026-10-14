from __future__ import annotations

import requests

from yahoo_fantasy_data.config import Settings
from yahoo_fantasy_data.errors import YahooAuthenticationError
from yahoo_fantasy_data.yahoo import YahooHTTPClient


class Response:
    status_code = 401
    text = "no"

    def json(self):
        return {}


class Session:
    headers: dict[str, str] = {}

    def mount(self, *args):
        pass

    def get(self, *args, **kwargs):
        return Response()


def test_http_maps_unauthorized_to_authentication_error() -> None:
    client = YahooHTTPClient(Settings(), session=Session())
    try:
        client.get("league/1")
    except YahooAuthenticationError:
        return
    raise AssertionError("expected YahooAuthenticationError")


def test_actual_players_request_week_on_stats_subresource():
    from yahoo_fantasy_data.providers.public import PublicYahooProvider

    class RecordingClient:
        def get(self, path, *, params):
            self.path, self.params = path, params
            return {}

    client = RecordingClient()
    PublicYahooProvider(client).players('461.l.1', 5, 25, 25)
    assert client.path == 'league/461.l.1/players;start=25;count=25/stats;type=week;week=5'
    assert client.params == {'format': 'json'}
