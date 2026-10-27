"""Decisions log: every recorded recommendation and human decision."""

import pandas as pd
import streamlit as st

from supply_sentinel.app.components import common

svc = common.page_header("Decisions log", "All recorded recommendations and human decisions.")
f1, f2 = st.columns(2)
status = f1.selectbox("Status", ["", "PENDING", "APPROVED", "EDITED", "REJECTED"])
part = f2.text_input("Part contains")
rows = svc.decisions_log(status or None, part or None)
frame = pd.DataFrame(rows)
st.caption(f"{len(rows)} decisions.")
st.dataframe(frame, hide_index=True)
st.download_button(
    "Export CSV", frame.to_csv(index=False), file_name="decisions.csv", mime="text/csv"
)
