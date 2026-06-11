"""
Actualiza team_strength usando resultados internacionales recientes (2023-2024).
Fuente: github.com/martj42/international_results — sin API key, gratis.

Ejecutar ANTES del primer partido:
    python backend/scripts/seed_recent_form.py

Luego re-generar predicciones:
    python backend/scripts/run_predictions.py
"""
import csv
import io
import math
import sqlite3
import requests
import os
from datetime import date, datetime

DB_PATH     = "wc2026.db"
RESULTS_URL = ("https://raw.githubusercontent.com/"
               "martj42/international_results/master/results.csv")

# Ventana de datos: últimos 2 años antes del Mundial
DATE_FROM = date(2023, 1, 1)
DATE_TO   = date(2026, 6, 10)   # hasta hoy (excluye el torneo mismo)

# Pesos por tipo de torneo (competencia más importante = mayor peso)
TOURNAMENT_WEIGHTS = {
    "FIFA World Cup":                    3.0,
    "FIFA World Cup qualification":      2.5,
    "Copa América":                      2.5,
    "UEFA Euro":                         2.5,
    "UEFA Euro qualification":           2.0,
    "Africa Cup of Nations":             2.5,
    "Africa Cup of Nations qualification": 2.0,
    "AFC Asian Cup":                     2.5,
    "AFC Asian Cup qualification":       2.0,
    "CONCACAF Gold Cup":                 2.0,
    "CONCACAF Nations League":           1.8,
    "UEFA Nations League":               1.8,
    "CONMEBOL–UEFA Cup of Champions":    2.0,
    "Friendly":                          0.8,
}

def tournament_weight(tournament: str) -> float:
    """Busca el peso exacto o por substring, default 1.0."""
    for key, w in TOURNAMENT_WEIGHTS.items():
        if key.lower() in tournament.lower():
            return w
    return 1.0

def recency_weight(match_date: date) -> float:
    """
    Peso exponencial por recencia.
    Partido de hoy = 1.0, partido de hace 1 año ≈ 0.61, hace 2 años ≈ 0.37
    """
    days_ago = (date.today() - match_date).days
    return math.exp(-days_ago / 365.0)

# Mapeo de nombres del dataset → nombres en team_strength (openfootball)
NAME_MAP = {
    "Côte d'Ivoire":            "Ivory Coast",
    "Cote d'Ivoire":            "Ivory Coast",
    "United States":            "USA",
    "Bosnia and Herzegovina":   "Bosnia & Herzegovina",
    "Democratic Republic of the Congo": "DR Congo",
    "Congo DR":                 "DR Congo",
    "South Korea":              "South Korea",
    "Republic of Korea":        "South Korea",
    "Korea Republic":           "South Korea",
    "Cape Verde":               "Cape Verde",
    "Saudi Arabia":             "Saudi Arabia",
    "New Zealand":              "New Zealand",
    "Czech Republic":           "Czech Republic",
    "Czechia":                  "Czech Republic",
}

def normalize(name: str) -> str:
    return NAME_MAP.get(name, name)

def download_csv() -> list:
    """Descarga y parsea el CSV. Retorna lista de dicts."""
    print(f"Descargando datos de {RESULTS_URL} ...")
    resp = requests.get(RESULTS_URL, timeout=60)
    resp.raise_for_status()
    print(f"  Descargado: {len(resp.content):,} bytes")

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        try:
            match_date = date.fromisoformat(row["date"])
        except (ValueError, KeyError):
            continue
        if match_date < DATE_FROM or match_date > DATE_TO:
            continue
        try:
            hs  = int(float(row["home_score"]))
            as_ = int(float(row["away_score"]))
        except (ValueError, TypeError):
            continue
        rows.append({
            "date":       match_date,
            "home_team":  normalize(row["home_team"].strip()),
            "away_team":  normalize(row["away_team"].strip()),
            "home_score": hs,
            "away_score": as_,
            "tournament": row.get("tournament", "Friendly").strip(),
            "neutral":    row.get("neutral", "FALSE").strip().upper() == "TRUE",
        })
    print(f"  Partidos en ventana {DATE_FROM}–{DATE_TO}: {len(rows):,}")
    return rows

def get_wc26_teams() -> list:
    """Retorna lista de team_names en team_strength."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT team_name FROM team_strength ORDER BY team_name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

def compute_ratings(matches: list, teams: list) -> dict:
    """
    Calcula ratings ponderados para cada equipo.
    Retorna dict {team_name: {attack, defense, games, goals_scored, goals_conceded}}
    """
    WC_AVG = 1.30   # goles promedio por equipo por partido en WC

    # Acumular stats ponderadas
    stats = {t: {
        "w_scored": 0.0, "w_conceded": 0.0,
        "w_total":  0.0,
        "games":    0,
        "scored":   0,
        "conceded": 0,
    } for t in teams}

    teams_set = set(teams)
    skipped   = 0

    for m in matches:
        home = m["home_team"]
        away = m["away_team"]
        tw   = tournament_weight(m["tournament"])
        rw   = recency_weight(m["date"])
        w    = tw * rw

        for team, scored, conceded in [
            (home, m["home_score"], m["away_score"]),
            (away, m["away_score"], m["home_score"]),
        ]:
            if team not in teams_set:
                skipped += 1
                continue
            s = stats[team]
            s["w_scored"]   += scored   * w
            s["w_conceded"] += conceded * w
            s["w_total"]    += w
            s["games"]      += 1
            s["scored"]     += scored
            s["conceded"]   += conceded

    print(f"  Ignorados (equipos fuera de WC2026): {skipped:,} registros")

    # Promedio global ponderado (para normalizar)
    total_scored = sum(s["w_scored"] for s in stats.values())
    total_weight = sum(s["w_total"]  for s in stats.values())
    league_avg   = (total_scored / total_weight) if total_weight > 0 else WC_AVG

    print(f"  Promedio de goles ponderado (ventana 2023-2026): {league_avg:.3f}")

    results = {}
    for team, s in stats.items():
        if s["w_total"] > 0 and s["games"] >= 3:
            avg_scored   = s["w_scored"]   / s["w_total"]
            avg_conceded = s["w_conceded"] / s["w_total"]
            attack  = round(avg_scored   / league_avg, 4)
            defense = round(avg_conceded / league_avg, 4)
            source  = "recent_form"
        else:
            attack  = None
            defense = None
            source  = "insufficient_data"
        results[team] = {
            "attack":   attack,
            "defense":  defense,
            "games":    s["games"],
            "scored":   s["scored"],
            "conceded": s["conceded"],
            "source":   source,
        }

    # ── NORMALIZACIÓN AL CONTEXTO WC ─────────────────────────────────────────
    # Los ratings están calibrados contra el dataset internacional completo,
    # que incluye partidos de equipos débiles con más goles.
    # Re-centra para que el equipo WC promedio tenga atk=1.0, def=1.0.
    # Esto asegura que WC_AVG_GOALS=1.30 en poisson.py sea el valor correcto.

    real_teams = {t: r for t, r in results.items()
                  if r['attack'] is not None and r['games'] >= 3}

    if real_teams:
        avg_atk = sum(r['attack']  for r in real_teams.values()) / len(real_teams)
        avg_def = sum(r['defense'] for r in real_teams.values()) / len(real_teams)

        print(f"\n  Promedio pre-normalización:")
        print(f"    avg_attack  = {avg_atk:.4f}  (target: 1.0)")
        print(f"    avg_defense = {avg_def:.4f}  (target: 1.0)")

        for team in results:
            if results[team]['attack'] is not None:
                results[team]['attack']  = round(results[team]['attack']  / avg_atk, 4)
                results[team]['defense'] = round(results[team]['defense'] / avg_def, 4)

        print(f"  OK Normalizacion aplicada")

    return results

def update_team_strength(ratings: dict) -> None:
    """Actualiza team_strength con los nuevos ratings."""
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.utcnow().isoformat()
    updated  = 0
    kept_elo = 0

    print("\n=== ACTUALIZACIÓN DE RATINGS ===")
    print(f"  {'EQUIPO':30s} {'FUENTE':15s} "
          f"{'ATK_OLD':>8} {'ATK_NEW':>8} "
          f"{'DEF_OLD':>8} {'DEF_NEW':>8}  GAMES")

    for team, r in sorted(ratings.items()):
        old = conn.execute(
            "SELECT attack_rating, defense_rating, elo_rating "
            "FROM team_strength WHERE team_name = ?", (team,)
        ).fetchone()

        if not old:
            continue

        old_atk, old_def, elo = old

        if r["attack"] is not None:
            new_atk = r["attack"]
            new_def = r["defense"]
            source  = r["source"]
            updated += 1
        else:
            new_atk = old_atk
            new_def = old_def
            source  = "elo_kept"
            kept_elo += 1

        conn.execute("""
            UPDATE team_strength
            SET attack_rating  = ?,
                defense_rating = ?,
                games_played   = ?,
                goals_scored   = ?,
                goals_conceded = ?,
                last_updated   = ?
            WHERE team_name = ?
        """, (new_atk, new_def,
              r["games"], r["scored"], r["conceded"],
              now, team))

        delta_atk = abs(new_atk - old_atk)
        delta_def = abs(new_def - old_def)
        flag = " <--" if delta_atk > 0.15 or delta_def > 0.15 else ""
        print(f"  {team:30s} {source:15s} "
              f"{old_atk:8.3f} {new_atk:8.3f} "
              f"{old_def:8.3f} {new_def:8.3f}  "
              f"{r['games']}{flag}")

    conn.commit()
    conn.close()
    print(f"\n  Actualizados con datos reales: {updated}")
    print(f"  Mantenidos con Elo (pocos datos): {kept_elo}")

def main():
    print("=" * 60)
    print("  seed_recent_form.py — Forma reciente 2023-2026")
    print("=" * 60)

    teams = get_wc26_teams()
    print(f"\nEquipos en team_strength: {len(teams)}")

    matches = download_csv()
    ratings = compute_ratings(matches, teams)
    update_team_strength(ratings)

    print("\nteam_strength actualizado con forma reciente")
    print("  Siguiente paso: python backend/scripts/run_predictions.py")

if __name__ == "__main__":
    main()
