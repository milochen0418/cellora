"""A real kernel runs a notebook's cells and writes the outputs into its shared document."""

import asyncio
import json
import time

from cellora import nbfile
from cellora.docs import DOCS
from cellora.kernels import KERNELS

ME = {"name": "Tester", "color": "#188038", "peer": "p1"}


async def wait_idle(nb_id: str, timeout: float = 60.0) -> None:
    session = KERNELS.get(nb_id)
    end = time.time() + timeout
    while time.time() < end:
        if session is not None and not session.busy and session.state == "idle":
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"kernel still busy: {session.info() if session else None}")


def outputs(live, cell_id):
    return [o for o in live.to_ipynb()["cells"][live.index_of(cell_id)]["outputs"]]


def test_kernel_runs_cells_into_the_notebook():
    asyncio.run(_scenario())


async def _scenario():
    nb = nbfile.new_notebook("Kernel test", cells=[
        nbfile.code_cell("import pandas as pd\ndf = pd.DataFrame({'region': ['N', 'S', 'N'], 'sales': [3, 4, 5]})\ndf"),
        nbfile.code_cell("for i in range(3):\n    print(i)\nprint('done')"),
        nbfile.code_cell("%%sql\nSELECT region, SUM(sales) AS total FROM df GROUP BY region ORDER BY region"),
        nbfile.code_cell("1/0"),
        nbfile.code_cell("print('never runs')"),
        nbfile.code_cell("import time\ntime.sleep(30)"),
    ])
    meta = DOCS.create(nb, "Kernel test", {"name": "Tester"})
    live = await DOCS.open(meta.id)
    ids = live.cell_ids()
    try:
        added = await KERNELS.run(meta.id, ids[:5], ME)
        assert added == 5
        assert live.run_info(ids[0])["state"] in ("queued", "running")
        await wait_idle(meta.id)

        # The DataFrame: an interactive table, and the usual HTML for other tools.
        first = outputs(live, ids[0])
        assert first[-1]["output_type"] == "execute_result"
        table = first[-1]["data"]["application/vnd.cellora.table+json"]
        assert [c["name"] for c in table["columns"]] == ["region", "sales"]
        assert table["rows"] == [["N", 3], ["S", 4], ["N", 5]]
        assert "text/html" in first[-1]["data"]
        assert live.run_info(ids[0])["state"] == "ok" and live.run_info(ids[0])["by"] == "Tester"

        # Printing: one stream output, joined.
        assert outputs(live, ids[1]) == [{"output_type": "stream", "name": "stdout", "text": "0\n1\n2\ndone\n"}]

        # SQL over the DataFrame.
        sql = outputs(live, ids[2])[-1]["data"]["application/vnd.cellora.table+json"]
        assert sql["rows"] == [["N", 8], ["S", 4]]

        # The error, and the cell after it cancelled.
        err = outputs(live, ids[3])[0]
        assert err["output_type"] == "error" and err["ename"] == "ZeroDivisionError"
        assert live.run_info(ids[3])["state"] == "error"
        assert live.run_info(ids[4])["state"] == "cancelled"
        assert outputs(live, ids[4]) == []
        counts = [c["execution_count"] for c in live.to_ipynb()["cells"][:4]]
        assert counts == [1, 2, 3, 4]

        # The variables panel sees df (with its columns) and i, not the app's helpers.
        session = KERNELS.get(meta.id)
        names = {v["name"]: v for v in session.variables}
        assert "df" in names and names["df"]["size"] == "3 × 2" and names["df"]["columns"][0].startswith("region")
        assert "i" in names and not any(n.startswith("_") for n in names)

        # Completion and docs from the kernel.
        comp = await session.complete("df.gro", 6)
        assert "groupby" in comp["matches"]
        doc = await session.inspect("len", 3)
        assert "Return the number of items" in doc

        # A long run can be interrupted.
        await KERNELS.run(meta.id, [ids[5]], ME)
        await asyncio.sleep(1.0)
        assert live.run_info(ids[5])["state"] == "running"
        await session.interrupt()
        await wait_idle(meta.id)
        assert outputs(live, ids[5])[0]["ename"] == "KeyboardInterrupt"

        # A restart forgets the variables.
        assert await session.restart()
        t0 = time.time()
        await KERNELS.run(meta.id, [ids[1]], ME)
        await wait_idle(meta.id)
        # Nothing the kernel printed right after the restart is lost, and the run doesn't hang.
        assert outputs(live, ids[1])[0]["text"] == "0\n1\n2\ndone\n"
        assert time.time() - t0 < 3
        assert "df" not in {v["name"] for v in session.variables}
        assert live.to_ipynb()["cells"][1]["execution_count"] == 1

        # Saved as an .ipynb that Jupyter reads.
        assert await DOCS.save(live)
        saved = nbfile.parse(DOCS.store.read(meta.id))
        assert saved.cells[0].outputs[-1]["data"]["application/vnd.cellora.table+json"]["total"] == 3
        assert json.loads(json.dumps(saved))  # plain JSON
    finally:
        await KERNELS.shutdown_all()
        DOCS.delete(meta.id)
