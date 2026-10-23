"""Cellora: a Python notebook that people use together.

Pages: ``/`` lists the notebooks; ``/nb/<id>`` is a notebook at its own address, used together by
everyone who opens it: they edit the same cells live (each with their own cursor, in their
colour), run them in one shared kernel, see who is here, and can call everyone. AI helps write,
fix and explain the code.
"""

import reflex as rx

try:
    from reflex_ddns_auth.intent import install_intent_host
except ImportError:  # a standalone Cellora, without the DDNS libraries
    install_intent_host = None

from cellora import rooms  # noqa: F401  (connects the kernels and the saving to the rooms)
from cellora.api import api, lifespan
from cellora.components.home import home_page
from cellora.components.page import notebook_page
from cellora.components.together import call_keepalive
from cellora.states.ai import AIState
from cellora.states.home import HomeState
from cellora.states.lang import LangState
from cellora.states.notebook import AuthState, NotebookState

app = rx.App(
    head_components=[
        rx.el.link(rel="preconnect", href="https://fonts.googleapis.com"),
        rx.el.link(rel="preconnect", href="https://fonts.gstatic.com", cross_origin=""),
        rx.el.link(
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap",
            rel="stylesheet",
        ),
        rx.el.link(rel="stylesheet", href="/cellora/cellora.css"),
        rx.el.link(rel="icon", type="image/svg+xml", href="/favicon.svg"),
    ],
    # Our own backend routes (the notebooks' shared documents, goodbyes, completions, AI
    # suggestions, downloads), next to Reflex's.
    api_transformer=api,
)
app.register_lifespan_task(lifespan)

# Dialogs of other apps (calls) live above every page, so a call goes on when you go back to
# the notebook list.
if install_intent_host is not None:
    install_intent_host(app)
    app.extra_app_wraps[(8, "CelloraCallKeepalive")] = lambda stateful: rx.fragment(call_keepalive())

_auth_on_load = [AuthState.load_auth] if AuthState is not None else []

app.add_page(
    home_page,
    route="/",
    title="Cellora",
    on_load=[*_auth_on_load, LangState.detect, NotebookState.leave_notebook, HomeState.refresh, AIState.load],
)
app.add_page(
    notebook_page,
    route="/nb/[notebook]",
    title="Cellora",
    on_load=[*_auth_on_load, LangState.detect, NotebookState.on_load, AIState.load],
)
