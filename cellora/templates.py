"""Ready-made notebooks: the getting-started tour, in English and 繁體中文."""

from __future__ import annotations

from cellora import nbfile

_DATA = '''import pandas as pd
import numpy as np

rng = np.random.default_rng(7)
sales = pd.DataFrame({
    "month": pd.date_range("2026-01-01", periods=12, freq="MS"),
    "region": rng.choice(["North", "South", "East", "West"], 12),
    "units": rng.integers(80, 260, 12),
    "price": rng.uniform(9.5, 14.5, 12).round(2),
})
sales["revenue"] = (sales["units"] * sales["price"]).round(2)
sales'''

_SQL = '''%%sql
SELECT region, COUNT(*) AS months, ROUND(SUM(revenue), 2) AS revenue
FROM sales
GROUP BY region
ORDER BY revenue DESC'''

_PLOT = '''import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(7, 3))
ax.plot(sales["month"], sales["revenue"], marker="o", color="#4f46e5")
ax.set_title("Revenue per month")
ax.grid(alpha=0.3)
plt.show()'''

_TYPO = '''# This cell has a mistake on purpose: run it, then press ✨ Fix with AI.
sales.groupby("regoin")["revenue"].sum()'''

TEXTS = {
    "en": {
        "title": "Welcome to Cellora",
        "intro": """# Welcome to Cellora 👋

Cellora is a Python notebook you use **together**. Everyone who opens this page edits the same cells at the same time, runs code in the same kernel, and sees the others' cursors as they type.

**Try it:** click into the code cell below and press **Shift + Enter** to run it.""",
        "table": """The table above is **interactive**: click a column to sort it, type to filter it, or switch to **Chart** to plot it, without writing code. Any pandas DataFrame shows like this.

## SQL on your DataFrames

A `%%sql` cell runs SQL (DuckDB) on the notebook's DataFrames by their names, and on files like `'data.csv'`.""",
        "plot": "## Pictures\n\nmatplotlib draws right into the notebook:",
        "ai": """## Let AI help

- Start typing in a code cell: a grey suggestion appears. Press **Tab** to take it.
- Press **⌘ I** (Ctrl I on Windows) in a cell and say what you want: the AI writes or changes the cell, and you see the change before you accept it.
- When a cell fails, press **✨ Fix with AI** under the error.
- Open the **AI assistant** (top right) to ask anything about this notebook: it knows the cells, the outputs and the variables.""",
        "together": """## Together

- **Share** the address of this page (🔗 at the top): whoever opens it works here with you.
- The people here show at the top. In the cells you see where each one is, in their colour.
- **Call everyone** starts a call with the people on this notebook; it goes on while you work.

## Keys

| Keys | What they do |
|---|---|
| Shift + Enter | Run the cell, go to the next |
| ⌘/Ctrl + Enter | Run the cell, stay |
| Alt + Enter | Run, and add a cell below |
| ⌘/Ctrl + I | Ask the AI to write or change the cell |
| Esc, then A / B | Add a cell above / below |
| Esc, then M / Y | Make the cell Markdown / code |
| Esc, then D D | Delete the cell |""",
    },
    "zh-TW": {
        "title": "歡迎使用 Cellora",
        "intro": """# 歡迎使用 Cellora 👋

Cellora 是一個可以**多人一起用**的 Python 筆記本。打開這一頁的每個人，同時編輯同一份儲存格、在同一個 kernel 裡執行程式，打字時也看得到彼此的游標。

**試試看：**點進下面的程式儲存格，按 **Shift + Enter** 執行。""",
        "table": """上面的表格是**互動式**的：點欄位名稱排序、輸入文字篩選，或切到**圖表**直接畫圖，不用寫程式。任何 pandas DataFrame 都會這樣顯示。

## 用 SQL 查 DataFrame

`%%sql` 儲存格可以用 SQL（DuckDB）查詢筆記本裡的 DataFrame（用變數名稱），也能直接查 `'data.csv'` 這類檔案。""",
        "plot": "## 畫圖\n\nmatplotlib 的圖會直接畫在筆記本裡：",
        "ai": """## 讓 AI 幫忙

- 在程式儲存格裡打字，會出現灰色的建議，按 **Tab** 接受。
- 在儲存格裡按 **⌘ I**（Windows 用 Ctrl I），說你想做什麼：AI 會寫好或修改這格，你先看過差異再決定要不要接受。
- 儲存格出錯時，按錯誤下方的 **✨ 用 AI 修正**。
- 打開右上角的 **AI 助理**，問任何關於這份筆記本的問題：它知道每一格的程式、輸出和變數。""",
        "together": """## 一起用

- **分享**這一頁的網址（上方的 🔗）：打開的人就能和你一起在這裡工作。
- 在這裡的人會顯示在上方；儲存格裡也看得到每個人在哪一格，用他們的顏色標出來。
- **Call everyone** 會撥電話給這份筆記本上的所有人；通話時可以繼續工作。

## 快捷鍵

| 按鍵 | 作用 |
|---|---|
| Shift + Enter | 執行這格，跳到下一格 |
| ⌘/Ctrl + Enter | 執行這格，留在原地 |
| Alt + Enter | 執行，並在下面新增一格 |
| ⌘/Ctrl + I | 請 AI 寫或修改這格 |
| Esc，再按 A / B | 在上方 / 下方新增一格 |
| Esc，再按 M / Y | 把這格改成 Markdown / 程式 |
| Esc，再按 D D | 刪除這格 |""",
    },
}

_TYPO_ZH = '''# 這格故意寫錯了：執行看看，再按 ✨ 用 AI 修正。
sales.groupby("regoin")["revenue"].sum()'''


def tour(lang: str = "en"):
    t = TEXTS.get(lang, TEXTS["en"])
    cells = [
        nbfile.markdown_cell(t["intro"]),
        nbfile.code_cell(_DATA),
        nbfile.markdown_cell(t["table"]),
        nbfile.code_cell(_SQL),
        nbfile.markdown_cell(t["plot"]),
        nbfile.code_cell(_PLOT),
        nbfile.markdown_cell(t["ai"]),
        nbfile.code_cell(_TYPO_ZH if lang == "zh-TW" else _TYPO),
        nbfile.markdown_cell(t["together"]),
    ]
    return nbfile.new_notebook(t["title"], cells=cells)
