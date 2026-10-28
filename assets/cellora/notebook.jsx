// Cellora's notebook: the cells of the shared document, drawn and edited in the browser.
//
// The page around it (title, people, kernel, side panels, the AI panel) is Reflex; this component
// is the cells. It reads and writes the notebook's Y.Doc (ydoc.js), which the server and every
// other browser on the notebook share, so typing, adding, moving and deleting cells reaches
// everyone at once, and outputs arrive as the kernel writes them. What only the server can do
// (run cells in the kernel, ask the AI) goes to Reflex through onEvent({type, ...}).
import React, { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { acquire, release, subscribe, Y, STRUCTURE, cellById, cellIndex, insertCell, deleteCell, moveCell, moveCellTo, duplicateCell, setCellType, clearOutputs, ours, setOurs, jupyterFlag, setJupyterFlag, replaceText, find } from "./ydoc.js";
import { loadMonaco, registerModel, EDITOR_OPTIONS, MonacoBinding, tagTextColor } from "./editor.js";
import { Outputs, Sparkles, useDeep } from "./outputs.jsx";
import { renderMarkdown, headings, slug } from "./md.js";

const Ctx = createContext(null);
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
const MOD = IS_MAC ? "⌘" : "Ctrl";

function t(labels, key, fallback, params) {
  let s = (labels && labels[key]) || fallback;
  if (params) Object.keys(params).forEach((k) => { s = s.replace(`{${k}}`, params[k]); });
  return s;
}

// ------------------------------------------------------------ hooks

function useEntry(nbId, peer) {
  const [entry, setEntry] = useState(null);
  useEffect(() => {
    if (!nbId) return undefined;
    const e = acquire(nbId, peer);
    setEntry(e);
    return () => release(e);
  }, [nbId]);
  return entry;
}

function useEntryStatus(entry) {
  const [, setTick] = useState(0);
  useEffect(() => (entry ? subscribe(entry, () => setTick((x) => x + 1)) : undefined), [entry]);
  // generation: the document was taken from the server again (ydoc.js reset); the hooks that
  // hold parts of it (the cells, the awareness) read it again then.
  return entry ? { synced: entry.synced, status: entry.status, generation: entry.generation } : { synced: false, status: "connecting", generation: 0 };
}

function useCells(entry, generation) {
  const [cells, setCells] = useState([]);
  useEffect(() => {
    if (!entry) return undefined;
    const arr = entry.cells;
    const read = () => setCells(arr.toArray());
    read();
    arr.observe(read);
    return () => arr.unobserve(read);
  }, [entry, generation]);
  return cells;
}

/** Re-renders when the cell's own fields change (type, run, count, metadata), not its text. */
function useCellFields(cell) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!cell) return undefined;
    const fn = () => setTick((x) => x + 1);
    cell.observe(fn);
    const md = cell.get("metadata");
    if (md instanceof Y.Map) md.observe(fn);
    return () => {
      cell.unobserve(fn);
      if (md instanceof Y.Map) md.unobserve(fn);
    };
  }, [cell]);
}

function useText(ytext) {
  const [text, setText] = useState(() => (ytext ? ytext.toString() : ""));
  useEffect(() => {
    if (!ytext) return undefined;
    setText(ytext.toString());
    let raf = 0;
    const fn = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; setText(ytext.toString()); });
    };
    ytext.observe(fn);
    return () => {
      ytext.unobserve(fn);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ytext]);
  return text;
}

function useMonaco() {
  const [monaco, setMonaco] = useState(null);
  useEffect(() => {
    let alive = true;
    loadMonaco().then((m) => alive && setMonaco(m)).catch((err) => console.warn("[cellora] Monaco didn't load:", err));
    return () => { alive = false; };
  }, []);
  return monaco;
}

/** Who else is on the notebook and in which cell (Yjs awareness): cell id -> [user]. */
function usePresence(entry, generation) {
  const [map, setMap] = useState({ byCell: {}, all: [] });
  useEffect(() => {
    if (!entry) return undefined;
    const aw = entry.awareness;
    const read = () => {
      const byCell = {};
      const all = [];
      aw.getStates().forEach((state, id) => {
        if (id === entry.doc.clientID || !state || !state.user) return;
        const u = { ...state.user, clientID: id, cell: state.cell || "", editing: !!state.editing };
        all.push(u);
        if (u.cell) (byCell[u.cell] = byCell[u.cell] || []).push(u);
      });
      setMap({ byCell, all });
    };
    read();
    aw.on("change", read);
    return () => aw.off("change", read);
  }, [entry, generation]);
  return map;
}

function useNearViewport(ref, margin = "800px") {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return undefined;
    }
    const io = new IntersectionObserver((items) => {
      if (items.some((i) => i.isIntersecting)) setNear(true);
    }, { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, near, margin]);
  return near;
}

function useNow(active, every = 100) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(id);
  }, [active, every]);
  return now;
}

// ------------------------------------------------------------ small parts

/** Takes the keyboard out of a cell's editor (Monaco may use a textarea or an EditContext). */
function blurEditor(ed) {
  if (!ed) return;
  const el = document.activeElement;
  if (el && ed.editor.getContainerDomNode().contains(el) && el.blur) el.blur();
}

function initials(name) {
  return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

function Dot({ user, size = 18 }) {
  return (
    <span className={"cl-dot" + (user.editing ? " is-editing" : "")} title={user.name} style={{ width: size, height: size, background: user.color, color: tagTextColor(user.color) }}>
      {initials(user.name)}
    </span>
  );
}

function Icon({ name }) {
  const paths = {
    play: <path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />,
    up: <path d="M12 19V5M5 12l7-7 7 7" />,
    down: <path d="M12 5v14M5 12l7 7 7-7" />,
    more: <g fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></g>,
    trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
    copy: <g><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></g>,
    grip: <g fill="currentColor" stroke="none"><circle cx="9" cy="6" r="1.5" /><circle cx="15" cy="6" r="1.5" /><circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="9" cy="18" r="1.5" /><circle cx="15" cy="18" r="1.5" /></g>,
    check: <path d="M5 12l5 5L20 7" />,
    x: <path d="M6 6l12 12M18 6L6 18" />,
    eye: <g><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></g>,
    text: <path d="M4 6h16M4 12h10M4 18h14" />,
    code: <path d="M8 6l-6 6 6 6M16 6l6 6-6 6" />,
    clock: <g><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></g>,
  };
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function duration(sec) {
  if (sec === undefined || sec === null) return "";
  if (sec < 1) return `${(sec * 1000 < 10 ? (sec * 1000).toFixed(1) : Math.round(sec * 1000))} ms`;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 2 : 1)} s`;
  const m = Math.floor(sec / 60);
  return `${m} min ${Math.round(sec - m * 60)} s`;
}

function clock(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

class Boundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error) {
    console.error("[cellora] a cell failed to draw:", error);
  }
  render() {
    if (this.state.error) return <div className="cl-cell-broken">{String(this.state.error && this.state.error.message || this.state.error)}</div>;
    return this.props.children;
  }
}

// ------------------------------------------------------------ the editor of a cell

let modelSeq = 0;

const EditorBox = memo(function EditorBox({ cell, cellId, language, focusRequest }) {
  const ctx = useContext(Ctx);
  const { entry, monaco } = ctx;
  const ytext = cell.get("source");
  const box = useRef(null);
  // The wrapper, not the editor's host: the host stays hidden until the editor exists, and a
  // hidden element never comes into view.
  const wrap = useRef(null);
  const near = useNearViewport(wrap);
  const text = useText(ytext);
  const [height, setHeight] = useState(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!monaco || !near || !(ytext instanceof Y.Text) || !box.current) return undefined;
    const host = box.current;
    const ext = language === "python" ? "py" : "md";
    let uri = monaco.Uri.parse(`cellora://${entry.nbId}/${cellId}.${ext}`);
    // Another editor may hold that name (two cells with one id: a cell moved by two people at
    // once); this one takes a name of its own rather than that editor's model.
    if (monaco.editor.getModel(uri)) uri = monaco.Uri.parse(`cellora://${entry.nbId}/${cellId}.${++modelSeq}.${ext}`);
    const model = monaco.editor.createModel(ytext.toString(), language, uri);
    const editor = monaco.editor.create(host, {
      ...EDITOR_OPTIONS,
      model,
      lineNumbers: ctx.lineNumbers ? "on" : "off",
      ariaLabel: language === "python" ? "Code cell" : "Text cell",
      wordBasedSuggestions: language === "python" ? "off" : "currentDocument",
    });
    registerModel(model, { nbId: entry.nbId, cellId, peer: ctx.me.peer, aiInline: () => ctx.aiInlineRef.current, kernelIdle: () => true });
    const binding = new MonacoBinding(monaco, entry, cellId, ytext, model, editor);
    const fit = () => {
      const h = Math.max(editor.getContentHeight(), 42);
      setHeight(h);
      editor.layout({ width: host.clientWidth, height: h });
    };
    const sizeSub = editor.onDidContentSizeChange(fit);
    const ro = new ResizeObserver(() => editor.layout({ width: host.clientWidth, height: Math.max(editor.getContentHeight(), 42) }));
    ro.observe(host);
    fit();

    const api = ctx.actions;
    const run = (mode) => () => api.run(cellId, mode);
    const word = /[\w.]$/;
    editor.addAction({ id: "cl.run.next", label: "Run cell and go to the next", keybindings: [monaco.KeyMod.Shift | monaco.KeyCode.Enter], run: run("next") });
    editor.addAction({ id: "cl.run.stay", label: "Run cell", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter], run: run("stay") });
    editor.addAction({ id: "cl.run.insert", label: "Run cell and insert below", keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.Enter], run: run("insert") });
    editor.addAction({ id: "cl.ai", label: "Ask AI to write or change this cell", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI], run: () => api.openAi(cellId) });
    editor.addAction({ id: "cl.undo", label: "Undo", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyZ], run: () => binding.undo.undo() });
    editor.addAction({ id: "cl.redo", label: "Redo", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyZ, monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyY], run: () => binding.undo.redo() });
    editor.addAction({
      id: "cl.escape",
      label: "Leave the editor",
      keybindings: [monaco.KeyCode.Escape],
      precondition: "!suggestWidgetVisible && !inlineSuggestionVisible && !findWidgetVisible && !parameterHintsVisible && !renameInputVisible",
      run: () => api.commandMode(cellId),
    });
    if (language === "python") {
      // Tab completes after a name or a dot (as in Jupyter), and indents elsewhere.
      editor.addAction({
        id: "cl.tab",
        label: "Complete or indent",
        keybindings: [monaco.KeyCode.Tab],
        precondition: "!inlineSuggestionVisible && !suggestWidgetVisible && !editorHasSelection && !inSnippetMode",
        run: (ed) => {
          const pos = ed.getPosition();
          const before = ed.getModel().getLineContent(pos.lineNumber).slice(0, pos.column - 1);
          if (word.test(before) && before.trim()) ed.trigger("keyboard", "editor.action.triggerSuggest", {});
          else ed.trigger("keyboard", "tab", null);
        },
      });
    }
    editor.addAction({
      id: "cl.up",
      label: "Previous cell",
      keybindings: [monaco.KeyCode.UpArrow],
      precondition: "!suggestWidgetVisible && !editorHasSelection && !parameterHintsVisible",
      run: (ed) => {
        const pos = ed.getPosition();
        if (pos.lineNumber === 1) api.focusNeighbour(cellId, -1);
        else ed.trigger("keyboard", "cursorUp", null);
      },
    });
    editor.addAction({
      id: "cl.down",
      label: "Next cell",
      keybindings: [monaco.KeyCode.DownArrow],
      precondition: "!suggestWidgetVisible && !editorHasSelection && !parameterHintsVisible",
      run: (ed) => {
        const pos = ed.getPosition();
        if (pos.lineNumber === ed.getModel().getLineCount()) api.focusNeighbour(cellId, 1);
        else ed.trigger("keyboard", "cursorDown", null);
      },
    });
    const focusSub = editor.onDidFocusEditorText(() => api.editing(cellId, true));
    const blurSub = editor.onDidBlurEditorText(() => api.editing(cellId, false));
    ctx.editors.set(cellId, { editor, binding, model });
    setReady(true);
    return () => {
      if (ctx.editors.get(cellId) && ctx.editors.get(cellId).editor === editor) ctx.editors.delete(cellId);
      sizeSub.dispose();
      focusSub.dispose();
      blurSub.dispose();
      ro.disconnect();
      binding.destroy();
      editor.dispose();
      model.dispose();
      setReady(false);
    };
  }, [monaco, near, ytext, language, cellId, entry]);

  // Focus when asked (a new cell, Enter in command mode, arrows across cells).
  useEffect(() => {
    if (!focusRequest || !ready) return;
    const ed = ctx.editors.get(cellId);
    if (!ed) return;
    ed.editor.focus();
    const model = ed.editor.getModel();
    if (focusRequest.where === "end") {
      const line = model.getLineCount();
      ed.editor.setPosition({ lineNumber: line, column: model.getLineMaxColumn(line) });
    } else if (focusRequest.where === "start") {
      ed.editor.setPosition({ lineNumber: 1, column: 1 });
    }
  }, [focusRequest, ready]);

  const empty = !text;
  return (
    <div ref={wrap} className={"cl-editor" + (language === "markdown" ? " is-md" : "")}>
      <div ref={box} className="cl-editor-host" style={{ height: ready && height ? height : undefined, display: ready ? "block" : "none" }} />
      {!ready && (
        <pre className="cl-static" onMouseDown={() => ctx.actions.select(cellId)}>
          {text || " "}
        </pre>
      )}
      {ready && empty && language === "python" && (
        <div className="cl-placeholder">{t(ctx.labels, "code_placeholder", "Start coding, or press {mod} I to let AI write it", { mod: MOD })}</div>
      )}
      {ready && empty && language === "markdown" && <div className="cl-placeholder">{t(ctx.labels, "md_placeholder", "Write text in Markdown…")}</div>}
    </div>
  );
});

// ------------------------------------------------------------ the AI parts of a cell

function AiPrompt({ cellId, onClose }) {
  const ctx = useContext(Ctx);
  const [value, setValue] = useState("");
  const ref = useRef(null);
  useEffect(() => { ref.current && ref.current.focus(); }, []);
  const submit = () => {
    const prompt = value.trim();
    if (!prompt) return;
    ctx.onEvent({ type: "ai_edit", cell: cellId, prompt });
    // The keyboard stays out of the editor: the change appears as a diff to accept (⌘↵) or not (Esc).
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    onClose(true);
  };
  return (
    <div className="cl-ai-prompt">
      <Sparkles />
      <input
        ref={ref}
        value={value}
        placeholder={t(ctx.labels, "ai_prompt_placeholder", "Ask AI to write or change this cell…")}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); }
          if (e.key === "Escape") { e.preventDefault(); onClose(); }
        }}
      />
      <button type="button" className="cl-btn cl-btn-ai" onClick={submit} disabled={!value.trim()}>
        {t(ctx.labels, "ai_generate", "Generate")}
      </button>
      <button type="button" className="cl-icon-btn" onClick={onClose} title={t(ctx.labels, "close", "Close")}><Icon name="x" /></button>
    </div>
  );
}

function DraftDiff({ cell, cellId, draft, language }) {
  const ctx = useContext(Ctx);
  const { monaco } = ctx;
  const host = useRef(null);
  const diffRef = useRef(null);
  const ytext = cell.get("source");
  const current = useText(ytext);
  const [height, setHeight] = useState(80);
  // The latest texts, for the diff view made after mounting.
  const currentRef = useRef(current);
  currentRef.current = current;
  const textRef = useRef(draft.text);
  textRef.current = draft.text;

  useEffect(() => {
    if (!monaco || !host.current) return undefined;
    let made = null;
    // Made a moment later: React (in development) mounts, unmounts and mounts again at once, and
    // a diff view disposed while Monaco's worker computes it makes the worker complain.
    const timer = setTimeout(() => {
      if (!host.current) return;
      const original = monaco.editor.createModel(currentRef.current, language);
      const modified = monaco.editor.createModel(textRef.current || "", language);
      const diff = monaco.editor.createDiffEditor(host.current, {
        ...EDITOR_OPTIONS,
        readOnly: true,
        renderSideBySide: false,
        renderOverviewRuler: false,
        enableSplitViewResizing: false,
        renderIndicators: true,
        originalEditable: false,
        lineNumbers: "off",
        scrollbar: { vertical: "hidden", alwaysConsumeMouseWheel: false },
        hideUnchangedRegions: { enabled: false },
      });
      diff.setModel({ original, modified });
      const fit = () => {
        const h = Math.min(Math.max(diff.getModifiedEditor().getContentHeight(), 42), 900);
        setHeight(h);
        diff.layout({ width: host.current ? host.current.clientWidth : 600, height: h });
      };
      const subs = [diff.getModifiedEditor().onDidContentSizeChange(fit), diff.onDidUpdateDiff(fit)];
      made = { diff, original, modified, subs };
      diffRef.current = made;
      fit();
    }, 40);
    return () => {
      clearTimeout(timer);
      if (!made) return;
      made.subs.forEach((d) => d.dispose());
      diffRef.current = null;
      // Detach first: that cancels a diff still being computed (its models would be gone).
      made.diff.setModel(null);
      made.diff.dispose();
      const { original, modified } = made;
      setTimeout(() => { original.dispose(); modified.dispose(); }, 0);
    };
  }, [monaco, language]);

  useEffect(() => {
    const d = diffRef.current;
    if (d && d.modified.getValue() !== (draft.text || "")) d.modified.setValue(draft.text || "");
  }, [draft.text]);
  useEffect(() => {
    const d = diffRef.current;
    if (d && d.original.getValue() !== current) d.original.setValue(current);
  }, [current]);

  const accept = () => {
    const ed = ctx.editors.get(cellId);
    if (ed) ed.binding.replaceWith(draft.text || "");
    else replaceText(ctx.entry, ytext, draft.text || "");
    ctx.onEvent({ type: "ai_accept", cell: cellId });
    ctx.actions.focus(cellId, "end");
  };
  const reject = () => ctx.onEvent({ type: "ai_reject", cell: cellId });
  const streaming = draft.status === "streaming";
  const failed = draft.status === "error";
  useEffect(() => {
    const onKey = (e) => {
      if (draft.status !== "done") return;
      const take = (fn) => {
        // Not also to the cell's editor (⌘↵ there would run the cell).
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        fn();
      };
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") take(accept);
      else if (e.key === "Escape") take(reject);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });
  return (
    <div className={"cl-draft" + (failed ? " is-error" : "")}>
      <div className="cl-draft-bar">
        <span className="cl-draft-title">
          <Sparkles /> {draft.kind === "fix" ? t(ctx.labels, "ai_fixing", "AI fix") : t(ctx.labels, "ai_suggestion", "AI suggestion")}
        </span>
        {streaming && <span className="cl-draft-status"><span className="cl-spinner" /> {t(ctx.labels, "ai_writing", "Writing…")}</span>}
        {failed && <span className="cl-draft-error">{draft.error}</span>}
        <span className="cl-grow" />
        {!failed && (
          <button type="button" className="cl-btn cl-btn-primary" disabled={streaming || !draft.text} onClick={accept} title={`${MOD} Enter`}>
            <Icon name="check" /> {t(ctx.labels, "accept", "Accept")}
          </button>
        )}
        <button type="button" className="cl-btn" onClick={reject} title="Esc">
          <Icon name="x" /> {failed ? t(ctx.labels, "close", "Close") : t(ctx.labels, "reject", "Reject")}
        </button>
      </div>
      {!failed && <div ref={host} className="cl-draft-host" style={{ height }} />}
    </div>
  );
}

// ------------------------------------------------------------ a cell

function RunFooter({ run, kernelState, labels, cellType }) {
  const state = run && run.state;
  const now = useNow(state === "running");
  if (!state || cellType !== "code") return null;
  const by = run.by ? (
    <span className="cl-by">
      <span className="cl-by-dot" style={{ background: run.color || "#9ca3af" }} />
      {run.by}
    </span>
  ) : null;
  if (state === "queued") {
    return (
      <div className="cl-footer is-queued">
        <Icon name="clock" /> {kernelState === "starting" ? t(labels, "kernel_starting_wait", "Waiting for the kernel to start…") : t(labels, "queued", "Waiting to run")}
        {by}
      </div>
    );
  }
  if (state === "running") {
    const sec = run.started ? Math.max(0, now / 1000 - run.started) : 0;
    return (
      <div className="cl-footer is-running">
        <span className="cl-spinner" /> {t(labels, "running", "Running")} {duration(sec)} {by}
      </div>
    );
  }
  if (state === "cancelled") {
    return <div className="cl-footer is-cancelled">{t(labels, "cancelled", "Not run: an earlier cell failed or the run was stopped")}</div>;
  }
  const ok = state === "ok";
  return (
    <div className={"cl-footer " + (ok ? "is-ok" : "is-error")}>
      {ok ? <Icon name="check" /> : <Icon name="x" />}
      <span>{ok ? "" : t(labels, "failed", "Failed")} {duration(run.duration)}</span>
      {by}
      {run.ended ? <span className="cl-when">{clock(run.ended)}</span> : null}
    </div>
  );
}

function Menu({ items, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [onClose]);
  return (
    <div className="cl-menu" ref={ref} role="menu">
      {items.map((it, i) => (it === "-" ? <div key={i} className="cl-menu-sep" /> : (
        <button key={i} type="button" role="menuitem" className={"cl-menu-item" + (it.danger ? " is-danger" : "")} onClick={() => { onClose(); it.run(); }}>
          <span>{it.label}</span>
          {it.keys && <kbd>{it.keys}</kbd>}
        </button>
      )))}
    </div>
  );
}

const Cell = memo(function Cell({ cell, index, count, selected, focusRequest, mdEditing, aiOpen, people, draft, kernelState, dropHint }) {
  const ctx = useContext(Ctx);
  const { labels, actions, entry } = ctx;
  useCellFields(cell);
  const id = cell.get("id");
  const type = cell.get("cell_type") || "code";
  const run = cell.get("run");
  const countValue = cell.get("execution_count");
  const title = ours(cell).title || "";
  const sourceHidden = jupyterFlag(cell, "source_hidden");
  const outputsHidden = jupyterFlag(cell, "outputs_hidden");
  const [menu, setMenu] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const state = run && run.state;
  const busy = state === "running" || state === "queued";
  const isMd = type === "markdown";
  const showMdEditor = isMd && mdEditing;
  const hasDraft = draft && draft.cell === id;

  const onOutputEvent = useCallback((e) => {
    if (e.type === "toggle_outputs") setJupyterFlag(entry, cell, "outputs_hidden", !outputsHidden);
    else ctx.onEvent({ ...e, cell: id });
  }, [entry, cell, id, outputsHidden]);

  const menuItems = [
    { label: t(labels, "run_cell", "Run cell"), keys: `${MOD} ↵`, run: () => actions.run(id, "stay") },
    { label: t(labels, "ai_edit_cell", "Edit with AI"), keys: `${MOD} I`, run: () => actions.openAi(id) },
    { label: t(labels, "ai_explain_cell", "Explain with AI"), run: () => ctx.onEvent({ type: "ai_explain", cell: id }) },
    "-",
    { label: title ? t(labels, "edit_title", "Edit title") : t(labels, "add_title", "Add title"), run: () => setEditingTitle(true) },
    { label: isMd ? t(labels, "to_code", "Change to code") : t(labels, "to_markdown", "Change to text (Markdown)"), keys: isMd ? "Y" : "M", run: () => setCellType(entry, id, isMd ? "code" : "markdown") },
    { label: sourceHidden ? t(labels, "show_code", "Show code") : t(labels, "hide_code", "Hide code"), run: () => setJupyterFlag(entry, cell, "source_hidden", !sourceHidden) },
    ...(type === "code" ? [
      { label: outputsHidden ? t(labels, "show_output", "Show output") : t(labels, "hide_output", "Hide output"), run: () => setJupyterFlag(entry, cell, "outputs_hidden", !outputsHidden) },
      { label: t(labels, "clear_output", "Clear output"), run: () => clearOutputs(entry, id) },
    ] : []),
    "-",
    { label: t(labels, "insert_above", "Add cell above"), keys: "A", run: () => actions.insert(index, "code") },
    { label: t(labels, "insert_below", "Add cell below"), keys: "B", run: () => actions.insert(index + 1, "code") },
    { label: t(labels, "duplicate", "Duplicate"), run: () => duplicateCell(entry, id) },
    { label: t(labels, "move_up", "Move up"), run: () => moveCell(entry, id, -1) },
    { label: t(labels, "move_down", "Move down"), run: () => moveCell(entry, id, 1) },
    "-",
    { label: t(labels, "delete_cell", "Delete cell"), keys: "D D", danger: true, run: () => actions.remove(id) },
  ];

  const editingSomeone = (people || []).find((p) => p.editing);
  const ring = editingSomeone ? editingSomeone.color : null;

  return (
    <div
      id={`cell-${id}`}
      data-cell-id={id}
      className={["cl-cell", selected ? "is-selected" : "", isMd ? "is-md" : "is-code", busy ? "is-busy" : "", state === "error" ? "is-failed" : "", dropHint ? `drop-${dropHint}` : ""].join(" ")}
      style={ring ? { "--cl-ring": ring } : undefined}
      onMouseDown={() => actions.select(id)}
      onDragOver={(e) => actions.dragOver(e, id)}
      onDrop={(e) => actions.drop(e, id)}
    >
      <div className="cl-gutter">
        {type === "code" ? (
          busy ? (
            <button type="button" className="cl-run is-stop" title={t(labels, "interrupt", "Stop")} onClick={(e) => { e.stopPropagation(); ctx.onEvent({ type: "interrupt" }); }}>
              {state === "running" ? <span className="cl-spinner" /> : <Icon name="clock" />}
              <span className="cl-run-stop"><Icon name="stop" /></span>
            </button>
          ) : (
            <button type="button" className="cl-run" title={t(labels, "run_cell", "Run cell") + ` (${MOD} ↵)`} onClick={(e) => { e.stopPropagation(); actions.run(id, "stay"); }}>
              <Icon name="play" />
            </button>
          )
        ) : (
          <span className="cl-md-mark" title="Markdown">M↓</span>
        )}
        {type === "code" && <span className="cl-count">[{state === "running" ? "*" : countValue == null ? " " : Math.round(countValue)}]</span>}
        <span className="cl-grip" draggable onDragStart={(e) => actions.dragStart(e, id)} onDragEnd={actions.dragEnd} title={t(labels, "drag_to_move", "Drag to move")}>
          <Icon name="grip" />
        </span>
        <div className="cl-people">
          {(people || []).slice(0, 3).map((p) => <Dot key={p.clientID} user={p} />)}
          {(people || []).length > 3 && <span className="cl-dot is-more">+{people.length - 3}</span>}
        </div>
      </div>
      <div className="cl-main">
        <div className="cl-cell-tools" onMouseDown={(e) => e.stopPropagation()}>
          <button type="button" className="cl-icon-btn is-ai" title={t(labels, "ai_edit_cell", "Edit with AI") + ` (${MOD} I)`} onClick={() => actions.openAi(id)}><Sparkles /></button>
          <button type="button" className="cl-icon-btn" title={t(labels, "move_up", "Move up")} onClick={() => moveCell(entry, id, -1)} disabled={index === 0}><Icon name="up" /></button>
          <button type="button" className="cl-icon-btn" title={t(labels, "move_down", "Move down")} onClick={() => moveCell(entry, id, 1)} disabled={index === count - 1}><Icon name="down" /></button>
          <button type="button" className="cl-icon-btn" title={t(labels, "delete_cell", "Delete cell")} onClick={() => actions.remove(id)}><Icon name="trash" /></button>
          <div className="cl-menu-anchor">
            <button type="button" className="cl-icon-btn" title={t(labels, "more", "More")} onClick={() => setMenu((m) => !m)}><Icon name="more" /></button>
            {menu && <Menu items={menuItems} onClose={() => setMenu(false)} />}
          </div>
        </div>
        {(title || editingTitle) && (
          editingTitle ? (
            <input
              className="cl-title-input"
              autoFocus
              defaultValue={title}
              placeholder={t(labels, "title_placeholder", "Cell title")}
              onMouseDown={(e) => e.stopPropagation()}
              onBlur={(e) => { setOurs(entry, cell, { title: e.target.value.trim() || undefined }); setEditingTitle(false); }}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") e.target.blur(); }}
            />
          ) : (
            <div className="cl-title" onDoubleClick={() => setEditingTitle(true)}>{title}</div>
          )
        )}
        {aiOpen && <AiPrompt cellId={id} onClose={(submitted) => actions.closeAi(id, submitted)} />}
        {hasDraft && <DraftDiff cell={cell} cellId={id} draft={draft} language={isMd ? "markdown" : "python"} />}
        {sourceHidden ? (
          <button type="button" className="cl-hidden-source" onClick={() => setJupyterFlag(entry, cell, "source_hidden", false)}>
            <Icon name="eye" /> {t(labels, "code_hidden", "Code hidden: click to show")}
          </button>
        ) : isMd && !showMdEditor ? (
          <MarkdownView cell={cell} id={id} />
        ) : (
          <div style={hasDraft ? { display: "none" } : undefined}>
            <EditorBox cell={cell} cellId={id} language={isMd ? "markdown" : "python"} focusRequest={focusRequest} />
          </div>
        )}
        {type === "code" && <Outputs entry={entry} cell={cell} labels={labels} onEvent={onOutputEvent} collapsed={outputsHidden} />}
        <RunFooter run={run} kernelState={kernelState} labels={labels} cellType={type} />
      </div>
    </div>
  );
});

function MarkdownView({ cell, id }) {
  const ctx = useContext(Ctx);
  const text = useText(cell.get("source"));
  const attachments = cell.get("attachments");
  const html = useMemo(() => renderMarkdown(text, typeof attachments === "string" ? JSON.parse(attachments || "{}") : attachments, `${id}-`), [text, attachments, id]);
  if (!text.trim()) {
    return (
      <div className="cl-md-empty" onDoubleClick={() => ctx.actions.editMd(id, true)}>
        {t(ctx.labels, "md_empty", "Empty text cell: double-click to write")}
      </div>
    );
  }
  return <div className="cl-md prose" onDoubleClick={() => ctx.actions.editMd(id, true)} dangerouslySetInnerHTML={{ __html: html }} />;
}

function AddBar({ index, labels, actions, last }) {
  return (
    <div className={"cl-add" + (last ? " is-last" : "")}>
      <button type="button" onClick={() => actions.insert(index, "code")}><Icon name="code" /> {t(labels, "add_code", "Code")}</button>
      <button type="button" onClick={() => actions.insert(index, "markdown")}><Icon name="text" /> {t(labels, "add_text", "Text")}</button>
    </div>
  );
}

// ------------------------------------------------------------ the notebook

export function CelloraNotebook(props) {
  const { notebookId, me, labels, draft, command, kernelState, aiInline, lineNumbers, onEvent } = props;
  const entry = useEntry(notebookId, me && me.peer);
  const status = useEntryStatus(entry);
  const cells = useCells(entry, status.generation);
  const monaco = useMonaco();
  const presence = usePresence(entry, status.generation);
  const [selected, setSelected] = useState(null);
  const [focusReq, setFocusReq] = useState({});
  const [mdEditing, setMdEditing] = useState({});
  const [aiOpen, setAiOpen] = useState(null);
  const [drag, setDrag] = useState(null);
  const editors = useRef(new Map()).current;
  const aiInlineRef = useRef(!!aiInline);
  aiInlineRef.current = !!aiInline;
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const emit = useCallback((e) => { if (onEventRef.current) onEventRef.current(e); }, []);
  const selectedRef = useRef(null);
  selectedRef.current = selected;
  const lastPress = useRef({ key: "", at: 0 });

  // Who I am, for the others' carets and cell markers.
  useEffect(() => {
    if (!entry || !me) return;
    entry.awareness.setLocalStateField("user", { name: me.name || "Guest", color: me.color || "#4F46E5", peer: me.peer || "" });
  }, [entry, status.generation, me && me.name, me && me.color, me && me.peer]);

  const announce = useCallback((cellId, editing) => {
    if (!entry) return;
    const st = entry.awareness.getLocalState() || {};
    if (st.cell !== cellId || !!st.editing !== !!editing) {
      entry.awareness.setLocalStateField("cell", cellId || "");
      entry.awareness.setLocalStateField("editing", !!editing);
    }
  }, [entry]);

  const focusCell = useCallback((id, where = "end") => {
    setSelected(id);
    setFocusReq((r) => ({ ...r, [id]: { where, at: Date.now() } }));
  }, []);

  const ids = useMemo(() => cells.map((c) => c.get("id")), [cells]);
  const idsRef = useRef(ids);
  idsRef.current = ids;

  const scrollTo = useCallback((id) => {
    const el = document.getElementById(`cell-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const actions = useMemo(() => ({
    select(id) {
      if (selectedRef.current !== id) {
        setSelected(id);
        emit({ type: "focus", cell: id });
        announce(id, false);
      }
    },
    editing(id, on) {
      if (on && selectedRef.current !== id) {
        setSelected(id);
        emit({ type: "focus", cell: id });
      }
      announce(id, on);
    },
    commandMode(id) {
      blurEditor(editors.get(id));
      setMdEditing((m) => (m[id] ? { ...m, [id]: false } : m));
      announce(id, false);
    },
    focus: focusCell,
    focusNeighbour(id, delta) {
      const list = idsRef.current;
      const i = list.indexOf(id);
      const j = i + delta;
      if (j < 0 || j >= list.length) return;
      const next = list[j];
      const m = cellById(entry, next);
      if (m && m.get("cell_type") === "markdown") setMdEditing((x) => ({ ...x, [next]: true }));
      focusCell(next, delta < 0 ? "end" : "start");
      scrollTo(next);
    },
    run(id, mode) {
      const m = cellById(entry, id);
      if (!m) return;
      if (m.get("cell_type") === "markdown") setMdEditing((x) => ({ ...x, [id]: false }));
      else emit({ type: "run", cells: [id] });
      const list = idsRef.current;
      const i = list.indexOf(id);
      if (mode === "next") {
        if (i === list.length - 1) {
          const nid = insertCell(entry, i + 1, "code");
          focusCell(nid, "start");
        } else {
          const next = list[i + 1];
          const nm = cellById(entry, next);
          if (nm && nm.get("cell_type") === "code") focusCell(next, "end");
          else {
            setSelected(next);
            emit({ type: "focus", cell: next });
            blurEditor(editors.get(id));
          }
          scrollTo(next);
        }
      } else if (mode === "insert") {
        const nid = insertCell(entry, i + 1, "code");
        focusCell(nid, "start");
      }
    },
    insert(index, type) {
      const nid = insertCell(entry, index, type);
      if (type === "markdown") setMdEditing((x) => ({ ...x, [nid]: true }));
      focusCell(nid, "start");
      setTimeout(() => scrollTo(nid), 30);
      return nid;
    },
    remove(id) {
      const list = idsRef.current;
      const i = list.indexOf(id);
      deleteCell(entry, id);
      const next = list[i + 1] || list[i - 1] || null;
      setSelected(next);
      if (next) emit({ type: "focus", cell: next });
      emit({ type: "toast", text: t(labels, "cell_deleted", "Cell deleted: press Z (outside the editor) to bring it back") });
    },
    openAi(id) {
      setSelected(id);
      setAiOpen(id);
    },
    closeAi(id, submitted) {
      setAiOpen((x) => (x === id ? null : x));
      if (!submitted) setTimeout(() => focusCell(id, "end"), 0);
    },
    editMd(id, on) {
      setMdEditing((x) => ({ ...x, [id]: on }));
      if (on) focusCell(id, "end");
    },
    dragStart(e, id) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/x-cellora-cell", id);
      setDrag({ id, over: null, where: null });
    },
    dragOver(e, id) {
      if (!e.dataTransfer.types.includes("text/x-cellora-cell")) return;
      e.preventDefault();
      const r = e.currentTarget.getBoundingClientRect();
      const where = e.clientY < r.top + r.height / 2 ? "before" : "after";
      setDrag((d) => (d && (d.over !== id || d.where !== where) ? { ...d, over: id, where } : d));
    },
    drop(e, id) {
      const from = e.dataTransfer.getData("text/x-cellora-cell");
      if (!from) return;
      e.preventDefault();
      const r = e.currentTarget.getBoundingClientRect();
      const where = e.clientY < r.top + r.height / 2 ? "before" : "after";
      const target = cellIndex(entry, id) + (where === "after" ? 1 : 0);
      if (from !== id) moveCellTo(entry, from, target);
      setDrag(null);
    },
    dragEnd() {
      setDrag(null);
    },
  }), [entry, emit, announce, focusCell, scrollTo, labels]);

  // Commands from the page (Reflex): add a cell, go to a cell, follow someone.
  const seen = useRef("");
  useEffect(() => {
    if (!entry || !command || !command.seq || command.seq === seen.current) return;
    seen.current = command.seq;
    if (command.type === "add") {
      const i = selected ? idsRef.current.indexOf(selected) + 1 : idsRef.current.length;
      actions.insert(i <= 0 ? idsRef.current.length : i, command.cell_type === "markdown" ? "markdown" : "code");
    } else if (command.type === "scroll") {
      const go = () => {
        setSelected(command.cell);
        scrollTo(command.cell);
      };
      setTimeout(go, 60);
    } else if (command.type === "follow") {
      const p = presence.all.find((u) => u.peer === command.peer);
      if (p && p.cell) {
        setSelected(p.cell);
        scrollTo(p.cell);
      } else {
        emit({ type: "toast", text: t(labels, "follow_nowhere", "They are not in a cell right now") });
      }
    }
  }, [command && command.seq, entry]);

  // The outline (and others) ask to go to a cell.
  useEffect(() => {
    const onGo = (e) => {
      if (!e.detail || e.detail.nb !== notebookId) return;
      setSelected(e.detail.cell);
      scrollTo(e.detail.cell);
      if (e.detail.anchor) {
        setTimeout(() => document.getElementById(e.detail.anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
      }
    };
    window.addEventListener("cellora:goto", onGo);
    return () => window.removeEventListener("cellora:goto", onGo);
  }, [notebookId, scrollTo]);

  // ⌘S / Ctrl S: Jupyter users press it by habit; Cellora saves by itself.
  useEffect(() => {
    const onSave = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        emit({ type: "toast", text: t(labels, "autosaved", "Cellora saves by itself: every change is kept as you go.") });
      }
    };
    window.addEventListener("keydown", onSave, true);
    return () => window.removeEventListener("keydown", onSave, true);
  }, [emit, labels]);

  // Command mode: keys when no editor or input has the focus (as in Jupyter).
  useEffect(() => {
    const onKey = (e) => {
      if (!entry) return;
      const tgt = e.target;
      const tag = tgt && tgt.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (tgt && tgt.isContentEditable)) return;
      if (tgt && tgt.closest && tgt.closest(".monaco-editor, [role=dialog], .cl-ai-prompt")) return;
      const id = selectedRef.current;
      if (!id) return;
      const list = idsRef.current;
      const i = list.indexOf(id);
      if (i < 0) return;
      const key = e.key;
      const now = Date.now();
      const twice = lastPress.current.key === key && now - lastPress.current.at < 600;
      lastPress.current = { key, at: now };
      const mod = e.metaKey || e.ctrlKey;
      if (key === "Enter" && e.shiftKey) { e.preventDefault(); actions.run(id, "next"); return; }
      if (key === "Enter" && mod) { e.preventDefault(); actions.run(id, "stay"); return; }
      if (key === "Enter" && e.altKey) { e.preventDefault(); actions.run(id, "insert"); return; }
      if (mod || e.altKey) {
        if (mod && (key === "i" || key === "I")) { e.preventDefault(); actions.openAi(id); }
        return;
      }
      if (key === "Enter") {
        e.preventDefault();
        const m = cellById(entry, id);
        if (m && m.get("cell_type") === "markdown") setMdEditing((x) => ({ ...x, [id]: true }));
        focusCell(id, "end");
      } else if (key === "ArrowUp" || key === "k") {
        e.preventDefault();
        if (i > 0) { actions.select(list[i - 1]); scrollTo(list[i - 1]); }
      } else if (key === "ArrowDown" || key === "j") {
        e.preventDefault();
        if (i < list.length - 1) { actions.select(list[i + 1]); scrollTo(list[i + 1]); }
      } else if (key === "a") {
        e.preventDefault();
        actions.insert(i, "code");
      } else if (key === "b") {
        e.preventDefault();
        actions.insert(i + 1, "code");
      } else if (key === "m") {
        e.preventDefault();
        setCellType(entry, id, "markdown");
      } else if (key === "y") {
        e.preventDefault();
        setCellType(entry, id, "code");
      } else if (key === "d" && twice) {
        e.preventDefault();
        lastPress.current = { key: "", at: 0 };
        actions.remove(id);
      } else if (key === "z") {
        e.preventDefault();
        entry.structUndo.undo();
      } else if (key === "Z") {
        e.preventDefault();
        entry.structUndo.redo();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [entry, actions, focusCell, scrollTo]);

  const ctx = useMemo(() => ({ entry, monaco, labels: labels || {}, me: me || {}, onEvent: emit, actions, editors, aiInlineRef, lineNumbers: !!lineNumbers }), [entry, monaco, labels, me, emit, actions, editors, lineNumbers]);

  if (!entry || (!status.synced && status.status !== "gone")) {
    return (
      <div className="cl-loading">
        <span className="cl-spinner" /> {t(labels, "opening", "Opening the notebook…")}
      </div>
    );
  }
  if (status.status === "gone") {
    return <div className="cl-gone">{t(labels, "notebook_gone", "This notebook doesn't exist any more.")}</div>;
  }
  return (
    <Ctx.Provider value={ctx}>
      <div className="cl-notebook" data-notebook={notebookId} data-generation={status.generation}>
        {status.status !== "connected" && (
          <div className="cl-offline"><span className="cl-spinner" /> {t(labels, "reconnecting", "Connection lost: reconnecting… Your edits are kept and sent when it is back.")}</div>
        )}
        {cells.length === 0 && (
          <div className="cl-empty">
            <p>{t(labels, "empty_notebook", "This notebook is empty.")}</p>
            <AddBar index={0} labels={labels} actions={actions} last />
          </div>
        )}
        {cells.map((cell, i) => {
          const id = cell.get("id");
          return (
            <React.Fragment key={id}>
              {i === 0 && <AddBar index={0} labels={labels} actions={actions} />}
              <Boundary>
                <Cell
                  cell={cell}
                  index={i}
                  count={cells.length}
                  selected={selected === id}
                  focusRequest={focusReq[id]}
                  mdEditing={!!mdEditing[id]}
                  aiOpen={aiOpen === id}
                  people={presence.byCell[id]}
                  draft={draft && draft.cell === id ? draft : null}
                  kernelState={kernelState}
                  dropHint={drag && drag.over === id && drag.id !== id ? drag.where : null}
                />
              </Boundary>
              <AddBar index={i + 1} labels={labels} actions={actions} last={i === cells.length - 1} />
            </React.Fragment>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

// ------------------------------------------------------------ the outline

export function CelloraOutline({ notebookId, peer, labels }) {
  const [entry, setEntry] = useState(null);
  useEffect(() => {
    if (!notebookId) return undefined;
    // Shares the notebook's connection (whichever of the two components comes first opens it).
    const e = acquire(notebookId, peer);
    setEntry(e);
    return () => release(e);
  }, [notebookId]);
  const { generation } = useEntryStatus(entry);
  const cells = useCells(entry, generation);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!entry) return undefined;
    const arr = entry.cells;
    let raf = 0;
    const fn = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; setTick((x) => x + 1); });
    };
    arr.observeDeep(fn);
    return () => arr.unobserveDeep(fn);
  }, [entry, generation]);
  const items = [];
  cells.forEach((cell, i) => {
    const id = cell.get("id");
    const title = ours(cell).title;
    if (title) items.push({ id, level: 3, text: title, key: `${id}-t`, kind: "title" });
    if (cell.get("cell_type") === "markdown") {
      const src = cell.get("source");
      headings(src ? src.toString() : "").forEach((h, k) => items.push({ id, level: h.level, text: h.text, key: `${id}-${k}`, anchor: `${id}-${slug(h.text)}` }));
    }
    const run = cell.get("run");
    if (run && (run.state === "running" || run.state === "error") && items.length && items[items.length - 1].id !== id) {
      // nothing: states show on the cells themselves
    }
  });
  const go = (it) => window.dispatchEvent(new CustomEvent("cellora:goto", { detail: { nb: notebookId, cell: it.id, anchor: it.anchor } }));
  if (!items.length) {
    return <div className="cl-outline-empty">{t(labels, "outline_empty", "Headings of the text cells (# Title) and cell titles show here.")}</div>;
  }
  return (
    <nav className="cl-outline">
      {items.map((it) => (
        <button key={it.key} type="button" className={`cl-outline-item level-${Math.min(it.level, 4)}` + (it.kind === "title" ? " is-title" : "")} onClick={() => go(it)}>
          {it.text}
        </button>
      ))}
    </nav>
  );
}
