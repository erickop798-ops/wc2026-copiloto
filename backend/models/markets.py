"""
Comparación modelo vs bookmaker para los 5 mercados.

IMPORTANTE — Decisiones de diseño:
- O/U 2.5: usa mercado 'totals' (una sola línea, comparación confiable)
- O/U 1.5 y O/U 3.5: SIN comparación bookmaker hasta que odds_data
  tenga columna 'point'. Las líneas de alternate_totals son
  indistinguibles en el esquema actual.
- Double Chance: informativa únicamente (bookmaker_odds derivado,
  no apuesta directa). is_informational=True → no se guarda en value_bets.
"""
import sqlite3

DB_PATH = "wc2026.db"

def _conn():
    return sqlite3.connect(DB_PATH)

def get_best_odd(fixture_id: int, market: str, outcome: str) -> dict | None:
    """
    Retorna {bookmaker_name, odd_value, outcome_name} del mejor odd disponible.
    Prioriza Pinnacle. Parámetro `point` eliminado (era código muerto sin
    columna point en odds_data).
    """
    conn = _conn()
    rows = conn.execute("""
        SELECT bookmaker_name, odd_value
        FROM odds_data
        WHERE fixture_id  = ?
          AND market_name = ?
          AND outcome_name = ?
        ORDER BY
            CASE bookmaker_name WHEN 'pinnacle' THEN 0 ELSE 1 END,
            odd_value DESC
    """, (fixture_id, market, outcome)).fetchall()
    conn.close()

    if not rows:
        return None
    bm, odd = rows[0]
    return {"bookmaker_name": bm, "odd_value": odd, "outcome_name": outcome}

def remove_vig(h: float, d: float, a: float) -> tuple:
    """Elimina margen del bookmaker de probabilidades implícitas 1X2."""
    if not all([h, d, a]):
        return None, None, None
    rh, rd, ra = 1/h, 1/d, 1/a
    total = rh + rd + ra
    return rh/total, rd/total, ra/total

def build_market_comparison(fixture_id: int, prediction: dict) -> list:
    """
    Lista de comparaciones modelo vs bookmaker para los 5 mercados.

    Retorna una lista de dicts. Cada dict tiene:
      market, outcome, model_prob, bookmaker_prob, bookmaker_odds,
      edge, bookmaker_name, is_informational (bool)

    Solo entran a value_bets los que tienen is_informational = False
    y edge >= umbral.
    """
    comparisons = []

    # Nombres reales de equipos (la BD guarda "Mexico", no "Home")
    _c = _conn()
    _tr = _c.execute(
        "SELECT home_team_name, away_team_name FROM fixtures WHERE fixture_id = ?",
        (fixture_id,)
    ).fetchone()
    _c.close()
    home_team = _tr[0] if _tr else "Home"
    away_team = _tr[1] if _tr else "Away"

    # ── 1X2 ─────────────────────────────────────────────────────────────────
    for label, prob_key, bm_out in [
        ("Home", "p_home", home_team),
        ("Draw", "p_draw", "Draw"),
        ("Away", "p_away", away_team),
    ]:
        best = get_best_odd(fixture_id, "h2h", bm_out)
        if best:
            bm_prob    = 1.0 / best["odd_value"]
            model_prob = prediction[prob_key]
            comparisons.append({
                "market":          "1X2",
                "outcome":         label,
                "model_prob":      round(model_prob, 4),
                "bookmaker_prob":  round(bm_prob,    4),
                "bookmaker_odds":  best["odd_value"],
                "edge":            round(model_prob - bm_prob, 4),
                "bookmaker_name":  best["bookmaker_name"],
                "is_informational": False,
            })

    # ── BTTS — mercado 'btts' no existe en odds_data; solo probabilidad modelo ──
    for label, prob_key in [
        ("BTTS Si", "p_btts_yes"),
        ("BTTS No", "p_btts_no"),
    ]:
        comparisons.append({
            "market":          "BTTS",
            "outcome":         label,
            "model_prob":      round(prediction.get(prob_key, 0), 4),
            "bookmaker_prob":  None,
            "bookmaker_odds":  None,
            "edge":            None,
            "bookmaker_name":  None,
            "is_informational": True,
        })

    # ── O/U 2.5 — usa mercado 'totals' (línea única, confiable) ─────────────
    for label, prob_key, bm_out in [
        ("Over 2.5",  "p_over_25",  "Over"),
        ("Under 2.5", "p_under_25", "Under"),
    ]:
        best = get_best_odd(fixture_id, "totals", bm_out)
        if best:
            bm_prob    = 1.0 / best["odd_value"]
            model_prob = prediction[prob_key]
            comparisons.append({
                "market":          "O/U 2.5",
                "outcome":         label,
                "model_prob":      round(model_prob, 4),
                "bookmaker_prob":  round(bm_prob,    4),
                "bookmaker_odds":  best["odd_value"],
                "edge":            round(model_prob - bm_prob, 4),
                "bookmaker_name":  best["bookmaker_name"],
                "is_informational": False,
            })

    # ── O/U 1.5 y O/U 3.5 — SOLO probabilidad del modelo ────────────────────
    # No hay columna 'point' en odds_data: las líneas de alternate_totals
    # son indistinguibles y asignar una odd a la línea equivocada generaría
    # falsos value bets (+56% de edge fantasma).
    for label, prob_key in [
        ("Over 1.5",  "p_over_15"),
        ("Under 1.5", "p_under_15"),
        ("Over 3.5",  "p_over_35"),
        ("Under 3.5", "p_under_35"),
    ]:
        comparisons.append({
            "market":          "O/U 1.5/3.5",
            "outcome":         label,
            "model_prob":      round(prediction[prob_key], 4),
            "bookmaker_prob":  None,
            "bookmaker_odds":  None,
            "edge":            None,
            "bookmaker_name":  None,
            "is_informational": True,
        })

    # ── Asian Handicap −0.5 / +0.5 ──────────────────────────────────────────
    for label, prob_key, bm_out in [
        (f"{home_team} −0.5", "p_ah_home", home_team),
        (f"{away_team} +0.5", "p_ah_away", away_team),
    ]:
        best = get_best_odd(fixture_id, "spreads", bm_out)
        if best:
            bm_prob    = 1.0 / best["odd_value"]
            model_prob = prediction[prob_key]
            comparisons.append({
                "market":          "Asian Handicap",
                "outcome":         label,
                "model_prob":      round(model_prob, 4),
                "bookmaker_prob":  round(bm_prob,    4),
                "bookmaker_odds":  best["odd_value"],
                "edge":            round(model_prob - bm_prob, 4),
                "bookmaker_name":  best["bookmaker_name"],
                "is_informational": False,
            })

    # ── Double Chance — informativa (sin odds directos en BD) ────────────────
    # Edge calculado contra probs SIN vig de h2h, pero ningún bookmaker
    # paga exactamente esa odd. Se marca informativa, NO entra a value_bets.
    conn = _conn()
    h2h_rows = {}
    for key, out in [("Home", home_team), ("Draw", "Draw"), ("Away", away_team)]:
        row = conn.execute("""
            SELECT bookmaker_name, odd_value FROM odds_data
            WHERE fixture_id = ? AND market_name = 'h2h' AND outcome_name = ?
            ORDER BY CASE bookmaker_name WHEN 'pinnacle' THEN 0 ELSE 1 END
            LIMIT 1
        """, (fixture_id, out)).fetchone()
        if row:
            h2h_rows[key] = row[1]
    conn.close()

    if len(h2h_rows) == 3:
        ph, pd, pa = remove_vig(
            h2h_rows.get("Home"), h2h_rows.get("Draw"), h2h_rows.get("Away"))
        if ph and pd and pa:
            for label, model_key, bm_p in [
                ("1X", "p_dc_1x", ph + pd),
                ("X2", "p_dc_x2", pd + pa),
                ("12", "p_dc_12", ph + pa),
            ]:
                model_prob  = prediction[model_key]
                implied_odd = round(1.0 / bm_p, 3) if bm_p > 0 else None
                comparisons.append({
                    "market":          "Double Chance",
                    "outcome":         label,
                    "model_prob":      round(model_prob, 4),
                    "bookmaker_prob":  round(bm_p,       4),
                    "bookmaker_odds":  implied_odd,
                    "edge":            round(model_prob - bm_p, 4),
                    "bookmaker_name":  "derived_h2h",
                    "is_informational": True,
                })

    return comparisons
