"""Typed Yahoo API errors."""


class YahooAPIError(RuntimeError):
    """Yahoo returned an unexpected API failure."""


class YahooAuthenticationError(YahooAPIError):
    """The requested endpoint requires OAuth authentication."""


class YahooRateLimitError(YahooAPIError):
    """Yahoo rejected requests due to rate limiting."""


class YahooNotFoundError(YahooAPIError):
    """A Yahoo game, league, or resource was not found."""


class YahooPrivateLeagueError(YahooAPIError):
    """The requested league is not publicly accessible."""


class YahooProjectionUnavailableError(YahooAPIError):
    """Historical player projections are unavailable without authentication."""


class YahooHistoricalRosterUnavailableError(YahooAPIError):
    """Yahoo did not return a roster for the requested historical week."""
