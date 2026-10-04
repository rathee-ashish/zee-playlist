#!/usr/bin/env python3
"""Filter an authorized upstream playlist down to Star Sports channels.

Supports an M3U playlist or the Star.json channel list. Stream URLs and
license fields are copied as opaque strings from the source. This script
does not generate Jio cookies, HMAC signatures, or DRM keys.
"""

from __future__ import annotations

import json
import sys

from update_zee import (
    NON_ALNUM,
    PlaylistEntry,
    PlaylistError,
    looks_like_m3u,
    normalize_name,
    parse_playlist,
    run_update,
)

OUTPUT_FILENAME = "star_sports.m3u"
COMBINED_FILENAME = "all-channels.m3u"
SOURCE_ID = "star-sports"
STAR_SPORTS_GROUP = "Star Sports"
# Replace this URL (or set SOURCE_STAR_M3U_URL) when the upstream list moves.
DEFAULT_SOURCE_STAR_M3U_URL = "https://sportlink-jtv.pages.dev/Star.json"


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


def _attr(value: str) -> str:
    return value.replace('"', "").strip()


def _star_json_item_to_entry(item: dict) -> PlaylistEntry | None:
    name = str(item.get("name") or "").strip()
    url = str(item.get("url") or "").strip()
    if not name or not url or url.startswith("#"):
        return None

    tvg_id = _attr(str(item.get("id") or ""))
    logo = _attr(str(item.get("logo") or ""))
    language = _attr(str(item.get("category") or ""))
    extinf = (
        f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{_attr(name)}" '
        f'tvg-logo="{logo}" tvg-language="{language}" '
        f'group-title="Sports",{_attr(name)}'
    )
    body: list[str] = []
    if ".mpd" in url.lower():
        body.extend(
            [
                "#KODIPROP:inputstream=inputstream.adaptive",
                "#KODIPROP:inputstream.adaptive.manifest_type=mpd",
            ]
        )
        key_id = str(item.get("keyId") or "").strip()
        key = str(item.get("key") or "").strip()
        if key_id and key:
            body.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            body.append(
                "#KODIPROP:inputstream.adaptive.license_key="
                f"{key_id}:{key}"
            )
    body.append(url)
    return PlaylistEntry(
        extinf=extinf,
        body_lines=tuple(body),
        url=url,
        tvg_id=tvg_id,
        tvg_name=_attr(name),
        tvg_logo=logo,
        group_title="Sports",
        display_name=_attr(name),
    )


def parse_star_json(text: str) -> list[PlaylistEntry]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as extra:
        raise PlaylistError("Star.json response is not valid JSON") from extra
    if not isinstance(payload, list):
        raise PlaylistError("Star.json must be a list of channels")

    entries: list[PlaylistEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entry = _star_json_item_to_entry(item)
        if entry is not None:
            entries.append(entry)
    if not entries:
        raise PlaylistError("Star.json contained no usable channels")
    return entries


def parse_star_source(body: str, status: int) -> list[PlaylistEntry]:
    stripped = body.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return parse_star_json(body)
    looks_like_m3u(body, status)
    _, entries = parse_playlist(body)
    return entries


def main() -> int:
    return run_update(
        source_env="SOURCE_STAR_M3U_URL",
        output_filename=OUTPUT_FILENAME,
        match_fn=is_star_sports_channel,
        label="Star Sports",
        default_source_url=DEFAULT_SOURCE_STAR_M3U_URL,
        combined_filename=COMBINED_FILENAME,
        source_id=SOURCE_ID,
        force_group_title=STAR_SPORTS_GROUP,
        filter_standalone=False,
        parse_entries_fn=parse_star_source,
    )


if __name__ == "__main__":
    sys.exit(main())
