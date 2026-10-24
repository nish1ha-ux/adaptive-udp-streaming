/**
 * Minimal dependency-free grouped bar chart rendered as inline SVG.
 *
 * Rendering uses a fixed viewBox so the SVG scales with its container; this keeps
 * the chart crisp and readable from phone to desktop without resize listeners.
 */

const NS = 'http://www.w3.org/2000/svg';

/** Create an SVG element with attributes. */
function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Round a maximum up to a "nice" axis bound (1, 2, 2.5, 5 x 10^n). */
function niceMax(value) {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  const n = value / pow;
  const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => n <= s) ?? 10;
  return step * pow;
}

/** Shorten "E2 — Mild impairment" to "E2" plus a second line for the label. */
function splitLabel(label) {
  const [code, ...rest] = label.split(/\s[—-]\s/).map((s) => s.trim());
  return { code, name: rest.join(' ') };
}

/**
 * Draw a grouped bar chart into `container`.
 *
 * @param {HTMLElement} container
 * @param {{labels: string[], fixed: number[], adaptive: number[], digits?: number,
 *          fixedSd?: number[], adaptiveSd?: number[]}} spec  Optional *Sd arrays draw error bars.
 */
export function groupedBars(container, { labels, fixed, adaptive, digits = 1, fixedSd = [], adaptiveSd = [] }) {
  const W = 640, H = 300, m = { t: 16, r: 8, b: 48, l: 44 };
  const innerW = W - m.l - m.r, innerH = H - m.t - m.b;
  const top = (vals, sds) => vals.map((v, i) => v + (sds[i] ?? 0));
  const max = niceMax(Math.max(...top(fixed, fixedSd), ...top(adaptive, adaptiveSd)));
  const y = (v) => m.t + innerH - (v / max) * innerH;
  const groupW = innerW / labels.length;
  const barW = Math.min(34, groupW / 3);

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' });

  // Horizontal gridlines with value labels (4 intervals).
  for (let i = 0; i <= 4; i += 1) {
    const v = (max / 4) * i;
    svg.append(el('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
    svg.append(el('text', { x: m.l - 6, y: y(v) + 4, 'text-anchor': 'end' }, String(Number(v.toFixed(1)))));
  }

  labels.forEach((label, i) => {
    const cx = m.l + groupW * i + groupW / 2;
    [['fixed', fixed[i], -barW - 2, fixedSd[i] ?? 0], ['adaptive', adaptive[i], 2, adaptiveSd[i] ?? 0]].forEach(([mode, value, dx, sd]) => {
      const h = innerH - (y(value) - m.t);
      const mid = cx + dx + barW / 2;
      svg.append(el('rect', { class: `bar-${mode}`, x: cx + dx, y: y(value), width: barW, height: Math.max(h, 0), rx: 3 }));
      if (sd > 0) {
        // Error bar: mean ± one standard deviation across repeats.
        svg.append(el('line', { class: 'err', x1: mid, x2: mid, y1: y(value + sd), y2: y(Math.max(value - sd, 0)) }));
        svg.append(el('line', { class: 'err', x1: mid - 4, x2: mid + 4, y1: y(value + sd), y2: y(value + sd) }));
      }
      svg.append(el('text', { class: 'val', x: mid, y: y(value + sd) - 6, 'text-anchor': 'middle' }, Number(value).toLocaleString(undefined, { maximumFractionDigits: digits })));
    });
    const { code, name } = splitLabel(label);
    svg.append(el('text', { x: cx, y: H - 24, 'text-anchor': 'middle' }, code));
    svg.append(el('text', { x: cx, y: H - 10, 'text-anchor': 'middle' }, name.split(' ')[0] ?? ''));
  });

  container.replaceChildren(svg);
}
