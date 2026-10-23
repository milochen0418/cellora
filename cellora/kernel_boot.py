"""Code every Cellora kernel runs once when it starts (sent as text; never imported by the app).

It adds, in the kernel:

* the interactive table: a pandas DataFrame or Series shows as
  ``application/vnd.cellora.table+json`` (columns, kinds, the first rows), which the notebook
  draws as a sortable, filterable table with charts, as Databricks' ``display()`` does. The
  usual ``text/html`` and ``text/plain`` stay in the output too, so the .ipynb reads the same in
  Jupyter.
* ``%%sql`` (and ``%sql``): SQL on the notebook's DataFrames by their variable names, and on
  files (``SELECT * FROM 'sales.csv'``), with DuckDB. ``%%sql result`` keeps the answer in
  ``result``.
* ``__cellora_vars()``: what the Variables panel and the AI see: every variable's name, type,
  size and a short preview (the columns of a DataFrame).
* sharp (retina) matplotlib pictures.

Everything is defined inside ``__cellora_boot`` and cleaned up, so the notebook's own names stay
its own.
"""


def __cellora_boot():
    import json as _json

    from IPython import get_ipython

    ip = get_ipython()
    TABLE = "application/vnd.cellora.table+json"
    ROWS = 1000
    # At most this many values in the table's JSON (a wide frame shows fewer rows).
    CELLS = 200_000

    def _table(obj, include=None, exclude=None):
        import pandas as pd

        df = obj.to_frame() if isinstance(obj, pd.Series) else obj
        shown = df.head(max(20, min(ROWS, CELLS // max(1, len(df.columns)))))
        index_named = not isinstance(shown.index, pd.RangeIndex) or any(n is not None for n in shown.index.names)
        flat = shown.reset_index() if index_named else shown
        columns = []
        for i, name in enumerate(flat.columns):
            s = flat.iloc[:, i]
            if pd.api.types.is_bool_dtype(s):
                kind = "bool"
            elif pd.api.types.is_numeric_dtype(s):
                kind = "number"
            elif pd.api.types.is_datetime64_any_dtype(s):
                kind = "date"
            else:
                kind = "text"
            label = " / ".join(str(p) for p in name) if isinstance(name, tuple) else str(name)
            columns.append({"name": label, "dtype": str(s.dtype), "kind": kind, "index": index_named and i < len(shown.index.names)})
        rows = _json.loads(flat.to_json(orient="values", date_format="iso", default_handler=str))
        return {TABLE: {"columns": columns, "rows": rows, "total": int(len(df)), "shown": int(len(shown))}}

    formatter = ip.display_formatter.mimebundle_formatter
    # pandas 3 calls its classes "pandas.DataFrame"; older ones "pandas.core.frame.DataFrame".
    for module, name in (("pandas", "DataFrame"), ("pandas", "Series"), ("pandas.core.frame", "DataFrame"), ("pandas.core.series", "Series")):
        formatter.for_type_by_name(module, name, _table)

    try:
        ip.run_line_magic("config", "InlineBackend.figure_formats = ['retina']")
    except Exception:
        pass

    from IPython.core.magic import register_line_cell_magic

    @register_line_cell_magic
    def sql(line, cell=None):
        """SQL with DuckDB over the notebook's DataFrames and files. ``%%sql name`` keeps the
        result in ``name``."""
        try:
            import duckdb
        except ImportError:
            raise RuntimeError("%%sql needs DuckDB: run  %pip install duckdb") from None
        target, query = (line.strip(), cell) if cell is not None else ("", line)
        con = getattr(sql, "con", None)
        if con is None:
            con = sql.con = duckdb.connect()
        try:
            import pandas as pd

            for name, value in list(ip.user_ns.items()):
                if not name.startswith("_") and isinstance(value, pd.DataFrame):
                    con.register(name, value)
        except ImportError:
            pass
        rel = con.sql(query)
        result = rel.df() if rel is not None else None
        if target and target.isidentifier():
            ip.user_ns[target] = result
        return result

    hidden = set(ip.user_ns_hidden)

    def variables():
        out = []
        for name, value in list(ip.user_ns.items()):
            if name.startswith("_") or name in hidden or name in ("In", "Out", "exit", "quit", "get_ipython"):
                continue
            t = type(value)
            if t.__name__ in ("module", "function", "builtin_function_or_method", "type", "method") or isinstance(value, type):
                continue
            entry = {"name": name, "type": t.__name__, "size": "", "preview": ""}
            try:
                shape = getattr(value, "shape", None)
                if isinstance(shape, tuple):
                    entry["size"] = " × ".join(str(s) for s in shape)
                elif hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
                    entry["size"] = str(len(value))
                elif isinstance(value, str):
                    entry["size"] = f"{len(value)} chars"
                cols = getattr(value, "columns", None)
                dtypes = getattr(value, "dtypes", None)
                if cols is not None and dtypes is not None and t.__name__ == "DataFrame":
                    entry["columns"] = [f"{c}: {d}" for c, d in list(zip(cols, dtypes))[:40]]
                    entry["preview"] = ", ".join(str(c) for c in list(cols)[:12])
                else:
                    r = repr(value)
                    entry["preview"] = r if len(r) <= 160 else r[:157] + "…"
                dtype = getattr(value, "dtype", None)
                if dtype is not None and t.__name__ != "DataFrame":
                    entry["type"] = f"{t.__name__} · {dtype}"
            except Exception:
                pass
            out.append(entry)
            if len(out) >= 300:
                break
        return _json.dumps(out)

    ip.user_ns["__cellora_vars"] = variables
    ip.user_ns_hidden["__cellora_vars"] = variables


__cellora_boot()
del __cellora_boot
