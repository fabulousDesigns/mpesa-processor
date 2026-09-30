"""Pull a place out of a merchant name ("PUREVISTA WATERS LIMITED KINOO" -> KINOO).

Only confident places go here. County is filled only where the place sits clearly in
one county; border areas get location_text but no county. Extend as the registry grows;
anything not caught here can be fixed by hand (location_source = MANUAL) later.
"""
import re

# place -> county (None = we know the place, not confident about the county)
PLACES: dict[str, str | None] = {
    # Kiambu
    "KINOO": "Kiambu", "KIKUYU": "Kiambu", "REGEN": "Kiambu", "MUGUGA": "Kiambu",
    "RUAKA": "Kiambu", "RUIRU": "Kiambu", "JUJA": "Kiambu", "THIKA": "Kiambu",
    "KIAMBU": "Kiambu", "LIMURU": "Kiambu", "KABETE": "Kiambu", "WANGIGE": "Kiambu",
    "GITHUNGURI": "Kiambu", "KIHUNGURU": "Kiambu", "SPUR MALL": "Kiambu",
    "UTHIRU": None,
    # Nairobi
    "KANGEMI": "Nairobi", "WESTLANDS": "Nairobi", "KASARANI": "Nairobi", "ROYSAMBU": "Nairobi",
    "EMBAKASI": "Nairobi", "UTAWALA": "Nairobi", "KAREN": "Nairobi", "LANGATA": "Nairobi",
    "KILIMANI": "Nairobi", "KILELESHWA": "Nairobi", "LAVINGTON": "Nairobi", "DAGORETTI": "Nairobi",
    "KAWANGWARE": "Nairobi", "RIVER ROAD": "Nairobi", "TOM MBOYA": "Nairobi", "MOI AVENUE": "Nairobi",
    "CBD": "Nairobi", "TRM": "Nairobi", "GARDEN CITY": "Nairobi", "SARIT": "Nairobi",
    "YAYA": "Nairobi", "JUNCTION": None, "TWO RIVERS": None, "KAHAWA": None,
    "SOUTH B": "Nairobi", "SOUTH C": "Nairobi", "BURUBURU": "Nairobi", "DONHOLM": "Nairobi",
    "KAYOLE": "Nairobi", "UMOJA": "Nairobi", "PIPELINE": "Nairobi", "GITHURAI": None, "ZIMMERMAN": "Nairobi",
    # Kajiado / Machakos
    "RONGAI": "Kajiado", "KITENGELA": "Kajiado", "NGONG": "Kajiado",
    "SYOKIMAU": "Machakos", "MLOLONGO": "Machakos", "ATHI RIVER": "Machakos",
}

# Longest first so "SPUR MALL" wins over shorter names inside it.
_RX = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(PLACES, key=len, reverse=True)) + r")\b")


def location_from_name(name: str | None) -> tuple[str | None, str | None]:
    """-> (location_text, county). Returns the LAST place mentioned, since names end
    with the branch: 'TOTALENERGIES UTHIRU 87 SHOP' -> 'UTHIRU 87'."""
    if not name:
        return None, None
    hits = _RX.findall(name.upper())
    if not hits:
        return None, None
    # Keep adjacent hits together ("UTHIRU 87"), otherwise the last one.
    text = " ".join(dict.fromkeys(hits)) if len(hits) <= 2 else hits[-1]
    county = next((PLACES[h] for h in reversed(hits) if PLACES[h]), None)
    return text, county