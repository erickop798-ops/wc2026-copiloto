"""
Kelly Criterion fraccionado para sizing de apuestas.
Solo aplica a value bets reales (is_informational = False).
"""

KELLY_FRACTION = 0.25   # 25% del Kelly completo (conservador)
MAX_BET_PCT    = 0.05   # Máximo 5% del bankroll por apuesta
MIN_EDGE       = 0.05   # Edge mínimo requerido

def kelly_stake(model_prob: float, decimal_odds: float,
                fraction: float = KELLY_FRACTION) -> float:
    """
    f* = (b·p − q) / b    donde b = decimal_odds − 1
    Retorna porcentaje del bankroll [0, MAX_BET_PCT].
    Retorna 0.0 si no hay value.
    """
    if not decimal_odds or decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    p = model_prob
    q = 1.0 - p
    raw = (b * p - q) / b
    if raw <= 0:
        return 0.0
    return round(min(raw * fraction, MAX_BET_PCT), 4)

def recommend_bets(value_bets: list, bankroll: float = 1000.0) -> list:
    """
    Añade stake_pct y stake_mxn a cada value bet.
    Filtra apuestas sin odds válidas o bajo el umbral.
    Ordena por edge descendente.
    """
    result = []
    for vb in value_bets:
        if vb.get("is_informational", False):
            continue
        if vb.get("edge", 0) < MIN_EDGE:
            continue
        odds = vb.get("bookmaker_odds")
        if not odds or odds <= 1.0:
            continue
        pct      = kelly_stake(vb["model_prob"], odds)
        enriched = dict(vb)
        enriched["stake_pct"] = pct
        enriched["stake_mxn"] = round(pct * bankroll, 2)
        result.append(enriched)

    result.sort(key=lambda x: x["edge"], reverse=True)
    return result
