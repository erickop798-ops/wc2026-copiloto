import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "wc2026.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id   INTEGER PRIMARY KEY,
    home_team_id INTEGER,
    home_team_name TEXT,
    away_team_id INTEGER,
    away_team_name TEXT,
    date_utc     TEXT,
    venue        TEXT,
    city         TEXT,
    round        TEXT,
    group_name   TEXT,
    status       TEXT,
    home_goals   INTEGER,
    away_goals   INTEGER,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
    team_id  INTEGER PRIMARY KEY,
    name     TEXT,
    code     TEXT,
    country  TEXT,
    logo_url TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    fixture_id      INTEGER PRIMARY KEY,
    winner_team     TEXT,
    home_pct        REAL,
    draw_pct        REAL,
    away_pct        REAL,
    home_goals_pred REAL,
    away_goals_pred REAL,
    advice          TEXT,
    raw_json        TEXT,
    fetched_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS odds_data (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id     INTEGER,
    bookmaker_name TEXT,
    market_name    TEXT,
    outcome_name   TEXT,
    odd_value      REAL,
    fetched_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS match_statistics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER,
    team_id    INTEGER,
    stat_name  TEXT,
    stat_value TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint      TEXT,
    params_hash   TEXT UNIQUE,
    response_json TEXT,
    cached_at     TEXT,
    expires_at    TEXT
);

CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT,
    endpoint    TEXT,
    params      TEXT,
    status_code INTEGER,
    cached      INTEGER DEFAULT 0,
    timestamp   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_calibration (
    team_id          INTEGER PRIMARY KEY,
    attack_strength  REAL,
    defense_strength REAL,
    data_source      TEXT,
    matches_count    INTEGER,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS standings (
    team_id      INTEGER PRIMARY KEY,
    team_name    TEXT,
    group_name   TEXT,
    played       INTEGER,
    wins         INTEGER,
    draws        INTEGER,
    losses       INTEGER,
    goals_for    INTEGER,
    goals_against INTEGER,
    goal_diff    INTEGER,
    points       INTEGER
);

CREATE TABLE IF NOT EXISTS injuries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id  INTEGER,
    player_id   INTEGER,
    player_name TEXT,
    team_id     INTEGER,
    type        TEXT,
    reason      TEXT,
    fetched_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db_connection() as conn:
        conn.executescript(_SCHEMA)
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
