/**
 * Data loading and analysis helpers.
 *
 * Results come from the project's own emulator runs (frontend/data/results.json).
 * This module only reads and summarises them; it never produces numbers.
 */

/**
 * @typedef {Object} RunRow
 * @property {string} experiment
 * @property {number} loss_configured    Configured loss probability (0-1).
 * @property {number} base_delay_ms
 * @property {number} jitter_ms
 * @property {'fixed'|'adaptive'} mode
 * @property {number} duration_s
 * @property {number} goodput_kbps
 * @property {number} packets_sent
 * @property {number} retransmissions
 * @property {number} avg_rtt_ms
 * @property {number} avg_jitter_ms
 * @property {number} final_rate_pps
 * @property {boolean} integrity_verified
 */

/** Goodput differences smaller than this fraction are reported as "similar". */
export const SIMILARITY_BAND = 0.05;

/**
 * Fetch a JSON file relative to the page.
 * @param {string} path
 * @returns {Promise<any>}
 */
export async function loadJson(path) {
  const res = await fetch(path, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`Could not load ${path} (HTTP ${res.status})`);
  return res.json();
}

/**
 * Group rows into one entry per experiment holding its fixed and adaptive run.
 * @param {RunRow[]} rows
 * @returns {{name: string, fixed: RunRow, adaptive: RunRow}[]}
 */
export function pairByExperiment(rows) {
  const map = new Map();
  for (const row of rows) {
    const entry = map.get(row.experiment) ?? { name: row.experiment };
    entry[row.mode] = row;
    map.set(row.experiment, entry);
  }
  return [...map.values()].filter((e) => e.fixed && e.adaptive);
}

/**
 * Classify each experiment by relative goodput change of adaptive vs fixed.
 * @param {ReturnType<typeof pairByExperiment>} pairs
 */
export function compare(pairs) {
  /** Coefficient of variation: spread relative to the mean (lower = steadier). */
  const cv = (r) => (r.goodput_kbps ? (r.goodput_kbps_sd ?? 0) / r.goodput_kbps : 0);
  const detail = pairs.map((p) => {
    const diff = p.adaptive.goodput_kbps - p.fixed.goodput_kbps;
    const change = diff / p.fixed.goodput_kbps;
    // A difference only counts if it exceeds both the 5% band and the run-to-run spread.
    const spread = (p.adaptive.goodput_kbps_sd ?? 0) + (p.fixed.goodput_kbps_sd ?? 0);
    const real = Math.abs(change) > SIMILARITY_BAND && Math.abs(diff) > spread;
    const outcome = !real ? 'similar' : diff > 0 ? 'better' : 'worse';
    return { name: p.name, change, outcome, steadier: cv(p.adaptive) < cv(p.fixed) };
  });
  const count = (o) => detail.filter((d) => d.outcome === o).length;
  return {
    detail,
    better: count('better'),
    similar: count('similar'),
    worse: count('worse'),
    steadier: detail.filter((d) => d.steadier).length,
  };
}

/**
 * Turn the comparison into an honest headline. "Supported" requires adaptive to
 * win everywhere; any loss with no wins means "not supported"; everything else
 * is "inconclusive" so small samples are never over-sold.
 * @param {ReturnType<typeof compare>} c
 * @returns {{tone: 'good'|'bad'|'warn', title: string, body: string}}
 */
export function verdict(c) {
  const total = c.detail.length;
  if (c.better === total) {
    return { tone: 'good', title: 'Claim supported in every tested condition', body: `Adaptive goodput beat fixed-rate by more than ${SIMILARITY_BAND * 100}% in all ${total} experiments.` };
  }
  if (c.better === 0 && c.worse > 0) {
    return {
      tone: 'bad',
      title: 'Claim not supported by the measurements',
      body: `Adaptive goodput was lower in ${c.worse} of ${total} conditions and higher in none; it was steadier in ${c.steadier} of ${total}.`,
    };
  }
  return {
    tone: 'warn',
    title: 'Mixed result',
    body: `Adaptive was better in ${c.better}, similar in ${c.similar} and worse in ${c.worse} of ${total} experiments on goodput.`,
  };
}

/**
 * Format a number for display with a fixed number of decimals.
 * @param {number} n
 * @param {number} [digits]
 */
export const fmt = (n, digits = 1) => Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
