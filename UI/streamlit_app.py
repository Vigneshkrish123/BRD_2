import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import defusedxml
defusedxml.defuse_stdlib()

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from app.auth import verify
from app.cleaner import clean_transcript
from app.extractor import extract
from app.generator import generate
from app.formatter import format_docx
from app.cost_guard import check_input_size

# ── Upload size caps ──────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES  = 5 * 1024 * 1024
_MAX_DECODED_CHARS = 2 * 1024 * 1024
_MAX_EXPAND_RATIO  = 10

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transcript → BRD",
    page_icon="📄",
    layout="centered",
)

# ── Username resolution ───────────────────────────────────────────────────────
# Priority: Easy Auth header (App Service) → az CLI (local) → session fallback

def _get_username() -> str:
    # 1. Easy Auth (Azure App Service with Entra ID login wall)
    try:
        name = st.context.headers.get("X-MS-CLIENT-PRINCIPAL-NAME", "")
        if name:
            return name
    except Exception:
        pass

    # 2. az CLI session (local development)
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "user.name", "-o", "tsv"],
            capture_output=True, text=True, timeout=5
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception:
        pass

    # 3. Session fallback — unique per browser tab
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid.uuid4())[:8]
    return f"User-{st.session_state['session_id']}"


if "username" not in st.session_state:
    st.session_state["username"] = _get_username()

username = st.session_state["username"]

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("📄 BRD Agent")
    st.divider()
    st.caption(f"👤 {username}")
    st.caption(f"Limit: 20k tokens/input")

# ── Main ──────────────────────────────────────────────────────────────────────

st.title("📄 Transcript to BRD")
st.divider()

# ── Authentication gate ───────────────────────────────────────────────────────

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

# ── Size guards ───────────────────────────────────────────────────────────────

raw_bytes = uploaded_file.getvalue()

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
        "Possible compressed or binary content."
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

    try:
        token_count = check_input_size(raw_text)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    docx_bytes = None

    with st.status("Running pipeline...", expanded=True) as status:

        st.write(f"🔒 Input ok — {token_count:,} tokens")

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
            docx_bytes = format_docx(brd)
        except Exception as e:
            st.error(f"Formatter failed: {e}")
            st.stop()
        st.write("✅ Document ready")

        status.update(label="✅ Done!", state="complete", expanded=False)

    project      = brd.get("document_info", {}).get("project_name", "BRD")
    safe_project = re.sub(r"[^\w\s\-]", "", project).strip() or "BRD"
    filename     = f"{safe_project.replace(' ', '_')}_BRD.docx"

    st.download_button(
        label="⬇️ Download BRD (.docx)",
        data=docx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )