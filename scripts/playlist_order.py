"""Category order and watch-rank for the combined playlist."""

from __future__ import annotations

import re

CATEGORY_ORDER = [
    "Entertainment",
    "Movies",
    "Infotainment",
    "Star Sports",
    "Sports",
    "Kids",
    "Music",
    "News",
    "Lifestyle",
    "Business",
    "Devotional",
]

GROUP_ALIASES = {
    "business news": "Business",
    "business": "Business",
    "entertainment": "Entertainment",
    "movies": "Movies",
    "infotainment": "Infotainment",
    "sports": "Sports",
    "star sports": "Star Sports",
    "kids": "Kids",
    "music": "Music",
    "news": "News",
    "lifestyle": "Lifestyle",
    "devotional": "Devotional",
}

SKIP_GROUPS = {"shopping", "educational"}

SKIP_LANGUAGES = {
    "tamil",
    "telugu",
    "kannada",
    "bengali",
    "bangla",
    "malayalam",
    "gujarati",
    "odia",
    "oriya",
    "assamese",
    "french",
    "nepali",
}

# Regional Zee brands whose names do not always include a language token.
SKIP_NAME_MARKERS = (
    "cinemalu",
    "keralam",
    "sarthak",
    "24 kalak",
    "24 ghanta",
    "thirai",
    "zee tamil",
    "zee telugu",
    "zee kannada",
    "zee bangla",
    "cartoon network hd marathi",
    "cartoon network marathi",
    "pogo marathi",
)

# Lower index = more watched. HD of a brand still ranks above its SD.
POPULARITY = {
    "Entertainment": [
        "star plus",
        "zee tv",
        "sony sab",
        "colors",
        "and tv",
        "zee marathi",
        "zee yuva",
        "zee punjabi",
        "star utsav",
        "set",
        "sony pal",
        "star bharat",
        "colors rishtey",
        "colors marathi",
        "star pravah",
    ],
    "Movies": [
        "sony max",
        "zee cinema",
        "star gold",
        "and pictures",
        "zee talkies",
        "colors cineplex",
        "star movies",
        "movies now",
        "zee action",
        "zee bollywood",
        "zee classic",
        "zee power",
        "zee biskope",
        "and flix",
        "mn+",
        "mnx",
        "sony pix",
        "b4u movies",
    ],
    "Infotainment": [
        "national geographic",
        "nat geo wild",
        "discovery",
        "history tv18",
        "sony bbc earth",
        "animal planet",
    ],
    "Sports": [
        "sony ten 1",
        "sony ten 2",
        "sony ten 3",
        "sony ten 5",
        "eurosport",
        "dd sports",
    ],
    "Star Sports": [
        "star sports 1 hindi",
        "star sports 1",
        "star sports 2 hindi",
        "star sports 2",
        "star sports 3",
        "star sports khel",
        "star sports select 1",
        "star sports select 2",
    ],
    "Kids": [
        "cartoon network",
        "nick",
        "pogo",
        "hungama",
        "disney",
        "sony yay",
        "sonic",
    ],
    "Music": [
        "mtv",
        "9xm",
        "zing",
        "b4u music",
        "zoom",
        "yrf music",
    ],
    "News": [
        "aaj tak",
        "news 18 india",
        "india tv",
        "zee news",
        "zee bharat",
        "republic bharat",
        "tv9 bharatvarsh",
        "times now navbharat",
        "abp news",
        "news 24",
        "ndtv india",
        "wion",
        "dd news",
        "zee 24 taas",
        "zee delhi ncr haryana",
        "zee uttar pradesh uttarakhand",
        "zee up uk",
        "zee punjab haryana himachal pradesh",
        "zee punjab haryana hp",
        "zee rajasthan news",
        "zee rajasthan",
        "zee bihar jharkhand",
        "zee madhya pradesh chattisgarh",
        "zee mp chattisgarh",
    ],
    "Lifestyle": ["tlc", "travelxp", "zee zest", "foodxp", "ftv"],
    "Business": [
        "cnbc tv18",
        "cnbc awaaz",
        "et now",
        "ndtv profit",
        "zee business",
    ],
    "Devotional": ["aastha", "sanskar", "sadhna", "disha tv"],
}

QUALITY_PREFIX = re.compile(
    r"^(?:hd|sd|fhd|uhd|4k)(?:\s+|\s*[-_]\s*)",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")
LANG_ATTR_RE = re.compile(r'tvg-language="([^"]*)"', re.IGNORECASE)
GROUP_ATTR_RE = re.compile(r'group-title="([^"]*)"', re.IGNORECASE)
NAME_ATTR_RE = re.compile(r'tvg-name="([^"]*)"', re.IGNORECASE)


def normalize_text(value: str) -> str:
    lowered = value.strip().lower().replace("&amp;", "&")
    lowered = QUALITY_PREFIX.sub("", lowered)
    if lowered.startswith("&"):
        lowered = "and " + lowered[1:].lstrip()
    return NON_ALNUM.sub(" ", lowered).strip()


def canonical_group(group_title: str) -> str:
    key = normalize_text(group_title)
    if not key:
        return "Other"
    return GROUP_ALIASES.get(key, group_title.strip() or "Other")


def category_rank(group_title: str) -> int:
    canonical = canonical_group(group_title)
    if canonical in CATEGORY_ORDER:
        return CATEGORY_ORDER.index(canonical)
    return len(CATEGORY_ORDER)


def _tokens(value: str) -> list[str]:
    return [token for token in normalize_text(value).split(" ") if token]


def _brand_matches(name: str, brand: str) -> bool:
    name_tokens = _tokens(name)
    brand_tokens = _tokens(brand)
    if not name_tokens or not brand_tokens:
        return False
    for index in range(len(name_tokens) - len(brand_tokens) + 1):
        if name_tokens[index : index + len(brand_tokens)] == brand_tokens:
            return True
    return False


def popularity_rank(group_title: str, name: str) -> int:
    canonical = canonical_group(group_title)
    brands = POPULARITY.get(canonical, [])
    best = None
    best_len = -1
    for index, brand in enumerate(brands):
        if _brand_matches(name, brand):
            length = len(_tokens(brand))
            if length > best_len:
                best = index
                best_len = length
    return 800 if best is None else best


def quality_rank(name: str) -> int:
    lowered = name.lower()
    if re.search(r"\bsd\b", lowered):
        return 2
    if "hd+" in lowered or re.search(r"\bhd\b", lowered):
        return 0
    return 1


def is_test_channel(name: str) -> bool:
    return bool(re.match(r"^test(\d+|a)?\b", name.lower().strip()))


def language_from_extinf(extinf: str) -> str:
    match = LANG_ATTR_RE.search(extinf)
    if match:
        return match.group(1).strip()
    return ""


def kids_language_rank(group_title: str, name: str, language: str = "") -> int:
    """Kids: Hindi first, then English, then remaining languages."""
    if canonical_group(group_title) != "Kids":
        return 0
    lang = normalize_text(language)
    if not lang:
        tokens = set(_tokens(name))
        if "hindi" in tokens:
            lang = "hindi"
        elif "english" in tokens:
            lang = "english"
    if lang == "hindi":
        return 0
    if lang == "english":
        return 1
    return 2


def skip_channel(group_title: str, name: str, extinf: str = "") -> bool:
    group_key = normalize_text(group_title)
    if group_key in SKIP_GROUPS:
        return True
    language_match = LANG_ATTR_RE.search(extinf)
    if language_match and language_match.group(1).strip().lower() in SKIP_LANGUAGES:
        return True
    name_tokens = set(_tokens(name))
    if name_tokens & SKIP_LANGUAGES:
        return True
    normalized = normalize_text(name)
    return any(marker in normalized for marker in SKIP_NAME_MARKERS)


def sort_key(
    group_title: str,
    name: str,
    original_index: int,
    language: str = "",
    extinf: str = "",
) -> tuple:
    lang = language or language_from_extinf(extinf)
    return (
        is_test_channel(name),
        category_rank(group_title),
        kids_language_rank(group_title, name, lang),
        popularity_rank(group_title, name) + quality_rank(name) * 2,
        quality_rank(name),
        original_index,
    )


def display_name_from_extinf(extinf: str) -> str:
    name_match = NAME_ATTR_RE.search(extinf)
    if name_match and name_match.group(1).strip():
        return name_match.group(1).strip()
    _, _, display = extinf.rpartition(",")
    return display.strip()


def group_from_extinf(extinf: str, fallback: str = "") -> str:
    match = GROUP_ATTR_RE.search(extinf)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return fallback
