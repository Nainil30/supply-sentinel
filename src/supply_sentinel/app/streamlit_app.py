"""Planner Console entry point: `sentinel ui` (or `streamlit run` on this file)."""

from pathlib import Path

import streamlit as st

# Absolute paths: Streamlit resolves relative page paths against the *main* script, which is
# hosted_app.py at the repo root when this file is run through it (Streamlit Community Cloud).
PAGES = Path(__file__).parent / "pages"

st.set_page_config(page_title="Supply Sentinel", page_icon=":material/inventory_2:", layout="wide")

pages = [
    st.Page(PAGES / "overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page(PAGES / "queue.py", title="Exception queue", icon=":material/list:"),
    st.Page(PAGES / "detail.py", title="Exception detail", icon=":material/search:"),
    st.Page(PAGES / "approvals.py", title="Approvals", icon=":material/task_alt:"),
    st.Page(PAGES / "what_if.py", title="What-if", icon=":material/tune:"),
    st.Page(PAGES / "decisions.py", title="Decisions log", icon=":material/history:"),
    st.Page(PAGES / "evals.py", title="Evals", icon=":material/fact_check:"),
    st.Page(PAGES / "run.py", title="Run", icon=":material/play_arrow:"),
]
st.navigation(pages).run()
