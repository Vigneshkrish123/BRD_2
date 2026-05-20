import os
import re
import sys
import tempfile
from pathlib import Path

import defusedxml
defusedxml.defuse_stdlib()          # Patches xml.etree, xml.dom, xml.sax globally.
                                    # Must run before any XML-capable import (python-docx, etc.)

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from app.auth import verify
from app.cleaner import clean_transcript
from app.extractor import extract
from app.generator import generate
from app.formatter import format_docx
from app.cost_guard import (
    check_input_size,
    check_rate_limit,
    check_daily_budget,
    get_summary,
)

# ── Upload size caps (zip-bomb + DoS guards) ─────────────────────────────────
# These fire before any processing — including before the LLM token guard in
# cost_guard.py — so they protect against unauthenticated-path abuse too.

_MAX_UPLOAD_BYTES  = 5 * 1024 * 1024   # 5 MB raw — matches --server.maxUploadSize
_MAX_DECODED_CHARS = 2 * 1024 * 1024   # 2 M chars decoded (well above any real transcript)
_MAX_EXPAND_RATIO  = 10                 # decoded / raw ratio; >10× means binary/compressed

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transcript → BRD",
    page_icon="📄",
    layout="centered",
)

# ── Sidebar — cost dashboard ──────────────────────────────────────────────────

with st.sidebar:
    st.header("💰 Usage Today")
    summary = get_summary()
    budget_pct = min(summary["spend_today_usd"] / summary["budget_usd"], 1.0)
    st.progress(budget_pct)
    col1, col2 = st.columns(2)
    col1.metric("Spent",     f"${summary['spend_today_usd']:.4f}")
    col2.metric("Remaining", f"${summary['remaining_usd']:.4f}")
    st.caption(
        f"Daily budget: ${summary['budget_usd']:.2f}  |  "
        f"Runs today: {summary['runs_today']}"
    )
    st.divider()
    st.caption("Limits: 10 runs/hour · 20k tokens/input · $5.00/day")

# ── Main ──────────────────────────────────────────────────────────────────────

st.title("📄 Transcript to BRD")
st.divider()

# ── Authentication gate ───────────────────────────────────────────────────────
# Auth runs once per session and is cached in session_state.
# Nothing below this block is visible until auth passes.

if "auth" not in st.session_state:
    with st.status("🔐 Authenticating with Azure...", expanded=True) as auth_status:
        st.write("Connecting to Key Vault...")
        result = verify(
            endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
        )
        if result.ok:
            st.write("✅ Key Vault — connected")
            st.write("✅ Azure OpenAI — connected")
            auth_status.update(label="🔐 Authenticated", state="complete", expanded=False)
        else:
            st.write("❌ Authentication failed")
            auth_status.update(label="❌ Authentication failed", state="error", expanded=True)

    st.session_state["auth"] = result

auth = st.session_state["auth"]

if not auth.ok:
    st.error(auth.error)
    st.caption(
        "Fix the issue above, then refresh the page to retry. "
        "Most common causes: `az login` session expired, wrong Key Vault URL, "
        "wrong secret name, or wrong endpoint in `.env`."
    )
    st.stop()

# Auth passed — show authenticated badge and continue
st.success("🔐 Authenticated with Azure")

# ── File upload ───────────────────────────────────────────────────────────────

st.caption("Upload a Teams meeting transcript (.txt) and download a structured BRD (.docx).")

uploaded_file = st.file_uploader(
    "Upload transcript",
    type=["txt"],
    help="Teams → ... → Download > Transcript (.txt)",
)

if not uploaded_file:
    st.info("Waiting for a transcript file.")
    st.stop()

# ── Size guards (must run before any content processing) ─────────────────────
raw_bytes = uploaded_file.getvalue()   # reads once into memory; no re-read needed

if len(raw_bytes) > _MAX_UPLOAD_BYTES:
    st.error(
        f"File too large: {len(raw_bytes) / 1024 / 1024:.1f} MB — "
        f"maximum is {_MAX_UPLOAD_BYTES // 1024 // 1024} MB."
    )
    st.stop()

raw_text = raw_bytes.decode("utf-8", errors="ignore")

if len(raw_text) > _MAX_DECODED_CHARS:
    st.error(
        f"Decoded content too large ({len(raw_text):,} chars). "
        f"Maximum is {_MAX_DECODED_CHARS:,} characters."
    )
    st.stop()

_expand = len(raw_text) / max(len(raw_bytes), 1)
if _expand > _MAX_EXPAND_RATIO:
    st.error(
        f"File expansion ratio {_expand:.1f}× exceeds limit ({_MAX_EXPAND_RATIO}×). "
        "Possible compressed or binary content masquerading as plain text."
    )
    st.stop()

word_count = len(raw_text.split())

col1, col2 = st.columns(2)
col1.metric("Words", f"{word_count:,}")
col2.metric("File",  uploaded_file.name)

if word_count < 300:
    st.warning("Transcript looks short — make sure you uploaded the full file.")

st.divider()

# ── Generate ──────────────────────────────────────────────────────────────────

if st.button("🚀 Generate BRD", type="primary", use_container_width=True):

    # Pre-flight cost guards
    try:
        check_rate_limit()
        check_daily_budget()
        token_count = check_input_size(raw_text)
    except (ValueError, RuntimeError) as e:
        st.error(str(e))
        st.stop()

    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    docx_bytes = None

    with st.status("Running pipeline...", expanded=True) as status:

        st.write(f"🔒 Guards passed — {token_count:,} tokens")

        st.write("🧹 Cleaning transcript...")
        try:
            cleaned, speakers = clean_transcript(raw_text)
        except Exception as e:
            st.error(f"Cleaner failed: {e}")
            st.stop()
        st.write(
            f"✅ Cleaned — **{len(cleaned.split()):,}** words | "
            f"Speakers: **{', '.join(speakers) if speakers else 'not detected'}**"
        )

        st.write("🔍 Extracting requirements...")
        try:
            extracted = extract(cleaned, speakers, auth.client, deployment)
        except Exception as e:
            st.error(f"Extractor failed: {e}")
            st.stop()
        st.write(
            f"✅ Extracted — **{len(extracted.get('functional_requirements', []))}** FRs | "
            f"**{len(extracted.get('non_functional_requirements', []))}** NFRs | "
            f"**{len(extracted.get('stakeholders', []))}** stakeholders"
        )

        st.write("📝 Generating BRD...")
        try:
            brd = generate(extracted, auth.client, deployment)
        except Exception as e:
            st.error(f"Generator failed: {e}")
            st.stop()
        st.write("✅ BRD generated")

        st.write("📄 Formatting .docx...")
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                tmp_path = tmp.name
            format_docx(brd, tmp_path)
            with open(tmp_path, "rb") as f:
                docx_bytes = f.read()
            os.unlink(tmp_path)
        except Exception as e:
            st.error(f"Formatter failed: {e}")
            st.stop()
        st.write("✅ Document ready")

        status.update(label="✅ Done!", state="complete", expanded=False)

    updated     = get_summary()
    run_cost    = updated["spend_today_usd"] - summary["spend_today_usd"]
    st.info(
        f"💰 This run cost approx. **${run_cost:.4f}** | "
        f"Daily total: **${updated['spend_today_usd']:.4f}** / ${updated['budget_usd']:.2f}"
    )

    project  = brd.get("document_info", {}).get("project_name", "BRD")
    # Sanitize: strip everything except word chars, spaces, hyphens.
    # LLM output goes directly here — must not allow path traversal or shell chars.
    safe_project = re.sub(r"[^\w\s\-]", "", project).strip() or "BRD"
    filename = f"{safe_project.replace(' ', '_')}_BRD.docx"

    st.download_button(
        label="⬇️ Download BRD (.docx)",
        data=docx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )
