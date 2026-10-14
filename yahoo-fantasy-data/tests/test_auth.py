from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from dotenv import dotenv_values

from yahoo_fantasy_data.auth import authorize
from yahoo_fantasy_data.errors import YahooAuthenticationError


def test_authorization_saves_token_without_displaying_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / '.env'
    path.write_text('YAHOO_CLIENT_ID=client\nYAHOO_CLIENT_SECRET=secret\nYAHOO_GAME_ID=461\n')
    monkeypatch.setattr('secrets.token_urlsafe', lambda n: 'state-value')
    monkeypatch.setattr('getpass.getpass', lambda prompt:
                        'https://localhost/callback?code=one-use-code&state=state-value')

    class Response:
        status_code = 200
        def json(self):
            return {'refresh_token': 'private-refresh-token'}

    def exchange(url, **kwargs):
        assert kwargs['auth'] == ('client', 'secret')
        assert kwargs['data'] == {'grant_type': 'authorization_code',
                                 'code': 'one-use-code', 'redirect_uri': 'https://localhost/callback'}
        return Response()
    monkeypatch.setattr('requests.post', exchange)
    authorize(path, 'https://localhost/callback')
    assert dotenv_values(path)['YAHOO_REFRESH_TOKEN'] == 'private-refresh-token'
    assert dotenv_values(path)['YAHOO_GAME_ID'] == '461'
    assert path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert 'private-refresh-token' not in output
    assert 'secret' not in output
    url = next(line for line in output.splitlines() if line.startswith('https://api.login'))
    assert parse_qs(urlsplit(url).query)['state'] == ['state-value']


@pytest.mark.parametrize('callback', [
    'https://localhost/callback?code=code&state=wrong',
    'https://wrong.example/callback?code=code&state=state-value',
    'https://localhost/callback?error=access_denied&state=state-value',
    'https://localhost/callback?state=state-value',
])
def test_bad_callback_never_exchanges_or_saves(tmp_path, monkeypatch, callback):
    path = tmp_path / '.env'
    original = 'YAHOO_CLIENT_ID=client\nYAHOO_CLIENT_SECRET=secret\n'
    path.write_text(original)
    monkeypatch.setattr('secrets.token_urlsafe', lambda n: 'state-value')
    monkeypatch.setattr('getpass.getpass', lambda prompt: callback)
    monkeypatch.setattr('requests.post', lambda *a, **k: pytest.fail('Must not exchange'))
    with pytest.raises(YahooAuthenticationError):
        authorize(path, 'https://localhost/callback')
    assert path.read_text() == original


def test_memory_authorization_preserves_settings_without_writing(monkeypatch, tmp_path):
    from yahoo_fantasy_data.auth import authorize_in_memory
    from yahoo_fantasy_data.config import Settings
    monkeypatch.setattr('yahoo_fantasy_data.auth._authorize_token', lambda *a: 'memory-token')
    monkeypatch.setattr('dotenv.set_key', lambda *a, **k: pytest.fail('Must not write'))
    original = Settings(data_dir=tmp_path, client_id='client', client_secret='private-secret')
    active = authorize_in_memory(original, 'https://localhost/callback')
    assert active.oauth_configured
    assert active.refresh_token == 'memory-token'
    assert original.refresh_token is None
    assert active.data_dir == tmp_path
    assert not list(tmp_path.iterdir())
    assert 'memory-token' not in repr(active)
    assert 'private-secret' not in repr(active)
