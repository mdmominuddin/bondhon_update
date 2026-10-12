import re

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def normalize_bd_phone(raw):
    """
    Return a Bangladesh mobile number as 8801XXXXXXXXX, or None if invalid.
    Accepts 01XXXXXXXXX, 1XXXXXXXXX, +8801XXXXXXXXX, 8801XXXXXXXXX, with spaces/dashes
    and Bangla digits.  Operator prefixes 013-019.
    """
    if not raw:
        return None
    s = str(raw).translate(_BN_DIGITS)
    s = re.sub(r"[\s\-().]", "", s)
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("00"):
        s = s[2:]
    if s.startswith("880"):
        s = s[3:]
    if len(s) == 10 and s.startswith("1"):
        s = "0" + s
    if re.fullmatch(r"01[3-9]\d{8}", s):
        return "88" + s
    return None


def display_bd_phone(raw):
    n = normalize_bd_phone(raw)
    return f"0{n[3:]}" if n else (raw or "")
