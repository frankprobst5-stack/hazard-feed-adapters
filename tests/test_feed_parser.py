"""Tests for feed_parser.py's pure helpers, built from the real feed
quirks documented in its own docstrings (BBC's double-encoded links,
USGS's GeoJSON (lon, lat) order, NOAA tsunami's urn:uuid links)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feed_parser as fp


def test_decode_link_fixes_double_encoded_ampersand():
    assert fp._decode_link("https://example.com/a?x=1&amp;y=2") == "https://example.com/a?x=1&y=2"


def test_decode_link_passthrough_for_clean_link():
    assert fp._decode_link("https://example.com/a?x=1&y=2") == "https://example.com/a?x=1&y=2"


def test_decode_link_handles_none():
    assert fp._decode_link(None) is None


def test_extract_coordinates_usgs_georss_point_lon_lat_order():
    # Real documented example: GeoJSON order is (lon, lat), the opposite
    # of every other lat/lon pair in this codebase.
    entry = {"where": {"type": "Point", "coordinates": (106.8742, -4.9832)}}
    lat, lon = fp._extract_coordinates(entry)
    assert lat == -4.9832
    assert lon == 106.8742


def test_extract_coordinates_noaa_tsunami_geo_lat_long():
    entry = {"geo_lat": "19.5", "geo_long": "-155.5"}
    lat, lon = fp._extract_coordinates(entry)
    assert lat == 19.5
    assert lon == -155.5


def test_extract_coordinates_none_when_absent():
    assert fp._extract_coordinates({}) == (None, None)


def test_best_link_prefers_http_entry_link():
    entry = {"link": "https://example.com/story"}
    assert fp._best_link(entry) == "https://example.com/story"


def test_best_link_falls_back_to_alternate_for_urn_uuid():
    # Real documented NOAA tsunami feed shape: entry.link is a bare
    # urn:uuid, the real readable bulletin is under links as "alternate".
    entry = {
        "link": "urn:uuid:1234",
        "links": [{"rel": "alternate", "href": "https://tsunami.gov/bulletin/1234"}],
    }
    assert fp._best_link(entry) == "https://tsunami.gov/bulletin/1234"


def test_extract_image_prefers_media_thumbnail():
    entry = {"media_thumbnail": [{"url": "https://example.com/thumb.jpg"}]}
    assert fp._extract_image(entry) == "https://example.com/thumb.jpg"


def test_extract_image_media_content_with_no_medium_or_type_is_treated_as_image():
    # Real documented Guardian case: media:content with neither medium
    # nor type set, just a bare url.
    entry = {"media_content": [{"url": "https://example.com/photo.jpg"}]}
    assert fp._extract_image(entry) == "https://example.com/photo.jpg"


def test_extract_image_skips_known_junk_in_regex_fallback():
    # Real documented NPR case: a broken "/undefined" src and a tracking
    # pixel, both must be skipped in favor of a real photo later in the
    # same blob.
    entry = {
        "summary": (
            '<img src="https://feeds.npr.org/1004/undefined">'
            '<img src="https://example.com/tracking/npr-rss-pixel.png">'
            '<img src="https://example.com/real-photo.jpg">'
        )
    }
    assert fp._extract_image(entry) == "https://example.com/real-photo.jpg"
