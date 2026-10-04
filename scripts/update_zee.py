#!/usr/bin/env python3
"""Filter an authorized upstream M3U playlist down to Zee-family channels.

This script only fetches and filters playlist text. Stream URLs are treated as
opaque strings. It does not generate, forge, modify, decode, or bypass
authorization tokens, cookies, signatures, or DRM.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from playlist_order import canonical_group, skip_channel, sort_key

HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
OUTPUT_FILENAME = "zee.m3u"
COMBINED_FILENAME = "all-channels.m3u"
SOURCE_ID = "zee"
# Replace this URL (or set SOURCE_M3U_URL) when the upstream playlist moves.
DEFAULT_SOURCE_M3U_URL = "https://premiumplugx.top/jiostb/mjelo.php?view=raw"
SOURCE_MARKER_PREFIX = "#PLAYLIST-SOURCE:"
USER_AGENT = "zee-playlist-updater/1.0 (+https://github.com)"

# Query keys that must never appear in logs.
SENSITIVE_QUERY_KEYS = {
    "token",
    "__hdnea__",
    "hdnea",
    "hdntl",
    "auth",
    "authorization",
    "signature",
    "sig",
    "key",
    "cookie",
    "jwt",
    "access_token",
    "expires",
    "hash",
    "hmac",
}

# Names that begin with these tokens after optional quality prefixes are Zee.
ZEE_BRAND_PREFIX = "zee"

# Exact first-token aliases that are Zee family but may not start with "Zee".
ZEE_FAMILY_ALIASES = {
    "zing",
    "zee5",
}

# "&TV" / "And Pictures" and related Zee Entertainment brands.
AND_FAMILY_PREFIXES = (
    "and tv",
    "and pictures",
    "and flix",
    "and prive",
    "and xplor",
)

AND_ID_PREFIXES = (
    "andtv",
    "andpictures",
    "andflix",
    "andprive",
    "andxplor",
)

# Extra substrings that must appear as a standalone brand token, not as part
# of an unrelated sentence such as "Amazing Zee Movie".
FALSE_POSITIVE_PATTERNS = (
    re.compile(r"\bzee[- ]related\b", re.IGNORECASE),
    re.compile(r"\bamazing\s+zee\b", re.IGNORECASE),
)

QUALITY_PREFIX = re.compile(
    r"^(?:hd|sd|fhd|uhd|4k)(?:\s+|\s*[-_]\s*)",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")
ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


class PlaylistError(Exception):
    """Raised when the upstream playlist or generated output is invalid."""


@dataclass(frozen=True)
class PlaylistEntry:
    """One M3U channel: EXTINF metadata, extra tags, and an opaque stream URL."""

    extinf: str
    body_lines: tuple[str, ...] = field(default_factory=tuple)
    url: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    group_title: str = ""
    display_name: str = ""

    def render(self) -> list[str]:
        # Rewrite leading "&" in the title only. Many players drop "&Pictures HD".
        return [player_safe_extinf(self.extinf), *self.body_lines]


def redact_url(url: str) -> str:
    """Return a log-safe URL with sensitive query values replaced."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[unparseable-url]"
    if not parts.query:
        return url
    redacted = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            redacted.append((key, "REDACTED"))
        else:
            redacted.append((key, value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment)
    )


def parse_extinf(extinf: str) -> dict[str, str]:
    """Extract common EXTINF attributes and the comma-separated display name."""
    prefix, sep, display_name = extinf.rpartition(",")
    attrs = {key.lower(): value for key, value in ATTR_RE.findall(prefix)}
    return {
        "tvg-id": attrs.get("tvg-id", "").strip(),
        "tvg-name": attrs.get("tvg-name", "").strip(),
        "tvg-logo": attrs.get("tvg-logo", "").strip(),
        "group-title": attrs.get("group-title", "").strip(),
        "display-name": display_name.strip() if sep else "",
    }


def parse_playlist(text: str) -> tuple[list[str], list[PlaylistEntry]]:
    """Parse M3U text into header lines and channel entries.

    An entry starts at #EXTINF. Following non-empty lines belong to that
    entry until the next #EXTINF. The first non-comment line is the stream
    URL and is stored unchanged. Other metadata comment lines are preserved
    in original order.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header: list[str] = []
    entries: list[PlaylistEntry] = []
    current: list[str] | None = None

    def flush_current(block: list[str]) -> None:
        if not block:
            return
        extinf = block[0]
        body = tuple(line.rstrip() for line in block[1:] if line.strip())
        url = next((line for line in body if not line.startswith("#")), "")
        meta = parse_extinf(extinf)
        entries.append(
            PlaylistEntry(
                extinf=extinf.rstrip(),
                body_lines=body,
                url=url,
                tvg_id=meta["tvg-id"],
                tvg_name=meta["tvg-name"],
                tvg_logo=meta["tvg-logo"],
                group_title=meta["group-title"],
                display_name=meta["display-name"],
            )
        )

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("#EXTINF"):
            if current is not None:
                flush_current(current)
            current = [line]
            continue
        if current is None:
            if line.strip():
                header.append(line)
            continue
        current.append(line)

    if current is not None:
        flush_current(current)
    return header, entries


def looks_like_m3u(text: str, status_code: int) -> None:
    if status_code < 200 or status_code >= 300:
        raise PlaylistError(f"HTTP status {status_code} is not successful")
    stripped = text.lstrip()
    if not stripped:
        raise PlaylistError("Upstream response is empty")
    first = stripped.splitlines()[0].strip()
    if not first.upper().startswith("#EXTM3U") and "#EXTINF" not in stripped[:4000]:
        raise PlaylistError("Response does not look like an M3U playlist")


def normalize_name(value: str) -> str:
    """Lowercase a channel name and treat '&Brand' as 'and brand'."""
    lowered = value.strip().lower().replace("&amp;", "&")
    lowered = QUALITY_PREFIX.sub("", lowered)
    if lowered.startswith("&"):
        lowered = "and " + lowered[1:].lstrip()
    return NON_ALNUM.sub(" ", lowered).strip()


def identity_tokens(value: str) -> list[str]:
    return [token for token in normalize_name(value).split(" ") if token]


def is_false_positive(value: str) -> bool:
    return any(pattern.search(value) for pattern in FALSE_POSITIVE_PATTERNS)


def player_safe_ampersand_name(value: str) -> str:
    """Turn '&Pictures HD' into 'And Pictures HD' for player-visible titles."""
    stripped = value.strip().replace("&amp;", "&")
    if stripped.startswith("&"):
        rest = stripped[1:].lstrip()
        if rest:
            return "And " + rest
    return value


def player_safe_extinf(extinf: str) -> str:
    """Keep stream URLs unchanged; make '&' brand titles readable in players."""
    prefix, sep, display = extinf.rpartition(",")
    if not sep:
        return extinf

    def replace_attr(match: re.Match[str]) -> str:
        key = match.group(1)
        val = match.group(2)
        if key.lower() == "tvg-name":
            val = player_safe_ampersand_name(val)
        return f'{key}="{val}"'

    return ATTR_RE.sub(replace_attr, prefix) + "," + player_safe_ampersand_name(display)


def is_and_family_name(value: str) -> bool:
    """True for &TV, &Pictures, And TV HD, and similar Zee network brands."""
    normalized = normalize_name(value)
    return any(
        normalized == prefix or normalized.startswith(prefix + " ")
        for prefix in AND_FAMILY_PREFIXES
    )


def starts_with_zee_brand(value: str) -> bool:
    """True when the channel identity begins with the Zee brand token."""
    if is_and_family_name(value):
        return True
    tokens = identity_tokens(value)
    if not tokens:
        return False
    first = tokens[0]
    if first in ZEE_FAMILY_ALIASES:
        return True
    if first == ZEE_BRAND_PREFIX:
        return True
    # Compact ids/names such as "zeetv" or "zee_cinema".
    return first.startswith(ZEE_BRAND_PREFIX) and len(first) >= 4


def tvg_id_is_zee(tvg_id: str) -> bool:
    """Match ids like zeetv, zee_tv, 0-9-zeetv, andtv, or andpictures."""
    cleaned = tvg_id.strip().lower()
    if not cleaned:
        return False
    cleaned = re.sub(r"^(\d+[-_.])+", "", cleaned)
    compact = NON_ALNUM.sub("", cleaned)
    if compact.startswith(ZEE_BRAND_PREFIX):
        return True
    return any(compact.startswith(prefix) for prefix in AND_ID_PREFIXES)


def is_zee_channel(entry: PlaylistEntry) -> bool:
    """Return True when metadata identifies a Zee-family channel.

    Matching uses tvg-name, display name, group-title, and tvg-id.
    Stream URLs are never inspected, to avoid false positives and to keep
    authorization query strings out of matching logic.
    """
    metadata_fields = (
        entry.tvg_name,
        entry.display_name,
        entry.group_title,
        entry.tvg_id,
    )
    if any(is_false_positive(field) for field in metadata_fields if field):
        return False

    for field_value in (entry.tvg_name, entry.display_name):
        if field_value and starts_with_zee_brand(field_value):
            return True

    if entry.group_title and starts_with_zee_brand(entry.group_title):
        return True

    if tvg_id_is_zee(entry.tvg_id):
        return True

    return False


def channel_identity(entry: PlaylistEntry) -> str:
    """Stable duplicate key. HD/SD remain distinct when names or URLs differ."""
    if entry.tvg_id.strip():
        return f"id:{entry.tvg_id.strip().lower()}"
    name = normalize_name(entry.tvg_name or entry.display_name)
    if name:
        return f"name:{name}"
    return f"url:{entry.url.strip()}"


def deduplicate(entries: Iterable[PlaylistEntry]) -> tuple[list[PlaylistEntry], int]:
    unique: list[PlaylistEntry] = []
    seen: set[str] = set()
    removed = 0
    for entry in entries:
        key = channel_identity(entry)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        unique.append(entry)
    return unique, removed


def validate_entries(entries: list[PlaylistEntry], label: str) -> None:
    if not entries:
        raise PlaylistError(f"No {label} channels found; keeping the previous playlist")
    for index, entry in enumerate(entries, start=1):
        if not entry.extinf.startswith("#EXTINF"):
            raise PlaylistError(f"Entry {index} is missing #EXTINF metadata")
        if not (entry.display_name or entry.tvg_name):
            raise PlaylistError(f"Entry {index} is missing a channel name")
        if not entry.url or entry.url.startswith("#"):
            raise PlaylistError(f"Entry {index} is missing a stream URL")


def render_playlist(entries: list[PlaylistEntry]) -> str:
    lines = ["#EXTM3U", ""]
    for entry in entries:
        lines.extend(entry.render())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def source_marker(source_id: str) -> str:
    return f"{SOURCE_MARKER_PREFIX}{source_id}"


def is_source_entry(entry: PlaylistEntry, source_id: str) -> bool:
    marker = source_marker(source_id)
    if marker in entry.body_lines:
        return True
    return f'source="{source_id}"' in entry.extinf.lower()


def strip_source_entries(
    entries: Iterable[PlaylistEntry], source_id: str
) -> list[PlaylistEntry]:
    return [entry for entry in entries if not is_source_entry(entry, source_id)]


def _set_group_title(extinf: str, group_title: str) -> str:
    prefix, sep, display = extinf.rpartition(",")
    if not sep:
        return extinf
    if 'group-title="' in prefix:
        prefix = re.sub(
            r'group-title="[^"]*"',
            f'group-title="{group_title}"',
            prefix,
            count=1,
            flags=re.IGNORECASE,
        )
        return prefix + "," + display
    return prefix + f' group-title="{group_title}",' + display


def tag_source_entry(
    entry: PlaylistEntry,
    source_id: str,
    force_group_title: str | None = None,
) -> PlaylistEntry:
    """Mark an upstream entry so a later failed fetch can remove it."""
    marker = source_marker(source_id)
    body = tuple(
        line
        for line in entry.body_lines
        if not line.startswith(SOURCE_MARKER_PREFIX)
    )
    group_title = force_group_title or canonical_group(entry.group_title)
    extinf = entry.extinf
    if group_title != "Other" and group_title != entry.group_title:
        extinf = _set_group_title(extinf, group_title)
    return replace(
        entry,
        extinf=extinf,
        body_lines=(marker, *body),
        group_title=group_title if group_title != "Other" else entry.group_title,
    )


def render_combined_playlist(header: list[str], entries: list[PlaylistEntry]) -> str:
    header_lines = header or ["#EXTM3U"]
    lines = list(header_lines)
    if lines and not lines[0].upper().startswith("#EXTM3U"):
        lines.insert(0, "#EXTM3U")
    for entry in entries:
        lines.extend(entry.render())
    return "\n".join(lines).rstrip() + "\n"


def should_keep_incoming(entry: PlaylistEntry) -> bool:
    name = entry.tvg_name or entry.display_name
    return not skip_channel(entry.group_title, name, entry.extinf)


def merge_combined_entries(
    base_entries: list[PlaylistEntry],
    incoming: list[PlaylistEntry],
    source_id: str,
    force_group_title: str | None = None,
) -> list[PlaylistEntry]:
    kept_base = strip_source_entries(base_entries, source_id)
    existing_ids = {
        entry.tvg_id.strip().lower()
        for entry in kept_base
        if entry.tvg_id.strip()
    }
    existing_names = {channel_identity(entry) for entry in kept_base}
    tagged = [
        tag_source_entry(entry, source_id, force_group_title=force_group_title)
        for entry in incoming
    ]
    added: list[PlaylistEntry] = []
    for entry in tagged:
        identity = channel_identity(entry)
        tvg_id = entry.tvg_id.strip().lower()
        if tvg_id and tvg_id in existing_ids:
            continue
        if identity in existing_names:
            continue
        added.append(entry)
        if tvg_id:
            existing_ids.add(tvg_id)
        existing_names.add(identity)

    merged = kept_base + added
    ranked = sorted(
        enumerate(merged),
        key=lambda item: sort_key(
            item[1].group_title,
            item[1].tvg_name or item[1].display_name,
            item[0],
        ),
    )
    return [entry for _, entry in ranked]


def write_combined_playlist(
    path: Path,
    header: list[str],
    entries: list[PlaylistEntry],
) -> None:
    atomic_write(path, render_combined_playlist(header, entries))


def load_combined_playlist(path: Path) -> tuple[list[str], list[PlaylistEntry]]:
    if not path.exists():
        raise PlaylistError(f"{path.name} is missing")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise PlaylistError(f"{path.name} is empty")
    return parse_playlist(text)


def remove_stale_source_channels(path: Path, source_id: str) -> int:
    """Drop previously merged channels from this source after a failed fetch."""
    header, entries = load_combined_playlist(path)
    cleaned = strip_source_entries(entries, source_id)
    removed = len(entries) - len(cleaned)
    write_combined_playlist(path, header, cleaned)
    return removed


def fetch_playlist(url: str) -> tuple[int, str]:
    """GET the authorized source playlist with limited retries.

    Retries only transient failures. Permanent client errors are not bypassed.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        retryable = False
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            status = response.status_code
            if status in RETRYABLE_STATUS_CODES:
                retryable = True
                last_error = PlaylistError(f"HTTP status: {status}")
            elif status >= 400:
                raise PlaylistError(f"HTTP status: {status}")
            else:
                response.encoding = response.encoding or "utf-8"
                return status, response.text
        except PlaylistError:
            raise
        except requests.Timeout as exc:
            retryable = True
            last_error = PlaylistError("Fetch failed: timeout")
            if attempt == MAX_RETRIES:
                raise last_error from exc
        except requests.RequestException as exc:
            retryable = True
            last_error = PlaylistError(f"Fetch failed: {exc.__class__.__name__}")
            if attempt == MAX_RETRIES:
                raise last_error from exc

        if not retryable or attempt == MAX_RETRIES:
            raise last_error or PlaylistError("Fetch failed")
        delay = 2 ** (attempt - 1)
        print(f"Transient error; retrying in {delay}s (attempt {attempt}/{MAX_RETRIES})")
        time.sleep(delay)
    raise last_error or PlaylistError("Fetch failed")


def atomic_write(path: Path, contents: str) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(contents, encoding="utf-8")
    if temp_path.stat().st_size == 0:
        temp_path.unlink(missing_ok=True)
        raise PlaylistError("Refusing to write an empty playlist")
    os.replace(temp_path, path)


def load_source_url(env_name: str, default: str = "") -> str:
    url = os.environ.get(env_name, "").strip()
    if url:
        return url
    if default.strip():
        return default.strip()
    raise PlaylistError(
        f"{env_name} is not set. Export an authorized playlist URL."
    )

def _clear_stale_combined(
    combined_path: Path | None, source_id: str | None, combined_name: str | None
) -> None:
    if combined_path is None or not source_id or not combined_name:
        return
    if not combined_path.exists():
        print(
            f"{combined_name} was not found; nothing to strip.",
            file=sys.stderr,
        )
        return
    try:
        removed = remove_stale_source_channels(combined_path, source_id)
        print(
            f"Removed {removed} stale {source_id} channels from {combined_name}.",
            file=sys.stderr,
        )
    except PlaylistError as extra:
        print(f"ERROR: could not clean {combined_name}: {extra}", file=sys.stderr)


def run_update(
    source_env: str,
    output_filename: str,
    match_fn,
    label: str,
    default_source_url: str = "",
    combined_filename: str | None = None,
    source_id: str | None = None,
    force_group_title: str | None = None,
    filter_standalone: bool = True,
    parse_entries_fn=None,
) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    output_path = repo_root / output_filename
    temp_path = output_path.with_name(output_path.name + ".tmp")
    combined_path = repo_root / combined_filename if combined_filename else None

    try:
        source_url = load_source_url(source_env, default=default_source_url)
        print("Fetching playlist...")
        print(f"Source: {redact_url(source_url)}")
        status, body = fetch_playlist(source_url)
        print(f"HTTP status: {status}")
        if parse_entries_fn is not None:
            entries = parse_entries_fn(body, status)
        else:
            looks_like_m3u(body, status)
            _, entries = parse_playlist(body)
        print(f"Downloaded: {len(body.splitlines())} lines")
        print(f"Parsed entries: {len(entries)}")

        matched = [entry for entry in entries if match_fn(entry)]
        if filter_standalone:
            matched = [entry for entry in matched if should_keep_incoming(entry)]
        print(f"{label} channels found: {len(matched)}")
        unique_entries, duplicates_removed = deduplicate(matched)
        print(f"Duplicates removed: {duplicates_removed}")
        validate_entries(unique_entries, label)

        playlist_text = render_playlist(unique_entries)
        atomic_write(output_path, playlist_text)
        print(f"Output written: {output_filename}")

        if combined_path is not None and source_id:
            incoming = [
                entry for entry in unique_entries if should_keep_incoming(entry)
            ]
            header, base_entries = load_combined_playlist(combined_path)
            merged = merge_combined_entries(
                base_entries,
                incoming,
                source_id,
                force_group_title=force_group_title,
            )
            write_combined_playlist(combined_path, header, merged)
            added = sum(1 for entry in merged if is_source_entry(entry, source_id))
            print(
                f"Combined playlist written: {combined_filename} "
                f"({added} {source_id} channels)"
            )
        return 0
    except PlaylistError as extra:
        print(f"ERROR: {extra}", file=sys.stderr)
        print(f"Existing {output_filename} was not overwritten.", file=sys.stderr)
        _clear_stale_combined(combined_path, source_id, combined_filename)
        return 1
    except Exception as extra:  # noqa: BLE001 - surface unexpected failures clearly
        print(f"ERROR: unexpected failure: {extra}", file=sys.stderr)
        print(f"Existing {output_filename} was not overwritten.", file=sys.stderr)
        _clear_stale_combined(combined_path, source_id, combined_filename)
        return 1
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main() -> int:
    return run_update(
        source_env="SOURCE_M3U_URL",
        output_filename=OUTPUT_FILENAME,
        match_fn=is_zee_channel,
        label="Zee",
        default_source_url=DEFAULT_SOURCE_M3U_URL,
        combined_filename=COMBINED_FILENAME,
        source_id=SOURCE_ID,
    )


if __name__ == "__main__":
    sys.exit(main())
