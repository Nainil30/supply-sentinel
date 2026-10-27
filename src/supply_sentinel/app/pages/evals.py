"""Evals: results files, per-category pass rates and the latest failures."""

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common

svc = common.page_header(
    "Evals", "Read from evals/results. Every number comes from a results file."
)
files = svc.eval_files()
if not files:
    st.info("No results yet. Run `sentinel eval --suite smoke --profile replay`.")
    st.stop()

path = st.selectbox("Results file", files, format_func=lambda p: p.name)
doc = svc.eval_doc(path)
meta = doc["meta"]
st.caption(
    f"Suite {meta['suite']}, system {meta['system']}, profile {meta['profile']}, "
    f"started {meta['started']}."
)
summ = svc.eval_summary(doc)


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


m = st.columns(5)
m[0].metric("Pass rate", pct(summ["pass_rate"]), help=f"{summ['runs']} runs")
m[1].metric("Action", pct(summ["action"]))
m[2].metric("Quantity", pct(summ["quantity"]))
m[3].metric("Grounding", pct(summ["grounding"]))
m[4].metric("Safety", pct(summ["safety"]))

st.subheader("Pass rate by category")
st.dataframe(pd.DataFrame(svc.eval_by_category(doc)), hide_index=True)
st.subheader("Failures")
fails = svc.eval_failures(doc)
if fails:
    st.dataframe(pd.DataFrame(fails), hide_index=True)
else:
    st.success("No failures in this file.")
board = svc.results_dir / "leaderboard.md"
if board.exists():
    with st.expander("Leaderboard"):
        st.markdown(board.read_text(encoding="utf-8"))
