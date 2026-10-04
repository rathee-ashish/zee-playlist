# Zee playlist

This repository fetches an **authorized** upstream M3U playlist and extracts Zee-family channels into `zee.m3u`.

It does **not** generate, forge, modify, decode, or bypass authentication tokens, cookies, signatures, WAF protections, or DRM. Stream URLs from the source playlist are copied as opaque strings. The resulting file is only useful if you are authorized to use the upstream source and the provider permits this kind of redistribution.

## What it does

1. Downloads the playlist at `SOURCE_M3U_URL`.
2. Parses M3U entries (`#EXTINF`, metadata lines, and stream URLs).
3. Keeps channels whose **metadata** identifies them as Zee-family.
4. Removes duplicates while keeping distinct HD/SD variants.
5. Writes `zee.m3u` only after validation succeeds.

If the source is down, returns a non-playlist body, or yields zero Zee channels, the previous `zee.m3u` is left unchanged.

## Setup

```bash
git clone <repo>
cd zee-playlist

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Then:

```bash
export SOURCE_M3U_URL="https://example.com/authorized-playlist.m3u"
python scripts/update_zee.py
```

Do not commit `SOURCE_M3U_URL` if it contains credentials.

## GitHub Actions

The workflow `.github/workflows/update.yml` runs on a schedule and on manual dispatch.

Configure the source URL in the GitHub UI:

1. Open the repository.
2. **Settings → Secrets and variables → Actions**.
3. If the URL is not sensitive, add a **Variables** entry named `SOURCE_M3U_URL`.
4. If the URL embeds credentials, add a **Secret** named `SOURCE_M3U_URL` instead.

The workflow reads `vars.SOURCE_M3U_URL` first, then `secrets.SOURCE_M3U_URL`.

Required permission: `contents: write` (already set in the workflow so the job can commit `zee.m3u`).

## Playlist URL

After the repository exists on GitHub, the generated playlist can be consumed from:

```text
https://raw.githubusercontent.com/<USERNAME>/<REPOSITORY>/main/zee.m3u
```

If GitHub Pages is enabled for the repository root on the `main` branch:

```text
https://<USERNAME>.github.io/<REPOSITORY>/zee.m3u
```

Replace `<USERNAME>` and `<REPOSITORY>` with the real GitHub owner and repo name. Do not assume the playlist will play unless the upstream source is authorized and redistribution is allowed.

## Refresh behavior

- The workflow periodically fetches the upstream playlist (see schedule below).
- `zee.m3u` is replaced only after HTTP, M3U, Zee-match, and per-entry validation succeed.
- GitHub Actions schedules are **not** guaranteed to run at the exact second; jobs can be delayed.
- A typical six-hour stream authorization window and this repository’s refresh cadence are **separate** concepts. This project does not refresh or regenerate tokens.

### Schedule limitation

GitHub Actions cron is UTC and calendar-based. It **cannot** represent a true repeating 5 hour 30 minute interval.

This workflow uses:

```yaml
cron: "0 */6 * * *"
```

That is four times per day (00:00, 06:00, 12:00, 18:00 UTC), a practical approximation of a 5h30m refresh with a safety buffer before a 6-hour authorization window. Do not treat execution time as exact.

The alternative `30 0,6,12,18 * * *` would also run four times per day at fixed clock times; it still is not a sliding 5h30m timer.

## Zee filtering rules

Matching inspects `tvg-name`, the `#EXTINF` display name, `group-title`, and `tvg-id` (case-insensitive). It does **not** search the stream URL.

A channel is kept when its identity starts with the Zee brand (including compact forms such as `ZeeTV` / `zeetv`) or matches an alias in `ZEE_FAMILY_ALIASES`.

To add or tighten rules later, edit `scripts/update_zee.py`:

- `ZEE_FAMILY_ALIASES` — extra names such as `zing` that do not start with `Zee`.
- `FALSE_POSITIVE_PATTERNS` — phrases to exclude.
- `is_zee_channel()` — main decision function.

## License / use

Use only with a playlist URL you are authorized to fetch. Respect the source provider’s terms.
