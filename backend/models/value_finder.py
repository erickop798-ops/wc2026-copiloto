"""
Detecta value bets reales: edge >= 5% y is_informational = False.
Double Chance y O/U 1.5/3.5 son informativos y NO entran a value_bets.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from backend.models.poisson  import predict_match
from backend.models.markets  import build_market_comparison

DB_PATH        = "wc2026.db"
EDGE_THRESHOLD = 0.05

def _conn():
    return sqlite3.connect(DB_PATH)

def find_value_bets(fixture_id: int, save: bool = True) -> list:
    prediction  = predict_match(fixture_id)
    comparisons = build_market_comparison(fixture_id, prediction)

    value = [
        c for c in comparisons
        if not c.get("is_informational", False)
        and c.get("edge") is not None
        and c["edge"] >= EDGE_THRESHOLD
    ]
    value.sort(key=lambda x: x["edge"], reverse=True)

    if save and value:
        conn = _conn()
        now  = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM value_bets WHERE fixture_id = ?", (fixture_id,))
        for vb in value:
            conn.execute("""
                INSERT INTO value_bets
                (fixture_id, market_name, outcome_name, model_prob,
                 bookmaker_prob, edge, kelly_stake, bookmaker_name,
                 odd_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fixture_id,
                vb["market"],    vb["outcome"],
                vb["model_prob"], vb["bookmaker_prob"],
                vb["edge"],       0.0,
                vb["bookmaker_name"], vb["bookmaker_odds"],
                now,
            ))
        conn.commit()
        conn.close()

    return value

def find_upcoming_value_bets(days_ahead: int = 3) -> list:
    conn   = _conn()
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
    rows   = conn.execute("""
        SELECT fixture_id, home_team_name, away_team_name, date_utc
        FROM fixtures
        WHERE tournament_year = 2026
          AND status          IN ('NS', 'scheduled')
          AND date_utc       <= ?
        ORDER BY date_utc
    """, (cutoff,)).fetchall()
    conn.close()

    all_value = []
    for fixture_id, home, away, date_utc in rows:
        try:
            vbs = find_value_bets(fixture_id, save=True)
            for vb in vbs:
                vb.update({"home_team": home, "away_team": away,
                           "date_utc": date_utc, "fixture_id": fixture_id})
            all_value.extend(vbs)
        except Exception as e:
            print(f"  ⚠ {home} vs {away}: {e}")

    return all_value
