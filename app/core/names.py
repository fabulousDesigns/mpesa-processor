"""Does the name on page 1 belong to the customer we registered?

Statement: 'BERNARD MBURU MAINA'. KYC: firstName 'Bernard', lastName 'Maina'.
Rule: the KYC first name AND last name must both appear in the statement name, in any
order (people register names in different orders). Middle names are ignored.
"""
import re


def _tokens(s: str | None) -> list[str]:
    return [t for t in re.split(r"[^A-Z']+", (s or "").upper()) if t]


def name_matches(statement_name: str, first_name: str | None, last_name: str | None) -> bool:
    stmt = set(_tokens(statement_name))
    first, last = _tokens(first_name), _tokens(last_name)
    if not first or not last:
        return False
    return all(t in stmt for t in first) and all(t in stmt for t in last)