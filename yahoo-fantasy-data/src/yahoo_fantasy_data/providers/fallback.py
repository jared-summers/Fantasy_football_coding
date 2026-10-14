"""Public-first requests with optional authenticated fallback."""
from __future__ import annotations

from .public import PublicYahooProvider
from .official import OfficialYahooProvider
from ..errors import YahooAPIError, YahooRateLimitError


class FallbackYahooProvider(PublicYahooProvider):
    """Keep successful public responses; use OAuth for unavailable data."""

    def __init__(self, client):
        super().__init__(client)
        self.official = OfficialYahooProvider(client, client.settings) if client.settings.oauth_configured else None

    def get(self, path, **params):
        try:
            return super().get(path, **params)
        except YahooRateLimitError:
            raise
        except YahooAPIError:
            if self.official is None:
                raise
            return self.official.get(path, **params)
