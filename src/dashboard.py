"""
Farmer dashboard (mock) for pataterno-app.

Read-only view of the PATATERNO field for the farmer at Petrizzo:
station grid, soil-moisture trend, CPB alerts and treatment advice.
Bilingual (Italian default, English toggle). All data is mocked in
this demo; the production version reads the AGRARIAN PostgreSQL.
"""

import html

# 24 hourly soil-moisture values (%) — field-realistic mock (dawn peak,
# midday dry-down, evening recovery)
MOISTURE_24H = [
    41.8, 42.0, 42.3, 42.6, 42.9, 43.1, 43.0, 42.4,
    41.5, 40.4, 39.2, 38.1, 37.3, 36.8, 36.6, 36.9,
    37.6, 38.5, 39.4, 40.2, 40.8, 41.2, 41.5, 41.7,
]

# 3x3 grid — station-03 deliberately low to show a warning state
STATIONS = [
    {"id": "station-01", "moisture": 41.2, "temp": 22.8, "status": "ok"},
    {"id": "station-02", "moisture": 40.5, "temp": 23.1, "status": "ok"},
    {"id": "station-03", "moisture": 31.4, "temp": 24.0, "status": "low"},
    {"id": "station-04", "moisture": 42.7, "temp": 22.5, "status": "ok"},
    {"id": "station-05", "moisture": 41.9, "temp": 22.9, "status": "ok"},
    {"id": "station-06", "moisture": 40.1, "temp": 23.3, "status": "ok"},
    {"id": "station-07", "moisture": 43.0, "temp": 22.4, "status": "ok"},
    {"id": "station-08", "moisture": 41.6, "temp": 22.7, "status": "ok"},
    {"id": "station-09", "moisture": 39.8, "temp": 23.0, "status": "ok"},
]

# UI strings — Italian default, English toggle (client-side)
I18N = {
    "it": {
        "title": "PATATERNO · Cruscotto campo — Petrizzo",
        "farm": "Azienda Petrizzo — campo patate",
        "demo": "DEMO · DATI SIMULATI",
        "synthetic": "DATI SINTETICI · schema dev",
        "live": "DATI DI CAMPO",
        "updated": "agg. 21 luglio 2026 · 07:40",
        "updated_label": "agg.",
        "avg_of_stations": "media delle stazioni",
        "stations_active": "Stazioni attive",
        "all_online": "✓ tutte online",
        "avg_moisture": "Umidità media suolo",
        "target_range": "obiettivo 35–45%",
        "soil_temp": "Temperatura suolo",
        "avg_of_9": "media delle 9 stazioni",
        "cpb_alert": "Allerta dorifora",
        "to_verify": "⚠ rilevamenti da verificare",
        "chart_title": "Umidità del suolo — ultime 24 ore (media campo)",
        "grid_title": "Griglia stazioni 3×3 — umidità per stazione",
        "st_ok": "✓ regolare",
        "st_low": "⚠ umidità bassa",
        "alerts_title": "Allerte dorifora (volo drone)",
        "cpb_detected": "Dorifora rilevata",
        "confidence": "confidenza",
        "advice_title": "Consiglio operativo",
        "window_label": "Finestra di trattamento:",
        "window_text": "domani 06:00–09:00 — vento previsto < 8 km/h, nessuna pioggia nelle 24 h successive.",
        "inspect_text": "Ispezionare la zona ST-03: umidità sotto soglia (31%). Verificare irrigazione settore nord-est.",
        "table_title": "Ultime letture per stazione (vista tabellare)",
        "th_station": "Stazione",
        "th_moisture": "Umidità %",
        "th_temp": "Temp °C",
        "th_status": "Stato",
        "row_ok": "regolare",
        "row_low": "umidità bassa",
        "chart_aria": "Umidità del suolo, ultime 24 ore",
    },
    "en": {
        "title": "PATATERNO · Field dashboard — Petrizzo",
        "farm": "Petrizzo farm — potato field",
        "demo": "DEMO · MOCK DATA",
        "synthetic": "SYNTHETIC DATA · schema dev",
        "live": "FIELD DATA",
        "updated": "updated 21 July 2026 · 07:40",
        "updated_label": "updated",
        "avg_of_stations": "average of the stations",
        "stations_active": "Active stations",
        "all_online": "✓ all online",
        "avg_moisture": "Avg soil moisture",
        "target_range": "target 35–45%",
        "soil_temp": "Soil temperature",
        "avg_of_9": "average of the 9 stations",
        "cpb_alert": "CPB alert",
        "to_verify": "⚠ detections to verify",
        "chart_title": "Soil moisture — last 24 hours (field average)",
        "grid_title": "3×3 station grid — moisture per station",
        "st_ok": "✓ normal",
        "st_low": "⚠ low moisture",
        "alerts_title": "CPB alerts (drone flight)",
        "cpb_detected": "Colorado beetle detected",
        "confidence": "confidence",
        "advice_title": "Operational advice",
        "window_label": "Treatment window:",
        "window_text": "tomorrow 06:00–09:00 — forecast wind < 8 km/h, no rain in the following 24 h.",
        "inspect_text": "Inspect area ST-03: moisture below threshold (31%). Check irrigation in the north-east sector.",
        "table_title": "Latest readings per station (table view)",
        "th_station": "Station",
        "th_moisture": "Moisture %",
        "th_temp": "Temp °C",
        "th_status": "Status",
        "row_ok": "normal",
        "row_low": "low moisture",
        "chart_aria": "Soil moisture, last 24 hours",
    },
}


def _t(key: str) -> str:
    """Italian default text plus the data-i18n hook for the JS toggle.

    Only ever wrap STATIC text: setLang() overwrites the element's content from
    the JS dictionary, so a dynamic value placed in here reverts to the
    hardcoded string the first time someone switches language.
    """
    return f'<span data-i18n="{key}">{I18N["it"][key]}</span>'


def _fmt(value, digits: int = 0, dash: str = "—") -> str:
    """Format a nullable number. Every numeric column in the schema is
    nullable, and f"{None:.0f}" raises."""
    if value is None:
        return dash
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return dash


def _short_id(station_id) -> str:
    """'station-03' -> 'ST-03'. Database ids such as 'A1' pass through."""
    return html.escape(str(station_id).replace("station-", "ST-"))


def _mock_series() -> list[dict]:
    return [
        {"hour": f"{i:02d}:00", "value": value, "samples": 0}
        for i, value in enumerate(MOISTURE_24H)
    ]


def _moisture_chart_svg(series: list[dict] | None = None) -> str:
    """Single-series line chart of field-average soil moisture.

    Takes whatever buckets it is given: real data has gaps (four deliberate
    outages in the sandbox), so neither the count nor a fixed y-range can be
    assumed, and the x labels come from the data rather than the index.
    """
    points = [p for p in (series or _mock_series()) if p.get("value") is not None]
    w, h = 720, 190
    pad_l, pad_r, pad_t, pad_b = 44, 16, 14, 26

    if len(points) < 2:
        return (
            f'<svg viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{I18N["it"]["chart_aria"]}">'
            f'<text x="{w / 2}" y="{h / 2}" text-anchor="middle" class="tick">—</text></svg>'
        )

    values = [float(p["value"]) for p in points]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.25, 1.0)
    y_min, y_max = lo - pad, hi + pad
    n = len(points)

    def x(i: float) -> float:
        return pad_l + i * (w - pad_l - pad_r) / (n - 1)

    def y(v: float) -> float:
        return pad_t + (y_max - v) * (h - pad_t - pad_b) / (y_max - y_min)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))

    ticks = [y_min + (y_max - y_min) * f for f in (0.0, 0.33, 0.66, 1.0)]
    grid = "".join(
        f'<line x1="{pad_l}" y1="{y(v):.1f}" x2="{w - pad_r}" y2="{y(v):.1f}" '
        f'class="grid"/><text x="{pad_l - 6}" y="{y(v) + 3.5:.1f}" class="tick" '
        f'text-anchor="end">{v:.0f}%</text>'
        for v in ticks
    )

    step = max(1, (n - 1) // 4)
    label_at = sorted({0, *range(step, n - 1, step), n - 1})
    hours = "".join(
        f'<text x="{x(i):.1f}" y="{h - 8}" class="tick" text-anchor="middle">'
        f'{html.escape(str(points[i].get("hour", i)))}</text>'
        for i in label_at
    )

    # invisible wide hit-targets for the hover tooltip
    hits = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="12" class="hit" '
        f'data-h="{html.escape(str(points[i].get("hour", i)))}" data-v="{v:.1f}"/>'
        for i, v in enumerate(values)
    )
    last = values[-1]
    return f"""
<svg viewBox="0 0 {w} {h}" role="img" aria-label="{I18N['it']['chart_aria']}">
  {grid}{hours}
  <line x1="{pad_l}" y1="{h - pad_b}" x2="{w - pad_r}" y2="{h - pad_b}" class="axis"/>
  <polyline points="{pts}" class="series"/>
  <circle cx="{x(n - 1):.1f}" cy="{y(last):.1f}" r="4" class="series-dot"/>
  <text x="{x(n - 1) - 8:.1f}" y="{y(last) - 8:.1f}" class="dlabel" text-anchor="end">{last:.1f}%</text>
  <line id="xh" y1="{pad_t}" y2="{h - pad_b}" class="crosshair" visibility="hidden"/>
  {hits}
</svg>"""


def _rows_from_readings(readings: list[dict]) -> list[dict]:
    """Map database readings onto the shape the template renders.

    A station counts as 'low' below 35% moisture, matching the mock's own
    threshold (station-03 sits at 31.4%).
    """
    rows = []
    for reading in readings:
        moisture = reading.get("moisture_pct")
        rows.append(
            {
                "id": reading.get("station_id"),
                "moisture": moisture,
                "temp": reading.get("temperature_c"),
                "status": "low" if (moisture is not None and moisture < 35) else "ok",
            }
        )
    return rows


def render_dashboard(
    detections: list[dict],
    stations: list[dict] | None = None,
    series: list[dict] | None = None,
    meta: dict | None = None,
) -> str:
    """Render the farmer dashboard.

    Called with only `detections` it reproduces the original mock page, so the
    demo keeps working when the database is unreachable. `stations` and
    `series` carry real rows; `meta` says where the data came from, which
    decides the provenance badge and the timestamp.
    """
    import json

    meta = meta or {}
    source = meta.get("source", "memory")
    # A real flight yields dozens of detections; listing them all pushes the
    # rest of the page off screen. The tile keeps the true total.
    detection_total = meta.get("detection_count", len(detections))
    detections = detections[: meta.get("detection_limit", 8)]
    rows = stations if stations is not None else STATIONS
    n_online = len(rows)
    n_total = meta.get("expected_stations", 9 if source == "memory" else n_online)

    moistures = [r["moisture"] for r in rows if r.get("moisture") is not None]
    temps = [r["temp"] for r in rows if r.get("temp") is not None]
    avg_m = sum(moistures) / len(moistures) if moistures else None
    avg_t = sum(temps) / len(temps) if temps else None

    # Provenance badge: synthetic sandbox data must never be mistaken for field
    # data in a screenshot.
    if source == "db":
        badge_key = "synthetic" if meta.get("synthetic") else "live"
        badge_class = "demo-tag synthetic" if meta.get("synthetic") else "demo-tag live"
    else:
        badge_key, badge_class = "demo", "demo-tag"

    as_of = meta.get("as_of")
    if as_of:
        # Rendered OUTSIDE any data-i18n element, or setLang() would replace
        # this real timestamp with the hardcoded mock one.
        when = (
            f"{_t('updated_label')} "
            f"<span>{html.escape(str(as_of).replace('T', ' · ').replace('Z', ' UTC'))}</span>"
        )
    else:
        when = _t("updated")

    cells = "".join(
        f"""<div class="cell {s['status']}">
  <div class="cell-id">{_short_id(s['id'])}</div>
  <div class="cell-val">{_fmt(s['moisture'])}%</div>
  <div class="cell-status">{_t('st_low') if s['status'] == 'low' else _t('st_ok')}</div>
</div>"""
        for s in rows
    )

    det_rows = "".join(
        f"""<li>
  <span class="det-badge">🪲</span>
  <div><strong>{_t('cpb_detected')}</strong> — {_t('confidence')} {_fmt((d.get('confidence') or 0) * 100)}%
  <div class="det-meta">{html.escape(str(d.get('captured_at', '')).replace('T', ' · ').replace('Z', ' UTC'))} · {_fmt(d.get('lat'), 2)}N {_fmt(d.get('lon'), 2)}E · {html.escape(str(d.get('frame', '')))}</div></div>
</li>"""
        for d in detections
    )

    table_rows = "".join(
        f"<tr><td>{html.escape(str(s['id']))}</td><td>{_fmt(s['moisture'], 1)}</td>"
        f"<td>{_fmt(s['temp'], 1)}</td>"
        f"<td>{_t('row_low') if s['status'] == 'low' else _t('row_ok')}</td></tr>"
        for s in rows
    )

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{I18N['it']['title']}</title>
<style>
  :root {{
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
    --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series: #2a78d6; --good: #0ca30c; --warn: #fab219; --serious: #ec835a;
    --brand: #1b6b35;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
      --muted: #898781; --grid: #2c2c2a; --axis: #383835;
      --border: rgba(255,255,255,0.10);
      --series: #3987e5; --brand: #4caf6d;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--page); color: var(--ink);
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 20px; }}
  .wrap {{ max-width: 980px; margin: 0 auto; display: grid; gap: 14px; }}
  header {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
  header h1 {{ font-size: 20px; }} header h1 span.brand {{ color: var(--brand); }}
  .demo-tag {{ font-size: 11px; letter-spacing: .06em; border: 1px solid var(--border);
    border-radius: 99px; padding: 2px 10px; color: var(--ink-2); }}
  /* Synthetic data must be unmissable in a screenshot. */
  .demo-tag.synthetic {{ border-color: var(--warn); color: var(--warn); font-weight: 700;
    background: repeating-linear-gradient(45deg, transparent, transparent 5px,
      color-mix(in srgb, var(--warn) 14%, transparent) 5px,
      color-mix(in srgb, var(--warn) 14%, transparent) 10px); }}
  .demo-tag.live {{ border-color: var(--good); color: var(--good); font-weight: 700; }}
  header .right {{ margin-left: auto; display: flex; gap: 10px; align-items: center; }}
  header .when {{ color: var(--muted); font-size: 13px; }}
  .lang {{ display: inline-flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }}
  .lang button {{ border: 0; background: transparent; color: var(--ink-2); font: 600 12px/1 inherit;
    font-family: inherit; padding: 6px 10px; cursor: pointer; }}
  .lang button.active {{ background: var(--brand); color: #fff; }}
  .card {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; }}
  .card h2 {{ font-size: 13px; font-weight: 600; color: var(--ink-2); margin-bottom: 10px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }}
  .tile .label {{ font-size: 12px; color: var(--ink-2); }}
  .tile .value {{ font-size: 30px; font-weight: 650; margin-top: 2px; }}
  .tile .sub {{ font-size: 12px; color: var(--muted); }}
  .status-line {{ display: inline-flex; gap: 6px; align-items: center; font-size: 13px; font-weight: 600; }}
  .status-line.good  {{ color: var(--good); }}
  .status-line.warn  {{ color: var(--serious); }}
  .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
  .cell {{ border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }}
  .cell-id {{ font-size: 11px; color: var(--muted); }}
  .cell-val {{ font-size: 20px; font-weight: 650; }}
  .cell-status {{ font-size: 11.5px; color: var(--ink-2); }}
  .cell.low {{ border-color: var(--warn); box-shadow: inset 3px 0 0 var(--warn); }}
  .cell.low .cell-status {{ font-weight: 600; }}
  .cols {{ display: grid; grid-template-columns: 1.15fr .85fr; gap: 14px; }}
  @media (max-width: 760px) {{ .cols {{ grid-template-columns: 1fr; }} }}
  svg {{ width: 100%; height: auto; display: block; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .axis {{ stroke: var(--axis); stroke-width: 1; }}
  .tick {{ fill: var(--muted); font-size: 10.5px; }}
  .series {{ fill: none; stroke: var(--series); stroke-width: 2;
    stroke-linejoin: round; stroke-linecap: round; }}
  .series-dot {{ fill: var(--series); }}
  .dlabel {{ fill: var(--ink-2); font-size: 11px; font-weight: 600; }}
  .crosshair {{ stroke: var(--axis); stroke-dasharray: 3 3; }}
  .hit {{ fill: transparent; cursor: crosshair; }}
  #tip {{ position: fixed; pointer-events: none; background: var(--surface);
    border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px;
    font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.12); visibility: hidden; }}
  ul.dets {{ list-style: none; display: grid; gap: 10px; }}
  ul.dets li {{ display: flex; gap: 10px; align-items: flex-start; }}
  .det-badge {{ font-size: 18px; }}
  .det-meta {{ font-size: 12px; color: var(--muted); }}
  .advice {{ border-left: 3px solid var(--good); padding-left: 12px; }}
  .advice strong {{ color: var(--brand); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--grid);
    font-variant-numeric: tabular-nums; }}
  th {{ color: var(--ink-2); font-weight: 600; }}
  footer {{ color: var(--muted); font-size: 12px; text-align: center; padding: 6px 0 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="brand">PATATERNO</span> · {_t('farm')}</h1>
    <span class="{badge_class}">{_t(badge_key)}</span>
    <span class="right">
      <span class="when">{when}</span>
      <span class="lang" role="group" aria-label="Lingua / Language">
        <button id="btn-it" class="active" onclick="setLang('it')">IT</button>
        <button id="btn-en" onclick="setLang('en')">EN</button>
      </span>
    </span>
  </header>

  <div class="tiles">
    <div class="card tile">
      <div class="label">{_t('stations_active')}</div>
      <div class="value">{n_online} / {n_total}</div>
      <div class="status-line good">{_t('all_online')}</div>
    </div>
    <div class="card tile">
      <div class="label">{_t('avg_moisture')}</div>
      <div class="value">{_fmt(avg_m, 1)}%</div>
      <div class="sub">{_t('target_range')}</div>
    </div>
    <div class="card tile">
      <div class="label">{_t('soil_temp')}</div>
      <div class="value">{_fmt(avg_t, 1)}°C</div>
      <div class="sub">{_t('avg_of_9') if n_total == 9 else _t('avg_of_stations')}</div>
    </div>
    <div class="card tile">
      <div class="label">{_t('cpb_alert')}</div>
      <div class="value">{detection_total}</div>
      <div class="status-line warn">{_t('to_verify')}</div>
    </div>
  </div>

  <div class="card">
    <h2>{_t('chart_title')}</h2>
    {_moisture_chart_svg(series)}
  </div>

  <div class="cols">
    <div class="card">
      <h2>{_t('grid_title')}</h2>
      <div class="grid3">{cells}</div>
    </div>
    <div style="display:grid; gap:14px; align-content:start;">
      <div class="card">
        <h2>{_t('alerts_title')}</h2>
        <ul class="dets">{det_rows}</ul>
      </div>
      <div class="card advice">
        <h2>{_t('advice_title')}</h2>
        <p><strong>{_t('window_label')}</strong> {_t('window_text')}</p>
        <p class="det-meta" style="margin-top:6px">{_t('inspect_text')}</p>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>{_t('table_title')}</h2>
    <table>
      <thead><tr><th>{_t('th_station')}</th><th>{_t('th_moisture')}</th><th>{_t('th_temp')}</th><th>{_t('th_status')}</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>

  <footer>PATATERNO · AGRARIAN Open Call 2 · Testbed 2 — Buontech Solutions srl</footer>
</div>
<div id="tip"></div>
<script>
  const I18N = {json.dumps(I18N, ensure_ascii=False)};
  function setLang(lang) {{
    document.documentElement.lang = lang;
    document.title = I18N[lang].title;
    document.querySelectorAll('[data-i18n]').forEach(el => {{
      const s = I18N[lang][el.dataset.i18n];
      if (s !== undefined) el.textContent = s;
    }});
    document.getElementById('btn-it').classList.toggle('active', lang === 'it');
    document.getElementById('btn-en').classList.toggle('active', lang === 'en');
  }}
  const tip = document.getElementById('tip');
  const xh = document.getElementById('xh');
  document.querySelectorAll('.hit').forEach(c => {{
    c.addEventListener('mousemove', e => {{
      tip.textContent = c.dataset.h + ' — ' + c.dataset.v + '%';
      tip.style.left = (e.clientX + 12) + 'px';
      tip.style.top = (e.clientY - 10) + 'px';
      tip.style.visibility = 'visible';
      xh.setAttribute('x1', c.getAttribute('cx'));
      xh.setAttribute('x2', c.getAttribute('cx'));
      xh.setAttribute('visibility', 'visible');
    }});
    c.addEventListener('mouseleave', () => {{
      tip.style.visibility = 'hidden';
      xh.setAttribute('visibility', 'hidden');
    }});
  }});
</script>
</body>
</html>"""
