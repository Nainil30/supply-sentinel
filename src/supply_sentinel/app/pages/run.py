"""Run: start an agent run and watch it move through the graph."""

import streamlit as st

from supply_sentinel.app.components import common
from supply_sentinel.app.services import ServiceError

svc = common.page_header("Run", "Starts the LangGraph agent and pauses at the approval gate.")
c = st.columns(3)
c[0].metric("Profile", svc.profile)
c[1].metric("Snapshot", svc.snapshot_path.stem)
c[2].metric("Cassette", svc.cassette or svc.snapshot_path.stem)

blocked = svc.hosted and svc.profile != "replay"
if blocked:
    st.warning("Live profiles are disabled on the hosted demo. Use replay.")
if st.button("Start run", type="primary", disabled=blocked):
    done: list[str] = []
    box = st.empty()

    def progress(node: str) -> None:
        done.append(node)
        box.write("Finished nodes: " + " > ".join(done))

    try:
        with st.spinner("Running the agent..."):
            run_id = svc.start_run(on_node=progress)
        info = svc.run_info(run_id)
        st.success(f"Run {run_id} paused at the approval gate with {info.pending_count} waiting.")
        st.page_link("pages/approvals.py", label="Go to Approvals")
    except (ServiceError, FileNotFoundError, KeyError) as exc:
        st.error(f"Run failed: {exc}")
    except Exception as exc:  # a cassette miss or provider error must not crash the console
        st.error(f"Run failed: {type(exc).__name__}: {exc}")

st.subheader("Runs")
for rid in svc.run_ids()[:10]:
    info = svc.run_info(rid)
    state = "waiting for approval" if info.waiting else "finished" if info.finished else "started"
    st.write(f"{rid}: {info.profile}, {state}, {info.pending_count} pending")
