"""Configuration loaded without ever persisting credentials."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    league_nickname: str | None = None
    timeout: float = 20.0
    request_delay: float = 0.35
    player_page_size: int = 200
    game_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)

    @property
    def oauth_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)


def load_settings(data_dir: Path | None = None) -> Settings:
    load_dotenv()
    return Settings(
        data_dir=data_dir or Path(os.getenv("YAHOO_DATA_DIR", "data")),
        league_nickname=os.getenv("YAHOO_LEAGUE_NICKNAME") or None,
        timeout=float(os.getenv("YAHOO_TIMEOUT", "20")),
        request_delay=float(os.getenv("YAHOO_REQUEST_DELAY", "1.5")),
        player_page_size=int(os.getenv("YAHOO_PLAYER_PAGE_SIZE", "200")),
        game_id=os.getenv("YAHOO_GAME_ID") or None,
        client_id=os.getenv("YAHOO_CLIENT_ID") or None,
        client_secret=os.getenv("YAHOO_CLIENT_SECRET") or None,
        refresh_token=os.getenv("YAHOO_REFRESH_TOKEN") or None,
    )


def storage_league_name(nickname: str | None, league_id: str) -> str:
    """Return a safe, stable local folder name without changing the Yahoo ID."""
    source = (nickname or str(league_id)).strip()
    folder = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip(".-")
    if not folder:
        raise ValueError("league nickname must contain at least one letter or number")
    return folder
