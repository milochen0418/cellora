"""The language of the page: English or 繁體中文, as the browser prefers until someone picks one
(kept in the browser)."""

from __future__ import annotations

import reflex as rx

from cellora.i18n import LANGS, TEXTS


class LangState(rx.State):
    # The language picked in the header (kept in the browser); "" until someone picks one.
    chosen: str = rx.LocalStorage("", name="cellora_lang", sync=True)
    # The browser's preference, from its Accept-Language.
    detected: str = "en"

    @rx.var
    def lang(self) -> str:
        return self.chosen if self.chosen in LANGS else (self.detected if self.detected in LANGS else "en")

    @rx.var
    def t(self) -> dict[str, str]:
        base = TEXTS["en"]
        texts = TEXTS.get(self.lang, base)
        return {k: texts.get(k, v) for k, v in base.items()}

    @rx.event
    def detect(self):
        accept = str(getattr(self.router.headers, "accept_language", "") or "").lower()
        first = accept.split(",")[0].strip()
        self.detected = "zh-TW" if first.startswith("zh") else "en"

    @rx.event
    def pick(self, lang: str):
        if lang in LANGS:
            self.chosen = lang


async def text(state: rx.State, key: str, **params: str) -> str:
    """A text in the tab's language, for what the server says (toasts)."""
    lang_state = await state.get_state(LangState)
    texts = TEXTS.get(lang_state.lang, TEXTS["en"])
    value = texts.get(key, TEXTS["en"].get(key, key))
    return value.format(**params) if params else value
