"""Streamlit UI — 搜索问答 + 对话历史"""

import json, os, uuid
from datetime import datetime

import streamlit as st
import httpx

API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000/api/v1")


def api_call(method: str, path: str, **kwargs) -> dict | list | None:
    url = f"{API_BASE}{path}"
    try:
        if method == "GET":
            resp = httpx.get(url, params=kwargs.get("params"), timeout=30)
        elif method == "POST":
            resp = httpx.post(url, **kwargs, timeout=120)
        elif method == "DELETE":
            resp = httpx.delete(url, timeout=30)
        else:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        st.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except httpx.RequestError as e:
        st.error(f"API 连接失败: {e}")
        return None


# ── Session state ──

if "conversations" not in st.session_state:
    st.session_state.conversations = []  # [{id, query, answer, sources, tokens, timestamp}]

if "viewing_history_id" not in st.session_state:
    st.session_state.viewing_history_id = None


def _timestamp():
    return datetime.now().strftime("%H:%M:%S")


# ── Page config ──

st.set_page_config(page_title="企业知识库", layout="wide")
st.title("📚 企业知识库检索系统")

# ── Sidebar ──

prev_page = st.session_state.get("_page", "🔍 搜索问答")
st.sidebar.header("导航")
page = st.sidebar.radio("", ["🔍 搜索问答", "📄 文档管理", "💬 对话历史"],
                        index=["🔍 搜索问答", "📄 文档管理", "💬 对话历史"].index(prev_page))
st.session_state["_page"] = page

# ── Helper: render a single conversation block ──

def render_conversation(conv: dict, expanded_sources: bool = False):
    """Render a Q&A pair as chat messages"""
    with st.chat_message("user"):
        st.markdown(conv["query"])

    with st.chat_message("assistant"):
        answer = conv.get("answer", "")
        if answer:
            st.markdown(answer)
        else:
            st.caption("（仅检索结果，未启用 RAG 回答）")

        sources = conv.get("sources", [])
        tokens = conv.get("tokens", 0)

        meta_parts = []
        if sources:
            meta_parts.append(f"📎 {len(sources)} 个参考来源")
        if tokens:
            meta_parts.append(f"🪙 {tokens} tokens")
        if meta_parts:
            st.caption(" | ".join(meta_parts))

        if sources:
            with st.expander("📎 参考来源", expanded=expanded_sources):
                for s in sources:
                    if s is None:
                        continue
                    doc_name = s.get("doc_name", "unknown")
                    score = s.get("score", 0)
                    chunk = s.get("chunk", "")
                    st.markdown(f"**[{score:.3f}] {doc_name}**")
                    st.text(chunk[:300] + ("..." if len(chunk) > 300 else ""))
                    st.divider()


def render_search_results(conv: dict):
    """Render a search-only result"""
    with st.chat_message("user"):
        st.markdown(conv["query"])
    with st.chat_message("assistant"):
        st.caption("（以下为检索到的相关文档片段）")
        results = conv.get("results", [])
        if results:
            for r in results:
                if r is None:
                    continue
                st.markdown(f"**{r.get('doc_name', 'unknown')}** (score: {r['score']:.3f})")
                st.text(r.get("chunk", "")[:200])
                st.divider()


# ════════════════════════════════════════════════════
#  TAB 1: 搜索问答
# ════════════════════════════════════════════════════

if page == "🔍 搜索问答":

    # ── Sidebar settings ──
    with st.sidebar:
        st.divider()
        st.subheader("检索设置")
        top_k = st.slider("返回结果数", 3, 20, 10, key="search_topk")
        use_rag = st.checkbox("启用 RAG 回答", value=True, key="search_rag")
        stream = st.checkbox("流式输出", value=False, key="search_stream")

        if st.session_state.conversations:
            if st.button("🗑️ 清空对话"):
                st.session_state.conversations = []
                st.rerun()

    # ── Chat area ──
    chat_container = st.container()

    with chat_container:
        # Display existing conversations
        for conv in st.session_state.conversations:
            if conv is None:
                continue
            if conv.get("type") == "rag":
                render_conversation(conv)
            elif conv.get("type") == "search":
                render_search_results(conv)

    # ── Chat input ──
    if prompt := st.chat_input("输入你的问题..."):
        conv_id = str(uuid.uuid4())[:8]

        # Display user message immediately
        with st.chat_message("user"):
            st.markdown(prompt)

        # Process
        with st.chat_message("assistant"):
            with st.spinner("正在检索..." if not use_rag else "思考中..."):
                if use_rag:
                    resp = api_call("POST", "/query", json={
                        "query": prompt, "top_k": top_k, "stream": stream,
                    })
                    if resp:
                        answer = resp.get("answer", "")
                    else:
                        answer = "请求失败，请稍后重试。"

                    st.markdown(answer)

                    sources = resp.get("sources", []) if resp else []
                    tokens = resp.get("tokens_used", 0)

                    meta_parts = []
                    if sources:
                        meta_parts.append(f"📎 {len(sources)} 个参考来源")
                    if tokens:
                        meta_parts.append(f"🪙 {tokens} tokens")
                    if meta_parts:
                        st.caption(" | ".join(meta_parts))

                    if sources:
                        with st.expander("📎 参考来源"):
                            for s in sources:
                                if s is None:
                                    continue
                                doc_name = s.get("doc_name", "unknown")
                                score = s.get("score", 0)
                                chunk = s.get("chunk", "")
                                st.markdown(f"**[{score:.3f}] {doc_name}**")
                                st.text(chunk[:300] + ("..." if len(chunk) > 300 else ""))
                                st.divider()

                    st.session_state.conversations.append({
                        "id": conv_id,
                        "type": "rag",
                        "query": prompt,
                        "answer": answer,
                        "sources": sources,
                        "tokens": tokens,
                        "timestamp": _timestamp(),
                    })
                else:
                    resp = api_call("POST", "/search", json={
                        "query": prompt, "top_k": top_k,
                    })
                    results = resp.get("results", []) if resp else []
                    if results:
                        st.caption(f"检索结果 ({len(results)} 条)")
                        for r in results:
                            if r is None:
                                continue
                            st.markdown(f"**{r.get('doc_name', 'unknown')}** "
                                        f"(score: {r['score']:.3f})")
                            st.text(r.get("chunk", "")[:200])
                            st.divider()
                    else:
                        st.info("未检索到相关内容")

                    st.session_state.conversations.append({
                        "id": conv_id,
                        "type": "search",
                        "query": prompt,
                        "results": results,
                        "timestamp": _timestamp(),
                    })

        st.rerun()


# ════════════════════════════════════════════════════
#  TAB 2: 文档管理
# ════════════════════════════════════════════════════

elif page == "📄 文档管理":
    st.subheader("上传文档")

    uploaded_file = st.file_uploader(
        "选择文件", type=["pdf", "docx", "doc", "pptx", "xlsx", "md", "txt", "html"]
    )
    tags_input = st.text_input("标签（英文逗号分隔）", "")
    if uploaded_file and st.button("上传"):
        tags = [t.strip() for t in tags_input.split(",") if t.strip()]
        files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
        data = {"tags": json.dumps(tags, ensure_ascii=False)}
        try:
            resp = httpx.post(f"{API_BASE}/documents/upload", files=files, data=data, timeout=120)
            if resp.status_code == 200:
                r = resp.json()
                st.success(f"上传成功! 文档 ID: {r['id'][:8]}... 状态: {r['status']}")
            else:
                st.error(f"上传失败: {resp.text[:200]}")
        except Exception as e:
            st.error(f"上传出错: {e}")

    st.divider()
    st.subheader("已上传文档")

    status_filter = st.selectbox("筛选状态", ["全部", "completed", "pending", "parsing",
                                              "chunking", "embedding", "storing", "failed"],
                                 key="doc_status_filter")
    docs = api_call("GET", "/documents",
                    params={"status": status_filter if status_filter != "全部" else ""})

    if docs:
        for d in docs:
            if d is None:
                continue
            col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
            with col1:
                st.markdown(f"**{d['filename']}**")
                st.caption(f"ID: {d['id'][:8]}... | {d.get('filetype', '?')} | "
                           f"{d.get('size', 0)//1024}KB | 块数: {d.get('chunk_count', 0)}")
            with col2:
                status = d.get("status", "unknown")
                color = {"completed": "green", "failed": "red", "pending": "orange",
                         "parsing": "blue", "embedding": "purple"}.get(status, "gray")
                st.markdown(f":{color}[**{status}**]")
            with col3:
                tags = d.get("tags", [])
                if isinstance(tags, str):
                    tags = json.loads(tags)
                st.text(", ".join(tags[:3]) if tags else "-")
            with col4:
                if st.button("删除", key=f"del_{d['id']}"):
                    api_call("DELETE", f"/documents/{d['id']}")
                    st.rerun()
            st.divider()
    else:
        st.info("暂无文档，请先上传")


# ════════════════════════════════════════════════════
#  TAB 3: 对话历史
# ════════════════════════════════════════════════════

elif page == "💬 对话历史":
    st.subheader("💬 对话历史")

    conversations = st.session_state.conversations

    if not conversations:
        st.info("暂无对话记录。前往「🔍 搜索问答」开始提问吧。")
    else:
        # Stats
        rag_count = sum(1 for c in conversations if c and c.get("type") == "rag")
        search_count = sum(1 for c in conversations if c and c.get("type") == "search")
        st.caption(f"共 {len(conversations)} 条记录（{rag_count} 次问答, {search_count} 次检索）")

        st.divider()

        # Display in reverse chronological order
        for idx, conv in enumerate(reversed(conversations)):
            if conv is None:
                continue
            conv_id = conv.get("id", f"conv_{idx}")
            q_preview = conv["query"][:60] + ("..." if len(conv["query"]) > 60 else "")
            ts = conv.get("timestamp", "")

            # Expandable conversation card
            with st.expander(f"**Q:** {q_preview}　⏱ {ts}"):
                col_left, col_right = st.columns([6, 1])

                with col_left:
                    if conv.get("type") == "rag":
                        render_conversation(conv, expanded_sources=True)
                    elif conv.get("type") == "search":
                        render_search_results(conv)

                with col_right:
                    # Delete single conversation
                    if st.button("🗑️ 删除", key=f"history_del_{conv_id}"):
                        real_idx = len(conversations) - 1 - idx
                        st.session_state.conversations.pop(real_idx)
                        st.rerun()

            st.divider()

        # Clear all
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            if st.button("🗑️ 清除全部对话记录", type="secondary", use_container_width=True):
                st.session_state.conversations = []
                st.rerun()
