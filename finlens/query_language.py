"""Normalize financial query vocabulary without rewriting source documents."""

import re

from finlens.text import normalize

# Whole phrases only. Preserve years, amounts, negation and comparison words.
# Short ambiguous abbreviations such as DT, LN and TS are intentionally absent.
ALIASES = {
    "lctt thuan": "luu chuyen tien thuan",
    "lctt": "luu chuyen tien te",
    "hdkd": "hoat dong kinh doanh",
    "hddt": "hoat dong dau tu",
    "hdtc": "hoat dong tai chinh",
    "bctc": "bao cao tai chinh",
    "bcdkt": "bang can doi ke toan",
    "kqkd": "ket qua kinh doanh",
    "tsnh": "tai san ngan han",
    "tsdh": "tai san dai han",
    "vcsh": "von chu so huu",
    "dtt": "doanh thu thuan",
    "lnst": "loi nhuan sau thue",
    "lntt": "loi nhuan truoc thue",
    "gvhb": "gia von hang ban",
    "tscd": "tai san co dinh",
    "cpbh": "chi phi ban hang",
    "cpqldn": "chi phi quan ly doanh nghiep",
    "ton kho thuan": "hang ton kho thuan",
}
_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(key) for key in sorted(ALIASES, key=len, reverse=True)
) + r")(?!\w)")


def canonical_query(query: str) -> str:
    text = normalize(query)
    def replace(match):
        if match.group() == "ton kho thuan" and re.search(r"\bhang\s+$", text[:match.start()]):
            return match.group()
        return ALIASES[match.group()]
    return _PATTERN.sub(replace, text)
