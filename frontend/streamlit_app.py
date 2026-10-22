"""SecureAgentOps dashboard (Streamlit).

Run: ``streamlit run frontend/streamlit_app.py`` with ``SAO_API_URL`` pointing at the API.
All authorisation happens in the API; the UI only hides controls a role can't use.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frontend.api_client import ApiClient, ApiError

API_URL = os.environ.get("SAO_API_URL", "http://localhost:8000")
EXAMPLES = [
    "What is our p95 latency objective for agent requests?",
    "Is the vector store healthy?",
    "Open a sev2 incident for elevated API latency in the agent gateway",
    "Show me the usage report",
]
STATUS_COLOURS = {
    "completed": "green",
    "succeeded": "green",
    "approved": "green",
    "pending_approval": "orange",
    "pending": "orange",
    "executing": "orange",
    "denied": "red",
    "rejected": "red",
    "error": "red",
    "failed": "red",
    "timeout": "red",
    "expired": "gray",
}

st.set_page_config(page_title="SecureAgentOps", page_icon="🛡️", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; max-width: 1200px;}
      .sao-pill {display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.78rem;
                 font-weight:600; margin-right:6px; border:1px solid rgba(128,128,128,.35);}
      .sao-tenant {background:rgba(56,132,255,.12); color:#2563eb;}
      .sao-admin {background:rgba(220,38,38,.12); color:#dc2626;}
      .sao-user {background:rgba(22,163,74,.12); color:#15803d;}
      .sao-mock {background:rgba(234,179,8,.15); color:#a16207;}
      .sao-muted {color:rgba(128,128,128,.95); font-size:0.82rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --- helpers -------------------------------------------------------------------
def client() -> ApiClient:
    return ApiClient(API_URL, st.session_state.get("token"))


def principal() -> dict:
    return st.session_state["principal"]


def is_admin() -> bool:
    return principal()["role"] == "admin"


def badge(status: str) -> str:
    return f":{STATUS_COLOURS.get(status, 'gray')}[**{status.replace('_', ' ')}**]"


def plain(text: str) -> str:
    """Show untrusted document text literally: no Markdown headings, links or emphasis."""
    return " ".join(re.sub(r"[#*_`>|\[\]]", " ", text).split())


def when(value: str | None) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")


def call(fn, *args, **kwargs):
    """Run an API call and turn errors into UI messages. Expired tokens send the user back to login."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        if exc.status == 401 and st.session_state.get("token"):
            st.session_state.clear()
            st.warning("Your session expired. Please sign in again.")
            st.rerun()
        st.error(str(exc))
        return None


def run_meta(run: dict) -> str:
    parts = [badge(run["status"]), f"intent `{run.get('intent') or '-'}`"]
    if run.get("tool_name"):
        parts.append(f"tool `{run['tool_name']}`")
    if run.get("latency_ms") is not None:
        parts.append(f"{run['latency_ms']:.0f} ms (measured)")
    if run.get("token_source") in {"measured", "estimated"}:
        total = (run.get("input_tokens") or 0) + (run.get("output_tokens") or 0)
        parts.append(f"{total} tokens ({run['token_source']})")
    if run.get("estimated_cost_usd") is not None:
        parts.append(f"≈ ${run['estimated_cost_usd']:.6f} (estimate)")
    return " · ".join(parts)


# --- login ---------------------------------------------------------------------
def login_page() -> None:
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        st.title("🛡️ SecureAgentOps")
        st.caption(
            "Secure, multi-tenant AI agent platform - tenant-isolated RAG, approval-gated tools, "
            "built-in observability."
        )
        sign_in, create = st.tabs(["Sign in", "Create organisation"])
        with sign_in, st.form("login"):
            email = st.text_input("Email", placeholder="analyst@acme.example")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
                result = call(ApiClient(API_URL).login, email, password)
                if result:
                    st.session_state.update(
                        token=result["access_token"], principal=result["principal"], chat=[]
                    )
                    st.rerun()
        with create, st.form("register"):
            tenant = st.text_input(
                "Organisation name", placeholder="my-team", help="lowercase, digits, hyphens"
            )
            email = st.text_input("Admin email")
            password = st.text_input(
                "Password", type="password", help="12+ characters with letters and digits"
            )
            if st.form_submit_button("Create organisation", width="stretch"):
                result = call(ApiClient(API_URL).register, tenant, email, password)
                if result:
                    st.session_state.update(
                        token=result["access_token"], principal=result["principal"], chat=[]
                    )
                    st.rerun()
        st.markdown(
            "<p class='sao-muted'>Demo accounts (if seeded): <code>admin@acme.example</code>, "
            "<code>analyst@acme.example</code>, <code>admin@globex.example</code>, "
            "<code>analyst@globex.example</code> - password is <code>SAO_DEMO_PASSWORD</code> from your "
            "<code>.env</code>.</p>",
            unsafe_allow_html=True,
        )


# --- pages -----------------------------------------------------------------------
def chat_page() -> None:
    st.header("Agent chat")
    st.caption(
        "Ask about your organisation's documents, check system status, or request an incident. "
        "State-changing actions always wait for an administrator."
    )
    cols = st.columns(len(EXAMPLES))
    pending_prompt = None
    for col, example in zip(cols, EXAMPLES, strict=True):
        if col.button(example, width="stretch"):
            pending_prompt = example

    for item in st.session_state.setdefault("chat", []):
        with st.chat_message("user"):
            st.write(item["message"])
        with st.chat_message("assistant", avatar="🛡️"):
            st.markdown(item["response"] or "")
            st.markdown(run_meta(item))
            if item.get("citations"):
                with st.expander(f"Sources ({len(item['citations'])})"):
                    for c in item["citations"]:
                        st.markdown(
                            f"**[{c['index']}] {c['filename']}** · chunk {c['chunk_index']} · "
                            f"score {c['score']:.3f}"
                        )
                        st.caption(plain(c["snippet"]))

    prompt = st.chat_input("Ask the agent…") or pending_prompt
    if prompt:
        with st.spinner("Running agent workflow…"):
            run = call(client().chat, prompt)
        if run:
            st.session_state["chat"].append(run)
            st.rerun()


def documents_page() -> None:
    st.header("Knowledge base")
    st.caption(
        f"Documents are indexed only for **{principal()['tenant_name']}**. PDF, TXT and Markdown, "
        "up to the configured size limit."
    )
    with st.form("upload", clear_on_submit=True):
        files = st.file_uploader("Upload documents", type=["pdf", "txt", "md"], accept_multiple_files=True)
        if st.form_submit_button("Upload and index", type="primary") and files:
            for f in files:
                doc = call(client().upload, f.name, f.getvalue())
                if doc:
                    st.success(f"Indexed {doc['filename']} ({doc['chunk_count']} chunks)")

    docs = call(client().documents) or []
    if docs:
        frame = pd.DataFrame(docs)[
            ["filename", "content_type", "chunk_count", "size_bytes", "created_at", "id"]
        ]
        frame["created_at"] = frame["created_at"].map(when)
        st.dataframe(frame, hide_index=True, width="stretch")
        if is_admin():
            with st.expander("Delete a document (admin)"):
                choice = st.selectbox("Document", docs, format_func=lambda d: d["filename"])
                if st.button("Delete", type="secondary") and choice:
                    call(client().delete_document, choice["id"])
                    st.rerun()
    else:
        st.info("No documents yet. Upload one above.")

    st.subheader("Try retrieval directly")
    query = st.text_input("Search query", placeholder="incident escalation")
    if query:
        result = call(client().search, query)
        if result is not None:
            st.caption(f"{len(result['results'])} result(s) in {result['latency_ms']} ms (measured)")
            for hit in result["results"]:
                st.markdown(f"**{hit['filename']}** · chunk {hit['chunk_index']} · score {hit['score']:.3f}")
                st.caption(plain(hit["snippet"]))


def approvals_page() -> None:
    st.header("Approvals")
    if is_admin():
        st.caption("Requests from your organisation. You can't approve your own request.")
    else:
        st.caption("Your requests. An administrator of your organisation decides them.")
    users = {u["id"]: u["email"] for u in (call(client().users) or [])} if is_admin() else {}
    me = principal()

    pending = call(client().approvals, "pending") or []
    st.subheader(f"Pending ({len(pending)})")
    if not pending:
        st.success("Nothing waiting for a decision.")
    for item in pending:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            with left:
                requester = item["requested_by"]
                who = "you" if requester == me["user_id"] else users.get(requester, requester[:8])
                st.markdown(f"**`{item['tool_name']}`** requested by {who}")
                st.code(json.dumps(item["arguments"], indent=2), language="json")
                st.caption(
                    f"Requested {when(item['created_at'])} · expires {when(item['expires_at'])} · "
                    f"id {item['id']}"
                )
            with right:
                if is_admin():
                    note = st.text_input("Note (optional)", key=f"note-{item['id']}")
                    approve_col, reject_col = st.columns(2)
                    approve = approve_col.button(
                        "Approve", key=f"a-{item['id']}", type="primary", width="stretch"
                    )
                    reject = reject_col.button("Reject", key=f"r-{item['id']}", width="stretch")
                    if (approve or reject) and call(client().decide, item["id"], approve, note):
                        st.rerun()
                else:
                    st.info("Waiting for an administrator.")

    decided = [a for a in (call(client().approvals) or []) if a["status"] != "pending"]
    if decided:
        st.subheader("Decided")
        frame = pd.DataFrame(
            [
                {
                    "tool": a["tool_name"],
                    "status": a["status"],
                    "decided": when(a["decided_at"]),
                    "note": a["decision_note"],
                    "result": json.dumps(a["result"])[:120],
                }
                for a in decided
            ]
        )
        st.dataframe(frame, hide_index=True, width="stretch")

    incidents = call(client().incidents) or []
    st.subheader("Incidents (simulated)")
    if incidents:
        frame = pd.DataFrame(incidents)[["severity", "title", "affected_component", "status", "created_at"]]
        frame["created_at"] = frame["created_at"].map(when)
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.caption("None yet. Ask the agent to open one, then approve it as an administrator.")


def activity_page() -> None:
    st.header("Activity")
    st.caption("Your activity, or your whole organisation's if you're an administrator.")
    tools_tab, runs_tab, catalogue_tab = st.tabs(["Tool executions", "Agent runs", "Tool catalogue"])
    with tools_tab:
        rows = call(client().tool_invocations, 100) or []
        if rows:
            frame = pd.DataFrame(
                [
                    {
                        "time": when(r["created_at"]),
                        "tool": r["tool_name"],
                        "status": r["status"],
                        "duration_ms": r["duration_ms"],
                        "arguments": json.dumps(r["arguments"])[:100],
                        "error": r["error"],
                    }
                    for r in rows
                ]
            )
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            st.info("No tool executions yet.")
    with runs_tab:
        rows = call(client().runs, 100) or []
        if rows:
            frame = pd.DataFrame(
                [
                    {
                        "time": when(r["created_at"]),
                        "message": r["message"][:80],
                        "intent": r["intent"],
                        "status": r["status"],
                        "latency_ms": r["latency_ms"],
                        "tokens": (r["input_tokens"] or 0) + (r["output_tokens"] or 0),
                        "token_source": r["token_source"],
                        "trace_id": r["trace_id"],
                    }
                    for r in rows
                ]
            )
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            st.info("No agent runs yet.")
    with catalogue_tab:
        for tool in call(client().tools) or []:
            flags = ["read-only" if tool["read_only"] else "changes state"]
            if tool["requires_approval"]:
                flags.append("approval required")
            st.markdown(
                f"**`{tool['name']}`** - {tool['description']}  \n"
                f"<span class='sao-muted'>{' · '.join(flags)} · roles: {', '.join(tool['allowed_roles'])}"
                f"</span>",
                unsafe_allow_html=True,
            )


def operations_page() -> None:
    st.header("Operations")
    health, ready = call(client().health), call(client().ready)
    cols = st.columns(4)
    cols[0].metric("API", (health or {}).get("status", "down").upper())
    checks = (ready or {}).get("checks", {})
    cols[1].metric("Database", checks.get("database", "unknown").upper())
    cols[2].metric("Vector store", checks.get("vector_store", "unknown").upper())
    cols[3].metric("LLM provider", (ready or {}).get("llm_provider", "?"))

    summary = call(client().summary)
    if not summary:
        return
    st.subheader("Agent performance" + (" (organisation)" if summary["scope"] == "tenant" else " (you)"))
    cols = st.columns(4)
    cols[0].metric("Runs", summary["runs_considered"])
    cols[1].metric("Latency p50", f"{summary['latency_ms_p50'] or 0:.0f} ms")
    cols[2].metric("Latency p95", f"{summary['latency_ms_p95'] or 0:.0f} ms")
    cols[3].metric("Pending approvals", summary["pending_approvals"])
    st.caption(summary["latency_note"])

    cols = st.columns(3)
    cols[0].metric("Tokens (measured)", summary["tokens_measured"])
    cols[1].metric("Tokens (estimated)", summary["tokens_estimated"])
    cost = summary["estimated_cost_usd"]
    cols[2].metric("Estimated cost", f"${cost:.4f}" if cost is not None else "n/a")
    st.caption(f"{summary['token_note']} {summary['cost_note']}")

    runs = call(client().runs, 100) or []
    if runs:
        chart = (
            pd.DataFrame(
                [
                    {"time": datetime.fromisoformat(r["created_at"]), "latency_ms": r["latency_ms"]}
                    for r in runs
                    if r["latency_ms"] is not None
                ]
            )
            .set_index("time")
            .sort_index()
        )
        left, right = st.columns(2)
        with left:
            st.markdown("**Run latency (ms)**")
            st.line_chart(chart, height=220)
        with right:
            st.markdown("**Tool calls by outcome**")
            if summary["tool_calls_by_status"]:
                st.bar_chart(pd.Series(summary["tool_calls_by_status"], name="calls"), height=220)

    st.subheader("Recent errors")
    if summary["recent_errors"]:
        st.dataframe(pd.DataFrame(summary["recent_errors"]), hide_index=True, width="stretch")
    else:
        st.success("No recent errors.")

    if is_admin():
        with st.expander("Security audit trail (admin)"):
            events = call(client().audit, 100) or []
            if events:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "time": when(e["created_at"]),
                                "action": e["action"],
                                "actor": (e["actor_id"] or "")[:8],
                                "target": (e["target"] or "")[:8],
                                "detail": json.dumps(e["detail"]),
                            }
                            for e in events
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
    st.caption(f"Raw Prometheus metrics: {API_URL}/metrics")


def admin_users_page() -> None:
    st.header("Users")
    st.caption(
        f"Members of **{principal()['tenant_name']}**. New users can only be added by an administrator."
    )
    users = call(client().users) or []
    if users:
        frame = pd.DataFrame(users)[["email", "role", "is_active", "created_at"]]
        frame["created_at"] = frame["created_at"].map(when)
        st.dataframe(frame, hide_index=True, width="stretch")
    with st.form("new-user", clear_on_submit=True):
        email = st.text_input("Email")
        password = st.text_input("Initial password", type="password")
        role = st.selectbox("Role", ["user", "admin"])
        if st.form_submit_button("Add user") and call(client().create_user, email, password, role):
            st.success(f"Added {email}")


# --- shell -----------------------------------------------------------------------
def main() -> None:
    if not st.session_state.get("token"):
        login_page()
        return
    me = principal()
    with st.sidebar:
        st.markdown("## 🛡️ SecureAgentOps")
        role_class = "sao-admin" if me["role"] == "admin" else "sao-user"
        st.markdown(
            f"<span class='sao-pill sao-tenant'>tenant: {me['tenant_name']}</span>"
            f"<span class='sao-pill {role_class}'>{me['role']}</span>",
            unsafe_allow_html=True,
        )
        st.caption(me["email"])
        ready = call(client().ready) or {}
        if ready.get("llm_provider") == "mock":
            st.markdown("<span class='sao-pill sao-mock'>MOCK LLM MODE</span>", unsafe_allow_html=True)
            st.caption("No paid model calls. Token counts are estimates.")
        pages = {
            "Chat": chat_page,
            "Knowledge base": documents_page,
            "Approvals": approvals_page,
            "Activity": activity_page,
            "Operations": operations_page,
        }
        if me["role"] == "admin":
            pages["Users"] = admin_users_page
        choice = st.radio("Navigate", list(pages), label_visibility="collapsed")
        st.divider()
        if st.button("Sign out", width="stretch"):
            st.session_state.clear()
            st.rerun()
    pages[choice]()


main()
