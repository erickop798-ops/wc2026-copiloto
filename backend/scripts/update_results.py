"""
SCRIPT POST-JORNADA — Ejecutar MANUALMENTE después de cada jornada WC2026.

Uso:   python backend/scripts/update_results.py
Costo: 2 unidades API por ejecución.

Pasos:
  1. Fetch resultados completados (The Odds API /scores, últimos 3 días)
  2. Normalizar nombres (The Odds API puede diferir de openfootball)
  3. UPDATE home_goals / away_goals / status = 'FT' en fixtures
  4. Registrar partidos sin match en BD (diagnóstico de nombres)
  5. Recalibrar Poisson con shrinkage bayesiano
  6. Log en model_calibration (si esquema compatible)
"""
import sqlite3, sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

DB_PATH      = "wc2026.db"
PRIOR_WEIGHT = 5       # partidos virtuales del prior (shrinkage bayesiano)
WC_AVG_GOALS = 1.30

# ── Normalización de nombres The Odds API → nombres en BD (openfootball) ────
# Añadir nuevas entradas si aparecen warnings "SIN MATCH" en el log
ODDS_API_TO_DB = {
    "USA":                          "United States",
    "United States of America":     "United States",
    "Côte d'Ivoire":                "Ivory Coast",
    "Cote d'Ivoire":                "Ivory Coast",
    "Côte d’Ivoire":           "Ivory Coast",
    "Korea Republic":               "South Korea",
    "Republic of Korea":            "South Korea",
    "Bosnia-Herzegovina":           "Bosnia and Herzegovina",
    "Bosnia & Herzegovina":         "Bosnia and Herzegovina",
    "Bosnia-Herzegowina":           "Bosnia and Herzegovina",
    "Congo DR":                     "DR Congo",
    "Congo, DR":                    "DR Congo",
    "D.R. Congo":                   "DR Congo",
    "Curaçao":                      "Curacao",
    "Czech Rep":                    "Czech Republic",
    "Czechia":                      "Czech Republic",
    "Cape Verde Islands":           "Cape Verde",
    "Cabo Verde":                   "Cape Verde",
}

def normalize_name(name: str) -> str:
    """Normaliza nombre de The Odds API al nombre en BD."""
    return ODDS_API_TO_DB.get(name, name)

def _try_team_resolver(name: str) -> str:
    """
    Usa team_resolver.resolve_team_name para alias de playoff UEFA.
    Si falla, retorna el nombre original.
    """
    try:
        from backend.lib.team_resolver import resolve_team_name
        result = resolve_team_name(name)
        if result and isinstance(result, str):
            return result
    except Exception:
        pass
    return name

def _conn():
    return sqlite3.connect(DB_PATH)

# ── PASO 1 y 2: Actualizar resultados ────────────────────────────────────────

def update_completed_results(client) -> tuple:
    """
    Retorna (n_updated, unmatched_list)
    unmatched_list: pares (home, away) de The Odds API sin match en BD.
    """
    print("Obteniendo resultados de The Odds API...")
    scores = client.get_scores(days_from=3)

    if not scores:
        print("  ⚠ Sin resultados devueltos por la API.")
        return 0, []

    conn      = _conn()
    updated   = 0
    unmatched = []

    for match in scores:
        if not match.get("completed"):
            continue

        raw_home = match.get("home_team", "")
        raw_away = match.get("away_team", "")

        # Normalizar nombre: primero dict local, luego team_resolver
        home = normalize_name(raw_home)
        away = normalize_name(raw_away)
        home = _try_team_resolver(home)
        away = _try_team_resolver(away)

        # Parsear scores (The Odds API devuelve dos formatos posibles)
        score_obj  = match.get("scores")
        home_score = None
        away_score = None

        try:
            if isinstance(score_obj, list):
                # Formato: [{name: "TeamA", score: "2"}, {name: "TeamB", score: "1"}]
                for s in score_obj:
                    s_name  = normalize_name(s.get("name", ""))
                    s_score = s.get("score")
                    if s_name == home:
                        home_score = int(s_score)
                    elif s_name == away:
                        away_score = int(s_score)
                # Fallback: si no matchearon por nombre, usar posición 0/1
                if home_score is None and len(score_obj) >= 2:
                    home_score = int(score_obj[0].get("score", 0))
                    away_score = int(score_obj[1].get("score", 0))
            elif isinstance(score_obj, dict):
                # Formato: {home: {score: "2"}, away: {score: "1"}}
                home_score = int(score_obj.get("home", {}).get("score", 0))
                away_score = int(score_obj.get("away", {}).get("score", 0))
        except (ValueError, TypeError, AttributeError):
            continue

        if home_score is None or away_score is None:
            continue

        result = conn.execute("""
            UPDATE fixtures
            SET home_goals = ?,
                away_goals = ?,
                status     = 'FT'
            WHERE tournament_year = 2026
              AND home_team_name  = ?
              AND away_team_name  = ?
              AND (home_goals IS NULL
                   OR home_goals != ?
                   OR away_goals != ?)
        """, (home_score, away_score, home, away,
              home_score, away_score))

        if result.rowcount > 0:
            print(f"  ✓ {home} {home_score}–{away_score} {away}")
            updated += 1
        elif match.get("completed"):
            existing = conn.execute("""
                SELECT home_goals FROM fixtures
                WHERE tournament_year = 2026
                  AND home_team_name  = ?
                  AND away_team_name  = ?
            """, (home, away)).fetchone()
            if existing is None:
                unmatched.append(f"{raw_home} vs {raw_away}  →  BD buscó: '{home}' vs '{away}'")

    conn.commit()
    conn.close()
    print(f"\n  Resultados actualizados: {updated}")

    if unmatched:
        print(f"\n  ⚠ SIN MATCH EN BD ({len(unmatched)} partidos):")
        for u in unmatched:
            print(f"    {u}")
        print("  → Añadir a ODDS_API_TO_DB en update_results.py y re-ejecutar")

    return updated, unmatched

# ── PASO 3: Recalibración con shrinkage bayesiano ────────────────────────────

def recalibrate_model() -> None:
    """
    Actualiza attack/defense ratings con shrinkage bayesiano.

    Formula:
      atk_new = (PRIOR_WEIGHT * atk_prior + games * atk_obs)
                / (PRIOR_WEIGHT + games)

    PRIOR_WEIGHT = 5 equivale a ~5 partidos virtuales del prior.
    Tras 1 partido real: resultado real pesa 17%  (1 / 6)
    Tras 3 partidos:     resultado real pesa 37%  (3 / 8)
    Tras 6 partidos:     resultado real pesa 55%  (6 / 11)
    Evita que un 4–0 de grupo destruya los priors de Elo/WC22.
    """
    conn      = _conn()
    completed = conn.execute("""
        SELECT home_team_name, away_team_name, home_goals, away_goals
        FROM fixtures
        WHERE tournament_year = 2026
          AND status          = 'FT'
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
    """).fetchall()
    conn.close()

    if not completed:
        print("  Sin partidos WC2026 completados todavía — modelo sin cambios.")
        return

    # Acumular stats WC2026 reales
    stats = {}
    for home, away, hg, ag in completed:
        for team, scored, conceded in [(home, hg, ag), (away, ag, hg)]:
            if team not in stats:
                stats[team] = {"scored": 0, "conceded": 0, "games": 0}
            stats[team]["scored"]   += scored
            stats[team]["conceded"] += conceded
            stats[team]["games"]    += 1

    if not stats:
        return

    # Promedio del torneo para normalizar
    total_scored = sum(s["scored"] for s in stats.values())
    total_games  = sum(s["games"]  for s in stats.values())
    tournament_avg = (total_scored / total_games) if total_games > 0 else WC_AVG_GOALS

    conn    = _conn()
    now     = datetime.now(timezone.utc).isoformat()
    changes = []

    for team, s in stats.items():
        g = s["games"]

        atk_obs = (s["scored"]   / g) / tournament_avg
        dfc_obs = (s["conceded"] / g) / tournament_avg

        old = conn.execute(
            "SELECT attack_rating, defense_rating FROM team_strength "
            "WHERE team_name = ?", (team,)
        ).fetchone()

        if old:
            old_attack, old_defense = old
        else:
            old_attack, old_defense = 1.0, 1.0

        # Shrinkage bayesiano
        atk_new = round(
            (PRIOR_WEIGHT * old_attack  + g * atk_obs) / (PRIOR_WEIGHT + g), 4)
        dfc_new = round(
            (PRIOR_WEIGHT * old_defense + g * dfc_obs) / (PRIOR_WEIGHT + g), 4)

        conn.execute("""
            UPDATE team_strength
            SET attack_rating  = ?,
                defense_rating = ?,
                games_played   = ?,
                goals_scored   = ?,
                goals_conceded = ?,
                last_updated   = ?
            WHERE team_name = ?
        """, (atk_new, dfc_new, g,
              s["scored"], s["conceded"], now, team))

        delta_atk = abs(old_attack  - atk_new)
        delta_dfc = abs(old_defense - dfc_new)
        if delta_atk > 0.01 or delta_dfc > 0.01:
            changes.append(
                f"  {team:30s} atk {old_attack:.3f}→{atk_new:.3f} "
                f"def {old_defense:.3f}→{dfc_new:.3f} ({g}p)"
            )

    # Log en model_calibration si el esquema es compatible
    # Columnas reales: team_id, attack_strength, defense_strength,
    #                  data_source, matches_count, updated_at
    # No tiene calibrated_at ni notes → se omite el log silenciosamente
    try:
        existing_cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(model_calibration)").fetchall()]
        if "calibrated_at" in existing_cols and "matches_used" in existing_cols:
            conn.execute("""
                INSERT INTO model_calibration (calibrated_at, matches_used, notes)
                VALUES (?, ?, ?)
            """, (now, len(completed),
                  f"Recalibracion post-jornada WC2026: {len(stats)} equipos"))
        else:
            print(f"  ℹ model_calibration tiene esquema diferente "
                  f"(cols: {existing_cols}) — log omitido, sin error")
    except Exception as e:
        print(f"  ℹ log model_calibration omitido: {e}")

    conn.commit()
    conn.close()

    print(f"\n  Recalibración (PRIOR_WEIGHT={PRIOR_WEIGHT}, "
          f"{len(completed)} partidos WC2026):")
    print(f"  tournament_avg_goals = {tournament_avg:.3f}")
    if changes:
        for c in changes:
            print(c)
    else:
        print("  Sin cambios significativos en ratings (delta < 0.01)")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # OddsAPIClient toma ODDS_API_KEY del entorno en su constructor
    from backend.lib.odds_api_client import OddsAPIClient
    try:
        client = OddsAPIClient()
    except ValueError as e:
        print(f"✗ {e}")
        sys.exit(1)

    print("=" * 60)
    print("  POST-JORNADA: Actualización de resultados WC2026")
    print("=" * 60)

    n_updated, unmatched = update_completed_results(client)

    if n_updated > 0:
        print("\n  Recalibrando modelo Poisson...")
        recalibrate_model()
    else:
        if not unmatched:
            print("\n  Sin resultados nuevos. Modelo sin cambios.")

    print("\n✓ Proceso completado")

if __name__ == "__main__":
    main()
