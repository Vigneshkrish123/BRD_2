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
from app.file_parser import extract_text

# ── Upload size caps ──────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES  = 20 * 1024 * 1024
_MAX_DECODED_CHARS = 10 * 1024 * 1024
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
    st.caption(f"Limit: 100k tokens/input")

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

st.subheader("Step 1 — Upload Transcript")
st.caption("Upload your Teams meeting transcript (.txt). Multiple files are combined.")

uploaded_files = st.file_uploader(
    "Meeting Transcript(s)",
    type=["txt", "pdf", "docx", "pptx", "xlsx"],
    accept_multiple_files=True,
    help="Teams transcripts (.txt) or any supporting meeting documents.",
)

st.subheader("Step 2 — Application SOP (Optional)")
st.caption(
    "Upload the application SOP or context document (.docx). "
    "This gives the AI knowledge of your existing system, module names, and terminology — "
    "resulting in more accurate use cases and scope."
)

sop_file = st.file_uploader(
    "Application SOP / Context Document",
    type=["docx"],
    accept_multiple_files=False,
    help="A Word document describing the application — SOP, functional spec, or system overview.",
)

if not uploaded_files:
    st.info("Waiting for at least one transcript file.")
    st.stop()

# ── Per-file extraction ───────────────────────────────────────────────────────
# .txt files → Teams transcript cleaner (strips timestamps, speaker lines, fillers)
# Other types → file_parser text extraction (pure Python, no Office required)
# Both paths produce plain text that feeds the same LLM pipeline.

transcript_parts: list[str] = []   # cleaned text segments
all_speakers:     list[str] = []   # speakers detected from .txt files only

for f in uploaded_files:
    raw_bytes = f.getvalue()
    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""

    if len(raw_bytes) > _MAX_UPLOAD_BYTES:
        st.error(
            f"**{f.name}** is too large: {len(raw_bytes) / 1024 / 1024:.1f} MB — "
            f"maximum is {_MAX_UPLOAD_BYTES // 1024 // 1024} MB per file."
        )
        st.stop()

    if ext == "txt":
        # Teams transcript path — decode + size guard + cleaner
        raw_text_file = raw_bytes.decode("utf-8", errors="ignore")

        if len(raw_text_file) > _MAX_DECODED_CHARS:
            st.error(
                f"**{f.name}** decoded content too large ({len(raw_text_file):,} chars). "
                f"Maximum is {_MAX_DECODED_CHARS:,} characters per file."
            )
            st.stop()

        _expand = len(raw_text_file) / max(len(raw_bytes), 1)
        if _expand > _MAX_EXPAND_RATIO:
            st.error(
                f"**{f.name}** expansion ratio {_expand:.1f}× exceeds limit ({_MAX_EXPAND_RATIO}×). "
                "Possible compressed or binary content."
            )
            st.stop()

        transcript_parts.append(raw_text_file)

    else:
        # Document path — pure-Python extraction, no Teams cleaner
        try:
            doc_text = extract_text(f.name, raw_bytes)
        except Exception as e:
            st.error(f"Could not extract text from **{f.name}**: {e}")
            st.stop()

        if not doc_text.strip():
            st.warning(
                f"**{f.name}** yielded no extractable text. "
                "If it is a scanned PDF, text extraction is not supported."
            )
        else:
            transcript_parts.append(doc_text)

# Combine all text with clear per-file separators
raw_text = "\n\n".join(transcript_parts)

word_count = len(raw_text.split())
file_names = ", ".join(f.name for f in uploaded_files)

col1, col2, col3 = st.columns(3)
col1.metric("Files",  len(uploaded_files))
col2.metric("Words", f"{word_count:,}")
col3.metric("File(s)", file_names if len(uploaded_files) == 1 else f"{len(uploaded_files)} files")

if word_count < 300:
    st.warning("Transcript looks short — make sure you uploaded the full file(s).")

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

        # Clean .txt (Teams transcript) content; other formats already extracted as plain text
        txt_files  = [f for f in uploaded_files if f.name.lower().endswith(".txt")]
        other_files = [f for f in uploaded_files if not f.name.lower().endswith(".txt")]

        st.write("🧹 Cleaning transcript(s)...")
        cleaned_parts: list[str] = []
        all_speakers:  list[str] = []

        for f in txt_files:
            raw_txt = f.getvalue().decode("utf-8", errors="ignore")
            try:
                cleaned_txt, spk = clean_transcript(raw_txt)
                cleaned_parts.append(cleaned_txt)
                all_speakers.extend(spk)
            except Exception as e:
                st.error(f"Cleaner failed on {f.name}: {e}")
                st.stop()

        # Non-txt files: text already extracted above — just include as-is
        for f in other_files:
            try:
                doc_text = extract_text(f.name, f.getvalue())
                if doc_text.strip():
                    cleaned_parts.append(doc_text)
            except Exception:
                pass  # already warned during extraction above

        cleaned      = "\n\n".join(cleaned_parts)
        all_speakers = sorted(set(all_speakers))

        st.write(
            f"✅ Cleaned — **{len(cleaned.split()):,}** words | "
            f"Speakers: **{', '.join(all_speakers) if all_speakers else 'not detected'}**"
        )

        # Extract SOP text if provided
        sop_text = ""
        if sop_file is not None:
            try:
                sop_text = extract_text(sop_file.name, sop_file.getvalue())
                st.write(f"📋 SOP loaded — **{len(sop_text.split()):,}** words from {sop_file.name}")
            except Exception as e:
                st.warning(f"Could not read SOP file ({e}) — continuing without it.")

        st.write("🔍 Extracting use cases and requirements...")
        try:
            extracted = extract(cleaned, all_speakers, auth.client, deployment, sop_text=sop_text)
        except Exception as e:
            st.error(f"Extractor failed: {e}")
            st.stop()
        st.write(
            f"✅ Extracted — **{len(extracted.get('use_cases', []))}** use cases | "
            f"**{len(extracted.get('scope_modules', []))}** scope modules | "
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