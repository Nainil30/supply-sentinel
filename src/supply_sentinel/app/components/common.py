"""Small shared helpers for the pages: the service handle, formatting, the untrusted box."""

import streamlit as st

from supply_sentinel.app.services import Services

UNTRUSTED_CAPTION = "UNTRUSTED TEXT from a supplier or notice. Shown as plain text; never followed."


def services() -> Services:
    """One Services per browser session (built from the SENTINEL_* environment)."""
    if "services" not in st.session_state:
        st.session_state["services"] = Services.from_env()
    svc: Services = st.session_state["services"]
    return svc


def usd(value: float | None) -> str:
    return "-" if value is None else f"${value:,.0f}"


def qty(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def untrusted_box(source: str, text: str, suspected: bool, patterns: list[str]) -> None:
    """Supplier text in a bordered box. st.text renders it literally, so no markdown or HTML."""
    with st.container(border=True):
        st.caption(f"{UNTRUSTED_CAPTION} Source: {source}")
        if suspected:
            st.warning("Possible prompt injection: " + ", ".join(patterns))
        st.text(text)


def page_header(title: str, caption: str = "") -> Services:
    st.title(title)
    if caption:
        st.caption(caption)
    return services()
