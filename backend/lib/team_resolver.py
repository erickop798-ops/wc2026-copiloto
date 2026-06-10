"""Maps openfootball placeholder team names to real qualified team names."""

TEAM_ALIASES: dict[str, str] = {
    # UEFA Playoff winners -- verificados por ESPN/UEFA/Wikipedia
    # Path A: Bosnia beat Italy on penalties (31 Mar 2026)
    "UEFA Path A winner": "Bosnia & Herzegovina",
    # Path B: Sweden beat Poland 3-2 (31 Mar 2026)
    "UEFA Path B winner": "Sweden",
    # Path C: Turkey beat Kosovo 1-0 (31 Mar 2026)
    "UEFA Path C winner": "Turkey",
    # Path D: Czech Republic beat Denmark on penalties (31 Mar 2026)
    "UEFA Path D winner": "Czech Republic",
}


def resolve_team_name(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def is_placeholder(name: str) -> bool:
    low = (name or "").lower()
    return "winner" in low or "path" in low
