/**
 * AUDP Lab — application entry point.
 *
 * Responsibilities: hash router, theme switching, and rendering each view.
 * All dynamic content is built with DOM APIs / textContent (never innerHTML),
 * so data files and API responses cannot inject markup.
 */

import { compare, fmt, loadJson, pairByExperiment } from './data.js';
import { groupedBars } from './charts.js';

/** Where the optional live backend lives. Empty string = same origin (local server). */
const API_BASE = window.AUDP_API_BASE ?? '';
const VIEWS = ['overview', 'experiments', 'protocol', 'run', 'credits'];
const $ = (id) => document.getElementById(id);

/**
 * Tiny element factory: h('div', {class: 'x'}, 'text', childNode).
 * @param {string} tag
 * @param {Record<string,string>} [attrs]
 * @param {...(string|Node)} children
 */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

/* ------------------------------------------------------------------ theme */

function initTheme() {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem('audp-theme'); } catch { /* storage may be blocked */ }
  const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  root.dataset.theme = saved ?? (prefersLight ? 'light' : 'dark');
  $('theme').addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('audp-theme', next); } catch { /* ignore */ }
  });
}

/* ----------------------------------------------------------------- router */

function route() {
  const name = location.hash.replace(/^#\//, '');
  const view = VIEWS.includes(name) ? name : 'overview';
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
  document.querySelectorAll('.tabs a').forEach((a) => {
    if (a.dataset.view === view) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  window.scrollTo({ top: 0 });
  if (view === 'run') refreshHistory();
}

/* --------------------------------------------------------------- overview */

function renderOverview(team, pairs, meta) {
  $('course').textContent = `${team.project.course} · ${team.project.track}`;
  $('claim').textContent = `Claim under test: “${team.project.claim}”`;

  const allOk = pairs.every((p) => p.fixed.integrity_verified && p.adaptive.integrity_verified);
  const retx = (mode) => pairs.reduce((n, p) => n + p[mode].retransmissions, 0);
  const kpis = [
    ['Conditions', String(pairs.length), 'fixed vs. adaptive pairs'],
    ['Repeats', `${meta.repeats} × ${meta.size_kb} KB`, `seeds ${meta.seeds[0]}–${meta.seeds.at(-1)}, same seeds in both modes`],
    ['Integrity', allOk ? 'All pass' : 'Failures', 'SHA-256 match on every run'],
    ['Mean retransmissions', `${fmt(retx('fixed'), 1)} / ${fmt(retx('adaptive'), 1)}`, 'fixed / adaptive, summed over conditions'],
  ];
  $('kpis').replaceChildren(...kpis.map(([label, value, note]) =>
    h('div', { class: 'kpi' }, h('small', {}, label), h('b', {}, value), h('span', {}, note))));

  renderConditions(pairs, compare(pairs).detail);

  $('legend').replaceChildren(
    h('span', { style: '--c: var(--fixed)' }, 'Fixed rate'),
    h('span', { style: '--c: var(--adaptive)' }, 'Adaptive'),
  );
  chartFor('chart-goodput', pairs, 'goodput_kbps');

  $('limits').textContent =
    `Bars show the mean of ${meta.repeats} seeded runs; whiskers are one standard deviation. ` +
    'Both modes see identical loss patterns for each seed. All transfers run through the project’s own ' +
    'emulator on one machine, so absolute numbers depend on that machine.';
}

/**
 * One card per tested condition: the emulator settings and how adaptive goodput
 * compares with fixed-rate (colour-coded chip with the percentage change).
 */
function renderConditions(pairs, detail) {
  const sign = (n) => `${n > 0 ? '+' : ''}${fmt(n * 100, 0)}%`;
  $('conditions').replaceChildren(...pairs.map((p, i) => {
    const d = detail[i];
    const [code, ...rest] = p.name.split(/\s[—-]\s/);
    return h('article', { class: 'cond' },
      h('header', {}, h('b', {}, code), h('span', { class: `chip ${d.outcome}` }, `${sign(d.change)} goodput`)),
      h('p', { class: 'cond-name' }, rest.join(' ')),
      h('dl', {},
        h('dt', {}, 'Loss'), h('dd', {}, `${fmt(p.fixed.loss_configured * 100, 0)}%`),
        h('dt', {}, 'Delay'), h('dd', {}, `${fmt(p.fixed.base_delay_ms, 0)} ms`),
        h('dt', {}, 'Jitter'), h('dd', {}, `${fmt(p.fixed.jitter_ms, 0)} ms`)));
  }));
}

/** Draw one metric as fixed-vs-adaptive grouped bars (with error bars when *_sd exists). */
function chartFor(id, pairs, key, digits = 1) {
  groupedBars($(id), {
    labels: pairs.map((p) => p.name),
    fixed: pairs.map((p) => p.fixed[key]),
    adaptive: pairs.map((p) => p.adaptive[key]),
    fixedSd: pairs.map((p) => p.fixed[`${key}_sd`] ?? 0),
    adaptiveSd: pairs.map((p) => p.adaptive[`${key}_sd`] ?? 0),
    digits,
  });
}

/* ------------------------------------------------------------ experiments */

function renderExperiments(rows, pairs) {
  chartFor('chart-rtt', pairs, 'avg_rtt_ms');
  chartFor('chart-jitter', pairs, 'avg_jitter_ms', 2);
  chartFor('chart-retx', pairs, 'retransmissions', 0);
  chartFor('chart-rate', pairs, 'final_rate_pps');

  const cols = [
    ['Experiment', (r) => r.experiment, false],
    ['Mode', (r) => r.mode, false],
    ['Loss %', (r) => fmt(r.loss_configured * 100, 1), true],
    ['Delay ms', (r) => fmt(r.base_delay_ms, 0), true],
    ['Jitter ms', (r) => fmt(r.jitter_ms, 0), true],
    ['Goodput kbps', (r) => `${fmt(r.goodput_kbps, 1)} ± ${fmt(r.goodput_kbps_sd ?? 0, 1)}`, true],
    ['Duration s', (r) => fmt(r.duration_s, 2), true],
    ['Retx', (r) => fmt(r.retransmissions, 1), true],
    ['RTT ms', (r) => fmt(r.avg_rtt_ms, 1), true],
    ['Jitter meas. ms', (r) => fmt(r.avg_jitter_ms, 1), true],
    ['Final rate pps', (r) => fmt(r.final_rate_pps, 1), true],
    ['Runs', (r) => String(r.repeats ?? 1), true],
    ['Integrity', (r) => (r.integrity_verified ? 'ok' : 'FAIL'), false],
  ];
  fillTable($('table'), cols, rows);
}

/**
 * Show how the adaptive controller was configured. Both the default configuration
 * and every grid point that was tried are listed, so the chosen setting is never
 * presented without the alternatives.
 */
function renderTuning(data, pairs) {
  if (!data.tuning_grid) return;
  const m = data.meta;
  $('tuning').hidden = false;
  $('tuning-note').textContent =
    `The adaptive bars on this site use the configuration chosen on tuning seeds ${m.tune_seeds.join(', ')}. ` +
    `Results are measured on separate seeds ${m.eval_seeds.join(', ')}, which were not used to choose it. ` +
    `Chosen: RTT ${m.tuned_config.rtt_threshold_ms} ms, jitter ${m.tuned_config.jitter_threshold_ms} ms, ` +
    `loss ${m.tuned_config.loss_threshold * 100}%. Default: RTT ${m.default_config.rtt_threshold_ms} ms, ` +
    `jitter ${m.default_config.jitter_threshold_ms} ms, loss ${m.default_config.loss_threshold * 100}%.`;

  const def = Object.fromEntries(data.default_adaptive_summary.map((r) => [r.experiment, r]));
  fillTable($('tuning-compare'), [
    ['Condition', (p) => p.name, false],
    ['Fixed kbps', (p) => fmt(p.fixed.goodput_kbps, 1), true],
    ['Default adaptive kbps', (p) => fmt(def[p.name].goodput_kbps, 1), true],
    ['Tuned adaptive kbps', (p) => fmt(p.adaptive.goodput_kbps, 1), true],
  ], pairs);

  fillTable($('tuning-grid'), [
    ['RTT ms', (g) => String(g.config.rtt_threshold_ms), true],
    ['Jitter ms', (g) => String(g.config.jitter_threshold_ms), true],
    ['Loss', (g) => `${g.config.loss_threshold * 100}%`, true],
    ['Score (adaptive ÷ fixed goodput)', (g) => fmt(g.score, 3), true],
  ], [...data.tuning_grid].sort((a, b) => b.score - a.score));
}

/** Render a table from column definitions: [header, getter, isNumeric]. */
function fillTable(table, cols, rows) {
  const head = h('tr', {}, ...cols.map(([t, , num]) => h('th', num ? { class: 'num' } : {}, t)));
  const body = rows.map((r) => h('tr', {}, ...cols.map(([, get, num]) => h('td', num ? { class: 'num' } : {}, get(r)))));
  table.replaceChildren(h('thead', {}, head), h('tbody', {}, ...body));
}

/* --------------------------------------------------------------- protocol */

/** Header fields straight from src/packet.py (offset, size, name, note). */
const FIELDS = [
  [0, 4, 'Magic', 'ASCII "AUDP"'],
  [4, 1, 'Version', '1'],
  [5, 1, 'Type', '1 DATA · 2 END · 3 ACK'],
  [6, 4, 'Sequence', 'uint32; chunk index, or total count for END'],
  [10, 8, 'Timestamp', 'uint64 monotonic nanoseconds; echoed by ACKs for RTT'],
  [18, 2, 'Length', 'uint16 payload bytes (0–1400)'],
  [20, 32, 'Checksum', 'SHA-256 over header fields and payload'],
];

function renderProtocol() {
  $('bytes').replaceChildren(...FIELDS.map(([, size, name], i) =>
    h('div', { style: `--w:${Math.max(size, 6)}; --t:${12 + i * 5}` }, h('b', {}, `${size} B`), name)));
  fillTable($('fields'), [
    ['Offset', (f) => String(f[0]), true], ['Size', (f) => `${f[1]} B`, true],
    ['Field', (f) => f[2], false], ['Notes', (f) => f[3], false],
  ], FIELDS);

  const item = (title, text) => h('li', {}, h('b', {}, `${title} `), text);
  $('types').replaceChildren(
    item('DATA', 'carries one chunk of up to 1400 bytes.'),
    item('END', 'closes the stream; its payload is the total DATA packet count.'),
    item('ACK', 'acknowledges a sequence number; always an empty payload.'),
  );
  $('reliab').replaceChildren(
    item('Stop-and-Wait', 'one packet in flight; retransmit on timeout up to a retry limit.'),
    item('RTO', 'Jacobson/Karels estimation with Karn’s rule and exponential back-off.'),
    item('Receiver', 'drops duplicates, re-acknowledges them, and lingers to re-ACK a lost final ACK.'),
    item('Integrity', 'the received file is compared with the original by SHA-256.'),
  );
}

/* ---------------------------------------------------------------- credits */

function renderCredits(team) {
  const initials = (name) => name.split(' ').map((p) => p[0]).slice(0, 2).join('');
  const bullets = (list) => h('ul', { class: 'plain' }, ...list.map((t) => h('li', {}, t)));

  $('people').replaceChildren(...team.members.map((m) => {
    const link = m.github
      ? h('a', { href: `https://github.com/${m.github}`, rel: 'noopener' }, `@${m.github}`)
      : h('span', { class: 'muted' }, 'GitHub handle not listed');
    return h('article', { class: 'person' },
      h('header', {}, h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(m.name)),
        h('div', {}, h('h2', {}, m.name), h('div', { class: 'meta' }, `${m.roll} · `, link))),
      h('p', { class: 'role' }, m.role),
      bullets(m.contributions));
  }));

  // Overall project summary, shown beneath the individual credits.
  const p = team.project;
  $('web').replaceChildren(
    h('h2', {}, p.title),
    h('p', { class: 'muted' }, `${p.course} · ${p.track}`),
    h('p', {}, p.summary),
    bullets(p.highlights),
    h('p', { class: 'muted foot' }, h('a', { href: p.repository, rel: 'noopener' }, 'Source repository')));
}

/* ------------------------------------------------------------- live runs */

/** Show a message in the run status line. */
const setStatus = (text) => { $('run-status').textContent = text; };

async function refreshHistory() {
  const cols = [
    ['Time', (r) => r.timestamp, false], ['File', (r) => r.filename, false],
    ['Bytes', (r) => String(r.bytes), true], ['Packets', (r) => String(r.packets), true],
    ['Retx', (r) => String(r.retransmissions), true], ['Elapsed s', (r) => String(r.elapsed_s), true],
    ['kbps', (r) => String(r.goodput_kbps), true], ['OK', (r) => (r.integrity_ok ? 'yes' : 'NO'), false],
  ];
  try {
    const rows = await loadJson(`${API_BASE}/api/history`);
    fillTable($('history'), cols, rows);
  } catch {
    $('history').replaceChildren(h('caption', { class: 'muted' }, 'Local server not reachable — live runs are unavailable here.'));
  }
}

function showRunResult(r) {
  $('run-result').hidden = false;
  const ok = $('run-verdict');
  ok.textContent = r.integrity_ok ? 'Complete — received file matches the original (SHA-256).' : 'Finished, but the SHA-256 hashes differ.';
  const rows = [['File', r.filename], ['Bytes', r.bytes], ['DATA packets', r.packets], ['Retransmissions', r.retransmissions],
    ['Duplicates', r.duplicates], ['Final RTO (s)', r.final_rto_s ?? 'n/a'], ['Elapsed (s)', r.elapsed_s],
    ['Goodput (kbps)', r.goodput_kbps], ['SHA-256 sent', r.sha256_sent], ['SHA-256 received', r.sha256_received]];
  $('run-stats').replaceChildren(...rows.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))]));
}

/** Human-readable byte size. */
const prettySize = (n) => (n >= 1024 ? `${fmt(n / 1024, 0)} KB` : `${n} B`);

/** Fill the predefined-file picker from the backend; the first sample is preselected. */
async function loadSamples() {
  const grid = $('sample-grid');
  try {
    const samples = await loadJson(`${API_BASE}/api/samples`);
    grid.replaceChildren(...samples.map((s, i) => h('label', { class: 'sample' },
      h('input', { type: 'radio', name: 'sample', value: s.id, ...(i === 1 ? { checked: '' } : {}) }),
      h('span', {}, h('b', {}, s.name), h('small', {}, `${s.description} · ${prettySize(s.size)}`)))));
    // Picking a sample clears any uploaded file so the choice is never ambiguous.
    grid.addEventListener('change', () => { $('file').value = ''; });
  } catch {
    grid.replaceChildren(h('p', { class: 'muted' }, 'Start the local server to load the sample files.'));
  }
}

function initRunForm() {
  // Choosing an upload deselects the samples.
  $('file').addEventListener('change', () => {
    if ($('file').files[0]) document.querySelectorAll('input[name="sample"]').forEach((r) => { r.checked = false; });
  });
  $('form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const file = $('file').files[0];
    const sample = document.querySelector('input[name="sample"]:checked')?.value;
    if (!file && !sample) { setStatus('Choose a test file first.'); return; }
    const params = new URLSearchParams({
      arq: $('arq').value, interval: $('interval').value,
      max_retries: $('max_retries').value, initial_rto: $('initial_rto').value,
    });
    if (file) params.set('filename', file.name); else params.set('sample', sample);
    $('go').disabled = true;
    setStatus('Transferring…');
    try {
      const res = await fetch(`${API_BASE}/api/transfer?${params}`, { method: 'POST', body: file ?? '' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      showRunResult(data);
      setStatus('');
      refreshHistory();
    } catch (err) {
      setStatus(`Error: ${err.message}. Is the local server running?`);
    } finally {
      $('go').disabled = false;
    }
  });
}

/* ------------------------------------------------------------------- boot */

/** How often the page checks the backend for newer experiment results. */
const REFRESH_MS = 8000;

/**
 * Fetch the latest recorded experiment results. The local server reads the file
 * from disk on every request. A static deployment (no server) falls back to a
 * results.json copied next to the page when the site was built.
 * @returns {Promise<{source: string, modified: string|null, data: any}>}
 */
async function fetchResults() {
  try {
    return await loadJson(`${API_BASE}/api/results`);
  } catch {
    return { source: 'data/results.json', modified: null, data: await loadJson('data/results.json') };
  }
}

let team = null;
let lastModified;   // undefined until the first successful load

/** Load results and redraw the data-driven views, but only when the file changed. */
async function refreshData() {
  try {
    const res = await fetchResults();
    if (res.modified !== null && res.modified === lastModified) return;
    lastModified = res.modified;
    const rows = res.data.summary;
    const pairs = pairByExperiment(rows);
    renderOverview(team, pairs, res.data.meta);
    renderExperiments(rows, pairs);
    renderTuning(res.data, pairs);
    $('data-status').textContent = res.modified
      ? `Live data: ${res.source}, last recorded ${res.modified.replace('T', ' ')}. Updates automatically.`
      : `Data: ${res.source}`;
  } catch (err) {
    $('data-status').textContent = `No experiment results available yet (${err.message}).`;
  }
}

async function main() {
  initTheme();
  renderProtocol();
  initRunForm();
  loadSamples();
  window.addEventListener('hashchange', route);
  route();
  try {
    team = await loadJson('data/team.json');
    renderCredits(team);
  } catch (err) {
    $('data-status').textContent = `Could not load team data: ${err.message}`;
  }
  if (team) await refreshData();
  setInterval(() => { if (team && !document.hidden) refreshData(); }, REFRESH_MS);
}

main();
