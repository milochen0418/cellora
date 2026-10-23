// The code editor of the cells: Monaco (VS Code's editor), loaded once from jsdelivr, with
// - the kernel's completions (Tab, Ctrl+Space, after a dot) and docs (hover),
// - the AI's grey suggestion while you type (Tab takes it),
// - the cell's text bound to the shared document (Yjs), with everyone's carets and selections in
//   their colour and a name tag (as in CoDoc in MD), and undo that takes back only your own edits.
import loader from "@monaco-editor/loader";
import { httpURL, Y, textUndo } from "./ydoc.js";
import { getToken } from "$/utils/state";

const MONACO_VERSION = "0.55.1";
loader.config({ paths: { vs: `https://cdn.jsdelivr.net/npm/monaco-editor@${MONACO_VERSION}/min/vs` } });

let monacoPromise = null;
// model -> {nbId, cellId, peer, aiInline: () => bool}
const modelInfo = new WeakMap();

// Monaco rejects promises nobody waits for when it cancels its own work: a request overtaken by
// more typing ("Canceled", Monaco's CancellationError), or a diff view closed while its worker
// still computes ("no diff result available"). Nothing is wrong, so they aren't reported.
if (typeof window !== "undefined" && !window.__clDiffGuard) {
  window.__clDiffGuard = true;
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    const text = String((r && (r.message || r.name)) || r || "");
    if (r && (r.name === "Canceled" || text === "Canceled" || /no diff result available/.test(text))) e.preventDefault();
  });
}

export function loadMonaco() {
  if (!monacoPromise) {
    monacoPromise = loader.init().then((monaco) => {
      setup(monaco);
      return monaco;
    });
  }
  return monacoPromise;
}

export function registerModel(model, info) {
  modelInfo.set(model, info);
}

async function post(path, body, signal) {
  const res = await fetch(httpURL(path), {
    method: "POST",
    // text/plain: a "simple" request, no CORS preflight when the backend is on another port.
    headers: { "Content-Type": "text/plain;charset=UTF-8" },
    body: JSON.stringify({ ...body, token: getToken() }),
    signal,
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// Jupyter counts the cursor in characters (code points), Monaco in UTF-16 units.
function codePoints(text, utf16Offset) {
  return Array.from(text.slice(0, utf16Offset)).length;
}

function utf16(text, cpOffset) {
  return Array.from(text).slice(0, cpOffset).join("").length;
}

const KIND = {
  function: "Function", method: "Method", class: "Class", module: "Module", instance: "Variable",
  statement: "Keyword", keyword: "Keyword", param: "Variable", path: "File", property: "Property",
};

function setup(monaco) {
  monaco.editor.defineTheme("cellora", {
    base: "vs",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6B7280", fontStyle: "italic" },
      { token: "keyword", foreground: "7C3AED" },
      { token: "string", foreground: "0F766E" },
      { token: "number", foreground: "B45309" },
    ],
    colors: {
      "editor.background": "#00000000",
      "editor.lineHighlightBackground": "#00000000",
      "editor.lineHighlightBorder": "#00000000",
      "editorLineNumber.foreground": "#C4C9D4",
      "editorLineNumber.activeForeground": "#6B7280",
      "editorGutter.background": "#00000000",
      "editorCursor.foreground": "#4F46E5",
      "editor.selectionBackground": "#C7D2FE",
      "editor.inactiveSelectionBackground": "#E0E7FF",
      "editorGhostText.foreground": "#9CA3AF",
      "editorSuggestWidget.selectedBackground": "#EEF2FF",
    },
  });

  // The kernel's completions.
  monaco.languages.registerCompletionItemProvider("python", {
    triggerCharacters: ["."],
    async provideCompletionItems(model, position, _context, token) {
      const info = modelInfo.get(model);
      if (!info) return { suggestions: [] };
      const code = model.getValue();
      const offset = model.getOffsetAt(position);
      const ctrl = new AbortController();
      token.onCancellationRequested(() => ctrl.abort());
      let reply;
      try {
        reply = await post("/_cellora/complete", { nb: info.nbId, peer: info.peer, code, cursor: codePoints(code, offset) }, ctrl.signal);
      } catch (err) {
        return { suggestions: [] };
      }
      const matches = reply.matches || [];
      if (!matches.length) return { suggestions: [] };
      const start = model.getPositionAt(utf16(code, reply.cursor_start));
      const end = model.getPositionAt(utf16(code, reply.cursor_end));
      const range = new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column);
      const types = new Map((reply.types || []).map((t) => [t.text, t]));
      return {
        suggestions: matches.map((m, i) => {
          const t = types.get(m) || {};
          const kind = monaco.languages.CompletionItemKind[KIND[t.type] || "Text"];
          return { label: m, kind, insertText: m, range, sortText: String(i).padStart(4, "0"), detail: t.signature || t.type || "" };
        }),
      };
    },
  });

  // The kernel's docs of the name under the mouse.
  monaco.languages.registerHoverProvider("python", {
    async provideHover(model, position, token) {
      const info = modelInfo.get(model);
      const word = model.getWordAtPosition(position);
      if (!info || !word || info.kernelIdle && !info.kernelIdle()) return null;
      const code = model.getValue();
      const offset = model.getOffsetAt({ lineNumber: position.lineNumber, column: word.endColumn });
      const ctrl = new AbortController();
      token.onCancellationRequested(() => ctrl.abort());
      try {
        const reply = await post("/_cellora/inspect", { nb: info.nbId, peer: info.peer, code, cursor: codePoints(code, offset) }, ctrl.signal);
        const text = (reply.text || "").trim();
        if (!text) return null;
        const short = text.length > 2400 ? text.slice(0, 2400) + "\n…" : text;
        return {
          range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
          contents: [{ value: "```text\n" + short.replace(/```/g, "ˋˋˋ") + "\n```" }],
        };
      } catch (err) {
        return null;
      }
    },
  });

  // The AI's grey suggestion at the cursor.
  monaco.languages.registerInlineCompletionsProvider("python", {
    async provideInlineCompletions(model, position, context, token) {
      const info = modelInfo.get(model);
      if (!info || !info.aiInline()) return { items: [] };
      const line = model.getLineContent(position.lineNumber);
      const before = line.slice(0, position.column - 1);
      const after = line.slice(position.column - 1);
      // Only at the end of a line (or before closing brackets and quotes), and not in an empty
      // cell: a suggestion in front of code already there reads as clutter.
      if (!/^[\s)\]}"'`,:;]*$/.test(after)) return { items: [] };
      if (!model.getValue().trim() || !before.trim() && !line.trim() && position.lineNumber === 1) return { items: [] };
      // Wait for a pause in the typing (Monaco asks after every key).
      const automatic = context.triggerKind === monaco.languages.InlineCompletionTriggerKind.Automatic;
      await new Promise((r) => setTimeout(r, automatic ? 420 : 0));
      if (token.isCancellationRequested) return { items: [] };
      const offset = model.getOffsetAt(position);
      const code = model.getValue();
      const ctrl = new AbortController();
      token.onCancellationRequested(() => ctrl.abort());
      try {
        const reply = await post("/_cellora/inline", {
          nb: info.nbId, peer: info.peer, cell: info.cellId, prefix: code.slice(0, offset), suffix: code.slice(offset),
        }, ctrl.signal);
        const text = reply.text || "";
        if (!text || token.isCancellationRequested) return { items: [] };
        return { items: [{ insertText: text, range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column) }] };
      } catch (err) {
        return { items: [] };
      }
    },
    freeInlineCompletions() {},
    disposeInlineCompletions() {},
  });
}

export const EDITOR_OPTIONS = {
  theme: "cellora",
  fontFamily: "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace",
  fontSize: 13.5,
  lineHeight: 21,
  fontLigatures: false,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  wordWrap: "on",
  wrappingStrategy: "advanced",
  lineNumbers: "off",
  lineNumbersMinChars: 3,
  glyphMargin: false,
  folding: false,
  lineDecorationsWidth: 10,
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  scrollbar: { vertical: "hidden", horizontal: "auto", alwaysConsumeMouseWheel: false, verticalScrollbarSize: 0, horizontalScrollbarSize: 8 },
  padding: { top: 10, bottom: 10 },
  automaticLayout: false,
  quickSuggestions: false,
  suggestOnTriggerCharacters: true,
  acceptSuggestionOnEnter: "on",
  tabCompletion: "off",
  inlineSuggest: { enabled: true, mode: "subwordSmart" },
  contextmenu: true,
  fixedOverflowWidgets: true,
  stickyScroll: { enabled: false },
  guides: { indentation: false },
  occurrencesHighlight: "off",
  renderWhitespace: "none",
  unicodeHighlight: { ambiguousCharacters: false },
  tabSize: 4,
  insertSpaces: true,
  detectIndentation: false,
  bracketPairColorization: { enabled: true },
};

// ------------------------------------------------------------ remote carets (from CoDoc in MD)

const TAG_SHOW_MS = 2000;
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
const FALLBACK_COLOR = "#1E88E5";
const safeColor = (c) => (typeof c === "string" && HEX_COLOR.test(c) ? c : FALLBACK_COLOR);

const rgba = (hex, alpha) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};

export function tagTextColor(hex) {
  const n = parseInt(safeColor(hex).slice(1), 16);
  const lin = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const lum = 0.2126 * lin(n >> 16) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return lum > 0.3 ? "#111827" : "#ffffff";
}

let cursorStyleEl = null;

/** One stylesheet with every remote person's colour, keyed by Yjs clientID. */
export function updateCursorStyles(states, selfID) {
  let css = "";
  states.forEach((state, id) => {
    if (id === selfID || !Number.isInteger(id)) return;
    const color = safeColor(state.user && state.user.color);
    css += `.yRemoteSelection-${id} { background-color: ${rgba(color, 0.25)}; }\n`;
    css += `.yRemoteSelectionHead-${id} { border-left-color: ${color}; color: ${color}; }\n`;
  });
  if (!cursorStyleEl || !cursorStyleEl.isConnected) {
    cursorStyleEl = document.createElement("style");
    cursorStyleEl.id = "cellora-remote-cursors";
    document.head.appendChild(cursorStyleEl);
  }
  if (cursorStyleEl.textContent !== css) cursorStyleEl.textContent = css;
}

class CursorTag {
  constructor(monaco, clientID) {
    this._m = monaco;
    this._id = "cellora.cursorTag." + clientID + "." + Math.random().toString(36).slice(2, 7);
    this.node = document.createElement("div");
    this.node.className = "cl-cursor-tag";
    this.position = null;
    this.active = false;
    this.hovered = false;
    this.timer = 0;
  }
  getId() { return this._id; }
  getDomNode() { return this.node; }
  getPosition() {
    if (!this.position) return null;
    const P = this._m.editor.ContentWidgetPositionPreference;
    return { position: this.position, preference: [P.ABOVE, P.BELOW] };
  }
  refresh() { this.node.classList.toggle("is-visible", this.active || this.hovered); }
}

const createMutex = () => {
  let token = true;
  return (f, g) => {
    if (token) {
      token = false;
      try { f(); } finally { token = true; }
    } else if (g !== undefined) {
      g();
    }
  };
};

/**
 * Binds a Y.Text to a Monaco model and its editor (adapted from y-monaco@0.1.6, MIT © Kevin
 * Jahns, by way of CoDoc in MD): typing goes into the shared text, the others' changes come into
 * the editor, and the carets and selections of everyone in this cell show in their colour.
 * Local edits carry this binding as their origin, so the person's own undo (Y.UndoManager)
 * takes back only what they typed.
 */
export class MonacoBinding {
  constructor(monaco, entry, cellId, ytext, model, editor) {
    this.m = monaco;
    this.entry = entry;
    this.doc = entry.doc;
    this.awareness = entry.awareness;
    this.ytext = ytext;
    this.model = model;
    this.editor = editor;
    this.cellId = cellId;
    this.mux = createMutex();
    this.saved = null;
    this.decorations = [];
    this.tags = new Map();
    this.spots = new Map();
    this.listeners = [];

    this.beforeTx = () => {
      this.mux(() => {
        const sel = editor.getSelection();
        if (!sel || editor.getModel() !== model) {
          this.saved = null;
          return;
        }
        this.saved = {
          start: Y.createRelativePositionFromTypeIndex(ytext, model.getOffsetAt(sel.getStartPosition())),
          end: Y.createRelativePositionFromTypeIndex(ytext, model.getOffsetAt(sel.getEndPosition())),
          dir: sel.getDirection(),
        };
      });
    };
    this.doc.on("beforeAllTransactions", this.beforeTx);

    this.ytextObserver = (event) => {
      this.mux(() => {
        let index = 0;
        event.delta.forEach((op) => {
          if (op.retain !== undefined) {
            index += op.retain;
          } else if (op.insert !== undefined) {
            const pos = model.getPositionAt(index);
            const range = new monaco.Selection(pos.lineNumber, pos.column, pos.lineNumber, pos.column);
            model.applyEdits([{ range, text: op.insert }]);
            index += op.insert.length;
          } else if (op.delete !== undefined) {
            const pos = model.getPositionAt(index);
            const endPos = model.getPositionAt(index + op.delete);
            const range = new monaco.Selection(pos.lineNumber, pos.column, endPos.lineNumber, endPos.column);
            model.applyEdits([{ range, text: "" }]);
          }
        });
        if (this.saved && editor.hasTextFocus()) {
          const s = Y.createAbsolutePositionFromRelativePosition(this.saved.start, this.doc);
          const e = Y.createAbsolutePositionFromRelativePosition(this.saved.end, this.doc);
          if (s && e && s.type === ytext && e.type === ytext) {
            const sp = model.getPositionAt(s.index);
            const ep = model.getPositionAt(e.index);
            editor.setSelection(monaco.Selection.createWithDirection(sp.lineNumber, sp.column, ep.lineNumber, ep.column, this.saved.dir));
          }
        }
      });
      this.scheduleRender();
    };
    ytext.observe(this.ytextObserver);

    const ytv = ytext.toString();
    if (model.getValue() !== ytv) model.setValue(ytv);

    this.changeHandler = model.onDidChangeContent((event) => {
      this.mux(() => {
        this.doc.transact(() => {
          event.changes
            .sort((a, b) => b.rangeOffset - a.rangeOffset)
            .forEach((change) => {
              ytext.delete(change.rangeOffset, change.rangeLength);
              ytext.insert(change.rangeOffset, change.text);
            });
        }, this);
      });
    });

    // Undo / redo: this person's own edits of this cell.
    this.undo = textUndo(entry, cellId, ytext, this);
    this.undo.on("stack-item-added", this.onStackAdded = (ev) => {
      const sel = editor.getSelection();
      if (sel) ev.stackItem.meta.set("sel", { start: model.getOffsetAt(sel.getStartPosition()), end: model.getOffsetAt(sel.getEndPosition()) });
    });
    this.undo.on("stack-item-popped", this.onStackPopped = (ev) => {
      const s = ev.stackItem.meta.get("sel");
      if (s && editor.getModel() === model) {
        const a = model.getPositionAt(Math.min(s.start, model.getValueLength()));
        const b = model.getPositionAt(Math.min(s.end, model.getValueLength()));
        editor.setSelection(new monaco.Selection(a.lineNumber, a.column, b.lineNumber, b.column));
      }
    });

    // Our caret and selection, for the others: only while we are in this editor.
    const publish = () => {
      const sel = editor.getSelection();
      if (!sel) return;
      let anchor = model.getOffsetAt(sel.getStartPosition());
      let head = model.getOffsetAt(sel.getEndPosition());
      if (sel.getDirection() === monaco.SelectionDirection.RTL) [anchor, head] = [head, anchor];
      this.awareness.setLocalStateField("selection", {
        anchor: Y.createRelativePositionFromTypeIndex(ytext, anchor),
        head: Y.createRelativePositionFromTypeIndex(ytext, head),
      });
    };
    this.listeners.push(editor.onDidChangeCursorSelection(() => { if (editor.hasTextFocus()) publish(); }));
    this.listeners.push(editor.onDidFocusEditorText(publish));
    this.listeners.push(editor.onMouseMove((e) => this.hover(e.target && e.target.position)));
    this.listeners.push(editor.onMouseLeave(() => this.hover(null)));

    this.onAwareness = ({ added, updated, removed }) => {
      this.scheduleRender();
      added.concat(updated).forEach((id) => { if (id !== this.doc.clientID) this.flashIds.add(id); });
    };
    this.awareness.on("change", this.onAwareness);
    this.flashIds = new Set();
    this.raf = 0;
    this.scheduleRender();
  }

  /** The carets are drawn on the next frame: never from inside Monaco's own edit or cursor
   * events (Monaco warns that decorations changed from there can leak), and once for many
   * changes. */
  scheduleRender() {
    if (this.raf || this.destroyed) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      if (this.destroyed) return;
      this.render();
      this.flashIds.forEach((id) => this.flash(id));
      this.flashIds.clear();
    });
  }

  render() {
    const { m, model, editor, awareness, ytext, doc } = this;
    if (editor.getModel() !== model) return;
    updateCursorStyles(awareness.getStates(), doc.clientID);
    const next = [];
    const spots = new Map();
    awareness.getStates().forEach((state, clientID) => {
      if (clientID === doc.clientID || !state.selection || state.selection.anchor == null || state.selection.head == null) return;
      let a, h;
      try {
        a = Y.createAbsolutePositionFromRelativePosition(state.selection.anchor, doc);
        h = Y.createAbsolutePositionFromRelativePosition(state.selection.head, doc);
      } catch (err) {
        return;
      }
      if (!a || !h || a.type !== ytext || h.type !== ytext) return;
      let start, end, after = null, before = null;
      if (a.index < h.index) {
        start = model.getPositionAt(a.index);
        end = model.getPositionAt(h.index);
        after = "yRemoteSelectionHead yRemoteSelectionHead-" + clientID;
      } else {
        start = model.getPositionAt(h.index);
        end = model.getPositionAt(a.index);
        before = "yRemoteSelectionHead yRemoteSelectionHead-" + clientID;
      }
      next.push({
        range: new m.Range(start.lineNumber, start.column, end.lineNumber, end.column),
        options: { className: "yRemoteSelection yRemoteSelection-" + clientID, afterContentClassName: after, beforeContentClassName: before, stickiness: 1 },
      });
      spots.set(clientID, { head: model.getPositionAt(h.index), start, end, user: state.user });
    });
    this.decorations = editor.deltaDecorations(this.decorations, next);
    this.spots = spots;
    this.renderTags(spots);
  }

  renderTags(spots) {
    Array.from(this.tags.keys()).forEach((id) => { if (!spots.has(id)) this.removeTag(id); });
    spots.forEach(({ head, user }, id) => {
      const name = (user && typeof user.name === "string" && user.name.trim()) || "Guest";
      const color = safeColor(user && user.color);
      let tag = this.tags.get(id);
      const isNew = !tag;
      if (isNew) {
        tag = new CursorTag(this.m, id);
        this.tags.set(id, tag);
      }
      if (tag.node.textContent !== name) tag.node.textContent = name;
      tag.node.style.backgroundColor = color;
      tag.node.style.color = tagTextColor(color);
      const moved = !tag.position || tag.position.lineNumber !== head.lineNumber || tag.position.column !== head.column;
      tag.position = { lineNumber: head.lineNumber, column: head.column };
      if (isNew) {
        this.editor.addContentWidget(tag);
        this.flash(id);
      } else if (moved) {
        this.editor.layoutContentWidget(tag);
      }
    });
  }

  removeTag(id) {
    const tag = this.tags.get(id);
    if (!tag) return;
    clearTimeout(tag.timer);
    try { this.editor.removeContentWidget(tag); } catch (err) { /* disposed */ }
    this.tags.delete(id);
  }

  flash(id) {
    const tag = this.tags.get(id);
    if (!tag) return;
    tag.active = true;
    tag.refresh();
    clearTimeout(tag.timer);
    tag.timer = setTimeout(() => { tag.active = false; tag.refresh(); }, TAG_SHOW_MS);
  }

  hover(pos) {
    let hit = null;
    if (pos) {
      this.spots.forEach(({ head, start, end }, id) => {
        if (hit !== null) return;
        const nearCaret = pos.lineNumber === head.lineNumber && Math.abs(pos.column - head.column) <= 1;
        const range = new this.m.Range(start.lineNumber, start.column, end.lineNumber, end.column);
        if (nearCaret || (!range.isEmpty() && range.containsPosition(pos))) hit = id;
      });
    }
    this.tags.forEach((tag, id) => {
      const hovered = id === hit;
      if (tag.hovered !== hovered) {
        tag.hovered = hovered;
        tag.refresh();
      }
    });
  }

  /** Puts `next` in as this person's edit (undoable by them). */
  replaceWith(next) {
    const old = this.ytext.toString();
    if (old === next) return;
    let start = 0;
    const limit = Math.min(old.length, next.length);
    while (start < limit && old.charCodeAt(start) === next.charCodeAt(start)) start++;
    let endOld = old.length;
    let endNew = next.length;
    while (endOld > start && endNew > start && old.charCodeAt(endOld - 1) === next.charCodeAt(endNew - 1)) {
      endOld--;
      endNew--;
    }
    this.undo.stopCapturing();
    this.doc.transact(() => {
      if (endOld > start) this.ytext.delete(start, endOld - start);
      if (endNew > start) this.ytext.insert(start, next.slice(start, endNew));
    }, this);
    this.undo.stopCapturing();
  }

  destroy() {
    this.destroyed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    try { this.changeHandler.dispose(); } catch (err) {}
    try { this.ytext.unobserve(this.ytextObserver); } catch (err) {}
    try { this.doc.off("beforeAllTransactions", this.beforeTx); } catch (err) {}
    try { this.undo.off("stack-item-added", this.onStackAdded); this.undo.off("stack-item-popped", this.onStackPopped); } catch (err) {}
    try { this.undo.removeTrackedOrigin(this); } catch (err) {}
    this.listeners.forEach((l) => { try { l.dispose(); } catch (err) {} });
    this.listeners = [];
    Array.from(this.tags.keys()).forEach((id) => this.removeTag(id));
    try { this.editor.deltaDecorations(this.decorations, []); } catch (err) {}
    try { this.awareness.off("change", this.onAwareness); } catch (err) {}
  }
}
