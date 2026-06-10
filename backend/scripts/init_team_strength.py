"""
Inicializa team_strength para los 48 equipos WC2026.
Ejecutar UNA VEZ antes del torneo.
"""
import sqlite3, json, os
from datetime import datetime, timezone

DB_PATH      = "wc2026.db"
RATINGS_PATH = "backend/data/team_ratings.json"
WC_AVG_GOALS = 1.30  # goles promedio por equipo por partido en Mundiales

# Nombres en fixtures que difieren de las keys en team_ratings.json
FIXTURE_TO_RATINGS = {
    "USA":                   "United States",
    "Bosnia & Herzegovina":  "Bosnia and Herzegovina",
    "Curaçao":               "Curacao",
}

def _is_placeholder(name: str) -> bool:
    """Filtra posiciones de fase eliminatoria (1A, W73, L101, 3A/B/C/D/F)."""
    import re
    return bool(re.match(r'^(\d[A-Z]|[WL]\d+|\d[A-Z](\/[A-Z])+)$', name))

def init_team_strength():
    conn = sqlite3.connect(DB_PATH)

    with open(RATINGS_PATH, encoding="utf-8") as f:
        ratings = json.load(f)

    # Stats WC2022 para equipos con historial
    wc22 = conn.execute("""
        SELECT home_team_name, away_team_name, home_goals, away_goals
        FROM fixtures
        WHERE tournament_year = 2022
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
    """).fetchall()

    stats = {}
    for home, away, hg, ag in wc22:
        for team, scored, conceded in [(home, hg, ag), (away, ag, hg)]:
            if team not in stats:
                stats[team] = {"scored": 0, "conceded": 0, "games": 0}
            stats[team]["scored"]   += scored
            stats[team]["conceded"] += conceded
            stats[team]["games"]    += 1

    # Equipos únicos WC2026
    rows = conn.execute("""
        SELECT DISTINCT home_team_name FROM fixtures WHERE tournament_year = 2026
        UNION
        SELECT DISTINCT away_team_name FROM fixtures WHERE tournament_year = 2026
    """).fetchall()
    teams_wc26 = sorted([r[0] for r in rows if not _is_placeholder(r[0])])

    now      = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for i, team in enumerate(teams_wc26, start=1):
        ratings_key = FIXTURE_TO_RATINGS.get(team, team)
        ext = ratings.get(ratings_key, {})
        elo = ext.get("elo", 1400)

        if team in stats and stats[team]["games"] > 0:
            g  = stats[team]["games"]
            attack_rating  = (stats[team]["scored"]   / g) / WC_AVG_GOALS
            defense_rating = (stats[team]["conceded"] / g) / WC_AVG_GOALS
            games_played   = g
            goals_scored   = stats[team]["scored"]
            goals_conceded = stats[team]["conceded"]
        else:
            # Estimar desde Elo (1200=débil → 2000=fuerte)
            elo_norm       = max(0.0, min(1.0, (elo - 1200) / 800))
            attack_rating  = 0.70 + elo_norm * 0.90
            defense_rating = 1.30 - elo_norm * 0.50
            games_played   = 0
            goals_scored   = 0
            goals_conceded = 0

        attack_rating  = round(attack_rating,  4)
        defense_rating = round(defense_rating, 4)

        conn.execute("""
            INSERT OR REPLACE INTO team_strength
            (team_id, team_name, elo_rating, attack_rating, defense_rating,
             games_played, goals_scored, goals_conceded, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (i, team, elo, attack_rating, defense_rating,
              games_played, goals_scored, goals_conceded, now))
        inserted += 1

        src = "WC22" if games_played > 0 else "Elo"
        print(f"  [{src:3s}] {team:30s} elo={elo:4d} "
              f"atk={attack_rating:.3f} def={defense_rating:.3f}")

    conn.commit()
    conn.close()
    print(f"\n[OK] {inserted} equipos insertados en team_strength")

if __name__ == "__main__":
    init_team_strength()
