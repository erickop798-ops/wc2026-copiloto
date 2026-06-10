"""
Utilidades de formato para el dashboard wc2026-copiloto.
- Conversion momios decimales a americanos
- Explicaciones de mercados en espanol
- Formato de probabilidades y edges
"""


def decimal_to_american(decimal: float) -> str:
    """
    Convierte momio decimal a formato americano.

    Ejemplos:
      1.95 -> "-105"    (favorito ligero)
      3.70 -> "+270"    (underdog)
      2.00 -> "+100"    (parejo)
      1.50 -> "-200"    (favorito fuerte)
      4.00 -> "+300"
    """
    if decimal is None or decimal <= 1.0:
        return "N/A"
    if decimal >= 2.0:
        return f"+{int(round((decimal - 1) * 100))}"
    else:
        return str(int(round(-100 / (decimal - 1))))


def format_pct(prob: float) -> str:
    """0.714 -> '71.4%'"""
    if prob is None:
        return "N/A"
    return f"{prob * 100:.1f}%"


def format_edge(edge: float) -> str:
    """0.201 -> '+20.1%'  -0.05 -> '-5.0%'"""
    if edge is None:
        return "N/A"
    sign = "+" if edge >= 0 else ""
    return f"{sign}{edge * 100:.1f}%"


# Explicaciones en espanol por mercado y outcome
# {home} y {away} se reemplazan con los nombres reales en el endpoint
MARKET_EXPLANATIONS = {
    "1X2": {
        "Home": "{home} gana el partido",
        "Draw": "El partido termina en empate",
        "Away": "{away} gana el partido",
    },
    "BTTS": {
        "Yes": "Ambos equipos anotan al menos 1 gol cada uno",
        "No":  "Al menos un equipo termina el partido sin anotar",
    },
    "O/U 2.5": {
        "Over 2.5":  "El partido termina con 3 o mas goles en total",
        "Under 2.5": "El partido termina con 0, 1 o 2 goles en total",
    },
    "Asian Handicap": {
        # Ambas variantes: unicode minus (U+2212) y ASCII hyphen
        "Home −0.5": "{home} debe ganar (el empate no sirve)",
        "Home -0.5":      "{home} debe ganar (el empate no sirve)",
        "Away +0.5":      "{away} gana o empata (con empate ganas igual)",
    },
    # markets.py agrupa Over/Under 1.5 y 3.5 bajo un solo market name
    "O/U 1.5/3.5": {
        "Over 1.5":  "2 o mas goles en total",
        "Under 1.5": "0 o 1 gol en total",
        "Over 3.5":  "4 o mas goles en total",
        "Under 3.5": "3 o menos goles en total",
    },
    "Double Chance": {
        "1X": "{home} gana o empata, no puede perder",
        "X2": "{away} gana o empata, no puede perder",
        "12": "Alguien gana — el empate hace perder la apuesta",
    },
}


def get_explanation(market: str, outcome: str,
                    home: str = "", away: str = "") -> str:
    """
    Retorna explicacion en espanol para un mercado/outcome.
    Reemplaza {home} y {away} con los nombres de los equipos.
    """
    market_map = MARKET_EXPLANATIONS.get(market, {})
    text = market_map.get(outcome, outcome)
    return text.replace("{home}", home).replace("{away}", away)


def edge_color_class(edge: float) -> str:
    """
    Retorna clase CSS de color segun el edge:
    >= 10%  -> verde fuerte (excelente)
    5-10%   -> verde normal (bueno)
    0-5%    -> gris (sin value)
    < 0%    -> rojo (bookmaker tiene ventaja)
    """
    if edge is None:
        return "neutral"
    if edge >= 0.10:
        return "strong-value"
    if edge >= 0.05:
        return "value"
    if edge >= 0:
        return "neutral"
    return "no-value"
