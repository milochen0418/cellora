// The notebook's shared document in this browser: one Y.Doc per open notebook, connected to the
// server's copy over /_cellora/yjs/<id> (y-websocket protocol; see cellora/docs.py for the
// layout). Everything the page shows of the cells comes from here, and every change people make
// goes in here: Yjs merges it with what the others do at the same moment.
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";
import { getBackendURL } from "$/utils/state";
import env from "$/env.json";

export { Y };

const entries = new Map();

function wsBase() {
  return new URL("/_cellora/yjs", getBackendURL(env.EVENT)).toString();
}

export function httpURL(path) {
  return new URL(path, getBackendURL(env.PING)).toString();
}

// Structural changes (cells added, moved, deleted, their type) are made with this origin, so
// "undo" in command mode (Z) can take them back for the person who made them.
export const STRUCTURE = "cellora-structure";

/** The notebook's document, connecting on first use; release() when done with it. */
export function acquire(nbId, peer = "") {
  let e = entries.get(nbId);
  if (!e) {
    const doc = new Y.Doc();
    const provider = new WebsocketProvider(wsBase(), nbId, doc, {
      connect: true,
      // Which tab this is, so the server can tell who changed the notebook.
      params: peer ? { peer } : {},
      // Tabs of one browser also sync with each other directly.
      disableBc: false,
      maxBackoffTime: 5000,
    });
    const cells = doc.getArray("cells");
    e = {
      nbId,
      doc,
      provider,
      awareness: provider.awareness,
      cells,
      refs: 0,
      synced: false,
      status: "connecting",
      listeners: new Set(),
      structUndo: new Y.UndoManager(cells, { trackedOrigins: new Set([STRUCTURE]), captureTimeout: 0 }),
      textUndo: new Map(), // cell id -> Y.UndoManager of its text (this person's edits)
    };
    const notify = () => e.listeners.forEach((fn) => fn());
    provider.on("sync", (synced) => {
      if (synced) e.synced = true;
      notify();
    });
    provider.on("status", ({ status }) => {
      e.status = status;
      notify();
    });
    provider.on("connection-close", (event) => {
      // 4404: the notebook doesn't exist (any more).
      if (event && event.code === 4404) {
        e.status = "gone";
        provider.shouldConnect = false;
        notify();
      }
    });
    entries.set(nbId, e);
  }
  e.refs += 1;
  return e;
}

export function release(e) {
  e.refs -= 1;
  if (e.refs <= 0) {
    // Kept a moment: React's StrictMode unmounts and mounts again right away.
    setTimeout(() => {
      if (e.refs > 0 || entries.get(e.nbId) !== e) return;
      entries.delete(e.nbId);
      try { e.awareness.setLocalState(null); } catch (err) { /* closed */ }
      e.textUndo.forEach((u) => u.destroy());
      e.structUndo.destroy();
      e.provider.destroy();
      e.doc.destroy();
    }, 1500);
  }
}

export function find(nbId) {
  return entries.get(nbId) || null;
}

export function subscribe(e, fn) {
  e.listeners.add(fn);
  return () => e.listeners.delete(fn);
}

// ------------------------------------------------------------ cells

export function newCellId() {
  const a = new Uint8Array(4);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function makeCell(cellType, source = "") {
  const m = new Y.Map();
  m.set("id", newCellId());
  m.set("cell_type", cellType);
  m.set("source", new Y.Text(source));
  m.set("metadata", new Y.Map());
  if (cellType === "code") {
    m.set("outputs", new Y.Array());
    m.set("execution_count", null);
  }
  return m;
}

export function cellIndex(e, id) {
  const arr = e.cells.toArray();
  for (let i = 0; i < arr.length; i++) if (arr[i].get("id") === id) return i;
  return -1;
}

export function cellById(e, id) {
  const i = cellIndex(e, id);
  return i >= 0 ? e.cells.get(i) : null;
}

export function insertCell(e, index, cellType = "code", source = "") {
  const m = makeCell(cellType, source);
  const at = Math.max(0, Math.min(index, e.cells.length));
  e.doc.transact(() => e.cells.insert(at, [m]), STRUCTURE);
  return m.get("id");
}

export function deleteCell(e, id) {
  const i = cellIndex(e, id);
  if (i >= 0) e.doc.transact(() => e.cells.delete(i, 1), STRUCTURE);
}

function copyValue(v) {
  if (v instanceof Y.Text) return new Y.Text(v.toString());
  if (v instanceof Y.Array) return Y.Array.from(v.toArray().map(copyValue));
  if (v instanceof Y.Map) {
    const m = new Y.Map();
    v.forEach((val, key) => m.set(key, copyValue(val)));
    return m;
  }
  return v;
}

/** A copy of a cell with a new id (Yjs can't move a shared type, so moving is copy + delete). */
export function cloneCell(m, keepId = true) {
  const c = new Y.Map();
  m.forEach((val, key) => c.set(key, copyValue(val)));
  if (!keepId) c.set("id", newCellId());
  return c;
}

export function moveCell(e, id, delta) {
  const i = cellIndex(e, id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= e.cells.length) return;
  e.doc.transact(() => {
    const copy = cloneCell(e.cells.get(i), true);
    e.cells.delete(i, 1);
    e.cells.insert(j, [copy]);
  }, STRUCTURE);
}

export function moveCellTo(e, id, target) {
  const i = cellIndex(e, id);
  if (i < 0) return;
  const j = Math.max(0, Math.min(target > i ? target - 1 : target, e.cells.length - 1));
  if (i === j) return;
  e.doc.transact(() => {
    const copy = cloneCell(e.cells.get(i), true);
    e.cells.delete(i, 1);
    e.cells.insert(j, [copy]);
  }, STRUCTURE);
}

export function duplicateCell(e, id) {
  const i = cellIndex(e, id);
  if (i < 0) return null;
  const copy = cloneCell(e.cells.get(i), false);
  e.doc.transact(() => e.cells.insert(i + 1, [copy]), STRUCTURE);
  return copy.get("id");
}

export function setCellType(e, id, cellType) {
  const m = cellById(e, id);
  if (!m || m.get("cell_type") === cellType) return;
  e.doc.transact(() => {
    m.set("cell_type", cellType);
    if (cellType === "code") {
      m.set("outputs", new Y.Array());
      m.set("execution_count", null);
    } else {
      m.delete("outputs");
      m.delete("execution_count");
      m.delete("run");
    }
  }, STRUCTURE);
}

export function clearOutputs(e, id) {
  const m = cellById(e, id);
  const outs = m && m.get("outputs");
  if (outs && outs.length) {
    e.doc.transact(() => {
      outs.delete(0, outs.length);
      m.set("execution_count", null);
    });
  }
}

/** The cell's metadata value `key` (plain JSON). */
export function meta(m, key) {
  const md = m.get("metadata");
  return md instanceof Y.Map ? md.get(key) : undefined;
}

export function setMeta(e, m, key, value) {
  let md = m.get("metadata");
  e.doc.transact(() => {
    if (!(md instanceof Y.Map)) {
      md = new Y.Map();
      m.set("metadata", md);
    }
    if (value === undefined || value === null) md.delete(key);
    else md.set(key, value);
  });
}

/** Cellora's own part of a cell's metadata ({title, views, ...}). */
export function ours(m) {
  const v = meta(m, "cellora");
  return v && typeof v === "object" ? v : {};
}

export function setOurs(e, m, patch) {
  const next = { ...ours(m), ...patch };
  Object.keys(next).forEach((k) => next[k] === undefined && delete next[k]);
  setMeta(e, m, "cellora", Object.keys(next).length ? next : null);
}

/** Jupyter's hidden-input / hidden-output flags (metadata.jupyter). */
export function jupyterFlag(m, key) {
  const j = meta(m, "jupyter");
  return !!(j && typeof j === "object" && j[key]);
}

export function setJupyterFlag(e, m, key, on) {
  const j = { ...(meta(m, "jupyter") || {}) };
  if (on) j[key] = true;
  else delete j[key];
  setMeta(e, m, "jupyter", Object.keys(j).length ? j : null);
}

/** Replaces a text with `next`, changing only the middle that differs. */
export function replaceText(e, ytext, next, origin = null) {
  const old = ytext.toString();
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
  e.doc.transact(() => {
    if (endOld > start) ytext.delete(start, endOld - start);
    if (endNew > start) ytext.insert(start, next.slice(start, endNew));
  }, origin);
}

/** This person's undo history of one cell's text. */
export function textUndo(e, cellId, ytext, origin) {
  let u = e.textUndo.get(cellId);
  if (u && u.scope[0] !== ytext) {
    u.destroy();
    u = null;
  }
  if (!u) {
    u = new Y.UndoManager(ytext, { trackedOrigins: new Set([origin]), captureTimeout: 600 });
    e.textUndo.set(cellId, u);
  } else {
    u.addTrackedOrigin(origin);
  }
  return u;
}
