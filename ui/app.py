"""
Thin Streamlit UI over the pipeline: paste a complaint (or txn_id), watch
the investigation happen stage by stage, get the RCA report + journey diagram.

    streamlit run ui/app.py
"""
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

SRC = Path(__file__).resolve().parents[1] / "src"
for sub in ("investigation", "ingestion", "correlation", "reporting"):
    sys.path.insert(0, str(SRC / sub))

from client import get_client
from correlator import correlate
from diagram import timeline_to_mermaid
from engine import investigate
from rca import generate_rca, render_markdown
from search import get_timeline_by_txn

sys.path.insert(0, str(SRC))
from pipeline import _txn_from_evidence

st.set_page_config(page_title="Sherlog — AI Incident Investigator", layout="wide")
st.title("Sherlog — AI Incident Investigator")
st.caption("Customer complaint in → evidence-backed root cause out. NovaStream demo, Scenario C.")

import json

_ANSWER_KEY = Path(__file__).resolve().parents[1] / "data" / "generated" / "answer_key.json"
try:
    _EXAMPLE_USER = json.loads(_ANSWER_KEY.read_text())[0]["user_id"]
except (OSError, json.JSONDecodeError, IndexError, KeyError):
    _EXAMPLE_USER = "USR-XXXXX"

EXAMPLE = (
    f"Customer {_EXAMPLE_USER} called in: they renewed this afternoon, the card "
    "was charged, but the account still shows expired and no confirmation "
    "email ever arrived."
)

complaint = st.text_area("Customer complaint or transaction id", value=EXAMPLE, height=100)

if st.button("Investigate", type="primary") and complaint.strip():
    with st.status("Investigating...", expanded=True) as status:
        st.write("**Stage 1 — AI investigation** (LLM agent querying the log store)")
        investigation = investigate(complaint.strip(), verbose=False)
        st.json(investigation)

        if not investigation.get("issue_found"):
            status.update(label="No failure found", state="complete")
            st.success("Investigation concluded: no failure for this complaint. Evidence above.")
            st.stop()

        st.write("**Stage 2 — Source code correlation** (GitPython over the demo-app repo)")
        correlation = correlate(investigation["failure_point"])
        if correlation:
            st.code(f"{correlation['file']}:{correlation['line']}")

        st.write("**Stage 3 — RCA report** (LLM writeup, evidence passed through verbatim)")
        report = generate_rca(investigation, correlation, verbose=False)

        st.write("**Stage 4 — Journey diagram**")
        txn_id = _txn_from_evidence(investigation.get("evidence", []))
        mermaid = None
        if txn_id:
            timeline = get_timeline_by_txn(get_client(), txn_id)
            mermaid = timeline_to_mermaid(timeline, issue_found=True)

        status.update(label="Investigation complete", state="complete")

    st.markdown(render_markdown(report))

    if mermaid:
        st.subheader("Transaction journey")
        # The components.html iframe doesn't inherit Streamlit's theme, so
        # detect it and configure mermaid + background to match; otherwise
        # the default theme draws dark-gray labels on the dark background.
        try:
            dark = st.context.theme.type == "dark"
        except AttributeError:
            dark = True
        mm_theme = "dark" if dark else "default"
        bg = "#0e1117" if dark else "#ffffff"
        components.html(
            f"""
            <script type="module">
              import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
              mermaid.initialize({{
                startOnLoad: true,
                theme: "{mm_theme}",
                themeVariables: {{ fontSize: "15px" }}
              }});
            </script>
            <body style="background:{bg}; margin:0;">
              <pre class="mermaid" style="background:{bg};">{mermaid}</pre>
            </body>
            """,
            height=650,
            scrolling=True,
        )
        with st.expander("Mermaid source"):
            st.code(mermaid, language="text")
