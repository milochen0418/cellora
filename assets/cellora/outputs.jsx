// What a code cell's run showed: printed text, errors, pictures, HTML, Markdown, LaTeX, JSON, and
// DataFrames as an interactive table with charts (like Databricks' display()). Outputs come from
// the shared document, written by the server while the kernel runs, so everyone sees them as
// they arrive.
import React, { useEffect, useMemo, useRef, useState } from "react";
import katex from "katex";
import { Chart, registerables } from "chart.js";
import { ansiToHtml, fixCarriageReturns, renderMarkdown, sanitizeHtml } from "./md.js";
import { Y, ours, setOurs } from "./ydoc.js";

Chart.register(...registerables);

const TABLE = "application/vnd.cellora.table+json";
// Categorical slots, in this fixed order (validated for colour-blind separation; see the
// dataviz palette): a series keeps its slot by its column, never by its rank.
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const INK = { primary: "#0b0b0b", secondary: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7", surface: "#ffffff" };
const MAX_SERIES = 8;
const MAX_SCATTER_SERIES = 3;
const MAX_PIE = 6;

const ORDER = [TABLE, "application/vnd.plotly.v1+json", "text/html", "image/svg+xml", "image/png", "image/jpeg", "image/gif", "text/markdown", "text/latex", "application/json", "text/plain"];

export function useDeep(ytype) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!ytype) return undefined;
    let raf = 0;
    const fn = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; setTick((t) => t + 1); });
    };
    ytype.observeDeep(fn);
    return () => {
      ytype.unobserveDeep(fn);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ytype]);
}

function parse(json, fallback) {
  if (typeof json !== "string") return json || fallback;
  try {
    return JSON.parse(json);
  } catch (err) {
    return fallback;
  }
}

const parsedCache = new Map();
function parsedData(text) {
  if (typeof text !== "string") return text || {};
  let v = parsedCache.get(text);
  if (!v) {
    v = parse(text, {});
    if (parsedCache.size > 200) parsedCache.clear();
    parsedCache.set(text, v);
  }
  return v;
}

function joinText(v) {
  return Array.isArray(v) ? v.join("") : String(v == null ? "" : v);
}

export function Outputs({ entry, cell, labels, onEvent, collapsed }) {
  const outputs = cell.get("outputs");
  useDeep(outputs);
  const [expanded, setExpanded] = useState(false);
  const boxRef = useRef(null);
  const [tall, setTall] = useState(false);
  const list = outputs instanceof Y.Array ? outputs.toArray() : [];

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setTall(el.scrollHeight > 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, [list.length]);

  if (!list.length) return null;
  if (collapsed) {
    return (
      <button type="button" className="cl-outputs-collapsed" onClick={() => onEvent({ type: "toggle_outputs" })}>
        {labels.outputs_hidden || "Output hidden"} · {list.length}
      </button>
    );
  }
  const views = ours(cell).views || {};
  const setView = (i, v) => setOurs(entry, cell, { views: { ...views, [i]: v } });
  return (
    <div className={"cl-outputs" + (tall && !expanded ? " is-capped" : "")} ref={boxRef}>
      {list.map((o, i) => (
        <Output key={i} o={o} index={i} labels={labels} onEvent={onEvent} view={views[i] || {}} setView={(v) => setView(i, v)} />
      ))}
      {tall && (
        <button type="button" className="cl-outputs-more" onClick={() => setExpanded((x) => !x)}>
          {expanded ? labels.show_less || "Show less" : labels.show_all || "Show all output"}
        </button>
      )}
    </div>
  );
}

function Output({ o, index, labels, onEvent, view, setView }) {
  const kind = o.get("output_type");
  if (kind === "stream") {
    const text = o.get("text");
    const s = fixCarriageReturns(text instanceof Y.Text ? text.toString() : joinText(text));
    return <pre className={"cl-stream" + (o.get("name") === "stderr" ? " is-stderr" : "")} dangerouslySetInnerHTML={{ __html: ansiToHtml(s) }} />;
  }
  if (kind === "error") {
    return <ErrorOutput o={o} labels={labels} onEvent={onEvent} />;
  }
  const data = parsedData(o.get("data"));
  const metadata = parsedData(o.get("metadata"));
  return <Rich data={data} metadata={metadata} labels={labels} view={view} setView={setView} />;
}

function ErrorOutput({ o, labels, onEvent }) {
  const [full, setFull] = useState(false);
  const tb = parse(o.get("traceback"), []);
  const ename = o.get("ename") || "Error";
  const evalue = o.get("evalue") || "";
  const lines = Array.isArray(tb) ? tb : [];
  // The last frame says where it failed; the rest is behind "Show traceback".
  const shown = full ? lines : lines.slice(-2);
  return (
    <div className="cl-error">
      <div className="cl-error-head">
        <span className="cl-error-name">{ename}</span>
        <span className="cl-error-value">{evalue}</span>
      </div>
      {lines.length > 0 && <pre className="cl-error-tb" dangerouslySetInnerHTML={{ __html: ansiToHtml(shown.join("\n")) }} />}
      <div className="cl-error-actions">
        <button type="button" className="cl-btn cl-btn-ai" onClick={() => onEvent({ type: "ai_fix" })}>
          <Sparkles /> {labels.fix_with_ai || "Fix with AI"}
        </button>
        <button type="button" className="cl-btn" onClick={() => onEvent({ type: "ai_explain" })}>
          {labels.explain_error || "Explain"}
        </button>
        {lines.length > 2 && (
          <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setFull((x) => !x)}>
            {full ? labels.hide_traceback || "Hide traceback" : labels.show_traceback || "Show traceback"}
          </button>
        )}
      </div>
    </div>
  );
}

export function Sparkles() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9.94 14.06 8 20l-1.94-5.94L0 12l6.06-1.94L8 4l1.94 6.06L16 12z" transform="translate(2 0) scale(0.9)" />
      <path d="M20 3v4M22 5h-4M18 17v3M19.5 18.5h-3" />
    </svg>
  );
}

function Rich({ data, metadata, labels, view, setView }) {
  const mime = ORDER.find((m) => data[m] !== undefined) || Object.keys(data)[0];
  if (!mime) return null;
  const value = data[mime];
  if (mime === TABLE) {
    const table = typeof value === "string" ? parse(value, null) : value;
    if (table && Array.isArray(table.columns)) return <DataTable table={table} labels={labels} view={view} setView={setView} />;
  }
  if (mime === "text/html" || mime === "application/vnd.plotly.v1+json") {
    const html = joinText(data["text/html"] || "");
    if (!html) return <pre className="cl-plain">{joinText(data["text/plain"] || "")}</pre>;
    return /<script|<style|<link|<iframe|<canvas|<svg/i.test(html) ? <HtmlFrame html={html} /> : <div className="cl-html" dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }} />;
  }
  if (mime.startsWith("image/")) {
    const md = (metadata && metadata[mime]) || {};
    const style = {};
    if (md.width) style.width = `${md.width}px`;
    if (md.height && md.width) style.height = "auto";
    const src = mime === "image/svg+xml"
      ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(joinText(value))}`
      : `data:${mime};base64,${joinText(value).replace(/\s/g, "")}`;
    return <img className="cl-image" src={src} style={style} alt="" />;
  }
  if (mime === "text/markdown") {
    return <div className="cl-md prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(joinText(value)) }} />;
  }
  if (mime === "text/latex") {
    const tex = joinText(value).trim().replace(/^\$\$?|\$\$?$/g, "");
    let html;
    try {
      html = katex.renderToString(tex, { displayMode: true, throwOnError: false });
    } catch (err) {
      html = "";
    }
    return html ? <div className="cl-latex" dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="cl-plain">{tex}</pre>;
  }
  if (mime === "application/json") {
    return <pre className="cl-plain">{JSON.stringify(value, null, 2)}</pre>;
  }
  return <pre className="cl-plain" dangerouslySetInnerHTML={{ __html: ansiToHtml(joinText(data["text/plain"] !== undefined ? data["text/plain"] : value)) }} />;
}

let frameSeq = 0;

/** HTML that runs its own scripts (plotly, widgets): in a sandboxed frame without access to
 * the app (no same-origin), as tall as its content. */
function HtmlFrame({ html }) {
  const id = useMemo(() => `f${++frameSeq}${Math.random().toString(36).slice(2, 6)}`, []);
  const [height, setHeight] = useState(60);
  useEffect(() => {
    const onMsg = (e) => {
      if (e.data && e.data.celloraFrame === id && typeof e.data.h === "number") setHeight(Math.min(Math.max(20, e.data.h + 4), 4000));
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [id]);
  const srcDoc = `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>html,body{margin:0;padding:0;background:transparent;font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111827}table{border-collapse:collapse}th,td{padding:4px 8px;border-bottom:1px solid #e5e7eb;text-align:right}th{background:#f9fafb}</style></head><body>${html}<script>(function(){var send=function(){parent.postMessage({celloraFrame:${JSON.stringify(id)},h:Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)},"*")};try{new ResizeObserver(send).observe(document.body)}catch(e){}window.addEventListener("load",send);setTimeout(send,50);setTimeout(send,600);send();})();</script></body></html>`;
  return <iframe className="cl-frame" title="output" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" srcDoc={srcDoc} style={{ height }} />;
}

// ------------------------------------------------------------ the table

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

function cellText(v, kind) {
  if (v === null || v === undefined) return "";
  if (kind === "number" && typeof v === "number") return nf.format(v);
  if (kind === "date" && typeof v === "string") return v.replace("T00:00:00.000", "").replace("T", " ").replace(/\.000$/, "");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function KindIcon({ kind }) {
  const text = { number: "#", date: "◷", bool: "✓", text: "Aa" }[kind] || "Aa";
  return <span className="cl-kind" aria-hidden="true">{text}</span>;
}

function toCSV(columns, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map((c) => esc(c.name)).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

function DataTable({ table, labels, view, setView }) {
  const { columns, rows } = table;
  const [sort, setSort] = useState(null);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(100);
  const tab = view.tab === "chart" ? "chart" : "table";

  const filtered = useMemo(() => {
    let out = rows;
    const q = query.trim().toLowerCase();
    if (q) out = out.filter((r) => r.some((v) => v !== null && String(v).toLowerCase().includes(q)));
    if (sort) {
      const { col, dir } = sort;
      out = [...out].sort((a, b) => {
        const x = a[col];
        const y = b[col];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (x < y ? -1 : 1) * dir;
      });
    }
    return out;
  }, [rows, query, sort]);

  const download = () => {
    const blob = new Blob([toCSV(columns, filtered)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "table.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  const total = table.total || rows.length;
  const count = (labels.rows_count || "{shown} of {total} rows").replace("{shown}", nf.format(Math.min(limit, filtered.length))).replace("{total}", nf.format(query ? filtered.length : total));
  return (
    <div className="cl-table-box">
      <div className="cl-table-bar">
        <div className="cl-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "table"} className={tab === "table" ? "is-on" : ""} onClick={() => setView({ ...view, tab: "table" })}>
            {labels.table || "Table"}
          </button>
          <button type="button" role="tab" aria-selected={tab === "chart"} className={tab === "chart" ? "is-on" : ""} onClick={() => setView({ ...view, tab: "chart" })}>
            {labels.chart || "Chart"}
          </button>
        </div>
        {tab === "table" && (
          <input className="cl-table-search" placeholder={labels.filter_rows || "Filter rows…"} value={query} onChange={(e) => { setQuery(e.target.value); setLimit(100); }} />
        )}
        <span className="cl-table-count">{count}</span>
        <button type="button" className="cl-btn cl-btn-ghost" onClick={download} title={labels.download_csv || "Download CSV"}>
          ⤓ CSV
        </button>
      </div>
      {tab === "table" ? (
        <div className="cl-table-scroll">
          <table className="cl-table">
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th
                    key={i}
                    className={(c.kind === "number" ? "is-num" : "") + (c.index ? " is-index" : "")}
                    onClick={() => setSort((s) => (!s || s.col !== i ? { col: i, dir: 1 } : s.dir === 1 ? { col: i, dir: -1 } : null))}
                    title={c.dtype}
                  >
                    <KindIcon kind={c.kind} />
                    <span>{c.name}</span>
                    {sort && sort.col === i && <span className="cl-sort">{sort.dir === 1 ? "▲" : "▼"}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, limit).map((r, ri) => (
                <tr key={ri}>
                  {r.map((v, ci) => (
                    <td key={ci} className={(columns[ci] && columns[ci].kind === "number" ? "is-num" : "") + (columns[ci] && columns[ci].index ? " is-index" : "") + (v === null ? " is-null" : "")}>
                      {v === null ? "null" : cellText(v, columns[ci] && columns[ci].kind)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > limit && (
            <button type="button" className="cl-table-more" onClick={() => setLimit((l) => l + 200)}>
              {labels.show_more_rows || "Show more rows"}
            </button>
          )}
          {table.shown < total && (
            <div className="cl-table-note">{(labels.table_first_rows || "The first {shown} rows of {total} are here; the chart and filter use them.").replace("{shown}", nf.format(table.shown)).replace("{total}", nf.format(total))}</div>
          )}
        </div>
      ) : (
        <ChartView table={table} labels={labels} config={view.chart || {}} setConfig={(chart) => setView({ ...view, tab: "chart", chart })} />
      )}
    </div>
  );
}

// ------------------------------------------------------------ charts

function defaultConfig(columns) {
  const nums = columns.map((c, i) => ({ ...c, i })).filter((c) => c.kind === "number");
  const cats = columns.map((c, i) => ({ ...c, i })).filter((c) => c.kind !== "number");
  const date = cats.find((c) => c.kind === "date");
  const x = (date || cats[0] || nums[0] || { name: "" }).name;
  const y = nums.filter((c) => c.name !== x).slice(0, 1).map((c) => c.name);
  return { type: date ? "line" : "bar", x, y, agg: date ? "sum" : "sum" };
}

function aggregate(values, agg) {
  const nums = values.filter((v) => typeof v === "number" && isFinite(v));
  if (agg === "count") return values.length;
  if (!nums.length) return null;
  if (agg === "mean") return nums.reduce((a, b) => a + b, 0) / nums.length;
  if (agg === "min") return Math.min(...nums);
  if (agg === "max") return Math.max(...nums);
  return nums.reduce((a, b) => a + b, 0);
}

// A vertical hairline at the pointer's X (line and area charts).
const crosshair = {
  id: "clCrosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip && chart.tooltip.getActiveElements && chart.tooltip.getActiveElements();
    if (!active || !active.length || !chart.$clCrosshair) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = INK.axis;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

function ChartView({ table, labels, config, setConfig }) {
  const { columns, rows } = table;
  const cfg = { ...defaultConfig(columns), ...config };
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const [yOpen, setYOpen] = useState(false);
  const colIndex = (name) => columns.findIndex((c) => c.name === name);
  const numeric = columns.filter((c) => c.kind === "number");
  const xi = colIndex(cfg.x);
  const yCols = (cfg.y || []).filter((n) => colIndex(n) >= 0);
  const cap = cfg.type === "scatter" ? MAX_SCATTER_SERIES : cfg.type === "pie" ? 1 : MAX_SERIES;
  const shownY = yCols.slice(0, cap);
  const dropped = yCols.length - shownY.length;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    if (chartRef.current) {
      chartRef.current.destroy();
      chartRef.current = null;
    }
    if (xi < 0 || (!shownY.length && cfg.agg !== "count")) return undefined;
    const ys = shownY.length ? shownY : ["count"];
    // A column keeps its colour whatever else is shown: its place among the number columns.
    const color = (name) => SERIES[Math.max(0, numeric.findIndex((c) => c.name === name)) % SERIES.length];
    let data;
    const xKind = columns[xi].kind;
    if (cfg.type === "scatter") {
      data = {
        datasets: ys.map((name) => {
          const yi = colIndex(name);
          return {
            label: name,
            data: rows.filter((r) => typeof r[xi] === "number" && typeof r[yi] === "number").map((r) => ({ x: r[xi], y: r[yi] })),
            backgroundColor: color(name),
            borderColor: INK.surface,
            borderWidth: 2,
            pointRadius: 4,
            pointHoverRadius: 6,
            hitRadius: 12,
          };
        }),
      };
    } else {
      // Group by X, aggregate each Y.
      const groups = new Map();
      rows.forEach((r) => {
        const key = r[xi] === null || r[xi] === undefined ? "∅" : String(r[xi]);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(r);
      });
      let keys = Array.from(groups.keys());
      if (xKind === "number" || xKind === "date") keys.sort((a, b) => (xKind === "number" ? Number(a) - Number(b) : a < b ? -1 : 1));
      const agg = cfg.agg || "sum";
      const value = (key, name) => {
        const yi = colIndex(name);
        const vals = groups.get(key).map((r) => (yi >= 0 ? r[yi] : 1));
        return agg === "none" ? (vals[0] ?? null) : aggregate(vals, name === "count" ? "count" : agg);
      };
      if (cfg.type === "pie") {
        const name = ys[0];
        let pairs = keys.map((k) => [k, value(k, name) || 0]).sort((a, b) => b[1] - a[1]);
        if (pairs.length > MAX_PIE) {
          const rest = pairs.slice(MAX_PIE - 1).reduce((s, p) => s + p[1], 0);
          pairs = [...pairs.slice(0, MAX_PIE - 1), [labels.other || "Other", rest]];
        }
        data = {
          labels: pairs.map((p) => cellText(p[0], xKind)),
          datasets: [{ label: name, data: pairs.map((p) => p[1]), backgroundColor: pairs.map((_, i) => SERIES[i % SERIES.length]), borderColor: INK.surface, borderWidth: 2 }],
        };
      } else {
        keys = keys.slice(0, 500);
        const area = cfg.type === "area";
        data = {
          labels: keys.map((k) => cellText(k, xKind)),
          datasets: ys.map((name) => {
            const c = color(name);
            const base = { label: name, data: keys.map((k) => value(k, name)) };
            if (cfg.type === "bar") {
              return { ...base, backgroundColor: c, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "start", maxBarThickness: 24, barPercentage: 0.82, categoryPercentage: 0.8 };
            }
            return {
              ...base,
              borderColor: c,
              backgroundColor: area ? c + "1a" : c,
              fill: area ? "origin" : false,
              borderWidth: 2,
              tension: 0,
              borderJoinStyle: "round",
              borderCapStyle: "round",
              pointRadius: keys.length <= 30 ? 4 : 0,
              pointBackgroundColor: c,
              pointBorderColor: INK.surface,
              pointBorderWidth: 2,
              pointHoverRadius: 5,
              hitRadius: 12,
            };
          }),
        };
      }
    }
    const many = (data.datasets || []).length > 1;
    const lineLike = cfg.type === "line" || cfg.type === "area";
    const chart = new Chart(canvas, {
      type: cfg.type === "area" ? "line" : cfg.type,
      data,
      plugins: [crosshair],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
        interaction: cfg.type === "scatter" ? { mode: "nearest", intersect: false } : cfg.type === "pie" ? { mode: "nearest", intersect: true } : { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: cfg.type === "pie" || many,
            position: "top",
            align: "start",
            labels: { color: INK.secondary, boxWidth: 12, boxHeight: lineLike ? 2 : 12, usePointStyle: lineLike, pointStyle: lineLike ? "line" : "rect", font: { size: 12 } },
          },
          tooltip: {
            backgroundColor: "#111827",
            titleColor: "#f9fafb",
            bodyColor: "#f9fafb",
            usePointStyle: true,
            boxPadding: 4,
            padding: 10,
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed && typeof ctx.parsed === "object" ? (cfg.type === "scatter" ? ctx.parsed.y : ctx.parsed.y ?? ctx.parsed) : ctx.parsed;
                const shown = typeof v === "number" ? nf.format(v) : String(v ?? "");
                return ` ${shown}   ${ctx.dataset.label || ""}`;
              },
              labelPointStyle: () => ({ pointStyle: "line", rotation: 0 }),
            },
          },
        },
        scales: cfg.type === "pie" ? {} : {
          x: {
            type: cfg.type === "scatter" ? "linear" : "category",
            grid: { display: cfg.type === "scatter", color: INK.grid, lineWidth: 1 },
            border: { color: INK.axis },
            ticks: { color: INK.muted, maxRotation: 0, autoSkip: true, font: { size: 11 } },
            title: { display: true, text: cfg.x, color: INK.secondary, font: { size: 12 } },
          },
          y: {
            beginAtZero: cfg.type !== "scatter",
            grid: { color: INK.grid, lineWidth: 1 },
            border: { display: false },
            ticks: { color: INK.muted, font: { size: 11 }, callback: (v) => (Math.abs(v) >= 1000 ? compact.format(v) : nf.format(v)) },
            title: { display: !many && cfg.type !== "scatter" ? false : cfg.type === "scatter", text: ys[0], color: INK.secondary, font: { size: 12 } },
          },
        },
      },
    });
    chart.$clCrosshair = lineLike;
    chartRef.current = chart;
    return () => {
      chart.destroy();
      if (chartRef.current === chart) chartRef.current = null;
    };
  }, [table, JSON.stringify(cfg)]);

  const set = (patch) => setConfig({ ...cfg, ...patch });
  const toggleY = (name) => {
    const has = yCols.includes(name);
    set({ y: has ? yCols.filter((n) => n !== name) : [...yCols, name] });
  };
  const types = [["bar", labels.chart_bar || "Bar"], ["line", labels.chart_line || "Line"], ["area", labels.chart_area || "Area"], ["scatter", labels.chart_scatter || "Scatter"], ["pie", labels.chart_pie || "Pie"]];
  const aggs = [["sum", labels.agg_sum || "Sum"], ["mean", labels.agg_mean || "Average"], ["count", labels.agg_count || "Count"], ["min", labels.agg_min || "Min"], ["max", labels.agg_max || "Max"], ["none", labels.agg_none || "None"]];
  const title = shownY.length === 1 && cfg.type !== "pie" ? `${shownY[0]}${cfg.type !== "scatter" && cfg.agg !== "none" ? ` (${(aggs.find((a) => a[0] === cfg.agg) || ["", ""])[1].toLowerCase()})` : ""} · ${cfg.x}` : "";
  return (
    <div className="cl-chart-box">
      <div className="cl-chart-controls">
        <label>
          <span>{labels.chart_type || "Type"}</span>
          <select value={cfg.type} onChange={(e) => set({ type: e.target.value })}>
            {types.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>
          <span>X</span>
          <select value={cfg.x} onChange={(e) => set({ x: e.target.value })}>
            {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <div className="cl-chart-y">
          <span>Y</span>
          <button type="button" className="cl-select-like" onClick={() => setYOpen((o) => !o)}>
            {yCols.length ? yCols.join(", ") : labels.choose_columns || "Choose columns"} ▾
          </button>
          {yOpen && (
            <div className="cl-popover" onMouseLeave={() => setYOpen(false)}>
              {numeric.length === 0 && <div className="cl-popover-empty">{labels.no_numeric || "No number columns"}</div>}
              {numeric.map((c) => (
                <label key={c.name} className="cl-check">
                  <input type="checkbox" checked={yCols.includes(c.name)} onChange={() => toggleY(c.name)} />
                  <span className="cl-swatch" style={{ background: SERIES[Math.max(0, numeric.findIndex((n) => n.name === c.name)) % SERIES.length] }} />
                  {c.name}
                </label>
              ))}
            </div>
          )}
        </div>
        {cfg.type !== "scatter" && (
          <label>
            <span>{labels.aggregate || "Aggregate"}</span>
            <select value={cfg.agg} onChange={(e) => set({ agg: e.target.value })}>
              {aggs.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
      </div>
      {title && <div className="cl-chart-title">{title}</div>}
      {dropped > 0 && <div className="cl-table-note">{(labels.series_cap || "Showing the first {n} columns; this chart type stays readable with no more.").replace("{n}", String(cap))}</div>}
      <div className="cl-chart-canvas">
        <canvas ref={canvasRef} role="img" aria-label={title || "chart"} />
      </div>
    </div>
  );
}
