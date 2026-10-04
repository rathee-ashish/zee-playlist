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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
OUTPUT_FILENAME = "zee.m3u"
TEMP_FILENAME = "zee.m3u.tmp"
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

# Exact display / tvg-name / group-title aliases that are Zee family but may
# not start with "Zee". Extend this set when adding known rebrands.
ZEE_FAMILY_ALIASES = {
    "zing",
    "zee5",
}

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
        return [self.extinf, *self.body_lines]


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

    An entry starts at #EXTINF and includes following metadata comment lines
    until the first non-comment line, which is treated as the stream URL.
    URLs are stored unchanged.
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
    lowered = QUALITY_PREFIX.sub("", value.strip().lower())
    return NON_ALNUM.sub(" ", lowered).strip()


def identity_tokens(value: str) -> list[str]:
    return [token for token in normalize_name(value).split(" ") if token]


def is_false_positive(value: str) -> bool:
    return any(pattern.search(value) for pattern in FALSE_POSITIVE_PATTERNS)


def starts_with_zee_brand(value: str) -> bool:
    """True when the channel identity begins with the Zee brand token."""
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
    """Match ids like zeetv, zee_tv, or 0-9-zeetv — not URLs."""
    cleaned = tvg_id.strip().lower()
    if not cleaned:
        return False
    cleaned = re.sub(r"^(\d+[-_.])+", "", cleaned)
    compact = NON_ALNUM.sub("", cleaned)
    return compact.startswith(ZEE_BRAND_PREFIX)


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


def validate_entries(entries: list[PlaylistEntry]) -> None:
    if not entries:
        raise PlaylistError("No Zee channels found; keeping the previous playlist")
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
    temp_path = path.with_name(TEMP_FILENAME)
    temp_path.write_text(contents, encoding="utf-8")
    if temp_path.stat().st_size == 0:
        temp_path.unlink(missing_ok=True)
        raise PlaylistError("Refusing to write an empty playlist")
    os.replace(temp_path, path)


def load_source_url() -> str:
    url = os.environ.get("SOURCE_M3U_URL", "").strip()
    if not url:
        raise PlaylistError(
            "SOURCE_M3U_URL is not set. Export an authorized playlist URL."
        )
    return url


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    output_path = repo_root / OUTPUT_FILENAME

    try:
        source_url = load_source_url()
        print("Fetching playlist...")
        print(f"Source: {redact_url(source_url)}")
        status, body = fetch_playlist(source_url)
        print(f"HTTP status: {status}")
        looks_like_m3u(body, status)
        downloaded_lines = body.splitlines()
        print(f"Downloaded: {len(downloaded_lines)} lines")

        _, entries = parse_playlist(body)
        print(f"Parsed entries: {len(entries)}")

        zee_entries = [entry for entry in entries if is_zee_channel(entry)]
        print(f"Zee channels found: {len(zee_entries)}")
        unique_entries, duplicates_removed = deduplicate(zee_entries)
        print(f"Duplicates removed: {duplicates_removed}")
        validate_entries(unique_entries)

        playlist_text = render_playlist(unique_entries)
        atomic_write(output_path, playlist_text)
        print(f"Output written: {OUTPUT_FILENAME}")
        return 0
    except PlaylistError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Existing zee.m3u was not overwritten.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures clearly
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        print("Existing zee.m3u was not overwritten.", file=sys.stderr)
        return 1
    finally:
        temp_path = repo_root / TEMP_FILENAME
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    sys.exit(main())
