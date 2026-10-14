"""Minimal OAuth refresh-token support for the official API."""
from __future__ import annotations

from typing import Any
import requests

from .config import Settings
from .errors import YahooAuthenticationError

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def refresh_access_token(settings: Settings, session: requests.Session | None = None) -> str:
    if not settings.oauth_configured:
        raise YahooAuthenticationError(
            "OAuth is required for this Yahoo endpoint. Set YAHOO_CLIENT_ID, "
            "YAHOO_CLIENT_SECRET, and YAHOO_REFRESH_TOKEN in your environment or .env."
        )
    response = (session or requests.Session()).post(
        TOKEN_URL,
        auth=(settings.client_id, settings.client_secret),
        data={"grant_type": "refresh_token", "refresh_token": settings.refresh_token},
        timeout=settings.timeout,
    )
    if response.status_code in (401, 403):
        raise YahooAuthenticationError("Yahoo rejected the configured OAuth credentials.")
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str):
        raise YahooAuthenticationError("Yahoo OAuth response contained no access token.")
    return token


def authorize_in_memory(settings: Settings, redirect_uri: str) -> Settings:
    """Return authenticated settings without writing tokens to disk."""
    from dataclasses import replace
    token = _authorize_token(settings.client_id, settings.client_secret, redirect_uri)
    print("Authorized for this Python session. No token was saved to disk.")
    return replace(settings, refresh_token=token)


def authorize(env_file, redirect_uri: str) -> None:
    """Complete browser consent and save the initial refresh token locally."""
    from pathlib import Path
    from dotenv import dotenv_values, set_key
    path = Path(env_file).resolve()
    values = dotenv_values(path)
    token = _authorize_token(values.get("YAHOO_CLIENT_ID"), values.get("YAHOO_CLIENT_SECRET"), redirect_uri)
    path.chmod(0o600)
    set_key(str(path), "YAHOO_REFRESH_TOKEN", token)
    path.chmod(0o600)
    print(f"Refresh token saved to {path}. Restart your notebook kernel before collecting.")


def _authorize_token(client_id: str | None, client_secret: str | None, redirect_uri: str) -> str:
    from getpass import getpass
    import secrets
    from urllib.parse import parse_qs, urlencode, urlsplit
    if not client_id or not client_secret:
        raise YahooAuthenticationError("Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET first.")
    target = urlsplit(redirect_uri)
    if target.scheme not in {"http", "https"} or not target.netloc or target.fragment:
        raise YahooAuthenticationError("Use the exact HTTP(S) redirect URI registered for your Yahoo app.")
    state = secrets.token_urlsafe(32)
    url = "https://api.login.yahoo.com/oauth2/request_auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "state": state,
    })
    print("Open this URL, sign in with your league account, and approve access:\n" + url)
    print("After Yahoo redirects, copy the entire address from your browser, even if the page cannot load.")
    callback = urlsplit(getpass("Paste the full redirected URL (input hidden): ").strip())
    query = parse_qs(callback.query)
    if (callback.scheme, callback.netloc, callback.path) != (target.scheme, target.netloc, target.path):
        raise YahooAuthenticationError("Redirect URL does not match the registered callback.")
    if query.get("state") != [state]:
        raise YahooAuthenticationError("OAuth state mismatch; run auth again.")
    if "error" in query:
        raise YahooAuthenticationError("Yahoo did not grant access; run auth again and approve access.")
    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise YahooAuthenticationError("The redirected URL contains no authorization code.")
    try:
        response = requests.post(TOKEN_URL, auth=(client_id, client_secret), data={
            "grant_type": "authorization_code", "code": codes[0], "redirect_uri": redirect_uri,
        }, timeout=20)
        if response.status_code != 200:
            raise YahooAuthenticationError(f"Yahoo token exchange failed (HTTP {response.status_code}); check app access and redirect URI, then run auth again.")
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise YahooAuthenticationError("Yahoo token exchange failed; run auth again.") from None
    token = payload.get("refresh_token")
    if not isinstance(token, str) or not token:
        raise YahooAuthenticationError("Yahoo returned no refresh token; authorization was not completed.")
    return token
