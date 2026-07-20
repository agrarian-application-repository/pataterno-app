"""
Farmer dashboard (mock) for pataterno-demo-app.

Read-only view of the PATATERNO field for the farmer at Petrizzo:
station grid, soil-moisture trend, CPB alerts and treatment advice.
All data is mocked in this demo; the production version reads the
AGRARIAN PostgreSQL. Italian UI — the end user is the local farmer.
"""

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


def _moisture_chart_svg() -> str:
    """Single-series line chart of the last 24 h of average soil moisture."""
    w, h = 720, 190
    pad_l, pad_r, pad_t, pad_b = 44, 16, 14, 26
    y_min, y_max = 30.0, 46.0
    n = len(MOISTURE_24H)

    def x(i: float) -> float:
        return pad_l + i * (w - pad_l - pad_r) / (n - 1)

    def y(v: float) -> float:
        return pad_t + (y_max - v) * (h - pad_t - pad_b) / (y_max - y_min)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(MOISTURE_24H))

    grid = "".join(
        f'<line x1="{pad_l}" y1="{y(v):.1f}" x2="{w - pad_r}" y2="{y(v):.1f}" '
        f'class="grid"/><text x="{pad_l - 6}" y="{y(v) + 3.5:.1f}" class="tick" '
        f'text-anchor="end">{v:.0f}%</text>'
        for v in (30, 35, 40, 45)
    )
    hours = "".join(
        f'<text x="{x(i):.1f}" y="{h - 8}" class="tick" text-anchor="middle">{i:02d}</text>'
        for i in (0, 6, 12, 18, 23)
    )
    # invisible wide hit-targets for the hover tooltip
    hits = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="12" class="hit" '
        f'data-h="{i:02d}:00" data-v="{v:.1f}"/>'
        for i, v in enumerate(MOISTURE_24H)
    )
    last = MOISTURE_24H[-1]
    return f"""
<svg viewBox="0 0 {w} {h}" role="img" aria-label="Umidita del suolo, ultime 24 ore">
  {grid}{hours}
  <line x1="{pad_l}" y1="{h - pad_b}" x2="{w - pad_r}" y2="{h - pad_b}" class="axis"/>
  <polyline points="{pts}" class="series"/>
  <circle cx="{x(n - 1):.1f}" cy="{y(last):.1f}" r="4" class="series-dot"/>
  <text x="{x(n - 1) - 8:.1f}" y="{y(last) - 8:.1f}" class="dlabel" text-anchor="end">{last:.1f}%</text>
  <line id="xh" y1="{pad_t}" y2="{h - pad_b}" class="crosshair" visibility="hidden"/>
  {hits}
</svg>"""


def render_dashboard(detections: list[dict]) -> str:
    n_online = len(STATIONS)
    avg_m = sum(s["moisture"] for s in STATIONS) / n_online
    avg_t = sum(s["temp"] for s in STATIONS) / n_online
    low = [s for s in STATIONS if s["status"] == "low"]

    cells = "".join(
        f"""<div class="cell {s['status']}">
  <div class="cell-id">{s['id'].replace('station-', 'ST-')}</div>
  <div class="cell-val">{s['moisture']:.0f}%</div>
  <div class="cell-status">{'⚠ umidità bassa' if s['status'] == 'low' else '✓ regolare'}</div>
</div>"""
        for s in STATIONS
    )

    det_rows = "".join(
        f"""<li>
  <span class="det-badge">🪲</span>
  <div><strong>Dorifora rilevata</strong> — confidenza {d['confidence'] * 100:.0f}%
  <div class="det-meta">{d['captured_at'].replace('T', ' · ').replace('Z', ' UTC')} · {d['lat']}N {d['lon']}E · {d['frame']}</div></div>
</li>"""
        for d in detections
    )

    table_rows = "".join(
        f"<tr><td>{s['id']}</td><td>{s['moisture']:.1f}</td><td>{s['temp']:.1f}</td>"
        f"<td>{'umidità bassa' if s['status'] == 'low' else 'regolare'}</td></tr>"
        for s in STATIONS
    )

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PATATERNO · Cruscotto campo — Petrizzo</title>
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
  header h1 {{ font-size: 20px; }} header h1 span {{ color: var(--brand); }}
  .demo-tag {{ font-size: 11px; letter-spacing: .06em; border: 1px solid var(--border);
    border-radius: 99px; padding: 2px 10px; color: var(--ink-2); }}
  header .when {{ margin-left: auto; color: var(--muted); font-size: 13px; }}
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
    <h1><span>PATATERNO</span> · Azienda Petrizzo — campo patate</h1>
    <span class="demo-tag">DEMO · DATI SIMULATI</span>
    <span class="when">agg. 21 luglio 2026 · 07:40</span>
  </header>

  <div class="tiles">
    <div class="card tile">
      <div class="label">Stazioni attive</div>
      <div class="value">{n_online} / 9</div>
      <div class="status-line good">✓ tutte online</div>
    </div>
    <div class="card tile">
      <div class="label">Umidità media suolo</div>
      <div class="value">{avg_m:.1f}%</div>
      <div class="sub">obiettivo 35–45%</div>
    </div>
    <div class="card tile">
      <div class="label">Temperatura suolo</div>
      <div class="value">{avg_t:.1f}°C</div>
      <div class="sub">media delle 9 stazioni</div>
    </div>
    <div class="card tile">
      <div class="label">Allerta dorifora</div>
      <div class="value">{len(detections)}</div>
      <div class="status-line warn">⚠ rilevamenti da verificare</div>
    </div>
  </div>

  <div class="card">
    <h2>Umidità del suolo — ultime 24 ore (media campo)</h2>
    {_moisture_chart_svg()}
  </div>

  <div class="cols">
    <div class="card">
      <h2>Griglia stazioni 3×3 — umidità per stazione</h2>
      <div class="grid3">{cells}</div>
    </div>
    <div style="display:grid; gap:14px; align-content:start;">
      <div class="card">
        <h2>Allerte dorifora (volo drone)</h2>
        <ul class="dets">{det_rows}</ul>
      </div>
      <div class="card advice">
        <h2>Consiglio operativo</h2>
        <p><strong>Finestra di trattamento:</strong> domani 06:00–09:00 —
        vento previsto &lt; 8 km/h, nessuna pioggia nelle 24 h successive.</p>
        <p class="det-meta" style="margin-top:6px">Ispezionare la zona ST-03:
        umidità sotto soglia (31%). Verificare irrigazione settore nord-est.</p>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Ultime letture per stazione (vista tabellare)</h2>
    <table>
      <thead><tr><th>Stazione</th><th>Umidità %</th><th>Temp °C</th><th>Stato</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>

  <footer>PATATERNO · AGRARIAN Open Call 2 · Testbed 2 — Buontech Solutions srl</footer>
</div>
<div id="tip"></div>
<script>
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
