"""One phone format everywhere in the processor: '2547XXXXXXXX' / '2541XXXXXXXX' (12 digits, no plus).

Inputs we see in the wild:
  users.phoneNumber        '+254713138481'
  statement filename       '254110026199'
  statement page 1         '0110026199'
  agents typing            '0713 138 481', '713138481', '+254 713-138-481'
"""
import re

_VALID = re.compile(r"^254[17]\d{8}$")


def normalize_msisdn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("254"):
        n = digits
    elif digits.startswith("0") and len(digits) == 10:
        n = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        n = "254" + digits
    else:
        return None
    return n if _VALID.match(n) else None


def to_users_format(msisdn: str) -> str:
    """How users.phoneNumber stores it: with the plus."""
    return "+" + msisdn


def masked_matches(msisdn: str, masked: str) -> bool:
    """'254110***199' style mask from the email subject vs a full number."""
    m = re.fullmatch(r"(\d+)\*+(\d+)", masked.strip())
    if not m:
        return False
    head, tail = m.groups()
    return (len(head) + len(tail) < len(msisdn)
            and msisdn.startswith(head) and msisdn.endswith(tail))


def msisdn_from_statement_filename(filename: str) -> str | None:
    """MPESA_Statement_2026-09-21_to_2026-08-21_254110026199.pdf -> 254110026199"""
    m = re.search(r"_(254[17]\d{8})(?:_[^_]*)?\.pdf$", filename, re.IGNORECASE)
    return m.group(1) if m else None
