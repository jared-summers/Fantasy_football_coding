"""OAuth-backed official Yahoo Fantasy Sports API provider."""
from __future__ import annotations

from typing import Any

from ..auth import refresh_access_token
from ..config import Settings
from ..yahoo import YahooHTTPClient


class OfficialYahooProvider:
    source_name = "official_oauth"

    def __init__(self, client: YahooHTTPClient, settings: Settings) -> None:
        self.client, self.settings = client, settings

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        token = refresh_access_token(self.settings, self.client.session)
        return self.client.get(path, params={"format": "json", **params}, token=token, official=True)
