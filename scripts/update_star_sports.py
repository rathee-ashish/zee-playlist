#!/usr/bin/env python3
"""Filter an authorized upstream playlist down to Star Sports channels.

Channel names, logos, URLs, and license fields come from Star.json.
Playback cookies are copied from the same published cookie feed used by
https://github.com/sportlive18/jio-tv-auto-update-playlist (script/jtv.py).
This script does not generate Jio cookies, HMAC signatures, or DRM keys.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from update_zee import (
    NON_ALNUM,
    PlaylistEntry,
    PlaylistError,
    fetch_playlist,
    looks_like_m3u,
    normalize_name,
    parse_playlist,
    run_update,
)

OUTPUT_FILENAME = "star_sports.m3u"
COMBINED_FILENAME = "all-channels.m3u"
SOURCE_ID = "star-sports"
STAR_SPORTS_GROUP = "Star Sports"
# Channel list (names, logos, keys). Replace if the JSON URL moves.
DEFAULT_SOURCE_STAR_M3U_URL = "https://sportlink-jtv.pages.dev/Star.json"
# Published cookie feed used by sportlive18/jio-tv-auto-update-playlist.
SPORTS_COOKIE_URL = (
    "https://allinonereborn2.online/jtv-fetch/jstarcookie/cookie.json"
)
STAR_M3U_COOKIE_SOURCE = (
    "https://raw.githubusercontent.com/sportlive18/jio-tv-auto-update-playlist/main/Star.m3u"
)
PLAYBACK_USER_AGENT = "Virat Paglu"


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


def _extract_cookie_from_url(url: str) -> tuple[str, str]:
    """Move an embedded __hdnea__ query param onto a cookie string."""
    if not url:
        return url, ""
    parsed = urlparse(url)
    if not parsed.query:
        return url, ""
    params = parse_qs(parsed.query, keep_blank_values=True)
    cookie_val = ""
    for key in ("__hdnea__", "__cookie__", "cookie", "cookies"):
        if key in params:
            raw = params.pop(key)[0]
            cookie_val = f"{key}={raw}"
            break
    if not cookie_val:
        return url, ""
    clean_url = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(params, doseq=True),
            parsed.fragment,
        )
    )
    return clean_url, cookie_val


def _clean_base_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", "")
    )


def fetch_sports_cookies() -> dict[str, dict[str, str]]:
    """Copy opaque playback URLs/cookies from the published sports cookie feed."""
    cookies: dict[str, dict[str, str]] = {}
    stamp = int(time.time() * 1000)
    url = f"{SPORTS_COOKIE_URL}{'&' if '?' in SPORTS_COOKIE_URL else '?'}t={stamp}"
    try:
        _status, body = fetch_playlist(url)
        data = json.loads(body)
    except (PlaylistError, json.JSONDecodeError) as extra:
        print(f"WARN: sports cookie feed unavailable: {extra}", file=sys.stderr)
        return cookies
    if not isinstance(data, dict):
        return cookies

    results = list(data.get("successful_results") or [])
    results.extend(data.get("failed_results") or [])
    for item in results:
        if not isinstance(item, dict):
            continue
        channel_id = item.get("channel_id")
        if not channel_id:
            continue
        details = item.get("error_details") or {}
        final_url = item.get("final_url") or details.get("final_url") or ""
        if not final_url:
            continue
        final_url = re.sub(r"/output/", "/WDVLive/", final_url, count=1, flags=re.I)
        clean_url, embedded_cookie = _extract_cookie_from_url(final_url)
        cookies[str(channel_id)] = {
            "url": clean_url,
            "cookie": embedded_cookie,
        }
    print(f"Sports cookies loaded for {len(cookies)} channel ids")
    return cookies


def fetch_cookies_from_star_m3u() -> dict[str, dict[str, str]]:
    """Copy EXTHTTP cookies from the published Star.m3u playlist."""
    cookies: dict[str, dict[str, str]] = {}
    try:
        _status, body = fetch_playlist(STAR_M3U_COOKIE_SOURCE)
        looks_like_m3u(body, _status)
        _, entries = parse_playlist(body)
    except PlaylistError as extra:
        print(f"WARN: Star.m3u cookie source unavailable: {extra}", file=sys.stderr)
        return cookies
    for entry in entries:
        cookie_line = next(
            (line for line in entry.body_lines if line.startswith("#EXTHTTP:")),
            "",
        )
        if not cookie_line:
            continue
        try:
            payload = json.loads(cookie_line[len("#EXTHTTP:") :])
        except json.JSONDecodeError:
            continue
        cookie = str(payload.get("cookie") or "").strip()
        if not cookie or not entry.tvg_id.strip():
            continue
        cookies[entry.tvg_id.strip()] = {
            "url": _clean_base_url(entry.url),
            "cookie": cookie,
        }
    print(f"Star.m3u cookies loaded for {len(cookies)} channel ids")
    return cookies


def load_all_sports_cookies() -> dict[str, dict[str, str]]:
    cookies = fetch_cookies_from_star_m3u()
    cookies.update(fetch_sports_cookies())
    return cookies


def _star_json_item_to_entry(
    item: dict[str, Any], sports_cookies: dict[str, dict[str, str]]
) -> PlaylistEntry | None:
    name = str(item.get("name") or "").strip()
    raw_url = str(item.get("url") or "").strip()
    if not name or not raw_url or raw_url.startswith("#"):
        return None

    tvg_id = _attr(str(item.get("id") or ""))
    logo = _attr(str(item.get("logo") or ""))
    language = _attr(str(item.get("category") or ""))
    cookie_entry = sports_cookies.get(tvg_id, {})
    final_url = cookie_entry.get("url") or raw_url
    cookie = cookie_entry.get("cookie") or ""
    if not cookie:
        final_url, cookie = _extract_cookie_from_url(final_url)
    if not cookie:
        final_url = _clean_base_url(final_url)

    extinf = (
        f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{_attr(name)}" '
        f'tvg-logo="{logo}" tvg-language="{language}" '
        f'group-title="Sports",{_attr(name)}'
    )
    body: list[str] = []
    if ".mpd" in (final_url + raw_url).lower():
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
    if cookie:
        body.append("#EXTHTTP:" + json.dumps({"cookie": cookie}))
    body.append(f"#EXTVLCOPT:http-user-agent={PLAYBACK_USER_AGENT}")
    body.append(final_url)
    return PlaylistEntry(
        extinf=extinf,
        body_lines=tuple(body),
        url=final_url,
        tvg_id=tvg_id,
        tvg_name=_attr(name),
        tvg_logo=logo,
        group_title="Sports",
        display_name=_attr(name),
    )


def parse_star_json(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as extra:
        raise PlaylistError("Star.json response is not valid JSON") from extra
    if not isinstance(payload, list):
        raise PlaylistError("Star.json must be a list of channels")
    return [item for item in payload if isinstance(item, dict)]


def parse_star_source(body: str, status: int) -> list[PlaylistEntry]:
    stripped = body.lstrip()
    if not (stripped.startswith("[") or stripped.startswith("{")):
        looks_like_m3u(body, status)
        _, entries = parse_playlist(body)
        return entries

    items = parse_star_json(body)
    sports_cookies = load_all_sports_cookies()
    entries: list[PlaylistEntry] = []
    skipped_without_cookie = 0
    for item in items:
        entry = _star_json_item_to_entry(item, sports_cookies)
        if entry is None:
            continue
        if not any(line.startswith("#EXTHTTP:") for line in entry.body_lines):
            skipped_without_cookie += 1
            continue
        entries.append(entry)
    if skipped_without_cookie:
        print(
            f"Skipped {skipped_without_cookie} Star.json channels with no playback cookie"
        )
    if not entries:
        raise PlaylistError("Star.json contained no usable channels")
    print(f"Star.json entries with playback cookies: {len(entries)}")
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
