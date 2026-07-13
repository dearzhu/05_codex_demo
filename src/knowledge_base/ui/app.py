"""Streamlit UI"""

import json
import os
import sys
from pathlib import Path

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
        st.error(f"API 连接失败: {e}. 请确认后端服务已启动 (uvicorn src.knowledge_base.main:app)")
        return None


st.set_page_config(page_title="企业知识库", layout="wide")
st.title("📚 企业知识库检索系统")

# ── Sidebar ──
st.sidebar.header("导航")
page = st.sidebar.radio("", ["🔍 搜索问答", "📄 文档管理", "💬 对话历史"])

# ── Tab: Search ──
if page == "🔍 搜索问答":
    col1, col2 = st.columns([3, 1])
    with col2:
        st.subheader("检索设置")
        top_k = st.slider("返回结果数", 3, 20, 10)
        use_rag = st.checkbox("启用 RAG 回答", value=True)
        stream = st.checkbox("流式输出", value=False)

    with col1:
        query = st.text_input("", placeholder="输入你的问题...", label_visibility="collapsed")

        if query:
            with st.spinner("正在检索..."):
                if use_rag:
                    resp = api_call("POST", "/query", json={
                        "query": query, "top_k": top_k, "stream": stream,
                    })
                    if resp:
                        st.markdown("### 💡 回答")
                        st.write(resp.get("answer", ""))
                        st.caption(f"Token 用量: {resp.get('tokens_used', 0)} | "
                                   f"来源数: {len(resp.get('sources', []))}")
                        if resp.get("sources"):
                            st.markdown("### 📎 参考来源")
                            for s in resp["sources"]:
                                with st.expander(f"[{s['score']:.3f}] {s.get('doc_name', 'unknown')}"):
                                    st.text(s.get("chunk", ""))
                else:
                    resp = api_call("POST", "/search", json={
                        "query": query, "top_k": top_k,
                    })
                    if resp and resp.get("results"):
                        st.markdown(f"### 搜索结果 ({len(resp['results'])} 条)")
                        for r in resp["results"]:
                            st.markdown(f"**{r.get('doc_name', 'unknown')}** "
                                        f"(score: {r['score']:.3f})")
                            st.text(r.get("chunk", "")[:300])
                            st.divider()

# ── Tab: Documents ──
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
                                              "chunking", "embedding", "storing", "failed"])
    docs = api_call("GET", "/documents",
                    params={"status": status_filter if status_filter != "全部" else ""})

    if docs:
        for d in docs:
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

# ── Tab: Conversation History ──
elif page == "💬 对话历史":
    st.subheader("对话历史")
    st.info("对话历史功能将在后续版本中实现。当前可通过 /api/v1/conversation 端点查询。")
