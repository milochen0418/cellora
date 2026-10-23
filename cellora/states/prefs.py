"""One person's settings, kept in their browser: the AI's grey suggestions while typing, and line
numbers in the editors."""

import reflex as rx


class PrefsState(rx.State):
    ai_inline_off: str = rx.LocalStorage("", name="cellora_ai_inline_off", sync=True)
    line_numbers_on: str = rx.LocalStorage("", name="cellora_line_numbers", sync=True)

    @rx.var
    def ai_inline(self) -> bool:
        return self.ai_inline_off != "1"

    @rx.var
    def line_numbers(self) -> bool:
        return self.line_numbers_on == "1"

    @rx.event
    def toggle_ai_inline(self):
        self.ai_inline_off = "" if self.ai_inline_off == "1" else "1"

    @rx.event
    def toggle_line_numbers(self):
        self.line_numbers_on = "" if self.line_numbers_on == "1" else "1"
