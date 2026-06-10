"""Fetches WC 2026 fixture/group data from the openfootball GitHub JSON feed."""
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from backend.database import get_db_connection

from .team_resolver import is_placeholder, resolve_team_name

logger = logging.getLogger(__name__)

FIXTURES_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json"
    "/master/2026/worldcup.json"
)
GROUPS_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json"
    "/master/2026/worldcup.groups.json"
)


class OpenFootballClient:

    # ------------------------------------------------------------------
    # Cache-backed HTTP helper
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str, cache_hours: int = 2) -> dict:
        cache_key = hashlib.sha256(url.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT response_json FROM api_cache WHERE params_hash = ? AND expires_at > ?",
                (cache_key, now_str),
            ).fetchone()
            if row:
                logger.debug("Cache hit: %s", url)
                return json.loads(row["response_json"])

        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        expires_at = (now + timedelta(hours=cache_hours)).isoformat()
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
                (url, cache_key, json.dumps(data), now_str, expires_at),
            )

        logger.info("Fetched %s", url)
        return data

    # ------------------------------------------------------------------
    # Internal parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_matches(raw: dict):
        """Yields individual match dicts regardless of top-level structure."""
        if "matches" in raw:
            yield from raw["matches"]
        elif "rounds" in raw:
            for rnd in raw.get("rounds", []):
                yield from rnd.get("matches", [])
        # Some releases use "groups" → "matches"
        elif "groups" in raw:
            for grp in raw.get("groups", []):
                yield from grp.get("matches", [])

    @staticmethod
    def _parse_datetime_utc(date_str: str, time_str: str | None) -> str | None:
        """
        date_str: "Jun/11", "2026-06-11", or similar
        time_str: "19:00 UTC-6", "15:00 UTC+3", "19:00 UTC", or None
        Returns ISO 8601 UTC string or None on parse failure.
        """
        # --- parse date ---
        dt_date = None
        for fmt in ("%b/%d", "%b %d", "%d %b", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                # If fmt doesn't include year, assume 2026
                dt_date = parsed.replace(year=2026) if "%Y" not in fmt else parsed
                break
            except ValueError:
                continue

        if dt_date is None:
            return None

        # --- parse time + UTC offset ---
        hour, minute, offset_h = 0, 0, 0
        if time_str:
            m = re.match(r"(\d{1,2}):(\d{2})(?:\s*UTC([+-]\d+(?:\.\d+)?)?)?", time_str.strip())
            if m:
                hour = int(m.group(1))
                minute = int(m.group(2))
                offset_h = float(m.group(3)) if m.group(3) else 0.0

        local_dt = dt_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Convert to UTC: UTC = local - offset
        utc_dt = local_dt - timedelta(hours=offset_h)
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        return utc_dt.isoformat()

    def _parse_fixture(self, match: dict, match_index: int) -> dict:
        # Team names — openfootball uses either {"name": "X"} objects or plain strings
        def _team_name(t) -> str:
            if isinstance(t, dict):
                return t.get("name", "")
            return str(t or "")

        team1_raw = _team_name(match.get("team1"))
        team2_raw = _team_name(match.get("team2"))
        team1 = resolve_team_name(team1_raw)
        team2 = resolve_team_name(team2_raw)

        date_str = match.get("date", "")
        time_str = match.get("time")
        time_utc = self._parse_datetime_utc(date_str, time_str)

        # Score
        score = match.get("score") or {}
        ft = score.get("ft")
        score_home = ft[0] if ft and len(ft) > 0 else None
        score_away = ft[1] if ft and len(ft) > 1 else None
        status = "completed" if "score" in match else "scheduled"

        # fixture_id: use "num" if present, else fall back to index
        fixture_id = match.get("num", match_index + 1)

        return {
            "fixture_id": fixture_id,
            "round": match.get("round") or match.get("group"),
            "date_str": date_str,
            "time_local": time_str,
            "time_utc": time_utc,
            "team1": team1,
            "team2": team2,
            "team1_raw": team1_raw,
            "team2_raw": team2_raw,
            "group": match.get("group"),
            "venue": match.get("stadium") or match.get("venue"),
            "status": status,
            "score_home": score_home,
            "score_away": score_away,
            "has_placeholder": is_placeholder(team1_raw) or is_placeholder(team2_raw),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_fixtures(self) -> list[dict]:
        raw = self._fetch_json(FIXTURES_URL)
        return [
            self._parse_fixture(m, i)
            for i, m in enumerate(self._iter_matches(raw))
        ]

    def get_groups(self) -> list[dict]:
        raw = self._fetch_json(GROUPS_URL)
        # Support {"groups": [...]} or top-level list
        groups = raw.get("groups", [])
        if not groups and isinstance(raw, list):
            groups = raw
        return groups

    def get_new_results(self, since_datetime: datetime) -> list[dict]:
        fixtures = self.get_fixtures()
        result = []
        for f in fixtures:
            if f["status"] != "completed":
                continue
            if f["time_utc"] is None:
                continue
            try:
                dt = datetime.fromisoformat(f["time_utc"])
                if dt > since_datetime:
                    result.append(f)
            except ValueError:
                pass
        return result
