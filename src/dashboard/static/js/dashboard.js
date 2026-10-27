/**
 * Adaptive UDP Streaming — Dashboard Interactive Controller
 * Native Canvas Charting & Real-Time Telemetry Polling
 */

document.addEventListener("DOMContentLoaded", () => {
  // Navigation Tabs
  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabContents = document.querySelectorAll(".tab-content");

  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-tab");
      tabBtns.forEach(b => b.classList.remove("active"));
      tabContents.forEach(c => c.classList.remove("active"));

      btn.classList.add("active");
      const target = document.getElementById(targetId);
      if (target) target.classList.add("active");

      if (targetId === "monitoring-tab") {
        renderCharts();
      }
    });
  });

  // Elements
  const transferForm = document.getElementById("transfer-form");
  const startBtn = document.getElementById("start-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  const fileSelect = document.getElementById("file-select");
  const customFileInput = document.getElementById("custom-file-input");
  const hostInput = document.getElementById("host-input");
  const portInput = document.getElementById("port-input");
  const intervalInput = document.getElementById("interval-input");

  // Telemetry DOM elements
  const elStatusBadge = document.getElementById("status-badge");
  const elBytesSent = document.getElementById("val-bytes-sent");
  const elBytesRecv = document.getElementById("val-bytes-recv");
  const elThroughput = document.getElementById("val-throughput");
  const elLossPct = document.getElementById("val-loss");
  const elRtt = document.getElementById("val-rtt");
  const elJitter = document.getElementById("val-jitter");
  const elRetrans = document.getElementById("val-retrans");
  const elRate = document.getElementById("val-rate");

  const elProgressBar = document.getElementById("progress-bar-fill");
  const elProgressPct = document.getElementById("progress-pct");
  const elProgressBytes = document.getElementById("progress-bytes");
  const elShaSource = document.getElementById("sha-source");
  const elShaRecv = document.getElementById("sha-recv");
  const elIntegrityStatus = document.getElementById("integrity-status");

  const logConsole = document.getElementById("log-console");
  const clearLogsBtn = document.getElementById("clear-logs-btn");

  let isTransferRunning = false;
  let pollInterval = null;
  let metricsHistory = [];

  // 1. Load Files for Dropdown
  async function loadFiles() {
    try {
      const res = await fetch("/api/files");
      const data = await res.json();
      if (data.files && data.files.length > 0) {
        fileSelect.innerHTML = "";
        data.files.forEach(f => {
          const opt = document.createElement("option");
          opt.value = f.path;
          opt.textContent = `${f.name} (${formatBytes(f.size_bytes)})`;
          fileSelect.appendChild(opt);
        });
        const customOpt = document.createElement("option");
        customOpt.value = "__custom__";
        customOpt.textContent = "Custom File Path...";
        fileSelect.appendChild(customOpt);
      }
    } catch (err) {
      console.error("Failed to load selectable files:", err);
    }
  }

  fileSelect.addEventListener("change", () => {
    if (fileSelect.value === "__custom__") {
      customFileInput.style.display = "block";
      customFileInput.focus();
    } else {
      customFileInput.style.display = "none";
    }
  });

  // 2. Poll Status
  async function pollStatus() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();
      const t = data.transfer;
      isTransferRunning = data.is_running;

      // Update Controls
      startBtn.disabled = isTransferRunning;
      cancelBtn.disabled = !isTransferRunning;

      // Update Status Badge and Branch
      updateStatusBadge(t.status);
      if (data.branch) {
        const sub = document.getElementById("brand-subtitle");
        if (sub) sub.textContent = `Branch: ${data.branch}`;
        const b = document.getElementById("brand-branch-badge");
        if (b) b.textContent = data.branch;
        const fb = document.getElementById("footer-branch");
        if (fb) fb.textContent = data.branch;
      }

      // Update Metrics
      elBytesSent.textContent = formatBytes(t.bytes_sent);
      elBytesRecv.textContent = formatBytes(t.bytes_received);
      elThroughput.textContent = `${t.average_throughput_kbps.toFixed(1)} KB/s`;

      if (t.packet_loss_pct !== null) {
        elLossPct.textContent = `${t.packet_loss_pct.toFixed(1)}%`;
      } else {
        elLossPct.textContent = "0.0%";
      }

      // RTT, Jitter, Retrans, Rate (Documented as N/A on baseline branch)
      elRtt.textContent = t.rtt_ms !== null ? `${t.rtt_ms} ms` : "N/A";
      elJitter.textContent = t.jitter_ms !== null ? `${t.jitter_ms} ms` : "N/A";
      elRetrans.textContent = t.retransmissions !== null ? t.retransmissions : "N/A";
      elRate.textContent = t.adaptive_rate_pps !== null ? `${t.adaptive_rate_pps} pps` : "N/A";

      // Progress Bar
      elProgressBar.style.width = `${t.progress_pct}%`;
      elProgressPct.textContent = `${t.progress_pct}%`;
      elProgressBytes.textContent = `${formatBytes(t.bytes_sent)} / ${formatBytes(t.total_bytes)}`;

      // Hashes
      elShaSource.textContent = t.sha256_source || "—";
      elShaRecv.textContent = t.sha256_received || "—";

      // Integrity badge
      if (t.integrity_verified === true) {
        elIntegrityStatus.innerHTML = '<span class="pill pill-adaptive">✓ SHA-256 MATCH</span>';
      } else if (t.integrity_verified === false) {
        elIntegrityStatus.innerHTML = '<span class="pill" style="background: rgba(244,63,94,0.2); color:#f43f5e;">✗ MISMATCH</span>';
      } else if (t.status === "running") {
        elIntegrityStatus.innerHTML = '<span class="pill" style="background: rgba(6,182,212,0.2); color:#06b6d4;">VERIFYING...</span>';
      } else {
        elIntegrityStatus.innerHTML = '<span class="metric-na-pill">Awaiting Transfer</span>';
      }

      // Adjust polling frequency based on activity
      const nextInterval = isTransferRunning ? 400 : 2000;
      clearInterval(pollInterval);
      pollInterval = setInterval(pollAll, nextInterval);

    } catch (err) {
      console.warn("Poll status error:", err);
    }
  }

  // 3. Poll Metrics History for Charting
  async function pollMetrics() {
    try {
      const res = await fetch("/api/metrics/history");
      if (!res.ok) return;
      const data = await res.json();
      metricsHistory = data.history || [];
      renderCharts();
    } catch (err) {
      console.warn("Metrics poll error:", err);
    }
  }

  // 4. Poll Logs
  async function pollLogs() {
    try {
      const res = await fetch("/api/logs?limit=150");
      if (!res.ok) return;
      const data = await res.json();
      renderLogs(data.logs || []);
    } catch (err) {
      console.warn("Logs poll error:", err);
    }
  }

  function pollAll() {
    pollStatus();
    pollLogs();
    if (document.getElementById("monitoring-tab").classList.contains("active")) {
      pollMetrics();
    }
  }

  function updateStatusBadge(status) {
    elStatusBadge.className = "badge";
    if (status === "running") {
      elStatusBadge.innerHTML = '<div class="badge-pulse"></div> Transferring (UDP)';
      elStatusBadge.style.color = "var(--accent-cyan)";
      elStatusBadge.style.borderColor = "var(--accent-cyan)";
    } else if (status === "completed") {
      elStatusBadge.innerHTML = '✓ Transfer Complete';
      elStatusBadge.style.color = "var(--accent-emerald)";
      elStatusBadge.style.borderColor = "var(--accent-emerald)";
    } else if (status === "failed" || status === "timed_out") {
      elStatusBadge.innerHTML = '⚠ Transfer Failed / Timeout';
      elStatusBadge.style.color = "var(--accent-rose)";
      elStatusBadge.style.borderColor = "var(--accent-rose)";
    } else if (status === "cancelled") {
      elStatusBadge.innerHTML = '⊘ Transfer Cancelled';
      elStatusBadge.style.color = "var(--accent-amber)";
      elStatusBadge.style.borderColor = "var(--accent-amber)";
    } else {
      elStatusBadge.innerHTML = '● Idle';
      elStatusBadge.style.color = "var(--text-secondary)";
      elStatusBadge.style.borderColor = "var(--border-subtle)";
    }
  }

  // 5. Start Transfer Form
  transferForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (isTransferRunning) return;

    let selectedPath = fileSelect.value;
    if (selectedPath === "__custom__") {
      selectedPath = customFileInput.value.trim();
    }

    if (!selectedPath) {
      alert("Please select or enter a valid file path.");
      return;
    }

    const payload = {
      file_path: selectedPath,
      host: hostInput.value.trim() || "127.0.0.1",
      port: parseInt(portInput.value, 10) || 5005,
      pacing_interval: parseFloat(intervalInput.value) || 0.001,
    };

    startBtn.disabled = true;
    startBtn.textContent = "Starting...";

    try {
      const res = await fetch("/api/transfer/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(`Failed to start transfer: ${data.message || data.error}`);
      } else {
        pollAll();
      }
    } catch (err) {
      alert(`Network error starting transfer: ${err.message}`);
    } finally {
      startBtn.textContent = "🚀 Start Transfer";
      pollAll();
    }
  });

  // 6. Cancel Transfer
  cancelBtn.addEventListener("click", async () => {
    if (!isTransferRunning) return;
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling...";

    try {
      const res = await fetch("/api/transfer/cancel", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        alert(data.message || "Failed to cancel transfer.");
      }
    } catch (err) {
      alert(`Error cancelling transfer: ${err.message}`);
    } finally {
      cancelBtn.textContent = "Cancel Transfer";
      pollAll();
    }
  });

  // 7. Clear Logs
  clearLogsBtn.addEventListener("click", async () => {
    try {
      await fetch("/api/logs/clear", { method: "POST" });
      logConsole.innerHTML = '<div class="log-line"><span class="log-msg">Logs cleared.</span></div>';
    } catch (err) {
      console.error(err);
    }
  });

  function renderLogs(logs) {
    if (!logConsole) return;
    const isScrolledToBottom = logConsole.scrollHeight - logConsole.clientHeight <= logConsole.scrollTop + 30;

    logConsole.innerHTML = "";
    if (logs.length === 0) {
      logConsole.innerHTML = '<div class="log-line"><span class="log-msg">Awaiting protocol activity...</span></div>';
      return;
    }

    logs.forEach(item => {
      const line = document.createElement("div");
      line.className = "log-line";
      line.innerHTML = `
        <span class="log-time">[${item.time}]</span>
        <span class="log-level ${item.level}">${item.level}</span>
        <span class="log-msg">${escapeHtml(item.message)}</span>
      `;
      logConsole.appendChild(line);
    });

    if (isScrolledToBottom) {
      logConsole.scrollTop = logConsole.scrollHeight;
    }
  }

  // 8. Canvas Line Chart for Throughput
  const canvas = document.getElementById("throughput-chart");
  function renderCharts() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;

    ctx.clearRect(0, 0, w, h);

    if (metricsHistory.length < 2) {
      ctx.fillStyle = "#6b7280";
      ctx.font = "13px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Real-time telemetry chart updates when a transfer runs", w / 2, h / 2);
      return;
    }

    // Determine bounds
    const maxVal = Math.max(...metricsHistory.map(m => m.throughput_kbps), 100);
    const padding = { top: 30, right: 30, bottom: 40, left: 60 };
    const chartW = w - padding.left - padding.right;
    const chartH = h - padding.top - padding.bottom;

    // Draw horizontal grid lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    ctx.lineWidth = 1;
    const gridLines = 4;
    for (let i = 0; i <= gridLines; i++) {
      const y = padding.top + (chartH / gridLines) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(w - padding.right, y);
      ctx.stroke();

      const labelVal = Math.round(maxVal - (maxVal / gridLines) * i);
      ctx.fillStyle = "#6b7280";
      ctx.font = "10px Roboto Mono, monospace";
      ctx.textAlign = "right";
      ctx.fillText(`${labelVal} KB/s`, padding.left - 8, y + 3);
    }

    // Plot line
    const pts = metricsHistory.map((m, idx) => {
      const x = padding.left + (chartW / (metricsHistory.length - 1)) * idx;
      const y = padding.top + chartH - (m.throughput_kbps / maxVal) * chartH;
      return { x, y };
    });

    // Area fill
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, padding.top + chartH);
    ctx.lineTo(pts[0].x, padding.top + chartH);
    ctx.closePath();

    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartH);
    gradient.addColorStop(0, "rgba(6, 182, 212, 0.35)");
    gradient.addColorStop(1, "rgba(6, 182, 212, 0.0)");
    ctx.fillStyle = gradient;
    ctx.fill();

    // Line stroke
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = "#06b6d4";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Last point pulse dot
    const last = pts[pts.length - 1];
    ctx.fillStyle = "#06b6d4";
    ctx.beginPath();
    ctx.arc(last.x, last.y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // 9. Load Experiment Benchmark Table
  async function loadExperiments() {
    const tableBody = document.getElementById("experiment-table-body");
    if (!tableBody) return;
    try {
      const res = await fetch("/api/experiments");
      const data = await res.json();
      const list = data.experiments || [];
      tableBody.innerHTML = "";

      list.forEach(item => {
        const tr = document.createElement("tr");
        const isAdaptive = item.mode.toLowerCase().includes("adaptive");
        const pillClass = isAdaptive ? "pill-adaptive" : "pill-fixed";

        tr.innerHTML = `
          <td><strong>${item.experiment}</strong></td>
          <td><span class="pill ${pillClass}">${item.mode}</span></td>
          <td>${(item.loss_configured * 100).toFixed(1)}%</td>
          <td>${item.base_delay_ms} ms</td>
          <td>${item.jitter_ms} ms</td>
          <td><strong>${item.goodput_kbps} KB/s</strong></td>
          <td>${item.avg_rtt_ms !== null ? item.avg_rtt_ms + ' ms' : '—'}</td>
          <td>${item.retransmissions}</td>
          <td>${item.integrity_verified ? '✓ Verified' : '✗ Failed'}</td>
        `;
        tableBody.appendChild(tr);
      });
    } catch (err) {
      console.error("Failed to load experiments:", err);
    }
  }

  // 10. Network Conditions Form (Explains branch status)
  const networkForm = document.getElementById("network-form");
  if (networkForm) {
    networkForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const res = await fetch("/api/network/conditions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const data = await res.json();
        alert(data.message);
      } catch (err) {
        alert(err.message);
      }
    });
  }

  // Helpers
  function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    if (!bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Init
  loadFiles();
  loadExperiments();
  pollAll();
  pollInterval = setInterval(pollAll, 2000);
});
