"""Streamlit local product UI for KnowledgeWeaver backed by FastAPI /v1 endpoints."""

from __future__ import annotations

import os
import time
from typing import Any, cast
from uuid import uuid4

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
CHANNELS = ("hybrid", "bm25", "dense", "symbol", "entity", "graph")
SOURCE_KINDS = ("python_repo", "pdf", "docx", "markdown")
POLICY_PROFILES = ("fixed", "rule")


class ApiError(RuntimeError):
    """Raised when the local KnowledgeWeaver API returns an error response."""

    def __init__(self, status_code: int, code: str, message: str, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        detail = f"[{status_code} {code}] {message}"
        if request_id:
            detail += f" (request_id={request_id})"
        super().__init__(detail)


class KwApiClient:
    """Thin synchronous HTTP client for the KnowledgeWeaver FastAPI service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("KW_API_URL") or DEFAULT_API_URL).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, "CONNECTION_ERROR", f"Cannot reach KnowledgeWeaver API at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            err = payload.get("error") if isinstance(payload, dict) else None
            if not isinstance(err, dict) and isinstance(payload, dict) and "code" in payload and "message" in payload:
                err = payload
            if isinstance(err, dict):
                raise ApiError(
                    response.status_code,
                    str(err.get("code", "API_ERROR")),
                    str(err.get("message", response.text)),
                    err.get("request_id"),
                )
            raise ApiError(response.status_code, "HTTP_ERROR", response.text or f"HTTP {response.status_code}")
        return response.json()

    def _request_dict(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return cast(dict[str, Any], self._request(method, path, **kwargs))

    def _request_list(self, method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._request(method, path, **kwargs))

    def healthz(self) -> dict[str, Any]:
        return self._request_dict("GET", "/healthz")

    def readyz(self) -> dict[str, Any]:
        return self._request_dict("GET", "/readyz")

    def list_sources(self) -> list[dict[str, Any]]:
        return self._request_list("GET", "/v1/sources")

    def register_source(self, *, path: str, kind: str, display_name: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": path, "kind": kind}
        if display_name and display_name.strip():
            payload["display_name"] = display_name.strip()
        return self._request_dict("POST", "/v1/sources", json=payload)

    def submit_ingestion(self, *, source_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._request_dict(
            "POST",
            "/v1/ingestions",
            json={"source_id": source_id, "idempotency_key": idempotency_key or f"ui-ingest-{uuid4()}"},
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request_dict("GET", f"/v1/jobs/{job_id}")

    def delete_source(self, *, source_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._request_dict(
            "DELETE",
            f"/v1/sources/{source_id}",
            json={"idempotency_key": idempotency_key or f"ui-delete-{uuid4()}"},
        )

    def retrieve(
        self,
        *,
        query: str,
        channel: str = "hybrid",
        top_k: int = 10,
        source_ids: list[str] | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "channel": channel, "top_k": top_k}
        if source_ids:
            payload["source_ids"] = source_ids
        if generation_id:
            payload["generation_id"] = generation_id
        return self._request_dict("POST", "/v1/retrievals", json=payload)

    def submit_investigation(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        policy: str = "fixed",
        max_steps: int = 6,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "policy": policy,
            "max_steps": max_steps,
            "idempotency_key": idempotency_key or f"ui-inv-{uuid4()}",
        }
        if source_ids:
            payload["source_ids"] = source_ids
        return self._request_dict("POST", "/v1/investigations", json=payload)

    def get_investigation(self, run_id: str) -> dict[str, Any]:
        return self._request_dict("GET", f"/v1/investigations/{run_id}")

    def get_evidence(
        self,
        *,
        evidence_id: str,
        generation_id: str,
        corpus_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"generation_id": generation_id}
        if corpus_id:
            params["corpus_id"] = corpus_id
        return self._request_dict("GET", f"/v1/evidence/{evidence_id}", params=params)

    def get_graph(
        self,
        *,
        query: str,
        generation_id: str | None = None,
        source_ids: list[str] | None = None,
        max_hops: int = 2,
        max_edges: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "max_hops": max_hops, "max_edges": max_edges}
        if generation_id:
            params["generation_id"] = generation_id
        if source_ids:
            params["source_ids"] = ",".join(source_ids)
        return self._request_dict("GET", "/v1/graphs", params=params)

    def list_experiments(self) -> list[dict[str, Any]]:
        return self._request_list("GET", "/v1/experiments")


def build_graph_dot(paths: list[dict[str, Any]]) -> str | None:
    """Build a Graphviz DOT string from RetrievalResult.paths payloads."""
    edges: list[tuple[str, str, str]] = []
    for path in paths:
        steps = path.get("edges") or path.get("relations") or path.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            src = str(step.get("source_name") or step.get("source_entity_id") or step.get("subject") or "?")
            pred = str(step.get("predicate") or step.get("relation_type") or "related_to")
            dst = str(step.get("target_name") or step.get("target_entity_id") or step.get("object") or "?")
            edges.append((src, pred, dst))
    if not edges:
        return None
    lines = ["digraph KnowledgeGraph {", '  rankdir=LR;', '  node [shape=box, style=rounded];']
    for src, pred, dst in edges:
        s_esc = src.replace('"', '\\"')
        p_esc = pred.replace('"', '\\"')
        d_esc = dst.replace('"', '\\"')
        lines.append(f'  "{s_esc}" -> "{d_esc}" [label="{p_esc}"];')
    lines.append("}")
    return "\n".join(lines)


def _render_evidence_details(st: Any, client: KwApiClient, evidence_id: str, generation_id: str) -> None:
    try:
        ev = client.get_evidence(evidence_id=evidence_id, generation_id=generation_id)
    except ApiError as exc:
        st.error(str(exc))
        return
    st.markdown(f"**Evidence `{ev.get('evidence_id')}`** — Source: `{ev.get('source_name')}`")
    if ev.get("relative_path"):
        st.caption(f"Path: `{ev.get('relative_path')}`")
    st.code(ev.get("quote") or "", language="text")
    st.json(ev.get("locator") or {})


def main() -> None:
    """Render the Streamlit KnowledgeWeaver application."""
    import streamlit as st

    st.set_page_config(page_title="KnowledgeWeaver Local Agent", layout="wide")
    st.title("KnowledgeWeaver — Local Multi-Source Knowledge Agent")

    api_url = st.sidebar.text_input("FastAPI URL", value=os.environ.get("KW_API_URL", DEFAULT_API_URL))
    client = KwApiClient(base_url=api_url)

    with st.sidebar:
        st.subheader("System Health")
        if st.button("Refresh Health", key="refresh_health"):
            st.rerun()
        try:
            health = client.healthz()
            ready = client.readyz()
            st.success(f"API: {health.get('status', 'ok')} | Runtime: {ready.get('status', 'unknown')}")
            st.json(ready.get("services", {}))
            st.caption(f"Local Q&A ready: {ready.get('can_answer_with_local_model')}")
        except ApiError as exc:
            st.error(str(exc))

    try:
        sources = client.list_sources()
    except ApiError:
        sources = []

    source_options = {f"{s['display_name']} ({s['kind']}) [{s['id'][:8]}]": s["id"] for s in sources}

    tab_sources, tab_search, tab_investigate, tab_graph, tab_experiments = st.tabs(
        ["Sources & Ingestion", "Search", "Investigate", "Graph Explorer", "Experiments"]
    )

    with tab_sources:
        st.subheader("Register Knowledge Source")
        with st.form("register_source_form"):
            src_path = st.text_input("Local Path (inside KW_ALLOWED_ROOTS)")
            src_kind = st.selectbox("Source Kind", options=list(SOURCE_KINDS))
            src_name = st.text_input("Display Name (optional)")
            submitted = st.form_submit_button("Register Source")
            if submitted and src_path.strip():
                try:
                    created = client.register_source(
                        path=src_path.strip(),
                        kind=src_kind,
                        display_name=src_name or None,
                    )
                    st.success(f"Registered source `{created['id']}` ({created['display_name']})")
                    st.rerun()
                except ApiError as exc:
                    st.error(str(exc))

        st.subheader("Registered Sources")
        if not sources:
            st.info("No sources registered yet.")
        else:
            for src in sources:
                cols = st.columns([4, 2, 2, 2])
                cols[0].markdown(f"**{src['display_name']}** (`{src['kind']}`) — `{src['id']}`")
                cols[1].write(f"Indexed: `{src.get('indexed')}`")
                if cols[2].button("Ingest", key=f"ingest_{src['id']}"):
                    try:
                        job = client.submit_ingestion(source_id=src["id"])
                        st.session_state["last_job_id"] = job["job_id"]
                        st.success(f"Ingestion job `{job['job_id']}` submitted ({job['state']})")
                    except ApiError as exc:
                        st.error(str(exc))
                if cols[3].button("Delete", key=f"delete_{src['id']}"):
                    try:
                        job = client.delete_source(source_id=src["id"])
                        st.session_state["last_job_id"] = job["job_id"]
                        st.warning(f"Delete job `{job['job_id']}` submitted ({job['state']})")
                    except ApiError as exc:
                        st.error(str(exc))

        st.subheader("Job Status")
        job_id_input = st.text_input("Job ID", value=st.session_state.get("last_job_id", ""))
        if st.button("Check Job", key="check_job_btn") and job_id_input.strip():
            try:
                job_data = client.get_job(job_id_input.strip())
                st.json(job_data)
            except ApiError as exc:
                st.error(str(exc))

    with tab_search:
        st.subheader("Multi-Channel Retrieval")
        query_text = st.text_input("Search Query", key="search_query")
        col1, col2, col3 = st.columns([2, 1, 3])
        channel = col1.selectbox("Channel", options=list(CHANNELS), key="search_channel")
        top_k = col2.number_input("Top K", min_value=1, max_value=50, value=10, key="search_top_k")
        selected_labels = col3.multiselect("Filter Sources (optional)", options=list(source_options.keys()), key="search_sources")
        selected_ids = [source_options[lbl] for lbl in selected_labels]

        if st.button("Run Search", key="run_search_btn") and query_text.strip():
            try:
                res = client.retrieve(
                    query=query_text.strip(),
                    channel=channel,
                    top_k=int(top_k),
                    source_ids=selected_ids or None,
                )
                st.session_state["last_search_result"] = res
            except ApiError as exc:
                st.error(str(exc))

        search_res = st.session_state.get("last_search_result")
        if search_res:
            gen_id = search_res.get("generation_id")
            result_body = search_res.get("result", {})
            hits = result_body.get("hits", [])
            st.caption(f"Channel: `{search_res.get('channel')}` | Generation: `{gen_id}` | Hits: {len(hits)}")
            for idx, hit in enumerate(hits, start=1):
                with st.expander(f"#{idx} score={hit.get('score')} chunk={hit.get('chunk_id')}"):
                    st.write(hit.get("text") or hit.get("snippet") or "")
                    ev_ids = hit.get("evidence_ids") or ([hit["evidence_id"]] if hit.get("evidence_id") else [])
                    for ev_id in ev_ids:
                        if gen_id and st.button(f"Inspect Evidence {ev_id[:8]}", key=f"search_ev_{idx}_{ev_id}"):
                            _render_evidence_details(st, client, ev_id, gen_id)
                    st.json(hit)

    with tab_investigate:
        st.subheader("Multi-Source Investigation")
        inv_query = st.text_area("Investigation Question", key="inv_query")
        c1, c2, c3 = st.columns([2, 1, 3])
        policy = c1.selectbox("Policy Profile", options=list(POLICY_PROFILES), key="inv_policy")
        max_steps = c2.number_input("Max Steps", min_value=1, max_value=6, value=6, key="inv_steps")
        inv_labels = c3.multiselect("Filter Sources (optional)", options=list(source_options.keys()), key="inv_sources")
        inv_source_ids = [source_options[lbl] for lbl in inv_labels]

        if st.button("Start Investigation", key="start_inv_btn") and inv_query.strip():
            try:
                submitted_job = client.submit_investigation(
                    query=inv_query.strip(),
                    source_ids=inv_source_ids or None,
                    policy=policy,
                    max_steps=int(max_steps),
                )
                run_id = submitted_job["run_id"]
                st.session_state["last_run_id"] = run_id
                with st.spinner(f"Running investigation `{run_id}`..."):
                    for _ in range(30):
                        inv_state = client.get_investigation(run_id)
                        if inv_state.get("state") in {"succeeded", "failed"}:
                            st.session_state["last_investigation"] = inv_state
                            break
                        time.sleep(0.5)
                    else:
                        st.session_state["last_investigation"] = client.get_investigation(run_id)
            except ApiError as exc:
                st.error(str(exc))

        col_poll_a, col_poll_b = st.columns([3, 1])
        run_id_box = col_poll_a.text_input("Investigation Run ID", value=st.session_state.get("last_run_id", ""))
        if col_poll_b.button("Refresh Run", key="refresh_inv_btn") and run_id_box.strip():
            try:
                st.session_state["last_investigation"] = client.get_investigation(run_id_box.strip())
            except ApiError as exc:
                st.error(str(exc))

        inv_data = st.session_state.get("last_investigation")
        if inv_data:
            st.write(f"Run: `{inv_data.get('run_id')}` | State: `{inv_data.get('state')}` | Policy: `{inv_data.get('policy_profile')}`")
            if inv_data.get("training"):
                st.caption(f"Training note: {inv_data.get('training')}")
            answer = inv_data.get("answer")
            if isinstance(answer, dict):
                gen_id = (answer.get("context") or {}).get("generation_id")
                st.markdown(f"### Answer Status: `{answer.get('status')}`")
                claims = answer.get("claims", [])
                if not claims:
                    st.warning("No grounded claims returned.")
                for idx, claim in enumerate(claims, start=1):
                    st.markdown(f"**Claim {idx}:** {claim.get('text')}")
                    for ev_id in claim.get("evidence_ids", []):
                        if gen_id:
                            with st.expander(f"Evidence `{ev_id}`"):
                                _render_evidence_details(st, client, ev_id, gen_id)
                if answer.get("unanswered"):
                    st.info(f"Unanswered: {answer.get('unanswered')}")
            if inv_data.get("trace"):
                with st.expander("Execution Trace"):
                    st.json(inv_data["trace"])

    with tab_graph:
        st.subheader("Graph Explorer")
        g_query = st.text_input("Graph Query", key="graph_query")
        gc1, gc2, gc3 = st.columns([1, 1, 3])
        g_hops = gc1.number_input("Max Hops", min_value=1, max_value=5, value=2, key="graph_hops")
        g_edges = gc2.number_input("Max Edges", min_value=1, max_value=500, value=200, key="graph_edges")
        g_labels = gc3.multiselect("Filter Sources (optional)", options=list(source_options.keys()), key="graph_sources")
        g_source_ids = [source_options[lbl] for lbl in g_labels]

        if st.button("Query Graph", key="query_graph_btn") and g_query.strip():
            try:
                g_res = client.get_graph(
                    query=g_query.strip(),
                    source_ids=g_source_ids or None,
                    max_hops=int(g_hops),
                    max_edges=int(g_edges),
                )
                st.session_state["last_graph_result"] = g_res
            except ApiError as exc:
                st.error(str(exc))

        g_data = st.session_state.get("last_graph_result")
        if g_data:
            result_body = g_data.get("result", {})
            paths = result_body.get("paths", [])
            st.caption(f"Generation: `{g_data.get('generation_id')}` | Paths: {len(paths)}")
            dot = build_graph_dot(paths)
            if dot:
                st.graphviz_chart(dot)
            st.json(result_body)

    with tab_experiments:
        st.subheader("Internal Evaluation & Experiment Provenance")
        try:
            exps = client.list_experiments()
            for exp in exps:
                st.markdown(f"### {exp.get('evidence_set')} (`{exp.get('status')}`)")
                st.write(exp.get("summary"))
                st.caption(f"Provenance: `{exp.get('provenance')}` | SHA256: `{exp.get('provenance_sha256')}`")
                st.caption(f"Training Policy: {exp.get('training_policy')}")
                if exp.get("rows"):
                    st.json(exp["rows"])
                if exp.get("limitations"):
                    st.warning("\n".join(f"- {item}" for item in exp["limitations"]))
        except ApiError as exc:
            st.error(str(exc))


if __name__ == "__main__":
    main()
