from __future__ import annotations

import json
import sys
from collections import Counter
from html import escape
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.project_state import load_paper_index, recommend_next_route, sync_project_state


DEFAULT_OUTPUT = Path("output") / "paper-workbench.html"
HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Workbench</title>
  <style>
    :root {
      --text: #eef2ff;
      --muted: #a9b3d1;
      --line: #2c3969;
      --accent: #6ea8fe;
      --accent-2: #7ef0c7;
      --warn: #ffd166;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: linear-gradient(180deg, #0b1020, #0f1730 45%, #0b1020); color: var(--text); }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 24px; }
    .hero { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 16px; }
    .panel { background: rgba(18, 25, 51, 0.92); border: 1px solid var(--line); border-radius: 18px; padding: 18px; box-shadow: 0 18px 60px rgba(0, 0, 0, 0.2); }
    .eyebrow { color: var(--accent-2); text-transform: uppercase; letter-spacing: .08em; font-size: 12px; margin-bottom: 8px; }
    .subtle { color: var(--muted); }
    .route { display: inline-flex; align-items: center; gap: 8px; background: rgba(110, 168, 254, 0.12); color: var(--accent); border: 1px solid rgba(110, 168, 254, 0.35); padding: 8px 12px; border-radius: 999px; font-weight: 600; }
    .cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px; }
    .card-value { font-size: 30px; font-weight: 700; margin-top: 8px; }
    .grid { display: grid; grid-template-columns: 1.15fr .85fr; gap: 16px; margin-bottom: 16px; }
    .list, .stage-list { display: grid; gap: 10px; }
    .list-item, .stage-item { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05); }
    .badge { padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
    .completed { background: rgba(126, 240, 199, .14); color: var(--accent-2); }
    .in_progress { background: rgba(255, 209, 102, .12); color: var(--warn); }
    .pending { background: rgba(169, 179, 209, .14); color: var(--muted); }
    .toolbar { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 12px; margin-bottom: 12px; }
    input, select { width: 100%; background: #0d1430; color: var(--text); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.06); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; font-size: 13px; }
    td small { color: var(--muted); }
    .title-cell { min-width: 280px; }
    .pill { display: inline-block; padding: 4px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); color: var(--muted); font-size: 12px; margin-right: 6px; margin-top: 6px; }
    .footer { color: var(--muted); margin-top: 12px; font-size: 13px; }
    @media (max-width: 980px) { .hero, .cards, .grid, .toolbar { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="panel">
        <div class="eyebrow">Research Control Center</div>
        <h1>Paper Workbench</h1>
        <p class="subtle" id="stateSummary"></p>
        <div class="route" id="routeLabel"></div>
        <div class="footer" id="routeReason"></div>
      </div>
      <div class="panel">
        <div class="eyebrow">Current Focus</div>
        <h2>__TITLE__</h2>
        <p class="subtle" id="lastSearch"></p>
      </div>
    </section>

    <section class="cards">
      <div class="panel"><div class="eyebrow">Indexed Papers</div><div class="card-value" id="paperCount"></div></div>
      <div class="panel"><div class="eyebrow">Verified</div><div class="card-value" id="verifiedCount"></div></div>
      <div class="panel"><div class="eyebrow">Downloaded</div><div class="card-value" id="downloadedCount"></div></div>
      <div class="panel"><div class="eyebrow">Current Stage</div><div class="card-value" id="currentStage"></div></div>
    </section>

    <section class="grid">
      <div class="panel"><div class="eyebrow">Pipeline Status</div><div class="stage-list" id="stageList"></div></div>
      <div class="panel"><div class="eyebrow">Coverage</div><div class="list" id="sourceList"></div></div>
    </section>

    <section class="grid">
      <div class="panel"><div class="eyebrow">Top Records</div><div class="list" id="topPapers"></div></div>
      <div class="panel"><div class="eyebrow">Disciplines</div><div class="list" id="disciplineList"></div><div class="eyebrow" style="margin-top:18px;">Years</div><div class="list" id="yearList"></div></div>
    </section>

    <section class="panel">
      <div class="eyebrow">Index Browser</div>
      <div class="toolbar">
        <input id="searchInput" placeholder="Search title, author, venue, DOI">
        <select id="sourceFilter"><option value="">All sources</option></select>
        <select id="disciplineFilter"><option value="">All disciplines</option></select>
        <select id="statusFilter"><option value="">Any status</option><option value="verified">Verified</option><option value="downloaded">Downloaded</option></select>
      </div>
      <table>
        <thead><tr><th>Paper</th><th>Year</th><th>Source</th><th>Status</th><th>Links</th></tr></thead>
        <tbody id="paperRows"></tbody>
      </table>
      <div class="footer" id="rowCount"></div>
    </section>
  </div>

  <script>
    const payload = __PAYLOAD_JSON__;
    const papers = payload.papers || [];
    const setText = (id, value) => document.getElementById(id).textContent = value;
    const stageState = payload.state.stage_status || {};
    const sourceCounts = payload.summary.source_counts || {};
    const disciplineCounts = payload.summary.discipline_counts || {};
    const yearCounts = payload.summary.year_counts || {};

    setText('stateSummary', payload.state.summary || '');
    setText('paperCount', String(payload.summary.paper_count || 0));
    setText('verifiedCount', String(payload.summary.verified_count || 0));
    setText('downloadedCount', String(payload.summary.downloaded_count || 0));
    setText('currentStage', payload.state.current_stage || 'focus');
    setText('routeLabel', `${payload.route.recommended_route} · ${payload.route.route_label || ''}`.trim());
    setText('routeReason', (payload.route.rationale || []).join(' '));

    const lastSearch = payload.state.last_search
      ? `Last search: ${payload.state.last_search.query} · ${payload.state.last_search.discipline}`
      : 'No persisted search yet. Use `python tools/unified_search.py "topic" --save` to seed the workbench.';
    setText('lastSearch', lastSearch);

    const stageList = document.getElementById('stageList');
    Object.entries(stageState).forEach(([stage, status]) => {
      const row = document.createElement('div');
      row.className = 'stage-item';
      row.innerHTML = `<span>${stage}</span><span class="badge ${status}">${status.replace('_', ' ')}</span>`;
      stageList.appendChild(row);
    });

    const renderCountList = (targetId, items) => {
      const target = document.getElementById(targetId);
      Object.entries(items).forEach(([label, count]) => {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `<span>${label}</span><strong>${count}</strong>`;
        target.appendChild(row);
      });
      if (!Object.keys(items).length) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = '<span class="subtle">No data yet</span><strong>0</strong>';
        target.appendChild(row);
      }
    };

    renderCountList('sourceList', sourceCounts);
    renderCountList('disciplineList', disciplineCounts);
    renderCountList('yearList', yearCounts);

    const topPapers = document.getElementById('topPapers');
    (payload.summary.top_papers || []).forEach((paper) => {
      const row = document.createElement('div');
      row.className = 'list-item';
      row.style.display = 'block';
      row.innerHTML = `<div><strong>${paper.title || 'Untitled'}</strong></div><div class="subtle">${(paper.authors || []).slice(0, 3).join(', ') || 'Unknown authors'}</div><div><span class="pill">${paper.year || 'n/a'}</span><span class="pill">${paper.source || 'unknown'}</span><span class="pill">citations: ${paper.citation_count || 0}</span></div>`;
      topPapers.appendChild(row);
    });
    if (!(payload.summary.top_papers || []).length) {
      topPapers.innerHTML = '<div class="list-item"><span class="subtle">No indexed papers yet</span></div>';
    }

    const sourceFilter = document.getElementById('sourceFilter');
    const disciplineFilter = document.getElementById('disciplineFilter');
    Object.keys(sourceCounts).forEach((source) => sourceFilter.insertAdjacentHTML('beforeend', `<option value="${source}">${source}</option>`));
    Object.keys(disciplineCounts).forEach((discipline) => disciplineFilter.insertAdjacentHTML('beforeend', `<option value="${discipline}">${discipline}</option>`));

    const rowCount = document.getElementById('rowCount');
    const paperRows = document.getElementById('paperRows');
    const searchInput = document.getElementById('searchInput');
    const statusFilter = document.getElementById('statusFilter');

    function renderRows() {
      const query = searchInput.value.trim().toLowerCase();
      const source = sourceFilter.value;
      const discipline = disciplineFilter.value;
      const status = statusFilter.value;

      const filtered = papers.filter((paper) => {
        const haystack = [paper.title, ...(paper.authors || []), paper.venue, paper.doi].join(' ').toLowerCase();
        const matchesQuery = !query || haystack.includes(query);
        const matchesSource = !source || paper.source === source;
        const matchesDiscipline = !discipline || paper.discipline === discipline;
        const matchesStatus = !status || (status === 'verified' ? paper.verified : (paper.downloaded || paper.local_path));
        return matchesQuery && matchesSource && matchesDiscipline && matchesStatus;
      });

      paperRows.innerHTML = filtered.map((paper) => {
        const links = [];
        if (paper.url) links.push(`<a href="${paper.url}" target="_blank" rel="noreferrer">source</a>`);
        if (paper.pdf_url) links.push(`<a href="${paper.pdf_url}" target="_blank" rel="noreferrer">pdf</a>`);
        if (paper.local_path) links.push(`<code>${paper.local_path}</code>`);
        const statusBadges = [];
        if (paper.verified) statusBadges.push('<span class="pill">verified</span>');
        if (paper.downloaded || paper.local_path) statusBadges.push('<span class="pill">downloaded</span>');
        return `<tr><td class="title-cell"><strong>${paper.title || 'Untitled'}</strong><br><small>${(paper.authors || []).join(', ') || 'Unknown authors'}</small><br><span class="pill">${paper.venue || 'no venue'}</span><span class="pill">${paper.discipline || 'generic'}</span><span class="pill">${paper.doi || paper.record_id}</span></td><td>${paper.year || 'n/a'}</td><td>${paper.source || 'unknown'}</td><td>${statusBadges.join('') || '<span class="pill">indexed</span>'}</td><td>${links.join(' · ') || '<span class="subtle">No links</span>'}</td></tr>`;
      }).join('');
      rowCount.textContent = `${filtered.length} visible record(s) · ${papers.length} total indexed`;
    }

    [searchInput, sourceFilter, disciplineFilter, statusFilter].forEach((element) => element.addEventListener('input', renderRows));
    [sourceFilter, disciplineFilter, statusFilter].forEach((element) => element.addEventListener('change', renderRows));
    renderRows();
  </script>
</body>
</html>
"""


def _counter_dict(values: list[str]) -> dict[str, int]:
    counter = Counter(value for value in values if value)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def build_dashboard_payload(project_root: str | Path = ".") -> dict:
    state = sync_project_state(project_root)
    papers = load_paper_index(project_root)
    route = recommend_next_route(project_root=project_root)

    source_counts = _counter_dict([paper.get("source", "") for paper in papers])
    discipline_counts = _counter_dict([paper.get("discipline", "") for paper in papers])
    year_counts = _counter_dict([str(paper.get("year", "")) for paper in papers if paper.get("year")])
    verified_count = sum(1 for paper in papers if paper.get("verified"))
    downloaded_count = sum(1 for paper in papers if paper.get("downloaded") or paper.get("local_path"))

    top_papers = sorted(
        papers,
        key=lambda paper: (
            int(bool(paper.get("verified"))),
            int(bool(paper.get("downloaded") or paper.get("local_path"))),
            int(paper.get("citation_count") or 0),
            int(paper.get("year") or 0),
        ),
        reverse=True,
    )[:10]

    return {
        "state": state,
        "route": route,
        "papers": papers,
        "summary": {
            "paper_count": len(papers),
            "verified_count": verified_count,
            "downloaded_count": downloaded_count,
            "source_counts": source_counts,
            "discipline_counts": discipline_counts,
            "year_counts": year_counts,
            "top_papers": top_papers,
        },
    }


def render_dashboard_html(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    title = escape(payload["route"]["route_label"] or "Paper Workbench")
    return HTML_TEMPLATE.replace("__PAYLOAD_JSON__", payload_json).replace("__TITLE__", title)


def build_dashboard(project_root: str | Path = ".", output_path: str | Path | None = None) -> Path:
    root = Path(project_root)
    destination = Path(output_path) if output_path else root / DEFAULT_OUTPUT
    destination.parent.mkdir(parents=True, exist_ok=True)
    html = render_dashboard_html(build_dashboard_payload(root))
    destination.write_text(html, encoding="utf-8")
    return destination


def _main(args: list[str]) -> int:
    command = args[0] if args else "build"
    project_root = args[1] if len(args) > 1 else "."
    output_path = args[2] if len(args) > 2 else None

    if command == "build":
        destination = build_dashboard(project_root=project_root, output_path=output_path)
        print(destination)
        return 0

    if command == "payload":
        print(json.dumps(build_dashboard_payload(project_root), ensure_ascii=False, indent=2))
        return 0

    print("Usage: python tools/paper_dashboard.py [build|payload] [project_root] [output_path]")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
