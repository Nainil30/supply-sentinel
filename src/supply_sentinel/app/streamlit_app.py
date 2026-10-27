"""Planner Console entry point: `sentinel ui` (or `streamlit run` on this file)."""

import streamlit as st

st.set_page_config(page_title="Supply Sentinel", page_icon=":material/inventory_2:", layout="wide")

pages = [
    st.Page("pages/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("pages/queue.py", title="Exception queue", icon=":material/list:"),
    st.Page("pages/detail.py", title="Exception detail", icon=":material/search:"),
    st.Page("pages/approvals.py", title="Approvals", icon=":material/task_alt:"),
    st.Page("pages/what_if.py", title="What-if", icon=":material/tune:"),
    st.Page("pages/decisions.py", title="Decisions log", icon=":material/history:"),
    st.Page("pages/evals.py", title="Evals", icon=":material/fact_check:"),
    st.Page("pages/run.py", title="Run", icon=":material/play_arrow:"),
]
st.navigation(pages).run()
