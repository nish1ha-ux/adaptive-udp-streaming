"use strict";

const $ = (id) => document.getElementById(id);

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function renderHistory(items) {
  const body = $("history");
  body.replaceChildren();
  for (const r of items) {
    const tr = document.createElement("tr");
    [r.timestamp, r.filename, r.bytes, r.packets, r.retransmissions,
     r.elapsed_s, r.goodput_kbps, r.integrity_ok ? "✔" : "✘"]
      .forEach((v) => tr.appendChild(cell(String(v))));
    body.appendChild(tr);
  }
}

function renderResult(r) {
  $("result").hidden = false;
  const verdict = $("verdict");
  verdict.textContent = r.integrity_ok
    ? "Transfer complete — received file matches the original (SHA-256)."
    : "Transfer finished but the SHA-256 hashes differ.";
  verdict.className = r.integrity_ok ? "ok" : "bad";
  const rows = [
    ["File", r.filename], ["Bytes", r.bytes], ["DATA packets", r.packets],
    ["Retransmissions", r.retransmissions], ["Duplicates at receiver", r.duplicates],
    ["Final RTO (s)", r.final_rto_s ?? "n/a"], ["Elapsed (s)", r.elapsed_s],
    ["Goodput (kbps)", r.goodput_kbps],
    ["SHA-256 sent", r.sha256_sent], ["SHA-256 received", r.sha256_received],
  ];
  const dl = $("stats");
  dl.replaceChildren();
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  }
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    renderHistory(await res.json());
  } catch (err) {
    console.error("history load failed", err);
  }
}

async function loadInfo() {
  try {
    const info = await (await fetch("/api/info")).json();
    $("proto").textContent =
      `${info.name} v${info.version} · header ${info.header_bytes} B · ` +
      `max payload ${info.max_payload_bytes} B · max upload ${info.max_upload_bytes / 1048576} MB`;
    $("file").dataset.max = info.max_upload_bytes;
  } catch (err) {
    $("proto").textContent = "Could not reach the backend.";
  }
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const file = $("file").files[0];
  if (!file) return;
  const params = new URLSearchParams({
    filename: file.name,
    arq: $("arq").value,
    interval: $("interval").value,
    max_retries: $("max_retries").value,
    initial_rto: $("initial_rto").value,
  });
  const btn = $("run");
  btn.disabled = true;
  $("status").textContent = "Transferring…";
  try {
    const res = await fetch(`/api/transfer?${params}`, { method: "POST", body: file });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    renderResult(data);
    $("status").textContent = "";
    loadHistory();
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

loadInfo();
loadHistory();
