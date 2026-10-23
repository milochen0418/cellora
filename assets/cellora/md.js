// Markdown cells and text/markdown outputs: GitHub-flavoured Markdown (marked), LaTeX math with
// KaTeX ($...$ and $$...$$, as Jupyter writes it), sanitized with DOMPurify. Headings get ids, so
// the outline can point at them; links open in a new tab.
import { Marked } from "marked";
import markedKatex from "marked-katex-extension";
import DOMPurify from "dompurify";
import "katex/dist/katex.min.css";

const marked = new Marked({ gfm: true, breaks: false });
marked.use(markedKatex({ throwOnError: false, nonStandard: true, output: "html" }));

let slugCounter = 0;

export function slug(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || `h-${++slugCounter}`;
}

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A" && node.getAttribute("href")) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/** HTML of a Markdown text; `attachments` resolves `attachment:name` images of the .ipynb. */
export function renderMarkdown(source, attachments, idPrefix = "") {
  let text = String(source || "");
  if (attachments && typeof attachments === "object") {
    text = text.replace(/\(attachment:([^)\s]+)\)/g, (all, name) => {
      const bundle = attachments[name];
      if (!bundle) return all;
      const mime = Object.keys(bundle).find((k) => k.startsWith("image/"));
      return mime ? `(data:${mime};base64,${bundle[mime]})` : all;
    });
  }
  let html;
  try {
    html = marked.parse(text);
  } catch (err) {
    html = `<pre>${escapeHtml(text)}</pre>`;
  }
  const clean = DOMPurify.sanitize(html, { ADD_ATTR: ["target"], FORBID_TAGS: ["style", "script", "iframe", "form"] });
  // Ids on headings (after sanitizing, which drops user-written ids).
  return clean.replace(/<h([1-6])>(.*?)<\/h\1>/g, (all, level, inner) => `<h${level} id="${idPrefix}${slug(inner)}">${inner}</h${level}>`);
}

export function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function sanitizeHtml(html) {
  return DOMPurify.sanitize(String(html || ""), { FORBID_TAGS: ["script", "iframe", "form"], ADD_ATTR: ["target"] });
}

/** The headings of a Markdown text: [{level, text}] (for the outline). */
export function headings(source) {
  const out = [];
  let fence = false;
  String(source || "").split("\n").forEach((line) => {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (fence) return;
    const m = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (m) out.push({ level: m[1].length, text: m[2].replace(/[*_`]/g, "") });
  });
  return out;
}

// ------------------------------------------------------------ terminal text

const ANSI_COLORS = ["#1f2937", "#dc2626", "#16a34a", "#ca8a04", "#2563eb", "#9333ea", "#0891b2", "#9ca3af"];
const ANSI_BRIGHT = ["#6b7280", "#ef4444", "#22c55e", "#eab308", "#3b82f6", "#a855f7", "#06b6d4", "#f3f4f6"];

/** Printed text and tracebacks as HTML: escaped, with the terminal's colours. */
export function ansiToHtml(text) {
  const parts = String(text || "").split(/\x1b\[([0-9;]*)m/);
  let html = "";
  let fg = null;
  let bg = null;
  let bold = false;
  let underline = false;
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const codes = parts[i] === "" ? [0] : parts[i].split(";").map((c) => parseInt(c, 10));
      for (let k = 0; k < codes.length; k++) {
        const c = codes[k];
        if (c === 0) { fg = bg = null; bold = underline = false; }
        else if (c === 1) bold = true;
        else if (c === 4) underline = true;
        else if (c === 22) bold = false;
        else if (c === 24) underline = false;
        else if (c >= 30 && c <= 37) fg = ANSI_COLORS[c - 30];
        else if (c === 39) fg = null;
        else if (c >= 40 && c <= 47) bg = ANSI_COLORS[c - 40];
        else if (c === 49) bg = null;
        else if (c >= 90 && c <= 97) fg = ANSI_BRIGHT[c - 90];
        else if (c >= 100 && c <= 107) bg = ANSI_BRIGHT[c - 100];
        else if ((c === 38 || c === 48) && codes[k + 1] === 5) {
          const n = codes[k + 2];
          const col = n < 8 ? ANSI_COLORS[n] : n < 16 ? ANSI_BRIGHT[n - 8] : null;
          if (c === 38) fg = col; else bg = col;
          k += 2;
        } else if ((c === 38 || c === 48) && codes[k + 1] === 2) {
          const col = `rgb(${codes[k + 2]},${codes[k + 3]},${codes[k + 4]})`;
          if (c === 38) fg = col; else bg = col;
          k += 4;
        }
      }
      continue;
    }
    if (!parts[i]) continue;
    const esc = escapeHtml(parts[i].replace(/\x1b\[[0-9;]*[A-Za-z]/g, ""));
    const style = [];
    if (fg) style.push(`color:${fg}`);
    if (bg) style.push(`background:${bg}`);
    if (bold) style.push("font-weight:600");
    if (underline) style.push("text-decoration:underline");
    html += style.length ? `<span style="${style.join(";")}">${esc}</span>` : esc;
  }
  return html;
}

/** Text as a terminal shows it: a carriage return rewrites its line. */
export function fixCarriageReturns(text) {
  if (text.indexOf("\r") < 0) return text;
  return text.split("\n").map((line) => {
    if (line.indexOf("\r") < 0) return line;
    let out = "";
    line.split("\r").forEach((seg) => { out = seg + out.slice(seg.length); });
    return out;
  }).join("\n");
}
