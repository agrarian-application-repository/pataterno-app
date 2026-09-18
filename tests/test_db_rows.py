"""Database-shaped rows through the renderer. No live database needed.

These guard the two places the dashboard crashes on real data:
`d['captured_at'].replace(...)` needs a string, and `d['confidence'] * 100`
needs a number - but every numeric column in the schema is nullable and
`captured_at` arrives from psycopg as a datetime.
"""

from datetime import datetime, timezone
from decimal import Decimal

import dashboard
import db


def _db_detection(**overrides) -> dict:
    """A detection as fetch_detections() returns it."""
    row = {
        "detection_id": 1,
        "flight_id": "PTZ-F07",
        "flown_at": "2026-09-06T09:00:00Z",
        "captured_at": "2026-09-06T09:12:00Z",
        "label": "colorado_potato_beetle",
        "frame": "DJI_0142.JPG",
        "confidence": 0.93,
        "lat": 40.331234,
        "lon": 15.609152,
        "sector": "A3",
        "life_stage": "adult",
        "count_n": 4,
        "density_per_m2": 0.8,
        "data_source": "synthetic",
    }
    row.update(overrides)
    return row


def _db_station(**overrides) -> dict:
    row = {"id": "A1", "moisture": 41.2, "temp": 22.8, "status": "ok"}
    row.update(overrides)
    return row


# --- timestamp normalisation ----------------------------------------------


def test_iso_z_converts_a_psycopg_datetime():
    value = datetime(2026, 9, 8, 23, 50, tzinfo=timezone.utc)
    assert db.iso_z(value) == "2026-09-08T23:50:00Z"


def test_iso_z_assumes_utc_for_a_naive_datetime():
    assert db.iso_z(datetime(2026, 9, 8, 23, 50)) == "2026-09-08T23:50:00Z"


def test_iso_z_output_survives_the_dashboard_replace():
    """The renderer does .replace('Z', ' UTC'); '+00:00' would not match."""
    rendered = db.iso_z(datetime(2026, 9, 8, 23, 50, tzinfo=timezone.utc))
    assert rendered.endswith("Z")
    assert "+00:00" not in rendered


# --- rendering database rows ----------------------------------------------


def test_render_accepts_database_shaped_rows():
    html_out = dashboard.render_dashboard(
        [_db_detection(), _db_detection(detection_id=2, confidence=0.97)],
        stations=[_db_station(id=f"A{i}") for i in range(1, 10)],
        series=[{"hour": f"{h:02d}:00", "value": 40 + h * 0.1, "samples": 54} for h in range(24)],
        meta={"source": "db", "synthetic": True, "as_of": "2026-09-08T23:50:00Z"},
    )
    assert "PATATERNO" in html_out
    assert "<svg" in html_out
    assert "A1" in html_out


def test_render_survives_null_confidence_and_moisture():
    """Nullable columns must not blow up the page."""
    html_out = dashboard.render_dashboard(
        [_db_detection(confidence=None, frame=None, lat=None, lon=None)],
        stations=[_db_station(moisture=None, temp=None)],
        series=[{"hour": "00:00", "value": None, "samples": 0}],
        meta={"source": "db", "synthetic": True},
    )
    assert "—" in html_out  # the null placeholder


def test_render_handles_decimal_values():
    html_out = dashboard.render_dashboard(
        [_db_detection(confidence=Decimal("0.93"))],
        stations=[_db_station(moisture=Decimal("41.20"))],
        meta={"source": "db", "synthetic": False},
    )
    assert "41" in html_out


# --- provenance -------------------------------------------------------------


# The whole i18n dictionary is serialised into the page for the language
# toggle, so every badge string appears in the source no matter which badge is
# rendered. These assertions therefore look at the rendered element.


def test_synthetic_data_is_labelled_on_the_page():
    html_out = dashboard.render_dashboard(
        [_db_detection()],
        stations=[_db_station()],
        meta={"source": "db", "synthetic": True},
    )
    assert 'class="demo-tag synthetic"' in html_out
    assert 'data-i18n="synthetic"' in html_out
    assert ">DATI SINTETICI · schema dev<" in html_out


def test_field_data_is_labelled_differently():
    html_out = dashboard.render_dashboard(
        [_db_detection(data_source="gateway")],
        stations=[_db_station()],
        meta={"source": "db", "synthetic": False},
    )
    assert 'data-i18n="live"' in html_out
    assert 'data-i18n="synthetic"' not in html_out
    assert 'class="demo-tag synthetic"' not in html_out


def test_mock_rendering_is_unchanged_without_extra_arguments():
    """The demo path must keep working when the database is unreachable."""
    html_out = dashboard.render_dashboard([_db_detection()])
    assert 'data-i18n="demo"' in html_out
    assert 'data-i18n="synthetic"' not in html_out
    assert "ST-01" in html_out  # the mock grid ids


def test_real_timestamp_is_not_inside_an_i18n_element():
    """setLang() overwrites data-i18n content, which would revert a real
    timestamp to the hardcoded mock one on the first language switch."""
    html_out = dashboard.render_dashboard(
        [_db_detection()],
        stations=[_db_station()],
        meta={"source": "db", "synthetic": True, "as_of": "2026-09-08T23:50:00Z"},
    )
    assert "2026-09-08 · 23:50:00 UTC" in html_out
    assert 'data-i18n="updated">2026' not in html_out


# --- safety -----------------------------------------------------------------


def test_database_strings_are_html_escaped():
    """Today these come from the sandbox; tomorrow from the classifier."""
    html_out = dashboard.render_dashboard(
        [_db_detection(frame="<script>alert(1)</script>")],
        stations=[_db_station(id="<b>A1</b>")],
        meta={"source": "db", "synthetic": True},
    )
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


# --- the chart --------------------------------------------------------------


def test_chart_handles_a_single_point_without_dividing_by_zero():
    svg = dashboard._moisture_chart_svg([{"hour": "00:00", "value": 41.0}])
    assert "<svg" in svg


def test_chart_handles_an_empty_series():
    assert "<svg" in dashboard._moisture_chart_svg([])


def test_chart_labels_come_from_the_data_not_the_index():
    """Outages mean bucket N is not hour N."""
    svg = dashboard._moisture_chart_svg(
        [{"hour": "18:00", "value": 40.0}, {"hour": "22:00", "value": 41.0}]
    )
    assert "18:00" in svg and "22:00" in svg


def test_detection_list_is_capped_but_the_count_stays_true():
    """A real flight yields dozens; the page shows a few, the tile shows all."""
    many = [_db_detection(detection_id=i) for i in range(20)]
    html_out = dashboard.render_dashboard(
        many,
        stations=[_db_station()],
        meta={"source": "db", "synthetic": True, "detection_count": len(many)},
    )
    assert html_out.count('class="det-badge"') == 8
    assert ">20<" in html_out  # the alert tile still reports the real total
