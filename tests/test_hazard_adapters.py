"""Tests built from the real-world edge cases already documented (and
fought out) in hazard_adapters.py's own docstrings/comments -- not
invented cases, the actual quirks each adapter exists to handle."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hazard_adapters as ha


def _item(title="", body=""):
    return {"title": title, "body": body}


# -- USGS earthquake: magnitude bands + title parsing --------------------

def test_earthquake_extreme():
    out = ha.adapt_usgs_earthquake(_item("M 7.2 - south of Tonga"))
    assert out["cap_severity"] == "Extreme"
    assert out["cap_event"] == "M7.2 Earthquake"
    assert out["cap_area_desc"] == "south of Tonga"


def test_earthquake_severity_bands():
    assert ha.adapt_usgs_earthquake(_item("M 6.5 - x"))["cap_severity"] == "Severe"
    assert ha.adapt_usgs_earthquake(_item("M 5.0 - x"))["cap_severity"] == "Moderate"
    assert ha.adapt_usgs_earthquake(_item("M 3.1 - x"))["cap_severity"] == "Minor"


def test_earthquake_non_matching_title_passes_through_unchanged():
    item = _item("Some unrelated headline")
    out = ha.adapt_usgs_earthquake(dict(item))
    assert out == item


# -- USGS volcano: color code vs alert level, GREEN is not an alert ------

def test_volcano_red_color_code_is_extreme():
    out = ha.adapt_usgs_volcano(_item("Update - Mount Foo Volcano - Alert - Color Code: RED"))
    assert out["cap_severity"] == "Extreme"
    assert out["cap_event"] == "Volcanic Activity"
    assert out["cap_area_desc"] == "Mount Foo Volcano"


def test_volcano_green_is_not_surfaced_as_an_alert():
    item = _item("Update - Mount Foo Volcano - Alert - Color Code: GREEN")
    out = ha.adapt_usgs_volcano(dict(item))
    assert "cap_severity" not in out


def test_volcano_alert_level_fallback_when_no_color_code():
    out = ha.adapt_usgs_volcano(_item("Update - Mount Foo Volcano - Alert level: WARNING"))
    assert out["cap_severity"] == "Extreme"


# -- NHC hurricane: Saffir-Simpson wind bands -----------------------------

def test_hurricane_major_is_extreme():
    out = ha.adapt_nhc_hurricane(_item(
        "Summary for Hurricane Example (...)",
        "Maximum sustained winds of about 130 mph. Storm is located near 25.0, -80.0 and moving west.",
    ))
    assert out["cap_severity"] == "Extreme"
    assert out["cap_event"] == "Hurricane Example"
    assert out["latitude"] == 25.0
    assert out["longitude"] == -80.0


def test_hurricane_tropical_storm_is_moderate():
    out = ha.adapt_nhc_hurricane(_item(
        "Summary for Tropical Storm Example (...)",
        "...maximum sustained winds of about 50 mph...",
    ))
    assert out["cap_severity"] == "Moderate"


def test_hurricane_non_summary_item_passes_through_unchanged():
    item = _item("Tropical Weather Outlook")
    out = ha.adapt_nhc_hurricane(dict(item))
    assert out == item


# -- InciWeb wildfire: acreage bands + the three real coordinate bugs ----

def test_wildfire_acreage_bands():
    assert ha.adapt_inciweb_wildfire(_item(body="12,000 acres"))["cap_severity"] == "Extreme"
    assert ha.adapt_inciweb_wildfire(_item(body="2,000 acres"))["cap_severity"] == "Severe"
    assert ha.adapt_inciweb_wildfire(_item(body="500 acres"))["cap_severity"] == "Moderate"
    assert ha.adapt_inciweb_wildfire(_item(body="10 acres"))["cap_severity"] == "Minor"
    assert ha.adapt_inciweb_wildfire(_item(body="no acreage mentioned"))["cap_severity"] == "Minor"


def test_wildfire_no_synthetic_expiry():
    out = ha.adapt_inciweb_wildfire(_item(body="100 acres"))
    assert out["cap_expires_at"] is None


def test_wildfire_unsigned_longitude_defaults_west():
    # Real example from the docstring: "IDNCF Moose Mountain" style, no
    # explicit sign on longitude -- InciWeb is US-only, so it's West.
    body = "Latitude: 44 ° 15 7 Longitude: 115 ° 13 01.1"
    out = ha.adapt_inciweb_wildfire(_item(body=body))
    assert out["longitude"] < 0


def test_wildfire_signed_longitude_not_double_negated():
    # Real example from the docstring: "IDBOF Crooked" style, already
    # carries an explicit minus sign -- abs() first must prevent a
    # double-negation back to positive.
    body = "Latitude: 44° 15 7 Longitude: -115° 13 01.1"
    out = ha.adapt_inciweb_wildfire(_item(body=body))
    assert out["longitude"] < 0


def test_wildfire_decimal_seconds_and_space_before_degree_symbol():
    # Real third variant from the docstring: a space before the degree
    # symbol, plus decimal seconds -- both must parse, not just integers.
    body = "Latitude: 44 ° 15 29.4 Longitude: 115 ° 13 01.1"
    out = ha.adapt_inciweb_wildfire(_item(body=body))
    assert out["latitude"] == round(44 + 15 / 60 + 29.4 / 3600, 6)


# -- Tsunami: category mapping, "Information" is not an alert ------------

def test_tsunami_warning_is_extreme():
    out = ha.adapt_tsunami(_item("50 miles SE of Atka Village, Alaska", "Category: Warning"))
    assert out["cap_severity"] == "Extreme"
    assert out["cap_event"] == "Tsunami Warning"
    assert out["cap_area_desc"] == "50 miles SE of Atka Village, Alaska"


def test_tsunami_information_is_not_surfaced_as_an_alert():
    item = _item("x", "Category: Information")
    out = ha.adapt_tsunami(dict(item))
    assert "cap_severity" not in out


# -- Registry --------------------------------------------------------------

def test_adapter_for_feed_url_matches_known_sources():
    assert ha.adapter_for_feed_url("https://earthquake.usgs.gov/feed.atom") is ha.adapt_usgs_earthquake
    assert ha.adapter_for_feed_url("https://inciweb.nwcg.gov/feeds/rss/incidents/") is ha.adapt_inciweb_wildfire
    assert ha.adapter_for_feed_url("https://inciweb.wildfire.gov/feeds/rss/incidents/") is ha.adapt_inciweb_wildfire


def test_adapter_for_feed_url_returns_none_for_unknown_source():
    assert ha.adapter_for_feed_url("https://example.com/rss") is None
