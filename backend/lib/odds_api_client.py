"""Fetches betting odds from The Odds API (the-odds-api.com)."""
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from backend.database import get_db_connection

logger = logging.getLogger(__name__)

_CREATE_QUOTA_TABLE = """
CREATE TABLE IF NOT EXISTS api_quota (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,
    date            TEXT,
    quota_remaining INTEGER,
    timestamp       TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class OddsAPIQuotaError(Exception):
    """Raised when remaining quota drops below the safety threshold."""


class OddsAPIClient:

    BASE_URL = "https://api.the-odds-api.com/v4"
    SPORT = "soccer_fifa_world_cup"
    QUOTA_FLOOR = 30

    def __init__(self):
        self.api_key = os.getenv("ODDS_API_KEY")
        if not self.api_key:
            raise ValueError("ODDS_API_KEY not set in environment")
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with get_db_connection() as conn:
            conn.executescript(_CREATE_QUOTA_TABLE)

    # ------------------------------------------------------------------
    # Core fetch with cache-first logic
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None, cache_minutes: int = 30):
        if params is None:
            params = {}

        cache_key_data = json.dumps({"path": path, "params": params}, sort_keys=True)
        cache_key = hashlib.sha256(cache_key_data.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        # 1. Cache-first
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT response_json FROM api_cache WHERE params_hash = ? AND expires_at > ?",
                (cache_key, now_str),
            ).fetchone()
            if row:
                logger.debug("Cache hit: %s", path)
                return json.loads(row["response_json"])

        # 2. Quota guard before making the request
        quota = self.get_quota_remaining()
        if quota < self.QUOTA_FLOOR:
            raise OddsAPIQuotaError(
                f"Remaining quota too low: {quota} (floor={self.QUOTA_FLOOR})"
            )

        # 3. HTTP request — inject apiKey
        full_params = {**params, "apiKey": self.api_key}
        url = self.BASE_URL + path
        response = requests.get(url, params=full_params, timeout=10)

        # Save quota from response header before raising for status
        remaining_header = response.headers.get("x-requests-remaining")
        if remaining_header is not None:
            self._save_quota(int(remaining_header))

        if not response.ok:
            body = ""
            try:
                body = response.json()
            except Exception:
                body = response.text[:300]
            raise requests.HTTPError(
                f"HTTP {response.status_code} from {url}: {body}",
                response=response,
            )
        data = response.json()

        # 4. Cache the response
        expires_at = (now + timedelta(minutes=cache_minutes)).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (endpoint, params_hash, response_json, cached_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(params_hash) DO UPDATE SET
                    response_json = excluded.response_json,
                    cached_at     = excluded.cached_at,
                    expires_at    = excluded.expires_at
                """,
                (path, cache_key, json.dumps(data), now_str, expires_at),
            )

        logger.info("OddsAPI request: %s (quota left: %s)", path, remaining_header)
        return data

    # ------------------------------------------------------------------
    # Quota tracking
    # ------------------------------------------------------------------

    def _save_quota(self, remaining: int) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO api_quota (source, date, quota_remaining) VALUES (?, ?, ?)",
                ("the_odds_api", today, remaining),
            )

    def get_quota_remaining(self) -> int:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT quota_remaining FROM api_quota "
                "WHERE source = 'the_odds_api' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return row["quota_remaining"] if row else 500

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_all_odds(self, regions: str = "eu") -> list:
        path = f"/sports/{self.SPORT}/odds"
        params = {
            "regions": regions,
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal",
        }
        return self._get(path, params, cache_minutes=30)

    def get_event_odds(self, event_id: str, regions: str = "eu") -> dict:
        path = f"/sports/{self.SPORT}/events/{event_id}/odds"
        params = {
            "regions": regions,
            "markets": "h2h,spreads,totals,double_chance",
            "oddsFormat": "decimal",
        }
        return self._get(path, params, cache_minutes=30)
