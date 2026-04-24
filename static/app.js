/* TraceForge frontend - vanilla JS, no framework */

var state = {
  runs: [],
  selectedRunId: null,
  runData: null,
  compareData: null,
  selectedStep: null,
  view: "waterfall",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt_dur(ms) {
  if (ms === null || ms === undefined || ms < 0) return "-";
  if (ms < 1000) return Math.round(ms) + "ms";
  return (ms / 1000).toFixed(2) + "s";
}

function fmt_tok(n) {
  n = n || 0;
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

function fmt_cost(usd) {
  if (!usd) return "$0.00";
  if (usd < 0.0001) return "<$0.0001";
  return "$" + usd.toFixed(4);
}

function time_ago(ts) {
  var diff = Date.now() / 1000 - ts;
  if (diff < 60) return Math.round(diff) + "s ago";
  if (diff < 3600) return Math.round(diff / 60) + "m ago";
  if (diff < 86400) return Math.round(diff / 3600) + "h ago";
  return new Date(ts * 1000).toLocaleDateString();
}

function badge(status) {
  return '<span class="badge badge-' + esc(status) + '">' + esc(status) + "</span>";
}

async function api_get(path) {
  var res = await fetch(path);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function api_post(path, body) {
  var res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function loadRuns() {
  try {
    var data = await api_get("/api/runs");
    state.runs = data.runs || [];
    renderSidebar();
  } catch (e) {
    document.getElementById("run-list").innerHTML =
      '<div class="empty">Error loading runs: ' + esc(e.message) + "</div>";
  }
}

async function selectRun(runId) {
  state.selectedRunId = runId;
  renderSidebar();
  try {
    state.runData = await api_get("/api/runs/" + runId);
    state.selectedStep = null;
    state.compareData = null;
    if (state.view === "compare") state.view = "waterfall";
    render();
  } catch (e) {
    document.getElementById("content").innerHTML =
      '<div class="empty">Error: ' + esc(e.message) + "</div>";
  }
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function renderSidebar() {
  var el = document.getElementById("run-list");
  if (!state.runs.length) {
    el.innerHTML = '<div class="empty">No runs yet.<br>Run demo.py to generate traces.</div>';
    return;
  }
  el.innerHTML = state.runs
    .map(function (r) {
      var active = r.run_id === state.selectedRunId ? " active" : "";
      var tok = fmt_tok((r.total_tokens || 0));
      var dur = r.ended_at
        ? fmt_dur((r.ended_at - r.started_at) * 1000)
        : "running";
      return (
        '<div class="run-item' + active + '" onclick="selectRun(\'' + r.run_id + '\')">' +
        '<div class="ri-name">' + esc(r.run_name) + "</div>" +
        '<div class="ri-meta">' +
        badge(r.status) +
        "<span>" + time_ago(r.started_at) + "</span>" +
        "<span>" + tok + " tok</span>" +
        "<span>" + dur + "</span>" +
        "</div></div>"
      );
    })
    .join("");
}

// ---------------------------------------------------------------------------
// View routing
// ---------------------------------------------------------------------------

function setView(v) {
  state.view = v;
  document.querySelectorAll(".tab").forEach(function (b) {
    b.classList.toggle("on", b.dataset.v === v);
  });
  render();
}

function render() {
  var el = document.getElementById("content");
  if (!state.runData) {
    el.innerHTML = '<div class="empty-main">Select a run from the sidebar</div>';
    return;
  }
  var run = state.runData.run;
  var steps = state.runData.steps || [];
  var dur = run.ended_at ? fmt_dur((run.ended_at - run.started_at) * 1000) : "running";

  var hdr =
    '<div class="rh">' +
    '<h2>' + esc(run.run_name) + "</h2>" +
    '<div class="rh-stats">' +
    badge(run.status) +
    "<span>" + steps.length + " steps</span>" +
    "<span>" + fmt_tok(state.runData.total_tokens) + " tokens</span>" +
    "<span>" + fmt_cost(state.runData.total_cost) + "</span>" +
    "<span>" + dur + "</span>" +
    "</div>" +
    '<div class="rh-actions">' +
    '<button class="btn" onclick="exportRun(\'' + run.run_id + '\')">Export JSONL</button>' +
    '<button class="btn" onclick="openCompare()">Compare</button>' +
    "</div></div>";

  var body = "";
  if (state.view === "waterfall") body = renderWaterfall(steps, run);
  else if (state.view === "tokens") body = renderTokens(steps);
  else if (state.view === "step") body = renderStepDetail();
  else if (state.view === "compare") body = renderCompare();

  el.innerHTML = hdr + body;
}

// ---------------------------------------------------------------------------
// Waterfall view
// ---------------------------------------------------------------------------

function computeDepths(steps) {
  var byId = {};
  steps.forEach(function (s) { byId[s.step_id] = s; });
  var depths = {};

  function depth(s) {
    if (depths[s.step_id] !== undefined) return depths[s.step_id];
    if (!s.parent_step_id || !byId[s.parent_step_id]) {
      depths[s.step_id] = 0;
    } else {
      depths[s.step_id] = depth(byId[s.parent_step_id]) + 1;
    }
    return depths[s.step_id];
  }
  steps.forEach(function (s) { depth(s); });
  return depths;
}

function timeMarkers(totalMs) {
  var nice = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000];
  var step = nice.find(function (n) { return totalMs / n <= 7; }) || 30000;
  var marks = [];
  for (var t = 0; t <= totalMs; t += step) marks.push(t);
  return marks;
}

function renderWaterfall(steps, run) {
  if (!steps.length) return '<div class="empty">No steps recorded</div>';

  var runStart = run.started_at;
  var runEnd = run.ended_at;
  if (!runEnd) {
    runEnd = steps.reduce(function (m, s) {
      return Math.max(m, s.ended_at || s.started_at);
    }, runStart);
  }
  var totalMs = Math.max(1, (runEnd - runStart) * 1000);
  var scale = 100 / totalMs;

  var depths = computeDepths(steps);

  var marks = timeMarkers(totalMs);
  var markerHtml = marks.map(function (t) {
    return '<span class="tm" style="left:' + (t * scale).toFixed(1) + '%">' + fmt_dur(t) + "</span>";
  }).join("");

  var rows = steps.map(function (s) {
    var off = Math.max(0, (s.started_at - runStart) * 1000 * scale);
    var w = Math.max(0.3, (s.latency_ms || 0) * scale);
    var d = depths[s.step_id] || 0;
    var errCls = s.error ? " err" : "";
    var tok = (s.tokens_input || 0) + (s.tokens_output || 0);
    var connector = d > 0 ? '<span class="wf-connector">|--</span>' : "";
    var model_tag = s.model ? '<span class="wf-tag">' + esc(s.model) + "</span>" : "";
    var ebadge = s.error ? '<span class="ebadge">ERR</span>' : "";

    return (
      '<div class="wf-row' + errCls + '" onclick="showStep(\'' + s.step_id + '\')">' +
      '<div class="wf-name" style="padding-left:' + (d * 14 + 4) + 'px">' +
      connector + esc(s.step_name) + model_tag + "</div>" +
      '<div class="wf-bc"><div class="wf-bar' + errCls + '" style="left:' +
      off.toFixed(2) + "%;width:" + w.toFixed(2) + '%"></div></div>' +
      '<div class="wf-stats"><span>' + fmt_dur(s.latency_ms) + "</span>" +
      "<span>" + fmt_tok(tok) + " tok</span>" + ebadge + "</div>" +
      "</div>"
    );
  }).join("");

  return (
    '<div class="wf">' +
    '<div class="wf-hdr">' +
    "<span>Step</span>" +
    '<div class="wf-col-bar">' + markerHtml + "</div>" +
    "<span>Latency / Tokens</span>" +
    "</div>" +
    rows +
    "</div>"
  );
}

// ---------------------------------------------------------------------------
// Token breakdown view
// ---------------------------------------------------------------------------

function renderTokens(steps) {
  if (!steps.length) return '<div class="empty">No steps recorded</div>';

  var maxTok = steps.reduce(function (m, s) {
    return Math.max(m, (s.tokens_input || 0) + (s.tokens_output || 0));
  }, 1);

  var rows = steps.map(function (s) {
    var total = (s.tokens_input || 0) + (s.tokens_output || 0);
    var pct = (total / maxTok * 100).toFixed(1);
    var inPct = total > 0 ? ((s.tokens_input || 0) / total * 100).toFixed(1) : "0";
    return (
      '<div class="tb-row" onclick="showStep(\'' + s.step_id + '\')">' +
      '<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(s.step_name) + "</div>" +
      '<div class="tb-bw"><div class="tb-bar" style="width:' + pct + '%">' +
      '<div class="tb-bar-in" style="width:' + inPct + '%"></div></div></div>' +
      '<div class="tb-nums"><span class="ti">' + fmt_tok(s.tokens_input) + " in</span>" +
      '<span class="to">' + fmt_tok(s.tokens_output) + " out</span>" +
      '<span class="tc">' + fmt_cost(s.cost_usd) + "</span></div>" +
      "</div>"
    );
  }).join("");

  var totIn = steps.reduce(function (a, s) { return a + (s.tokens_input || 0); }, 0);
  var totOut = steps.reduce(function (a, s) { return a + (s.tokens_output || 0); }, 0);
  var totCost = steps.reduce(function (a, s) { return a + (s.cost_usd || 0); }, 0);

  return (
    '<div class="tb">' +
    '<div class="tb-legend">' +
    '<span class="li">Input tokens</span><span class="lo">Output tokens</span>' +
    "</div>" +
    rows +
    '<div class="tb-total">Total: ' +
    fmt_tok(totIn) + " in + " + fmt_tok(totOut) + " out = " +
    fmt_tok(totIn + totOut) + " tokens &nbsp; " + fmt_cost(totCost) +
    "</div></div>"
  );
}

// ---------------------------------------------------------------------------
// Step detail view
// ---------------------------------------------------------------------------

function showStep(stepId) {
  if (!state.runData) return;
  var step = state.runData.steps.find(function (s) { return s.step_id === stepId; });
  if (step) {
    state.selectedStep = step;
    setView("step");
  }
}

function renderStepDetail() {
  var s = state.selectedStep;
  if (!s) return '<div class="empty">Click a step in the Waterfall or Tokens view to inspect it</div>';

  var errHtml = s.error
    ? '<div class="err-box">' + esc(s.error) + "</div>"
    : "";

  var statusText = s.error
    ? '<span style="color:#f85149">Error</span>'
    : '<span style="color:#56d364">OK</span>';

  var replayJson = JSON.stringify({ model: s.model, input_data: s.input_data }, null, 2);

  return (
    '<div class="sd">' +
    '<h3>' + esc(s.step_name) + "</h3>" +
    '<div class="meta-grid">' +
    cell("Model", s.model || "-") +
    cell("Latency", fmt_dur(s.latency_ms)) +
    cell("Input tokens", s.tokens_input || 0) +
    cell("Output tokens", s.tokens_output || 0) +
    cell("Cost", fmt_cost(s.cost_usd)) +
    cell("Status", statusText) +
    "</div>" +
    errHtml +
    '<div class="io-grid">' +
    '<div class="io-panel"><h4>Input</h4><pre>' +
    esc(JSON.stringify(s.input_data, null, 2)) +
    "</pre></div>" +
    '<div class="io-panel"><h4>Output</h4><pre>' +
    esc(JSON.stringify(s.output_data, null, 2)) +
    "</pre></div>" +
    "</div>" +
    '<div class="replay-box">' +
    "<h4>Replay Input</h4>" +
    "<p style='color:#8b949e;font-size:11px;margin:4px 0 0'>Copy this to re-run the step in isolation with any LLM client:</p>" +
    '<pre class="replay-pre">' + esc(replayJson) + "</pre>" +
    '<button class="btn" onclick="copyReplay()">Copy to Clipboard</button>' +
    "</div></div>"
  );
}

function cell(label, value) {
  return (
    '<div class="meta-cell"><label>' + esc(label) + "</label><span>" + value + "</span></div>"
  );
}

function copyReplay() {
  if (!state.selectedStep) return;
  var s = state.selectedStep;
  var text = JSON.stringify({ model: s.model, input_data: s.input_data }, null, 2);
  navigator.clipboard.writeText(text).then(function () {
    alert("Replay input copied to clipboard");
  });
}

// ---------------------------------------------------------------------------
// Compare view
// ---------------------------------------------------------------------------

function openCompare() {
  state.compareData = null;
  setView("compare");
}

async function loadCompare(runId) {
  try {
    state.compareData = await api_get(
      "/api/compare?run_id_a=" + state.selectedRunId + "&run_id_b=" + runId
    );
    render();
  } catch (e) {
    alert("Error loading compare: " + e.message);
  }
}

function renderCompare() {
  if (!state.compareData) {
    var others = state.runs.filter(function (r) {
      return r.run_id !== state.selectedRunId;
    });
    var items = others.length
      ? others.map(function (r) {
          return (
            '<div class="cmp-item" onclick="loadCompare(\'' + r.run_id + '\')">' +
            badge(r.status) + " " + esc(r.run_name) + " &mdash; " + time_ago(r.started_at) +
            "</div>"
          );
        }).join("")
      : '<div class="empty">No other runs to compare with</div>';

    return (
      '<div class="cmp-setup"><h3>Compare Runs</h3>' +
      '<p>Select a second run to diff against "' +
      esc(state.runData.run.run_name) +
      '":</p>' +
      '<div class="cmp-list">' + items + "</div></div>"
    );
  }

  var cd = state.compareData;
  var rows = cd.diff.map(function (d) {
    var a = d.run_a;
    var b = d.run_b;
    var trCls = !a ? " class='added'" : !b ? " class='removed'" : "";
    var latA = a ? fmt_dur(a.latency_ms) : "-";
    var latB = b ? fmt_dur(b.latency_ms) : "-";
    var tokA = a ? fmt_tok((a.tokens_input || 0) + (a.tokens_output || 0)) : "-";
    var tokB = b ? fmt_tok((b.tokens_input || 0) + (b.tokens_output || 0)) : "-";

    var latDiff = "", tokDiff = "";
    if (a && b) {
      var ld = (b.latency_ms || 0) - (a.latency_ms || 0);
      var sign = ld > 0 ? "+" : "";
      var cls = ld > 0 ? "worse" : ld < 0 ? "better" : "";
      latDiff = '<span class="' + cls + '">' + sign + fmt_dur(ld) + "</span>";
      var td_ = ((b.tokens_input || 0) + (b.tokens_output || 0)) -
                ((a.tokens_input || 0) + (a.tokens_output || 0));
      var tsign = td_ > 0 ? "+" : "";
      var tcls = td_ > 0 ? "worse" : td_ < 0 ? "better" : "";
      tokDiff = '<span class="' + tcls + '">' + tsign + fmt_tok(td_) + "</span>";
    }

    return (
      "<tr" + trCls + ">" +
      "<td>" + esc(d.step_name) + "</td>" +
      "<td>" + latA + "</td><td>" + latB + "</td><td>" + latDiff + "</td>" +
      "<td>" + tokA + "</td><td>" + tokB + "</td><td>" + tokDiff + "</td>" +
      "</tr>"
    );
  }).join("");

  return (
    '<div>' +
    '<div class="cmp-hdr">' +
    '<strong>' + esc(cd.run_a.run_name) + "</strong>" + badge(cd.run_a.status) +
    '<span class="vs">vs</span>' +
    '<strong>' + esc(cd.run_b.run_name) + "</strong>" + badge(cd.run_b.status) +
    "</div>" +
    '<table class="cmp-tbl">' +
    "<thead><tr>" +
    "<th>Step</th><th>Latency A</th><th>Latency B</th><th>Diff</th>" +
    "<th>Tokens A</th><th>Tokens B</th><th>Diff</th>" +
    "</tr></thead>" +
    "<tbody>" + rows + "</tbody>" +
    "</table></div>"
  );
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

function exportRun(runId) {
  var a = document.createElement("a");
  a.href = "/api/runs/" + runId + "/export";
  a.download = "trace-" + runId.slice(0, 8) + ".jsonl";
  a.click();
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {
  loadRuns();
  setInterval(loadRuns, 6000);
});
