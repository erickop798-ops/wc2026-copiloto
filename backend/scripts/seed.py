"""
Seed script - arquitectura hibrida 3 fuentes:
  PASO 1: openfootball (sin cuota) -> fixtures WC 2026 + grupos
  PASO 2: The Odds API             -> cuotas partidos proximos
  PASO 3: API-Football             -> fixtures historicos WC 2022
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.database import get_db_connection, init_db
from backend.lib.api_client import APIFootballClient, RateLimitError
from backend.lib.odds_api_client import OddsAPIClient, OddsAPIQuotaError
from backend.lib.openfootball_client import OpenFootballClient
from backend.lib.team_resolver import is_placeholder, resolve_team_name


# ---------------------------------------------------------------------------
# DB migration helpers (can't touch database.py per session rules)
# ---------------------------------------------------------------------------

def _migrate(conn) -> None:
    """Add columns that didn't exist in Session 1 schema."""
    migrations = [
        "ALTER TABLE fixtures ADD COLUMN tournament_year INTEGER DEFAULT 2026",
        "ALTER TABLE fixtures ADD COLUMN source TEXT DEFAULT 'openfootball'",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists


# ---------------------------------------------------------------------------
# Savers
# ---------------------------------------------------------------------------

def _team_id_from_name(name: str) -> int:
    """Deterministic pseudo-ID from team name (for openfootball data)."""
    return int(hashlib.md5(name.encode()).hexdigest()[:8], 16)


def save_of_fixture(conn, f: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO fixtures
          (fixture_id, home_team_name, away_team_name,
           date_utc, venue, round, group_name, status,
           home_goals, away_goals, tournament_year, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f["fixture_id"],
            f["team1"],
            f["team2"],
            f["time_utc"] or f["date_str"],
            f["venue"],
            f["round"],
            f["group"],
            f["status"],
            f["score_home"],
            f["score_away"],
            2026,
            "openfootball",
        ),
    )


def save_standings_from_groups(conn, groups: list) -> int:
    count = 0
    for grp in groups:
        grp_name = grp.get("name", "")
        teams = grp.get("teams", [])
        for t in teams:
            name = t.get("name", "") if isinstance(t, dict) else str(t)
            team_id = _team_id_from_name(name)
            conn.execute(
                """
                INSERT OR REPLACE INTO standings
                  (team_id, team_name, group_name, played, wins, draws, losses,
                   goals_for, goals_against, goal_diff, points)
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0)
                """,
                (team_id, name, grp_name),
            )
            count += 1
    return count


def save_apifootball_fixture(conn, item: dict) -> None:
    f = item.get("fixture", {})
    teams = item.get("teams", {})
    goals = item.get("goals", {})
    league = item.get("league", {})
    venue = (f.get("venue") or {})

    conn.execute(
        """
        INSERT OR REPLACE INTO fixtures
          (fixture_id, home_team_id, home_team_name, away_team_id, away_team_name,
           date_utc, venue, city, round, group_name, status,
           home_goals, away_goals, tournament_year, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f.get("id"),
            (teams.get("home") or {}).get("id"),
            (teams.get("home") or {}).get("name"),
            (teams.get("away") or {}).get("id"),
            (teams.get("away") or {}).get("name"),
            f.get("date"),
            venue.get("name"),
            venue.get("city"),
            league.get("round"),
            league.get("group"),
            (f.get("status") or {}).get("short"),
            goals.get("home"),
            goals.get("away"),
            2022,
            "api_football",
        ),
    )


# ---------------------------------------------------------------------------
# Odds matching helper
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return (s or "").lower().replace("-", " ").replace("&", "and").strip()


def find_fixture_id(conn, home: str, away: str, commence_time: str) -> int | None:
    """
    Match an Odds API event to a fixture_id in DB.
    Uses date as primary filter, then fuzzy team-name match.
    """
    # Extract date portion from ISO timestamp
    event_date = commence_time[:10] if commence_time else ""
    rows = conn.execute(
        "SELECT fixture_id, home_team_name, away_team_name FROM fixtures "
        "WHERE date_utc LIKE ? AND tournament_year = 2026",
        (event_date + "%",),
    ).fetchall()

    nh, na = _normalize(home), _normalize(away)
    for row in rows:
        rh = _normalize(row["home_team_name"] or "")
        ra = _normalize(row["away_team_name"] or "")
        # At least partial match on both teams
        home_match = nh in rh or rh in nh or nh[:5] in rh
        away_match = na in ra or ra in na or na[:5] in ra
        if home_match and away_match:
            return row["fixture_id"]
        # Also try reversed (some APIs swap home/away)
        home_match2 = nh in ra or ra in nh
        away_match2 = na in rh or rh in na
        if home_match2 and away_match2:
            return row["fixture_id"]
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 55)
    print("  WC2026 Copiloto - Seed (arquitectura hibrida)")
    print("=" * 55)

    # Init DB and run migrations
    init_db()
    with get_db_connection() as conn:
        _migrate(conn)
    print("DB lista.\n")

    # ==================================================================
    # PASO 1 - openfootball (sin cuota)
    # ==================================================================
    print("-" * 55)
    print("PASO 1 - openfootball (0 cuota consumida)")
    print("-" * 55)

    of_client = OpenFootballClient()

    try:
        fixtures = of_client.get_fixtures()
    except Exception as e:
        print(f"  [ERROR] No se pudo obtener fixtures de openfootball: {e}")
        fixtures = []

    unresolved_placeholders = set()
    saved_fixtures = 0

    if fixtures:
        with get_db_connection() as conn:
            for f in fixtures:
                save_of_fixture(conn, f)
                saved_fixtures += 1
                if f["has_placeholder"]:
                    for raw in (f["team1_raw"], f["team2_raw"]):
                        if is_placeholder(raw) and resolve_team_name(raw) == raw:
                            unresolved_placeholders.add(raw)

    completados = sum(1 for f in fixtures if f["status"] == "completed")
    pendientes = len(fixtures) - completados
    print(f"  {saved_fixtures} fixtures WC2026 cargados | "
          f"completados: {completados} | pendientes: {pendientes}")

    if unresolved_placeholders:
        print(f"\n  [!] PLACEHOLDERS SIN MAPEO: {unresolved_placeholders}")
        print("      Agregar a backend/lib/team_resolver.py TEAM_ALIASES")
    else:
        print("  Todos los placeholders resueltos.")

    # Groups / standings
    try:
        groups = of_client.get_groups()
    except Exception as e:
        print(f"  [ERROR] No se pudo obtener grupos: {e}")
        groups = []

    n_standings = 0
    if groups:
        with get_db_connection() as conn:
            n_standings = save_standings_from_groups(conn, groups)
    print(f"  {n_standings} entradas de standings cargadas ({len(groups)} grupos)\n")

    # ==================================================================
    # PASO 2 - The Odds API (consume cuota)
    # ==================================================================
    print("-" * 55)
    print("PASO 2 - The Odds API")
    print("-" * 55)

    try:
        odds_client = OddsAPIClient()
        quota = odds_client.get_quota_remaining()
        print(f"  Cuota disponible: {quota}/500")

        if quota >= 10:
            try:
                events = odds_client.get_all_odds()
                odds_rows = 0
                eventos_con_odds = 0

                with get_db_connection() as conn:
                    for event in events:
                        home_team = event.get("home_team", "")
                        away_team = event.get("away_team", "")
                        commence = event.get("commence_time", "")

                        fid = find_fixture_id(conn, home_team, away_team, commence)

                        for bookmaker in event.get("bookmakers", []):
                            bname = bookmaker.get("title", bookmaker.get("key", ""))
                            for market in bookmaker.get("markets", []):
                                mname = market.get("key", "")
                                for outcome in market.get("outcomes", []):
                                    try:
                                        odd_val = float(outcome.get("price", 0))
                                    except (ValueError, TypeError):
                                        odd_val = None
                                    conn.execute(
                                        "INSERT INTO odds_data "
                                        "(fixture_id, bookmaker_name, market_name, outcome_name, odd_value) "
                                        "VALUES (?, ?, ?, ?, ?)",
                                        (fid, bname, mname, outcome.get("name"), odd_val),
                                    )
                                    odds_rows += 1
                        eventos_con_odds += 1

                quota_after = odds_client.get_quota_remaining()
                print(f"  Odds para {eventos_con_odds} partidos ({odds_rows} filas)")
                print(f"  Cuota restante: {quota_after}")

            except OddsAPIQuotaError as e:
                print(f"  [!] Cuota insuficiente: {e}")
            except Exception as e:
                print(f"  [!] Error The Odds API: {e}")
        else:
            print("  [!] Cuota < 10, saltando odds")

    except ValueError as e:
        print(f"  [!] OddsAPIClient no configurado: {e}")

    print()

    # ==================================================================
    # PASO 3 - API-Football WC 2022 historico
    # ==================================================================
    print("-" * 55)
    print("PASO 3 - API-Football WC 2022 (historico)")
    print("-" * 55)

    try:
        api_client = APIFootballClient()
        antes = api_client.get_daily_request_count()

        data_2022 = api_client.get_fixtures_2022()
        fixtures_2022 = data_2022.get("response", [])

        if data_2022.get("errors"):
            print(f"  [!] API devolvio errores: {data_2022['errors']}")

        saved_2022 = 0
        if fixtures_2022:
            with get_db_connection() as conn:
                for item in fixtures_2022:
                    save_apifootball_fixture(conn, item)
                    saved_2022 += 1

        despues = api_client.get_daily_request_count()
        print(f"  {saved_2022} fixtures WC2022 guardados")
        print(f"  Requests API-Football hoy: {antes} -> {despues}/100")

    except RateLimitError as e:
        print(f"  [!] Rate limit API-Football: {e}")
    except Exception as e:
        print(f"  [!] Error API-Football: {e}")

    # ==================================================================
    # RESUMEN FINAL
    # ==================================================================
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db_connection() as conn:
        tables = [
            "fixtures", "teams", "predictions", "odds_data",
            "match_statistics", "standings", "injuries",
            "model_calibration", "api_cache", "request_log",
        ]
        counts = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                counts[t] = "N/A"

        # Breakdown fixtures by source
        try:
            f2026 = conn.execute(
                "SELECT COUNT(*) FROM fixtures WHERE tournament_year = 2026"
            ).fetchone()[0]
            f2022 = conn.execute(
                "SELECT COUNT(*) FROM fixtures WHERE tournament_year = 2022"
            ).fetchone()[0]
        except Exception:
            f2026 = f2022 = "N/A"

        real_req = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE date = ? AND cached = 0", (today,)
        ).fetchone()[0]
        cached_req = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE date = ? AND cached = 1", (today,)
        ).fetchone()[0]

    print()
    print("=" * 55)
    print("  RESUMEN FINAL")
    print("=" * 55)
    for t, c in counts.items():
        print(f"  {t:<22}: {c}")
    print(f"  {'fixtures WC2026':<22}: {f2026}")
    print(f"  {'fixtures WC2022':<22}: {f2022}")
    print(f"  {'requests reales hoy':<22}: {real_req}")
    print(f"  {'requests cacheados hoy':<22}: {cached_req}")
    print("=" * 55)


if __name__ == "__main__":
    main()
