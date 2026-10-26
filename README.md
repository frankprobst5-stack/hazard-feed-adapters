# hazard-feed-adapters

A small Python library that fetches real-world disaster/hazard feeds — USGS
earthquakes and volcanoes, NOAA tsunami bulletins, NHC hurricane advisories,
InciWeb wildfires, and the US National Weather Service's own alerts API —
and normalizes every one of them into the same flat dict shape, with a
common `cap_severity` / `cap_event` / `cap_area_desc` / `cap_expires_at`
vocabulary borrowed from the real Common Alerting Protocol (CAP), even for
the sources (most of them) that don't actually speak CAP themselves.

## Why this exists

NWS publishes real, structured CAP-shaped alert data. Almost nobody else
does — USGS, InciWeb, and NOAA's own tsunami/hurricane feeds are all plain
RSS/Atom with no native severity field at all. Getting a severity, an
affected area, and a sensible expiry out of each of those means reading
each source's own real quirks: USGS earthquake titles are always
`"M <magnitude> - <location>"`; InciWeb's coordinate field is sometimes
signed and sometimes not, depending on which entry you're looking at, in
the same live feed, on the same day. None of that is guessable from a
spec — it's the result of actually fetching each real feed and reading what
came back.

This library did exactly that, originally as part of
[Citadel](https://github.com/frankprobst5-stack/Project-Citadel)'s News
Archive module (itself a Python port of a retired PHP project,
Masthead). It's pulled out here because the normalization logic has zero
real dependency on Citadel — it's just "turn a hazard feed into a common
shape," useful to any project that wants to show real disaster alerts
without writing five feed-specific parsers from scratch.

## What's here

- **`feed_parser.py`** — fetches and normalizes a plain RSS/Atom feed
  (via [`feedparser`](https://pypi.org/project/feedparser/)) into a common
  item shape: `guid`, `title`, `url`, `body`, `published_at`, `image_url`,
  `video_url`, `latitude`, `longitude`. Handles real, previously-found feed
  quirks: double-HTML-encoded links, two different point-coordinate
  conventions (GeoRSS vs. `geo:lat`/`geo:long`), a multi-tier image/video
  extraction fallback chain (structured `media:content` first, a raw
  `<img>` regex last), and feeds that set a `urn:uuid:` as their main link
  instead of a real URL.
- **`hazard_adapters.py`** — five adapters, one per non-CAP source, each
  deriving a `cap_severity`/`cap_event`/`cap_area_desc`/`cap_expires_at`
  from that source's own real signal (a magnitude, a wind speed, an
  acreage, a color code) since none of them publish one directly:
  USGS earthquakes, USGS volcanoes, NHC hurricanes, InciWeb wildfires, NOAA
  tsunami bulletins.
- **`nws_alerts.py`** — a direct client for `api.weather.gov/alerts/active`,
  which already returns real CAP fields as plain JSON, so it needs no
  adapter — just a normalizer into the same common shape.

## Usage

```python
import feed_parser, hazard_adapters, nws_alerts

# A non-CAP source: fetch, then run it through its matching adapter.
items = feed_parser.fetch_feed("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.atom")
adapter = hazard_adapters.adapter_for_feed_url("https://earthquake.usgs.gov/...")
if adapter:
    items = [adapter(item) for item in items]

# NWS: already CAP-shaped, no feed parsing or adapter needed.
alerts = nws_alerts.fetch_active_alerts(lat=31.9, lon=-105.3)
```

Each item/alert is a plain dict — store it however you like (this started
life as rows headed straight into a SQLite `news_articles` table; nothing
here assumes that).

## Install

No packaging framework — this is meant to be copied in or vendored, the
same way [pi-tier-picker](https://github.com/frankprobst5-stack/pi-tier-picker)
is. Three files, two real dependencies:

```bash
pip install feedparser requests
```

## Tests

```bash
pip install pytest
python3 -m pytest tests/
```

29 tests, built from the real feed quirks documented in the source's own
comments (BBC's double-encoded links, the Guardian's untagged
`media:content`, NPR's broken `/undefined` image and tracking-pixel junk,
InciWeb's signed-vs-unsigned longitude inconsistency within the same live
feed) — not invented edge cases, the ones that were actually hit fetching
these feeds for real.

## License

GPL-3.0-or-later, matching the Citadel ecosystem this came out of.
