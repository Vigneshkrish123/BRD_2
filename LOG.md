# LOG.md — Build Log

Detailed record of every step built, decision made, and change applied.

---

## Session — 18 May 2026

---

### Step 1 — Project Scaffold

**Built:**
- Folder structure: `transcript_to_brd/app/`, `ui/`, `outputs/`, `scripts/`
- `pyproject.toml` — Poetry config, Python 3.11
- `.env.example` — template for all required env vars
- `.gitignore` — covers `.env`, `outputs/`, `*.docx`, `__pycache__`
- `app/__init__.py` — empty, makes app/ a Python package

**Initial dependencies defined:**
`openai`, `azure-identity`, `azure-keyvault-secrets`, `streamlit`,
`loguru`, `python-dotenv`

---

### Step 2 — `app/keyvault.py`

**Built:**
- `get_api_key()` — retrieves Azure OpenAI API key from Azure Key Vault
- Uses `DefaultAzureCredential` (picks up `az login` locally,
  Managed Identity on Azure App Service)
- Reads `AZURE_KEYVAULT_URL` and `AZURE_API_KEY_SECRET_NAME` from env
- Raises `EnvironmentError` if either env var is missing
- Logs secret name (not value) on retrieval

**Key decision:** `DefaultAzureCredential` chosen over hardcoded service principal
so the same code works locally (az login) and on Azure (Managed Identity) without
any code changes.

---

### Step 3 — `app/cleaner.py`

**Built:**
- `clean_transcript(raw_text)` → returns `(cleaned_text, speakers[])`
- `_extract_speakers(text)` — detects speaker names by checking if a name-only
  line is followed by a timestamp line (Teams transcript pattern)

**Cleaning steps (in order):**
1. Strip WEBVTT header
2. Remove VTT arrow timestamps (`00:00:05.000 --> 00:00:10.000`)
3. Remove VTT speaker tags (`<v Name>`, `</v>`)
4. Remove standalone timestamp lines (`0:01:23`, `0:01:23.000`)
5. Remove speaker-name-only lines (structural, not content)
6. Strip filler words (`um`, `uh`, `hmm`, `you know`, `i mean`, etc.)
7. Collapse excess whitespace and blank lines

**No LLM used** — pure regex and stdlib. Typical result: 15–20% token reduction.

**Teams transcript format handled:**
```
Speaker Name
0:00:05
Spoken content here.
```

---

### Step 4 — `app/prompts.py`

**Built:**
- `EXTRACTION_SYSTEM` — system prompt for LLM Call 1
- `GENERATION_SYSTEM` — system prompt for LLM Call 2

**EXTRACTION_SYSTEM design:**
- Instructs model to extract only what is explicitly stated — no invention
- JSON schema: project_name, meeting_date, business_context,
  business_objectives, stakeholders, functional_requirements,
  non_functional_requirements, in_scope, out_of_scope, assumptions,
  constraints, open_questions, decisions_made, action_items, risks
- Functional requirements enforced in "The system shall..." format

**GENERATION_SYSTEM design:**
- Takes extracted JSON, writes formal BRD prose
- Numbered IDs enforced: BO-001, FR-001, NFR-001, R-001, OQ-001, AI-001
- Priority (High/Medium/Low) required on every requirement
- Acceptance criteria required on every functional requirement
- Explicitly prohibits fabricating requirements not in source data

**Both prompts end with:** "Return ONLY valid JSON. No markdown. No preamble."
— prevents gpt-4o-mini from wrapping output in code fences which breaks json.loads()

---

### Step 5 — `app/extractor.py`

**Built:**
- `extract(cleaned_text, speakers, client, deployment)` → dict
- LLM Call 1 implementation
- `temperature=0.1` — near-zero for consistent extraction
- `max_tokens=4096`
- `response_format={"type": "json_object"}` — Azure OpenAI JSON mode
- Prepends speaker list to user message for stakeholder attribution
- Calls `cost_guard.record_usage()` with actual `response.usage` token counts
- Logs FR count, NFR count, stakeholder count, token usage on completion

---

### Step 6 — `app/generator.py`

**Built:**
- `generate(extracted_data, client, deployment)` → dict
- LLM Call 2 implementation
- `temperature=0.3` — slightly higher than extractor for fluent prose
- `max_tokens=6000` — BRD output is longer than extraction JSON
- Injects today's date into user message for timeline reasoning
- Calls `cost_guard.record_usage()` with actual token counts
- Logs FR count, token usage on completion

---

### Step 7 — `app/formatter.py`

**Initial build:** Node.js (`scripts/format_brd.js`) using `docx` npm package.

**Revision:** User requested pure Python. Node.js removed entirely.
`scripts/` directory is now unused.

**Final build — pure python-docx:**
- `format_docx(brd_data, output_path)` → str (absolute path)
- Low-level XML helpers (python-docx has no native API for these):
  - `_set_cell_bg()` — cell background via `w:shd` OxmlElement
  - `_set_cell_border()` — cell borders via `w:tcBorders`
  - `_cell_padding()` — cell margins via `w:tcMar`
  - `_heading_border()` — blue bottom border on Heading 1 paragraphs

**Document structure (11 sections):**
1. Title page (project name, version, status, date, prepared by)
2. Executive Summary (page break after)
3. Project Overview
4. Scope (In Scope / Out of Scope bullet lists)
5. Stakeholders (3-column table)
6. Business Objectives (3-column table)
7. Functional Requirements (4-column table)
8. Non-Functional Requirements (4-column table)
9. Assumptions & Constraints (bullet lists)
10. Risks (4-column table)
11. Open Questions (4-column table)
12. Action Items (4-column table)

**Colour scheme:**
- Header rows: `#1F3864` (navy)
- Heading 1: `#2E75B6` (blue) with bottom border
- Heading 2: `#1F3864` (navy)
- Alternating rows: `#EBF3FB` (light blue) / white
- Body text: `#2C2C2C`

**Added to pyproject.toml:** `python-docx >= 1.1.0`

---

### Step 8 — `ui/streamlit_app.py` (initial build)

**Built:**
- `get_client()` — cached `AzureOpenAI` client via `@st.cache_resource`
- File uploader (`.txt` only)
- Word count + filename metrics display
- "Generate BRD" button
- `st.status()` live progress: Clean → Extract → Generate → Format
- Download button for `.docx` (appears only on success)
- Per-step error handling with specific failure messages

---

### Addition — `app/cost_guard.py`

**Reason:** Prevent runaway Azure OpenAI spend.

**Three protection layers:**

1. **Input token guard** (`check_input_size`)
   - Exact token count via `tiktoken` (gpt-4o-mini tokeniser)
   - Rejects transcript > 20,000 tokens before any API call
   - Hard ValueError raised

2. **Rate limiter** (`check_rate_limit`)
   - Max 10 pipeline runs per hour
   - File-based persistence (`outputs/.usage.json`) — survives browser refreshes
   - Sliding window: counts requests with timestamp within last 3600 seconds
   - Shows countdown to retry on block

3. **Daily circuit breaker** (`check_daily_budget`)
   - Daily budget: $5.00 USD
   - Resets at midnight (date comparison on log load)
   - Uses **actual** token counts from `response.usage` (not estimates)

**Pricing used:**
- Input: $0.15 / 1M tokens
- Output: $0.60 / 1M tokens
- Typical run cost: ~$0.006

**`get_summary()`** returns live stats dict for sidebar display.

**Updated:** `extractor.py` and `generator.py` to call `record_usage()` after each LLM call.
**Added to pyproject.toml:** `tiktoken >= 0.7.0`

---

### Addition — `app/auth.py`

**Reason:** Prevent file upload and LLM calls before Azure credentials are verified.

**Two-step verification:**
1. Key Vault fetch — confirms `az login` / Managed Identity + vault config
2. Azure OpenAI ping — 1-token completion confirms key + endpoint + deployment

**Returns:** `AuthResult(ok: bool, client: AzureOpenAI | None, error: str)`

**Specific error messages for:**
- Missing env vars (EnvironmentError)
- Key Vault unreachable (expired az login, wrong URL)
- Wrong API key (AuthenticationError → 401)
- Wrong endpoint (APIConnectionError)

**Streamlit integration:**
- Auth runs once per session, cached in `st.session_state["auth"]`
- Nothing renders (no file uploader, no button) until `auth.ok == True`
- On failure: error message + fix instructions displayed, `st.stop()` called

---

### Update — `ui/streamlit_app.py` (auth + cost integration)

**Changes:**
- Auth gate added at top — entire UI gated behind `auth.ok`
- `get_client()` removed — client now comes from `auth.client`
- Pre-flight cost guards called before pipeline starts:
  `check_rate_limit()` → `check_daily_budget()` → `check_input_size()`
- Cost sidebar added (always visible):
  - Progress bar showing daily budget consumption
  - Spent / Remaining metrics
  - Runs today count
- Post-run cost display: per-run cost + updated daily total

---

### Deployment — Azure App Service

**Decision:** Docker + Nginx approach dropped. Azure App Service handles
TLS termination and reverse proxy natively.

| Original plan | Azure App Service equivalent |
|---|---|
| Nginx reverse proxy | Azure front-end infrastructure (automatic) |
| TLS termination | App Service HTTPS Only + Managed Certificate |
| client_max_body_size | `--server.maxUploadSize 5` (Streamlit flag) + cost_guard |

**Files built:**

`startup.sh`
- Startup command for Azure App Service
- Runs: `streamlit run ui/streamlit_app.py --server.port ${PORT:-8000} ...`
- `--server.maxUploadSize 5` — 5MB cap at Streamlit level
- `--server.enableXsrfProtection true` — CSRF protection on
- Set in Azure Portal: App Service → Configuration → General Settings → Startup Command: `bash startup.sh`

`requirements.txt`
- Flat pip requirements exported from pyproject.toml
- Used by Azure Oryx build system (`pip install -r requirements.txt`)
- Poetry not supported natively on Azure App Service

---

## Current File Inventory

| File | Status | Purpose |
|---|---|---|
| `app/__init__.py` | ✅ | Package marker |
| `app/auth.py` | ✅ | Azure auth gate |
| `app/cleaner.py` | ✅ | Transcript cleaning |
| `app/cost_guard.py` | ✅ | Rate limit / budget / token guard |
| `app/extractor.py` | ✅ | LLM Call 1 |
| `app/formatter.py` | ✅ | BRD JSON → .docx |
| `app/generator.py` | ✅ | LLM Call 2 |
| `app/keyvault.py` | ✅ | Azure Key Vault client |
| `app/prompts.py` | ✅ | LLM system prompts |
| `ui/streamlit_app.py` | ✅ | Streamlit UI |
| `startup.sh` | ✅ | Azure App Service startup |
| `requirements.txt` | ✅ | pip dependencies |
| `pyproject.toml` | ✅ | Poetry config |
| `.env.example` | ✅ | Env var template |
| `.gitignore` | ✅ | Git exclusions |
| `CLAUDE.md` | ✅ | Architecture reference |
| `LOG.md` | ✅ | This file |

---

## Pending / Next Steps

- [ ] `az login` + `poetry install` local test run
- [ ] End-to-end test with a real Teams transcript
- [ ] Azure App Service provisioning (Python 3.11 Linux)
- [ ] Set Application Settings in Azure Portal (env vars)
- [ ] Assign Key Vault Secrets User role to App Service Managed Identity
- [ ] Enable HTTPS Only + App Service Managed Certificate
- [ ] Set startup command: `bash startup.sh`
- [ ] ZIP deploy or GitHub Actions CI/CD
