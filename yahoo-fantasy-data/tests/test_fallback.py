import pytest

from yahoo_fantasy_data.config import Settings
from yahoo_fantasy_data.errors import YahooAuthenticationError, YahooRateLimitError
from yahoo_fantasy_data.providers.fallback import FallbackYahooProvider
from yahoo_fantasy_data.collectors.divisions import get_divisions
from yahoo_fantasy_data.utils import entity_objects


class Client:
    def __init__(self, payload, oauth=True):
        self.settings = Settings(client_id='id', client_secret='secret', refresh_token='token' if oauth else None)
        self.payload = payload
        self.calls = []
    def get(self, path, **kwargs):
        self.calls.append(path)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def team(name='--hidden--', email=None):
    return {'team_key': '461.l.1.t.1', 'name': 'Public team', 'managers': {'manager': {
        'manager_id': '1', 'nickname': name, 'email': email}}}


def test_successful_public_request_does_not_use_oauth():
    provider = FallbackYahooProvider(Client({'ok': True}))
    provider.official.get = lambda *a, **k: pytest.fail('Unnecessary OAuth')
    assert provider.settings('461.l.1') == {'ok': True}


def test_failed_public_request_uses_oauth():
    provider = FallbackYahooProvider(Client(YahooAuthenticationError('denied')))
    provider.official.get = lambda path, **params: {'path': path}
    assert provider.draft('461.l.1') == {'path': 'league/461.l.1/draftresults'}


def test_public_rate_limit_stops_without_fallback():
    provider = FallbackYahooProvider(Client(YahooRateLimitError('blocked')))
    provider.official.get = lambda *a, **k: pytest.fail('Must stop')
    with pytest.raises(YahooRateLimitError):
        provider.teams('461.l.1')
