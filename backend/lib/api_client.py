import hashlib
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from backend.database import get_db_connection

logger = logging.getLogger(__name__)

DAILY_LIMIT = 95


class RateLimitError(Exception):
    """Raised when the daily API request limit (95) has been reached."""


class APIFootballClient:

    def __init__(self):
        self.api_key = os.getenv("API_FOOTBALL_KEY")
        self.base_url = os.getenv(
            "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
        ).rstrip("/")
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY not set in environment")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _params_hash(endpoint: str, params: dict) -> str:
        key = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        return hashlib.sha256(key.encode()).hexdigest()

    def get_daily_request_count(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE date = ? AND cached = 0",
                (today,),
            ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Core fetch with cache-first logic
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict | None = None, cache_hours: int = 24) -> dict:
        if params is None:
            params = {}

        params_hash = self._params_hash(endpoint, params)
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        # 1. Cache-first: return cached response if still valid
        with get_db_connection() as conn:
            cached = conn.execute(
                "SELECT response_json FROM api_cache WHERE params_hash = ? AND expires_at > ?",
                (params_hash, now_str),
            ).fetchone()

            if cached:
                conn.execute(
                    "INSERT INTO request_log (date, endpoint, params, status_code, cached, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (now.strftime("%Y-%m-%d"), endpoint, json.dumps(params), 200, 1, now_str),
                )
                logger.debug("Cache hit: %s %s", endpoint, params)
                return json.loads(cached["response_json"])

        # 2. Rate-limit guard before making any HTTP request
        daily_count = self.get_daily_request_count()
        if daily_count >= DAILY_LIMIT:
            raise RateLimitError(
                f"Daily request limit reached: {daily_count}/{DAILY_LIMIT}. "
                "No more API calls will be made today."
            )

        # 3. Live HTTP request
        url = f"{self.base_url}/{endpoint}"
        headers = {"x-apisports-key": self.api_key}
        response = requests.get(url, headers=headers, params=params, timeout=30)
        status_code = response.status_code
        response.raise_for_status()
        data = response.json()

        expires_at = (now + timedelta(hours=cache_hours)).isoformat()

        # 4. Persist to cache and log the real request
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
                (endpoint, params_hash, json.dumps(data), now_str, expires_at),
            )
            conn.execute(
                "INSERT INTO request_log (date, endpoint, params, status_code, cached, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now.strftime("%Y-%m-%d"), endpoint, json.dumps(params), status_code, 0, now_str),
            )

        logger.info("API request: %s %s (HTTP %s)", endpoint, params, status_code)
        return data

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_fixtures(self, league: int = 1, season: int = 2026) -> dict:
        return self._get("fixtures", {"league": league, "season": season}, cache_hours=6)

    def get_teams(self, league: int = 1, season: int = 2026) -> dict:
        return self._get("teams", {"league": league, "season": season}, cache_hours=24)

    def get_predictions(self, fixture_id: int) -> dict:
        return self._get("predictions", {"fixture": fixture_id}, cache_hours=12)

    def get_odds(self, fixture_id: int) -> dict:
        # Only last 7 days available from the API
        return self._get("odds", {"fixture": fixture_id}, cache_hours=2)

    def get_headtohead(self, team_a_id: int, team_b_id: int, last: int = 10) -> dict:
        return self._get(
            "fixtures/headtohead",
            {"h2h": f"{team_a_id}-{team_b_id}", "last": last},
            cache_hours=24,
        )

    def get_standings(self, league: int = 1, season: int = 2026) -> dict:
        return self._get("standings", {"league": league, "season": season}, cache_hours=6)

    def get_injuries(self, league: int = 1, season: int = 2026) -> dict:
        return self._get("injuries", {"league": league, "season": season}, cache_hours=6)

    def get_fixture_statistics(self, fixture_id: int) -> dict:
        return self._get("fixtures/statistics", {"fixture": fixture_id}, cache_hours=12)
