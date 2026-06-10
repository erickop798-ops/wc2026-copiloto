"""Maps openfootball placeholder team names to real qualified team names."""

TEAM_ALIASES: dict[str, str] = {
    "UEFA Path A winner": "Bosnia & Herzegovina",
    "UEFA Path B winner": "Sweden",
    "UEFA Path C winner": "Turkey",
    "UEFA Path D winner": "Czech Republic",
    "CONCACAF Path winner": "Trinidad & Tobago",
    "AFC/OFC playoff winner": "Solomon Islands",
    "CONMEBOL/OFC playoff winner": "Venezuela",
    "CONCACAF playoff winner": "Costa Rica",
}


def resolve_team_name(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def is_placeholder(name: str) -> bool:
    low = (name or "").lower()
    return "winner" in low or "path" in low
