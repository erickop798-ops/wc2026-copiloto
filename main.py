"""
wc2026-copiloto — Servidor FastAPI
Ejecutar: python main.py
Ver en:   http://localhost:8000
"""
import re
import sys
import os
import sqlite3
import uvicorn
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Asegurar que backend/ sea importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from backend.models.poisson   import predict_match
from backend.models.markets   import build_market_comparison
from backend.models.kelly     import recommend_bets, kelly_stake
from backend.utils.format     import (
    decimal_to_american, format_pct, format_edge,
    get_explanation, edge_color_class
)

DB_PATH  = "wc2026.db"
BANKROLL = 1000.0  # MXN

app = FastAPI(title="wc2026-copiloto", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

_PLACEHOLDER_RE = re.compile(r'^(\d[A-Z](/[A-Z])*|[WL]\d+)$')

def _is_placeholder(name: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(name or ""))

# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.get("/")
def serve_dashboard():
    """Sirve el dashboard principal."""
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "frontend", "index.html")
    return FileResponse(html_path)


@app.get("/api/predictions")
def get_predictions():
    """
    Retorna predicciones completas para partidos WC2026 proximos (3 dias).
    Incluye todos los mercados, momios americanos y value bets.
    """
    conn = get_db()
    cutoff = (datetime.now(timezone.utc) + timedelta(days=4)).strftime('%Y-%m-%dT23:59:59')

    rows = conn.execute("""
        SELECT fixture_id, home_team_name, away_team_name,
               date_utc, group_name, round, status
        FROM fixtures
        WHERE tournament_year = 2026
          AND status NOT IN ('FT', 'finished', 'completed')
          AND date_utc <= ?
        ORDER BY date_utc
    """, (cutoff,)).fetchall()

    matches = []

    for row in rows:
        fixture_id = row["fixture_id"]
        home       = row["home_team_name"]
        away       = row["away_team_name"]
        date_utc   = row["date_utc"]
        group_name = row["group_name"] or ""
        round_name = row["round"] or "Group Stage"

        if _is_placeholder(home) or _is_placeholder(away):
            continue

        try:
            pred = predict_match(fixture_id)
        except Exception:
            continue

        try:
            comparisons = build_market_comparison(fixture_id, pred)
        except Exception:
            comparisons = []

        # Value bets guardados en BD por run_predictions.py
        vb_rows = conn.execute("""
            SELECT market_name, outcome_name, model_prob,
                   bookmaker_prob, edge, odd_value, bookmaker_name
            FROM value_bets
            WHERE fixture_id = ?
            ORDER BY edge DESC
        """, (fixture_id,)).fetchall()

        value_bets = []
        for vb in vb_rows:
            edge      = vb["edge"] or 0
            odd_val   = vb["odd_value"] or 0
            bm_prob   = vb["bookmaker_prob"]
            stake_pct = kelly_stake(vb["model_prob"] or 0, odd_val)
            stake_mxn = round(stake_pct * BANKROLL, 0)
            value_bets.append({
                "market":                  vb["market_name"],
                "outcome":                 vb["outcome_name"],
                "explanation":             get_explanation(
                                               vb["market_name"],
                                               vb["outcome_name"],
                                               home, away),
                "model_pct":               format_pct(vb["model_prob"]),
                "bm_pct":                  format_pct(bm_prob) if bm_prob else None,
                "bookmaker_odds_american": decimal_to_american(odd_val),
                "bookmaker_odds_decimal":  round(odd_val, 2) if odd_val else None,
                "edge_pct":                format_edge(edge),
                "edge_raw":                round(edge * 100, 1),
                "edge_color":              edge_color_class(edge),
                "kelly_stake_mxn":         int(stake_mxn),
                "kelly_pct":               f"{stake_pct * 100:.1f}%",
                "bookmaker":               vb["bookmaker_name"],
            })

        # Formatear comparaciones de mercados
        markets_formatted = []
        seen = set()
        for c in comparisons:
            market  = c.get("market", "")
            outcome = c.get("outcome", "")
            key     = f"{market}_{outcome}"
            if key in seen:
                continue
            seen.add(key)

            is_info  = c.get("is_informational", False)
            edge_val = c.get("edge")
            bm_odds  = c.get("bookmaker_odds")
            is_value = (not is_info
                        and edge_val is not None
                        and edge_val >= 0.05)

            markets_formatted.append({
                "market":      market,
                "outcome":     outcome,
                "explanation": get_explanation(market, outcome, home, away),
                "model_pct":   format_pct(c.get("model_prob")),
                "model_raw":   round((c.get("model_prob") or 0) * 100, 1),
                "bm_american": decimal_to_american(bm_odds) if bm_odds else None,
                "bm_decimal":  round(bm_odds, 2) if bm_odds else None,
                "bm_pct":      format_pct(c.get("bookmaker_prob")),
                "edge":        format_edge(edge_val) if edge_val is not None else None,
                "edge_color":  edge_color_class(edge_val),
                "is_value":    is_value,
                "is_info":     is_info,
                "bookmaker":   c.get("bookmaker_name", ""),
            })

        matches.append({
            "fixture_id": fixture_id,
            "home":       home,
            "away":       away,
            "date_utc":   date_utc,
            "group":      group_name,
            "round":      round_name,
            "model": {
                "lambda_home": pred.get("lambda_home"),
                "lambda_away": pred.get("lambda_away"),
                "home_win":    format_pct(pred.get("p_home")),
                "draw":        format_pct(pred.get("p_draw")),
                "away_win":    format_pct(pred.get("p_away")),
                "btts_yes":    format_pct(pred.get("p_btts_yes")),
                "over_15":     format_pct(pred.get("p_over_15")),
                "over_25":     format_pct(pred.get("p_over_25")),
                "over_35":     format_pct(pred.get("p_over_35")),
                "dc_1x":       format_pct(pred.get("p_dc_1x")),
                "dc_x2":       format_pct(pred.get("p_dc_x2")),
                "dc_12":       format_pct(pred.get("p_dc_12")),
            },
            "markets":    markets_formatted,
            "value_bets": value_bets,
            "has_value":  len(value_bets) > 0,
        })

    conn.close()

    total_vbs   = sum(len(m["value_bets"]) for m in matches)
    total_stake = sum(
        sum(vb["kelly_stake_mxn"] for vb in m["value_bets"])
        for m in matches
    )

    return JSONResponse({
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "matches":          matches,
        "total_value_bets": total_vbs,
        "total_stake_mxn":  int(total_stake),
    })


@app.get("/api/standings")
def get_standings():
    """Retorna standings de los 12 grupos WC2026 calculados dinamicamente."""
    conn = get_db()

    completed = conn.execute("""
        SELECT home_team_name, away_team_name, home_goals, away_goals, group_name
        FROM fixtures
        WHERE tournament_year = 2026
          AND status IN ('FT', 'finished', 'completed')
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
        ORDER BY group_name, date_utc
    """).fetchall()

    all_fixtures = conn.execute("""
        SELECT DISTINCT home_team_name, away_team_name, group_name
        FROM fixtures
        WHERE tournament_year = 2026
          AND group_name IS NOT NULL
          AND group_name != ''
    """).fetchall()

    conn.close()

    standings = {}
    for f in all_fixtures:
        for team in [f["home_team_name"], f["away_team_name"]]:
            if _is_placeholder(team):
                continue
            g = f["group_name"]
            if g not in standings:
                standings[g] = {}
            if team not in standings[g]:
                standings[g][team] = {
                    "team": team, "played": 0, "won": 0,
                    "drawn": 0, "lost": 0, "gf": 0, "ga": 0, "pts": 0
                }

    for r in completed:
        g = r["group_name"]
        if not g or g not in standings:
            continue
        home = r["home_team_name"]
        away = r["away_team_name"]
        hg   = r["home_goals"]
        ag   = r["away_goals"]
        if home not in standings[g] or away not in standings[g]:
            continue

        standings[g][home]["played"] += 1
        standings[g][away]["played"] += 1
        standings[g][home]["gf"] += hg
        standings[g][home]["ga"] += ag
        standings[g][away]["gf"] += ag
        standings[g][away]["ga"] += hg

        if hg > ag:
            standings[g][home]["won"]  += 1; standings[g][home]["pts"] += 3
            standings[g][away]["lost"] += 1
        elif ag > hg:
            standings[g][away]["won"]  += 1; standings[g][away]["pts"] += 3
            standings[g][home]["lost"] += 1
        else:
            standings[g][home]["drawn"] += 1; standings[g][home]["pts"] += 1
            standings[g][away]["drawn"] += 1; standings[g][away]["pts"] += 1

    result = []
    for group_name in sorted(standings.keys()):
        teams = sorted(
            standings[group_name].values(),
            key=lambda x: (-x["pts"], -(x["gf"] - x["ga"]), -x["gf"])
        )
        for t in teams:
            t["gd"] = t["gf"] - t["ga"]
        result.append({"group": group_name, "teams": teams})

    return JSONResponse({"groups": result})


@app.get("/api/stats")
def get_stats():
    """Info del modelo, cuota API y ultima calibracion."""
    conn = get_db()

    total_wc26 = conn.execute(
        "SELECT COUNT(*) FROM fixtures WHERE tournament_year=2026"
    ).fetchone()[0]

    completed = conn.execute(
        "SELECT COUNT(*) FROM fixtures WHERE tournament_year=2026 "
        "AND status IN ('FT','finished','completed')"
    ).fetchone()[0]

    total_vbs = conn.execute("SELECT COUNT(*) FROM value_bets").fetchone()[0]

    team_count = conn.execute(
        "SELECT COUNT(*) FROM team_strength"
    ).fetchone()[0]

    last_cal = None
    try:
        row = conn.execute(
            "SELECT * FROM model_calibration ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            last_cal = dict(row)
    except Exception:
        pass

    conn.close()

    return JSONResponse({
        "model":             "Poisson bivariado + Kelly 25%",
        "total_fixtures":    total_wc26,
        "completed":         completed,
        "remaining":         total_wc26 - completed,
        "teams_calibrated":  team_count,
        "value_bets_stored": total_vbs,
        "last_calibration":  last_cal,
        "bankroll_mxn":      BANKROLL,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  wc2026-copiloto — Dashboard")
    print("  Abrir en browser: http://localhost:8000")
    print("  Detener: Ctrl+C")
    print("=" * 55)
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="warning",
    )
