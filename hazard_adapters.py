"""Hazard severity adapters — real Python port of Masthead's own five
adapters (UsgsEarthquakeSeverityAdapter.php, UsgsVolcanoSeverityAdapter.php,
NhcHurricaneAdapter.php, InciWebWildfireAdapter.php,
TsunamiSeverityAdapter.php), ported field-for-field from the real PHP
source, not reimplemented from the general idea. Every regex, threshold,
and edge case below is copied from logic Frank already fought out against
each real API's own quirks — see each function's own docstring for the
specific real-world detail it exists to handle.

Not every hazard-monitoring agency publishes real CAP fields the way NWS
does (NWS is handled separately and directly, see news_nws_alerts.py) —
USGS, InciWeb, and NOAA's tsunami/NHC feeds are all plain RSS/Atom with no
native severity field. An adapter derives the same cap_* shape from each
source's own real signal (title text, magnitude, coordinates) instead.
Adding a new non-NWS hazard source later means writing one small adapter
function and registering it in ADAPTERS_BY_URL_SUBSTRING, nothing else.
"""
import re
from datetime import datetime, timedelta, timezone

_FORTY_EIGHT_HOURS = timedelta(hours=48)


def _expires_in_48h():
    return (datetime.now(timezone.utc) + _FORTY_EIGHT_HOURS).strftime("%Y-%m-%d %H:%M:%S")


def adapt_usgs_earthquake(item):
    """USGS's earthquake feeds (confirmed live) use Atom + GeoRSS, not
    CAP — no severity/urgency fields at all. Magnitude is the natural
    severity proxy, and the title is always formatted "M <magnitude> -
    <location>" (e.g. "M 5.1 - south of Tonga"), confirmed live against
    real entries, which also gives a usable location for free.

    cap_expires_at is set to +48h — an earthquake is a point-in-time
    report, not an ongoing hazard warning; "recent seismic activity"
    stays relevant for a short window, then it's just historical data
    (still permanently in news_articles, just out of "Active Alerts")."""
    match = re.match(r"^M\s*([\d.]+)\s*-\s*(.+)$", item["title"], re.IGNORECASE)
    if not match:
        return item

    magnitude = float(match.group(1))
    location = match.group(2).strip()

    if magnitude >= 7.0:
        severity = "Extreme"
    elif magnitude >= 6.0:
        severity = "Severe"
    elif magnitude >= 5.0:
        severity = "Moderate"
    else:
        severity = "Minor"

    item["cap_severity"] = severity
    item["cap_event"] = f"M{match.group(1)} Earthquake"
    item["cap_area_desc"] = location or None
    item["cap_expires_at"] = _expires_in_48h()
    return item


def adapt_usgs_volcano(item):
    """USGS's HANS volcano feed (confirmed live) is plain RSS with no
    cap: fields — but every title follows a consistent pattern
    (confirmed against real ingested titles): "... - <Volcano Name>
    Volcano - <Alert|Cancel|...> - Alert level: X, Color Code: Y".

    Only alert levels above baseline are surfaced — USGS also issues
    routine "Alert level: NORMAL, Color Code: GREEN" daily updates for
    quiet, monitored volcanoes; treating every one as an "active alert"
    would flood the ticker with nothing-is-happening noise.

    cap_expires_at is +48h, not permanent — HANS posts a fresh dated
    update on the same ongoing event repeatedly (each with its own
    dedupe_key, so each becomes a distinct article); without an expiry,
    months of superseded daily updates for one eruption would all sit
    in "Active Alerts" forever."""
    title = item["title"]

    color_match = re.search(r"Color Code:\s*(GREEN|YELLOW|ORANGE|RED)", title, re.IGNORECASE)
    color_code = color_match.group(1).upper() if color_match else None

    level_match = re.search(r"Alert level:\s*(NORMAL|ADVISORY|WATCH|WARNING)", title, re.IGNORECASE)
    alert_level = level_match.group(1).upper() if level_match else None

    color_severity = {"RED": "Extreme", "ORANGE": "Severe", "YELLOW": "Moderate", "GREEN": None}
    level_severity = {"WARNING": "Extreme", "WATCH": "Severe", "ADVISORY": "Moderate"}

    if color_code is not None:
        severity = color_severity.get(color_code)
    else:
        severity = level_severity.get(alert_level)

    if severity is None:
        return item

    volcano_match = re.search(r"-\s*([^-]+?Volcano)\s*-", title, re.IGNORECASE)
    volcano_name = re.sub(r"\s+", " ", volcano_match.group(1)).strip() if volcano_match else None

    item["cap_severity"] = severity
    item["cap_event"] = "Volcanic Activity"
    item["cap_area_desc"] = volcano_name
    item["cap_expires_at"] = _expires_in_48h()
    return item


def adapt_nhc_hurricane(item):
    """NHC's real RSS feeds (Atlantic/Eastern Pacific/Central Pacific)
    mix several item shapes; only "Summary for {type} {name}" items
    carry real per-storm data. Deliberately parses the plain
    description text (which already restates wind speed and
    coordinates in prose) rather than a bespoke NHC XML namespace.

    Severity uses the real Saffir-Simpson wind scale, not invented:
    Tropical Depression (<39 mph) -> Minor, Tropical Storm (39-73) ->
    Moderate, Hurricane Category 1-2 (74-110) -> Severe, Category 3+ /
    NOAA's own "Major Hurricane" (111+) -> Extreme.

    48h synthetic expiry, same reasoning as earthquake/volcano/tsunami:
    a hurricane's own guid changes per advisory, but articles are never
    deleted, so without an expiry every historical advisory for a
    long-dissipated storm would stay "active" forever."""
    title = item["title"]
    body = item.get("body") or ""

    match = re.match(
        r"^Summary for (Tropical Depression|Tropical Storm|Hurricane) (.+?) \(",
        title,
    )
    if not match:
        return item

    storm_type, name = match.group(1), match.group(2).strip()

    item["cap_event"] = f"{storm_type} {name}"
    item["cap_expires_at"] = _expires_in_48h()

    wind_match = re.search(r"maximum sustained winds of about (\d+)\s*mph", body, re.IGNORECASE)
    if wind_match:
        mph = int(wind_match.group(1))
        if mph >= 111:
            severity = "Extreme"
        elif mph >= 74:
            severity = "Severe"
        elif mph >= 39:
            severity = "Moderate"
        else:
            severity = "Minor"
        item["cap_severity"] = severity

    coord_match = re.search(r"located near ([\d.]+),\s*(-?[\d.]+)", body)
    if coord_match:
        item["latitude"] = float(coord_match.group(1))
        item["longitude"] = float(coord_match.group(2))

    return item


def adapt_inciweb_wildfire(item):
    """InciWeb (confirmed live) publishes plain RSS, not CAP — every
    real item's description embeds "Latitude: {d} {m} {s} Longitude:
    {d} {m} {s}" (no hemisphere marker; InciWeb is US wildfires only,
    so longitude is always West, negated here).

    Severity uses NWCG's real wildland fire size classes when acreage
    is parseable, floored at 'Minor' rather than None when it isn't —
    InciWeb only lists currently-active incidents at all, so mere
    presence already means "a real fire is being tracked," and a None
    severity would silently exclude every wildfire from an "active"
    filter.

    No synthetic expiry (unlike the other four adapters) — wildfires
    can burn for weeks, so a short expiry would incorrectly mark a
    real, still-active fire as cleared."""
    body = item.get("body") or ""

    acres = None
    acres_match = re.search(r"([\d,]+)\s*acres", body, re.IGNORECASE)
    if acres_match:
        acres = int(acres_match.group(1).replace(",", ""))

    if acres is None:
        severity = "Minor"
    elif acres >= 5000:
        severity = "Extreme"
    elif acres >= 1000:
        severity = "Severe"
    elif acres >= 100:
        severity = "Moderate"
    else:
        severity = "Minor"

    item["cap_severity"] = severity
    item["cap_expires_at"] = None

    state_match = re.search(r"State:\s*([^\n\-]+)", body)
    if state_match:
        item["cap_area_desc"] = state_match.group(1).strip()

    # Real bug found live, 2026-09-16, testing against the actual current
    # feed (not present in Masthead's own original regex, or silently
    # broken there too): InciWeb's real data is inconsistent about the
    # longitude sign — most entries write it unsigned ("115° 13 01.1",
    # relying on "InciWeb is US-only, so West" to negate it), but some
    # real entries already include an explicit minus sign ("-115° 13
    # 01.1"). The original regex only matched the unsigned form, so a
    # signed entry silently failed to match at all -- not a wrong value,
    # a total non-match -- falling through to "no location data," which
    # incorrectly made a real, geolocatable wildfire pass the location
    # filter as if it were volcano/space-weather-style locationless data.
    # Confirmed live: a real Idaho fire ("IDNCF Moose Mountain") uses the
    # unsigned form, while another real Idaho fire ("IDBOF Crooked") uses
    # the signed form in the same live feed, same day.
    # Also real, found in the same live pass: seconds (and occasionally
    # minutes) can be decimal ("29.4", not just "29") -- [\d.]+ throughout,
    # not \d+, matching how every other adapter in this file already
    # handles decimal values (magnitude, acreage).
    # Real, third format variant found live in the same pass: some
    # entries put a space before the degree symbol ("44 ° 15 7"), which
    # `\s*°?` (degree symbol directly after the digits) can't match --
    # `\s*°?\s*` handles a space on either side of an optional symbol.
    coord_match = re.search(
        r"Latitude:\s*(-?[\d.]+)\s*°?\s*([\d.]+)\s+([\d.]+)\s+Longitude:\s*(-?[\d.]+)\s*°?\s*([\d.]+)\s+([\d.]+)",
        body,
    )
    if coord_match:
        lat_deg, lat_min, lat_sec, lon_deg, lon_min, lon_sec = (float(g) for g in coord_match.groups())
        item["latitude"] = round(lat_deg + lat_min / 60 + lat_sec / 3600, 6)
        # InciWeb is US wildfires only, so the real answer is always
        # West (negative) regardless of whether the source text included
        # its own sign or not -- abs() first so a source that already
        # signed it negative doesn't get double-negated back to positive.
        lon_value = abs(lon_deg) + lon_min / 60 + lon_sec / 3600
        item["longitude"] = round(-1 * lon_value, 6)

    return item


def adapt_tsunami(item):
    """NOAA's Tsunami Warning Centers (NTWC + PTWC, confirmed live)
    publish real Atom feeds where every entry's body already contains
    a structured "Category: X" field (Information / Watch / Advisory /
    Warning).

    "Information" bulletins are deliberately not treated as an alert —
    confirmed live, that category specifically means "an earthquake
    occurred but a tsunami is *not* expected." Surfacing "no tsunami
    expected" as an active alert would be exactly the noise this
    feature exists to cut through."""
    body = re.sub(r"<[^>]+>", "", item.get("body") or "")

    category_match = re.search(r"Category:\s*(Information|Watch|Advisory|Warning)", body, re.IGNORECASE)
    if not category_match:
        return item

    category = category_match.group(1).capitalize()
    severity = {"Warning": "Extreme", "Watch": "Severe", "Advisory": "Moderate"}.get(category)
    if severity is None:
        return item

    item["cap_severity"] = severity
    item["cap_event"] = f"Tsunami {category}"
    # The feed's own title is already a clean location string (e.g. "50
    # miles SE of Atka Village, Alaska") — no extraction needed.
    item["cap_area_desc"] = item.get("title")
    item["cap_expires_at"] = _expires_in_48h()
    return item


# The registry side of the adapter pattern -- news_scheduler.py asks this
# "does any adapter recognize this source's feed URL," once per source per
# fetch, rather than every ingestion path needing the full list itself.
ADAPTERS_BY_URL_SUBSTRING = (
    ("volcanoes.usgs.gov", adapt_usgs_volcano),
    ("earthquake.usgs.gov", adapt_usgs_earthquake),
    ("tsunami.gov", adapt_tsunami),
    # InciWeb's real feed now lives at inciweb.nwcg.gov (confirmed live,
    # 2026-09-16) -- article permalinks still resolve under the older
    # inciweb.wildfire.gov domain, so both are matched here for safety
    # rather than assuming the migration is total.
    ("inciweb.nwcg.gov", adapt_inciweb_wildfire),
    ("inciweb.wildfire.gov", adapt_inciweb_wildfire),
    ("nhc.noaa.gov", adapt_nhc_hurricane),
)


def adapter_for_feed_url(feed_url):
    for substring, adapter in ADAPTERS_BY_URL_SUBSTRING:
        if substring in feed_url:
            return adapter
    return None
