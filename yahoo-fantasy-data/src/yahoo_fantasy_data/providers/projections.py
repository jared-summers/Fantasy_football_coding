"""Projection provider that makes anonymous historical attempts explicit."""
from __future__ import annotations

from typing import Any

from ..errors import YahooAuthenticationError, YahooProjectionUnavailableError
from .public import PublicYahooProvider


class ProjectionProvider:
    source_name = "public_internal_projection"

    def __init__(self, public: PublicYahooProvider) -> None:
        self.public = public

    def players(self, league_key: str, week: int, start: int, count: int) -> dict[str, Any]:
        try:
            return self.public.players(league_key, week, start, count, projected=True)
        except YahooAuthenticationError as error:
            raise YahooProjectionUnavailableError(
                "Yahoo requires authentication for historical projections; no HTML or cookie fallback was used."
            ) from error
