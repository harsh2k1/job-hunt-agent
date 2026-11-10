import re

# Legal-form words that say nothing about which company it is. "Group",
# "Holdings" and the like stay: dropping them would merge different firms.
LEGAL_SUFFIXES = frozenset(
    {
        "ltd",
        "limited",
        "plc",
        "llp",
        "lp",
        "llc",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "uk",
    }
)
NON_WORD = re.compile(r"[^a-z0-9]+")
# "ROOFOODS LTD T/A Deliveroo": the legal name, then the name it trades as
TRADING_AS = re.compile(r"\s+t/a\s+", re.IGNORECASE)


# One spelling per company, for matching the same employer across sources
# and against the sponsor register: "The Monzo Bank Ltd." -> "monzo bank".
def normalise_company_name(name: str) -> str:
    words = NON_WORD.sub(" ", name.lower().replace("&", " and ")).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    if len(words) > 1 and words[0] == "the":
        words.pop(0)
    return " ".join(words)


# "ROOFOODS LTD T/A Deliveroo" -> ("ROOFOODS LTD", "Deliveroo")
def split_trading_name(name: str) -> tuple[str, str | None]:
    legal_name, *trading_name = TRADING_AS.split(name.strip(), maxsplit=1)
    return legal_name.strip(), (trading_name[0].strip() or None) if trading_name else None
