"""Generic RSS/Atom feed parsing for the News Archive & Local Log card
(ROADMAP.md DISCOVERY item) — a real Python port of Masthead's own
FeedParser.php (github.com/frankprobst5-stack/masthead, private,
archived-but-reusable), not a fresh implementation. Deliberately narrower
than the original: Masthead's own CAP-namespace extraction (reading
cap:event/severity/etc. directly out of NWS's raw feed XML) is NOT ported
here, because Citadel talks to NWS's own real, structured alerts JSON API
instead (see news_nws_alerts.py) — cleaner and more robust than re-parsing
CAP-XML by hand for that one source. What IS ported: the real,
independently-confirmed-live point-coordinate conventions (GeoRSS for
USGS, geo:lat/geo:long for NOAA's tsunami feeds) that non-NWS hazard
adapters (news_hazard_adapters.py) depend on, and the one real,
already-caught bug worth carrying over rather than rediscovering.

Every field name below was verified directly against real, live feeds
with Python's own `feedparser` library before writing this, not assumed
from the PHP source's own field names (which use a different library,
SimplePie, with a different shape):
  - USGS earthquake feed (real Atom): coordinates live in `entry.where`
    as GeoJSON `{"type": "Point", "coordinates": (lon, lat)}` — note the
    real GeoJSON (lon, lat) order, opposite of every other lat/lon pair
    in this codebase; confirmed against a real entry (106.8742, -4.9832
    for a real quake near Indonesia), not assumed.
  - NOAA tsunami feed (real Atom): coordinates are simpler, exposed
    directly as top-level `entry.geo_lat` / `entry.geo_long`.
  - Both feeds: `entry.id`, `entry.title`, `entry.summary`, and either
    `entry.published_parsed` or `entry.updated_parsed` (USGS's own feed
    only sets the latter — checked live, not assumed both exist).
"""
import hashlib
import html
import re
from datetime import datetime, timezone

import feedparser


def _decode_link(link):
    """Real bug, caught live against BBC News's actual feed (documented
    in Masthead's own FeedParser.php): some feeds' raw XML has the query
    string double-entity-encoded, so the parsed link can still contain a
    literal "&amp;" instead of "&". A URL should never contain an HTML
    entity — decode defensively rather than storing (and later
    re-escaping into "&amp;amp;") a broken link."""
    if not link:
        return link
    return html.unescape(link)


def _dedupe_key(guid_or_link):
    """sha256 of the feed's own guid (falling back to the link when a
    feed doesn't set one) — same real approach as Masthead's own
    ArticleService, and the same reason: cheap, stable, and only ever
    needs to be unique per-source (see the real UNIQUE(source_id,
    dedupe_key) constraint), not globally."""
    return hashlib.sha256(guid_or_link.encode("utf-8")).hexdigest()


def _extract_coordinates(entry):
    """Two real, independently-confirmed-live point-coordinate
    conventions, checked in order — a source only ever uses one:
      - GeoRSS `where` (USGS earthquakes) — GeoJSON order, (lon, lat).
      - geo:lat / geo:long (NOAA tsunami feeds) — two separate fields,
        already in the expected (lat, lon) order.
    Returns (lat, lon) or (None, None)."""
    where = entry.get("where")
    if isinstance(where, dict) and where.get("type") == "Point":
        coords = where.get("coordinates")
        if coords and len(coords) == 2:
            lon, lat = coords
            return float(lat), float(lon)

    geo_lat = entry.get("geo_lat")
    geo_long = entry.get("geo_long")
    if geo_lat is not None and geo_long is not None:
        try:
            return float(geo_lat), float(geo_long)
        except (TypeError, ValueError):
            pass

    return None, None


_IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"\s]+)', re.IGNORECASE)


def _extract_image(entry):
    """Real fallback chain. History of real gaps found live, each against
    an actual configured feed, not a hypothetical:
      - Fox News: no media:thumbnail, image lives in media:content
        instead (the second tier below).
      - NHC Atlantic (2026-10-09): "Graphics"/"Map" entries set NONE of
        the structured tiers -- the real image is a plain `<img src>`
        tag buried in the entry's own `summary` HTML.
      - The Guardian (2026-10-09): real media:content entries, but
        NEITHER `medium` nor `type` is ever set on them -- just a bare
        `url` (sometimes several, at different widths). The original
        media:content tier required one of those two attributes and
        silently rejected every one of these.
      - USNI News / NPR World (2026-10-09): their only real image is in
        `content:encoded` (WordPress's full-article HTML, exposed by
        feedparser as `entry.content[0].value`), never in `summary` at
        all -- the regex tier originally only checked `summary`.

    Order: a feed that sets more than one tier should have its most
    deliberate, structured signal win, same reasoning as Masthead's own
    chain -- the regex tier is intentionally last since it's the least
    deliberate/most fragile signal."""
    media_thumb = entry.get("media_thumbnail")
    if media_thumb and isinstance(media_thumb, list):
        url = media_thumb[0].get("url")
        if url:
            return url

    candidates = []
    for media in entry.get("media_content", []) or []:
        medium = media.get("medium")
        mime = media.get("type")
        url = media.get("url")
        if not url:
            continue
        # Real explicit image signal, or (The Guardian's real shape) no
        # medium/type at all -- media:content with neither set is, in
        # practice, almost always an image, and an explicit non-image
        # medium (e.g. "video", handled separately by _extract_video)
        # still correctly falls through and is skipped here.
        if medium == "image" or (mime and str(mime).startswith("image/")) or (medium is None and not mime):
            try:
                width = int(media.get("width", 0))
            except (TypeError, ValueError):
                width = 0
            candidates.append((width, url))
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)  # prefer the largest real size offered
        return candidates[0][1]

    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
            return link.get("href")

    content_list = entry.get("content") or []
    content_html = content_list[0].get("value") if content_list else None
    for html_blob in (content_html, entry.get("summary")):
        if not html_blob:
            continue
        for match in _IMG_SRC_RE.finditer(html_blob):
            url = match.group(1)
            # Real junk found live against NPR's own feed (2026-10-09):
            # one story's first <img> had a literal, broken
            # src="https://feeds.npr.org/1004/undefined" (NPR's own
            # client-side templating bug, not something re-parsing can
            # fix), and its second <img> was an analytics tracking pixel
            # (".../tracking/npr-rss-pixel.png") -- neither is a real
            # article photo. Skip both known-garbage shapes and keep
            # looking at the next <img> in the same blob rather than
            # blindly trusting the first match.
            low = url.lower()
            if "/undefined" in low or "pixel" in low or "tracking" in low:
                continue
            return url

    return None


def _extract_video(entry):
    """Real playable-video detection, ported from Masthead's own
    extractPlayableEnclosure() -- a feed's actual <enclosure> or
    media:content, filtered to a video medium/MIME type, same real
    distinction Masthead draws against its sibling image/audio checks.
    feedparser exposes both shapes: `media_content` (a list of dicts
    with their own `medium`/`type` keys) for media:content, and
    `links` with `rel == "enclosure"` for the plain RSS <enclosure> tag
    -- checked in that order since media:content is the more deliberate,
    structured signal when a feed sets both.

    Returns (video_url, mime_type) or (None, None)."""
    for media in entry.get("media_content", []) or []:
        medium = media.get("medium")
        mime = media.get("type")
        if medium == "video" or (mime and str(mime).startswith("video/")):
            url = media.get("url")
            if url:
                return url, mime

    for link in entry.get("links", []):
        if link.get("rel") == "enclosure":
            mime = link.get("type", "")
            if str(mime).startswith("video/"):
                href = link.get("href")
                if href:
                    return href, mime

    return None, None


def _best_link(entry):
    """Most feeds' own `entry.link` is a real, clickable HTTP URL — but
    NOAA's tsunami feeds set it to a bare `urn:uuid:...` instead (real,
    confirmed live) and put the actual readable bulletin under `links`
    as `rel: "alternate"`. Prefer a real http(s) link over a urn: one."""
    link = entry.get("link", "")
    if link.startswith("http"):
        return _decode_link(link)

    for l in entry.get("links", []):
        href = l.get("href", "")
        if l.get("rel") == "alternate" and href.startswith("http"):
            return _decode_link(href)

    return _decode_link(link) or None


def _published_at(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_DEFAULT_USER_AGENT = "hazard-feed-adapters/1.0 (+https://github.com/frankprobst5-stack/hazard-feed-adapters)"


def fetch_feed(feed_url, timeout=15, user_agent=_DEFAULT_USER_AGENT):
    """Fetches and normalizes one feed's items into the plain shape the
    rest of this module consumes — the same real job Masthead's own
    FeedParser::fetch() does, minus the CAP-XML extraction (see this
    module's own docstring for why).

    Returns a list of dicts: guid, title, url, body, published_at,
    image_url, latitude, longitude. Raises no exception on a feed-level
    failure (a malformed/unreachable feed) -- `feedparser` itself never
    raises for that, it sets `bozo` and returns whatever it could parse
    (often nothing); callers should check for an empty return and log
    accordingly rather than expect a raised error, matching feedparser's
    own real behavior confirmed above, not assumed.
    """
    parsed = feedparser.parse(feed_url, request_headers={"User-Agent": user_agent})

    items = []
    for entry in parsed.entries:
        link = _best_link(entry)
        if not link:
            continue

        guid = entry.get("id") or link
        lat, lon = _extract_coordinates(entry)
        video_url, video_mime = _extract_video(entry)

        items.append({
            "guid": guid,
            "dedupe_key": _dedupe_key(guid),
            "title": (entry.get("title") or "(untitled)").strip(),
            "url": link,
            "body": entry.get("summary"),
            "published_at": _published_at(entry),
            "image_url": _extract_image(entry),
            "video_url": video_url,
            "media_type": "video" if video_url else None,
            "latitude": lat,
            "longitude": lon,
        })

    return items
