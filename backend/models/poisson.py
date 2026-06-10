"""
Modelo Poisson bivariado — predicción de partidos WC2026.
"""
import sqlite3, math

DB_PATH        = "wc2026.db"
HOME_ADVANTAGE = 1.10
WC_AVG_GOALS   = 1.30
MAX_GOALS      = 8

def _conn():
    return sqlite3.connect(DB_PATH)

def get_team_params(team: str) -> dict:
    conn = _conn()
    row  = conn.execute(
        "SELECT attack_rating, defense_rating, elo_rating "
        "FROM team_strength WHERE team_name = ?", (team,)
    ).fetchone()
    conn.close()
    if row:
        return {"attack": row[0], "defense": row[1], "elo": row[2]}
    return {"attack": 1.0, "defense": 1.0, "elo": 1400}

def get_lambda(home: str, away: str) -> tuple:
    h = get_team_params(home)
    a = get_team_params(away)
    lh = max(0.3, min(5.0, h["attack"] * a["defense"] * HOME_ADVANTAGE * WC_AVG_GOALS))
    la = max(0.3, min(5.0, a["attack"] * h["defense"] * WC_AVG_GOALS))
    return round(lh, 4), round(la, 4)

def poisson_pmf(lam: float, k: int) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def score_matrix(lh: float, la: float) -> list:
    n = MAX_GOALS + 1
    return [[poisson_pmf(lh, i) * poisson_pmf(la, j)
             for j in range(n)] for i in range(n)]

def predict_match(fixture_id: int) -> dict:
    conn = _conn()
    row  = conn.execute(
        "SELECT home_team_name, away_team_name, date_utc, group_name, round "
        "FROM fixtures WHERE fixture_id = ?", (fixture_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise ValueError(f"fixture_id {fixture_id} no encontrado")

    home, away, date_utc, group_name, round_name = row
    lh, la = get_lambda(home, away)
    mx     = score_matrix(lh, la)
    n      = MAX_GOALS + 1

    p_home = sum(mx[i][j] for i in range(n) for j in range(n) if i > j)
    p_draw = sum(mx[i][i] for i in range(n))
    p_away = sum(mx[i][j] for i in range(n) for j in range(n) if j > i)

    p_btts_yes = sum(mx[i][j] for i in range(1, n) for j in range(1, n))
    p_btts_no  = 1.0 - p_btts_yes

    def p_over(line):
        return sum(mx[i][j] for i in range(n) for j in range(n)
                   if (i + j) > line)

    p_o15 = p_over(1.5); p_u15 = 1.0 - p_o15
    p_o25 = p_over(2.5); p_u25 = 1.0 - p_o25
    p_o35 = p_over(3.5); p_u35 = 1.0 - p_o35

    return {
        "fixture_id":  fixture_id,
        "home_team":   home,   "away_team":  away,
        "date_utc":    date_utc, "group_name": group_name, "round": round_name,
        "lambda_home": lh,     "lambda_away": la,
        # 1X2
        "p_home": round(p_home, 4), "p_draw": round(p_draw, 4),
        "p_away": round(p_away, 4),
        # BTTS
        "p_btts_yes": round(p_btts_yes, 4), "p_btts_no": round(p_btts_no, 4),
        # Over/Under
        "p_over_15": round(p_o15, 4), "p_under_15": round(p_u15, 4),
        "p_over_25": round(p_o25, 4), "p_under_25": round(p_u25, 4),
        "p_over_35": round(p_o35, 4), "p_under_35": round(p_u35, 4),
        # Asian Handicap −0.5 / +0.5
        "p_ah_home": round(p_home, 4), "p_ah_away": round(p_away, 4),
        # Double Chance
        "p_dc_1x": round(p_home + p_draw, 4),
        "p_dc_x2": round(p_draw + p_away, 4),
        "p_dc_12": round(p_home + p_away, 4),
    }
