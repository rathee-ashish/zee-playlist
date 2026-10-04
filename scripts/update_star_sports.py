#!/usr/bin/env python3
"""Filter an authorized upstream M3U playlist down to Star Sports channels.

This script only fetches and filters playlist text. It does not generate
Jio cookies, HMAC signatures, or DRM keys. Stream URLs are copied as opaque
strings from the source playlist.
"""

from __future__ import annotations

import sys

from update_zee import PlaylistEntry, NON_ALNUM, normalize_name, run_update

OUTPUT_FILENAME = "star_sports.m3u"


def is_star_sports_channel(entry: PlaylistEntry) -> bool:
    """Keep channels whose metadata identifies Star Sports, not Star Plus/Gold."""
    for field_value in (entry.tvg_name, entry.display_name):
        if field_value and _looks_like_star_sports(field_value):
            return True
    if entry.tvg_id and _tvg_id_is_star_sports(entry.tvg_id):
        return True
    return False


def _looks_like_star_sports(value: str) -> bool:
    normalized = normalize_name(value)
    return normalized == "star sports" or normalized.startswith("star sports ")


def _tvg_id_is_star_sports(tvg_id: str) -> bool:
    compact = NON_ALNUM.sub("", tvg_id.strip().lower())
    return compact.startswith("starsports")


def main() -> int:
    return run_update(
        source_env="SOURCE_STAR_M3U_URL",
        output_filename=OUTPUT_FILENAME,
        match_fn=is_star_sports_channel,
        label="Star Sports",
    )


if __name__ == "__main__":
    sys.exit(main())
