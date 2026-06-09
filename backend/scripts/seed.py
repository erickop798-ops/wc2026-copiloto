"""Seed script: fetches real WC data from API-Football and stores it in SQLite.

On free API plans, season=2026 may not be available yet; the script falls
back to season=2022 (last available WC) so the full pipeline can be validated
with real data.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make project root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.database import get_db_connection, init_db
from backend.lib.api_client import APIFootballClient, RateLimitError


# ---------------------------------------------------------------------------
# Parsers / savers
# ---------------------------------------------------------------------------

def save_fixtures(conn, data: dict) -> int:
    count = 0
    for item in data.get("response", []):
        f = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        league = item.get("league", {})
        venue = f.get("venue", {}) or {}

        conn.execute(
            """
            INSERT OR REPLACE INTO fixtures
              (fixture_id, home_team_id, home_team_name, away_team_id, away_team_name,
               date_utc, venue, city, round, group_name, status, home_goals, away_goals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f.get("id"),
                teams.get("home", {}).get("id"),
                teams.get("home", {}).get("name"),
                teams.get("away", {}).get("id"),
                teams.get("away", {}).get("name"),
                f.get("date"),
                venue.get("name"),
                venue.get("city"),
                league.get("round"),
                league.get("group"),
                (f.get("status") or {}).get("short"),
                goals.get("home"),
                goals.get("away"),
            ),
        )
        count += 1
    return count


def save_teams(conn, data: dict) -> int:
    count = 0
    for item in data.get("response", []):
        t = item.get("team", {})
        conn.execute(
            "INSERT OR REPLACE INTO teams (team_id, name, code, country, logo_url) VALUES (?, ?, ?, ?, ?)",
            (t.get("id"), t.get("name"), t.get("code"), t.get("country"), t.get("logo")),
        )
        count += 1
    return count


def save_standings(conn, data: dict) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS standings (
            team_id INTEGER PRIMARY KEY, team_name TEXT, group_name TEXT,
            played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
            goals_for INTEGER, goals_against INTEGER, goal_diff INTEGER, points INTEGER
        )
        """
    )
    count = 0
    for league_item in data.get("response", []):
        for group in (league_item.get("league") or {}).get("standings", []):
            for entry in group:
                team = entry.get("team", {})
                stats = entry.get("all", {})
                gls = stats.get("goals", {})
                conn.execute(
                    """
                    INSERT OR REPLACE INTO standings
                      (team_id, team_name, group_name, played, wins, draws, losses,
                       goals_for, goals_against, goal_diff, points)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        team.get("id"),
                        team.get("name"),
                        entry.get("group"),
                        stats.get("played", 0),
                        stats.get("win", 0),
                        stats.get("draw", 0),
                        stats.get("lose", 0),
                        gls.get("for", 0),
                        gls.get("against", 0),
                        entry.get("goalsDiff", 0),
                        entry.get("points", 0),
                    ),
                )
                count += 1
    return count


def save_injuries(conn, data: dict) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER, player_id INTEGER, player_name TEXT,
            team_id INTEGER, type TEXT, reason TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    count = 0
    for item in data.get("response", []):
        fixture = item.get("fixture", {}) or {}
        player = item.get("player", {}) or {}
        team = item.get("team", {}) or {}
        conn.execute(
            "INSERT INTO injuries (fixture_id, player_id, player_name, team_id, type, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                fixture.get("id"),
                player.get("id"),
                player.get("name"),
                team.get("id"),
                player.get("type"),
                player.get("reason"),
            ),
        )
        count += 1
    return count


def _parse_pct(val) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip("%"))
    except (ValueError, TypeError):
        return None


def save_predictions(conn, fixture_id: int, data: dict) -> None:
    for item in data.get("response", []):
        pred = item.get("predictions", {}) or {}
        winner = pred.get("winner") or {}
        pct = pred.get("percent") or {}
        goals = pred.get("goals") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO predictions
              (fixture_id, winner_team, home_pct, draw_pct, away_pct,
               home_goals_pred, away_goals_pred, advice, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture_id,
                winner.get("name"),
                _parse_pct(pct.get("home")),
                _parse_pct(pct.get("draw")),
                _parse_pct(pct.get("away")),
                float(goals["home"]) if goals.get("home") not in (None, "-") else None,
                float(goals["away"]) if goals.get("away") not in (None, "-") else None,
                pred.get("advice"),
                json.dumps(item),
            ),
        )


def save_odds(conn, fixture_id: int, data: dict) -> int:
    count = 0
    for item in data.get("response", []):
        for bookmaker in item.get("bookmakers", []):
            bname = bookmaker.get("name", "")
            for bet in bookmaker.get("bets", []):
                market = bet.get("name", "")
                for val in bet.get("values", []):
                    try:
                        odd_value = float(val.get("odd", 0))
                    except (ValueError, TypeError):
                        odd_value = None
                    conn.execute(
                        "INSERT INTO odds_data (fixture_id, bookmaker_name, market_name, outcome_name, odd_value) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (fixture_id, bname, market, val.get("value"), odd_value),
                    )
                    count += 1
    return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_plan_error(data: dict) -> bool:
    errors = data.get("errors") or {}
    return bool(errors.get("plan") or errors.get("token") or errors.get("requests"))


def _fetch_with_fallback(client: APIFootballClient, method_name: str,
                         season_preferred: int = 2026,
                         season_fallback: int = 2022) -> tuple[dict, int]:
    """Call client.<method_name>(league=1, season=season) with fallback."""
    method = getattr(client, method_name)
    data = method(league=1, season=season_preferred)
    if _has_plan_error(data):
        print(f"      [!] season={season_preferred} no disponible en plan gratuito, "
              f"reintentando con season={season_fallback}...")
        data = method(league=1, season=season_fallback)
        return data, season_fallback
    return data, season_preferred


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 50)
    print("  WC2026 Copiloto - Seed Script")
    print("=" * 50)
    print()

    # 1. Init DB
    print("[1/5] Inicializando base de datos...")
    init_db()

    client = APIFootballClient()
    print(f"      API key: ...{client.api_key[-6:]}\n")

    # 2. Fixtures
    print("[2/5] Descargando fixtures...")
    fixtures_data, season_used = _fetch_with_fallback(client, "get_fixtures")
    print(f"      Usando season={season_used}")
    if fixtures_data.get("errors"):
        print(f"      [!] Errores API: {fixtures_data['errors']}")
    with get_db_connection() as conn:
        n_fixtures = save_fixtures(conn, fixtures_data)
    print(f"      -> {n_fixtures} fixtures guardados\n")

    # 3. Teams
    print("[3/5] Descargando equipos...")
    teams_data, _ = _fetch_with_fallback(client, "get_teams",
                                          season_preferred=season_used,
                                          season_fallback=season_used)
    with get_db_connection() as conn:
        n_teams = save_teams(conn, teams_data)
    print(f"      -> {n_teams} equipos guardados\n")

    # 4. Standings
    print("[4/5] Descargando standings...")
    standings_data, _ = _fetch_with_fallback(client, "get_standings",
                                              season_preferred=season_used,
                                              season_fallback=season_used)
    with get_db_connection() as conn:
        n_standings = save_standings(conn, standings_data)
    print(f"      -> {n_standings} entradas de standings guardadas\n")

    # 5. Injuries
    print("[5/5] Descargando lesiones...")
    injuries_data, _ = _fetch_with_fallback(client, "get_injuries",
                                             season_preferred=season_used,
                                             season_fallback=season_used)
    with get_db_connection() as conn:
        n_injuries = save_injuries(conn, injuries_data)
    print(f"      -> {n_injuries} lesiones guardadas\n")

    # 6. Upcoming fixtures (today -> +4 days UTC) -> predictions + odds
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=4)

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT fixture_id, home_team_name, away_team_name, date_utc FROM fixtures ORDER BY date_utc"
        ).fetchall()

    upcoming = []
    for row in rows:
        date_str = row["date_utc"]
        if not date_str:
            continue
        try:
            fixture_dt = datetime.fromisoformat(date_str)
            if fixture_dt.tzinfo is None:
                fixture_dt = fixture_dt.replace(tzinfo=timezone.utc)
            if now <= fixture_dt <= cutoff:
                upcoming.append(row)
        except ValueError:
            pass

    print(f"--- Partidos proximos ({now.strftime('%Y-%m-%d')} a {cutoff.strftime('%Y-%m-%d')}): "
          f"{len(upcoming)} partido(s) ---\n")

    processed = 0
    for row in upcoming:
        fid = row["fixture_id"]
        home = row["home_team_name"]
        away = row["away_team_name"]
        print(f"  >> Fixture #{fid}: {home} vs {away}")
        try:
            pred_data = client.get_predictions(fid)
            odds_data_r = client.get_odds(fid)
            with get_db_connection() as conn:
                save_predictions(conn, fid, pred_data)
                n_odds = save_odds(conn, fid, odds_data_r)
            pred_resp = pred_data.get("response", [])
            advice = ""
            if pred_resp:
                advice = (pred_resp[0].get("predictions") or {}).get("advice", "")
            print(f"     predictions: {'OK' if pred_resp else '(vacio)'}  "
                  f"odds: {n_odds} cuotas  advice: {advice}")
        except RateLimitError as e:
            print(f"     [!] Rate limit alcanzado: {e}")
            break
        except Exception as e:
            print(f"     [X] Error en fixture #{fid}: {e}")
        processed += 1

    # Summary
    today_str = now.strftime("%Y-%m-%d")
    with get_db_connection() as conn:
        total_fixtures = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        total_teams_db = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        real_req = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE date = ? AND cached = 0", (today_str,)
        ).fetchone()[0]
        cached_req = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE date = ? AND cached = 1", (today_str,)
        ).fetchone()[0]

    print()
    print("=" * 50)
    print(f"  RESUMEN - season={season_used}")
    print("=" * 50)
    print(f"  Total fixtures en DB              : {total_fixtures}")
    print(f"  Total equipos en DB               : {total_teams_db}")
    print(f"  Partidos proximos procesados      : {processed}")
    print(f"  Requests HTTP reales hoy          : {real_req}")
    print(f"  Requests cacheados hoy            : {cached_req}")
    print("=" * 50)


if __name__ == "__main__":
    main()
