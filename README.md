# Zee playlist

This repository fetches **authorized** upstream M3U playlists and writes:

- `zee.m3u` — Zee-family channels
- `all-channels.m3u` — combined playlist (base channels, Zee-family channels by category, and a **Star Sports** group)
- `star_sports.m3u` — Star Sports channels

It does **not** generate, forge, modify, decode, or bypass authentication tokens, cookies, signatures, WAF protections, or DRM. Stream URLs from the source playlist are copied as opaque strings. The resulting file is only useful if you are authorized to use the upstream source and the provider permits this kind of redistribution.

## What it does

1. Downloads the playlist at `SOURCE_M3U_URL`.
2. Parses M3U entries (`#EXTINF`, metadata lines, and stream URLs).
3. Keeps channels whose **metadata** identifies them as Zee-family.
4. Removes duplicates while keeping distinct HD/SD variants.
5. Writes `zee.m3u` only after validation succeeds.
6. Merges those Zee channels into `all-channels.m3u` by category (Entertainment, Movies, Sports, and so on). Each merged entry is tagged with `#PLAYLIST-SOURCE:zee`.
7. From the same source, prefers **Colors** channels in `all-channels.m3u` (tagged `#PLAYLIST-SOURCE:colors`). If the source has no Colors feeds or the fetch fails, Colors URLs fall back to `jiotv_playlist.m3u` (localhost).

If the Zee source fails, previously merged `#PLAYLIST-SOURCE:zee` channels are removed from `all-channels.m3u`, and Colors channels are restored from `jiotv_playlist.m3u`. If the Star Sports source fails, previously merged `#PLAYLIST-SOURCE:star-sports` channels (the **Star Sports** group) are removed.

## Setup

```bash
git clone https://github.com/rathee-ashish/zee-playlist.git
cd zee-playlist

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Then:

```bash
# Optional: override the default Zee source URL in scripts/update_zee.py
export SOURCE_M3U_URL="https://example.com/authorized-playlist.m3u"
python scripts/update_zee.py

export SOURCE_STAR_M3U_URL="https://sportlink-jtv.pages.dev/Star.json"
python scripts/update_star_sports.py
```

Do not commit `SOURCE_M3U_URL` if it contains credentials.

## GitHub Actions

The workflow `.github/workflows/update.yml` runs on a schedule and on manual dispatch.

Configure the source URL in the GitHub UI:

1. Open the repository.
2. **Settings → Secrets and variables → Actions**.
3. If the URL is not sensitive, add a **Variables** entry named `SOURCE_M3U_URL`.
4. If the URL embeds credentials, add a **Secret** named `SOURCE_M3U_URL` instead.

The workflow reads `vars.SOURCE_M3U_URL` / `vars.SOURCE_STAR_M3U_URL` first, then the matching secrets. If `SOURCE_M3U_URL` is unset, `scripts/update_zee.py` uses `DEFAULT_SOURCE_M3U_URL`. Change that constant (or the env var) when the upstream playlist URL moves. The job refetches every 4 hours and rewrites `zee.m3u`, `all-channels.m3u`, and `star_sports.m3u`.

`SOURCE_STAR_M3U_URL` defaults to [Star.json](https://sportlink-jtv.pages.dev/Star.json). Names, logos, URLs, and license fields are copied from that JSON. Playback `__hdnea__` cookies are copied from the published cookie feed used by [sportlive18/jio-tv-auto-update-playlist](https://github.com/sportlive18/jio-tv-auto-update-playlist) (`script/jtv.py`). The same token is copied onto the stream URL query plus `#EXTHTTP` / `stream_headers` so OTT Navigator Pro and TiviMate-style players both receive it. This project does **not** generate HMAC cookies. Only channels whose names match Star Sports are kept.

Required permission: `contents: write` (already set in the workflow so the job can commit `zee.m3u`).

## Playlist URL

After the repository exists on GitHub, the generated playlist can be consumed from:

```text
https://raw.githubusercontent.com/rathee-ashish/zee-playlist/main/zee.m3u
https://raw.githubusercontent.com/rathee-ashish/zee-playlist/main/star_sports.m3u
```

If GitHub Pages is enabled for the repository root on the `main` branch:

```text
https://rathee-ashish.github.io/zee-playlist/zee.m3u
```

The file is only useful if the upstream source is authorized and the provider permits redistribution. Until `SOURCE_M3U_URL` is configured and the workflow has produced a filtered playlist, `zee.m3u` contains only the `#EXTM3U` header.

## Refresh behavior

- The workflow periodically fetches the upstream playlist (see schedule below).
- `zee.m3u` is replaced only after HTTP, M3U, Zee-match, and per-entry validation succeed.
- On a failed Zee fetch, tagged Zee entries are stripped from `all-channels.m3u` so stale URLs are not kept.
- GitHub Actions schedules are **not** guaranteed to run at the exact second; jobs can be delayed.
- Upstream stream URLs often expire after about 6 hours. This repo does **not** extend or regenerate those URLs. It downloads a fresh playlist from `SOURCE_M3U_URL` on a schedule so `zee.m3u` is replaced before the previous copy goes stale.
- GitHub Actions schedules are **not** guaranteed to run at the exact second; jobs can be delayed.

### Schedule limitation

GitHub Actions cron is UTC and calendar-based. It **cannot** represent a true repeating 5 hour 30 minute interval.

This workflow uses:

```yaml
cron: "0 */4 * * *"
```

That is every 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC). Against a typical 6-hour source URL lifetime, that leaves about a 2-hour buffer, including some slack if GitHub starts the job late.

A 6-hour cron (`0 */6 * * *`) is too close to expiry: a delayed workflow can publish after the links are already dead. Do not treat execution time as exact.

## Zee filtering rules

Matching inspects `tvg-name`, the `#EXTINF` display name, `group-title`, and `tvg-id` (case-insensitive). It does **not** search the stream URL.

Source names such as `&Pictures HD` and `&TV HD` are kept. In the written `zee.m3u` they are shown as `And Pictures HD` and `And TV HD` because many IPTV players treat `&` as a special character and hide those channels. Stream URLs are not rewritten.

A channel is kept when its identity starts with the Zee brand (including compact forms such as `ZeeTV` / `zeetv`), matches `ZEE_FAMILY_ALIASES` (for example `Zing`), or is a Zee Entertainment `&` / `And` brand such as `&TV`, `&Pictures`, `&flix`, `&prive`, and `&xplor`.

To add or tighten rules later, edit `scripts/update_zee.py`:

- `ZEE_FAMILY_ALIASES` — extra names such as `zing` that do not start with `Zee`.
- `AND_FAMILY_PREFIXES` — `&TV` / `And Pictures` and similar Zee network brands.
- `FALSE_POSITIVE_PATTERNS` — phrases to exclude.
- `is_zee_channel()` — main decision function.

## License / use

Use only with a playlist URL you are authorized to fetch. Respect the source provider’s terms.
