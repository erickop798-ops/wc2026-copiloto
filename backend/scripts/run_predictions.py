"""
SCRIPT DIARIO — Ejecutar cada mañana antes de los partidos WC2026.

Uso: python backend/scripts/run_predictions.py

Muestra:
  - Probabilidades del modelo (todos los mercados)
  - Comparación vs bookmaker (1X2, BTTS, O/U 2.5, Asian Handicap)
  - Edge detectado y stake Kelly recomendado
  - O/U 1.5 y 3.5 como información del modelo (sin comparación bookmaker)
  - Double Chance como informativa (sin guardar en value_bets)
"""
import sys, os, sqlite3
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from backend.models.poisson       import predict_match
from backend.models.markets       import build_market_comparison
from backend.models.value_finder  import find_upcoming_value_bets
from backend.models.kelly         import recommend_bets

DB_PATH  = "wc2026.db"
BANKROLL = 1000.0  # MXN

def get_upcoming_fixtures(days: int = 3) -> list:
    conn   = sqlite3.connect(DB_PATH)
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    rows   = conn.execute("""
        SELECT fixture_id, home_team_name, away_team_name,
               date_utc, group_name, round
        FROM fixtures
        WHERE tournament_year = 2026
          AND status          IN ('NS', 'scheduled')
          AND date_utc        <= ?
        ORDER BY date_utc
    """, (cutoff,)).fetchall()
    conn.close()
    return rows

def print_match_block(fixture_id, home, away, date_utc, group, round_name,
                      recommended_vbs):
    SEP = "-" * 70
    print(f"\n{SEP}")
    print(f"  {home:24s}  vs  {away}")
    print(f"  {date_utc[:16]} UTC   {group}   {round_name}")

    try:
        pred = predict_match(fixture_id)
        comp = build_market_comparison(fixture_id, pred)
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return

    # Lambdas y 1X2
    print(f"\n  λ {pred['lambda_home']:.2f} vs {pred['lambda_away']:.2f}")
    print(f"  1X2  →  H {pred['p_home']:.1%}  D {pred['p_draw']:.1%}  "
          f"A {pred['p_away']:.1%}")

    # Mercados con comparación (reales)
    reales = [c for c in comp if not c.get("is_informational", False)
              and c.get("bookmaker_prob") is not None]
    if reales:
        print(f"\n  {'MERCADO':<16} {'OUTCOME':<13} "
              f"{'MODELO':>7} {'BM':>7} {'EDGE':>7} "
              f"{'ODDS':>6} {'BOOKMAKER'}")
        print(f"  {'-'*68}")
        for c in reales:
            edge_str = f"+{c['edge']:.1%}" if c['edge'] >= 0.05 else f"{c['edge']:.1%}"
            mark     = " ★" if c['edge'] >= 0.05 else ""
            print(f"  {c['market']:<16} {c['outcome']:<13} "
                  f"{c['model_prob']:>6.1%} {c['bookmaker_prob']:>6.1%} "
                  f"{edge_str:>7} {c['bookmaker_odds']:>5.2f}  "
                  f"{c['bookmaker_name']}{mark}")

    # O/U 1.5 y 3.5 (solo modelo, sin comparación)
    print(f"\n  O/U (solo modelo, sin comparación bookmaker):")
    print(f"    Over 1.5  {pred['p_over_15']:.1%}  |  "
          f"Over 2.5  {pred['p_over_25']:.1%}  |  "
          f"Over 3.5  {pred['p_over_35']:.1%}")

    # BTTS
    print(f"    BTTS Yes  {pred['p_btts_yes']:.1%}  |  "
          f"BTTS No   {pred['p_btts_no']:.1%}")

    # Double Chance (informativa)
    print(f"    DC 1X {pred['p_dc_1x']:.1%}  |  "
          f"DC X2 {pred['p_dc_x2']:.1%}  |  "
          f"DC 12 {pred['p_dc_12']:.1%}  [informativa]")

    # Value bets de este partido
    fid_vbs = [v for v in recommended_vbs if v.get("fixture_id") == fixture_id]
    if fid_vbs:
        print(f"\n  ★ VALUE BETS ({len(fid_vbs)}):")
        for v in fid_vbs:
            print(f"    [{v['market']:<15}] {v['outcome']:<12} "
                  f"edge +{v['edge']:.1%}  "
                  f"odds {v['bookmaker_odds']:.2f} ({v['bookmaker_name']})  "
                  f"stake {v['stake_mxn']:.0f} MXN ({v['stake_pct']:.1%})")
    else:
        print(f"\n  → Sin value bets (edge < 5% en mercados con comparación)")

def main():
    print("=" * 70)
    print(f"  wc2026-copiloto — {datetime.now().strftime('%d %b %Y %H:%M')} UTC")
    print("=" * 70)

    fixtures = get_upcoming_fixtures(days=3)
    if not fixtures:
        print("\n  Sin partidos en los próximos 3 días.")
        return

    all_vbs      = find_upcoming_value_bets(days_ahead=3)
    recommended  = recommend_bets(all_vbs, bankroll=BANKROLL)

    for fixture_id, home, away, date_utc, group, round_name in fixtures:
        print_match_block(fixture_id, home, away, date_utc,
                          group, round_name, recommended)

    # Resumen
    if recommended:
        total_stake = sum(v["stake_mxn"] for v in recommended)
        print(f"\n{'='*70}")
        print(f"  RESUMEN: {len(recommended)} value bet(s) recomendado(s)")
        print(f"  Stake total sugerido: {total_stake:.0f} MXN "
              f"({total_stake/BANKROLL:.1%} de {BANKROLL:.0f} MXN)")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
