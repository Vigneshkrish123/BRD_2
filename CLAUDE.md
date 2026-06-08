# CLAUDE.md — Architecture Reference

This file exists so Claude (or any developer) can understand the full architecture
of this project without reading every file. Keep it updated when modules change.

---

## What This Project Does

Converts a Microsoft Teams meeting transcript (`.txt`, 10k–15k words) into a
structured Business Requirements Document (`.docx`) using a two-call LLM pipeline.

**Input:** Teams transcript `.txt` file
**Output:** Styled BRD `.docx` — Polycab house style with title page, Introduction,
Scope tables, AS IS / TO BE flows, Use Cases (actor-driven), NFR, Risks, Open Questions

---

## Tech Stack

| Layer         | Technology                          |
|---------------|-------------------------------------|
| Language      | Python 3.11                         |
| UI            | Streamlit                           |
| LLM           | Azure AI Foundry (gpt-4o-mini)      |
| Auth          | Azure Key Vault + DefaultAzureCredential |
| Secrets       | Azure Key Vault (no hardcoded keys) |
| Doc generation| python-docx                         |
| Token counting| tiktoken                            |
| Logging       | loguru                              |
| Deps          | Poetry (dev) / requirements.txt (Azure deploy) |
| Deployment    | Azure App Service (Linux, Python 3.11) |

---

## Project Structure

```
transcript_to_brd/
├── app/
│   ├── __init__.py       Empty — makes app/ a Python package
│   ├── auth.py           Azure auth gate (Key Vault + OpenAI ping)
│   ├── cleaner.py        Teams transcript cleaning (rule-based, no LLM)
│   ├── cost_guard.py     Token guard only (rate limit / budget removed)
│   ├── extractor.py      LLM Call 1 — structured JSON extraction
│   ├── formatter.py      BRD JSON → styled .docx via python-docx
│   ├── generator.py      LLM Call 2 — formal BRD prose generation
│   ├── keyvault.py       Azure Key Vault secret retrieval
│   ├── prompts.py        All LLM system prompts (extraction + generation)
│   └── sanitizer.py      Injection filtering + Pydantic output validation
├── ui/
│   ├── auth.py           ⚠️  STALE DUPLICATE — same content as app/auth.py,
│   │                         4KB, dated 5/18/2026. streamlit_app.py imports
│   │                         from app.auth, not ui.auth. Safe to delete.
│   └── streamlit_app.py  Streamlit frontend — upload, pipeline, download
├── outputs/              Generated .docx files (gitignored)
├── .env                  Local environment variables (gitignored)
├── .env.example          Template for .env
├── .gitignore
├── pyproject.toml        Poetry dependency definition
├── requirements.txt      Flat deps for Azure App Service pip install
└── startup.sh            Azure App Service startup command
```

---

## Pipeline Flow

```
User uploads .txt
        │
        ▼
[ auth.py ]
  DefaultAzureCredential → Key Vault → API key
  AzureOpenAI ping (1 token) — confirms key + endpoint + deployment
  Cached in st.session_state — runs once per session
        │
        ▼ (auth passed)
[ cost_guard.py ] — pre-flight check
  check_input_size()      → reject if transcript > 20,000 tokens
  (rate limiter and daily budget removed — Azure TPM/RPM quotas handle cost protection)
        │
        ▼
[ cleaner.py ]
  Strips: WEBVTT headers, timestamps (H:MM:SS), speaker-name-only lines,
          VTT tags, filler words (um, uh, you know...)
  Returns: (cleaned_text, speakers[])
  No LLM — pure regex, stdlib only
        │
        ▼
[ extractor.py ] — LLM Call 1
  Model: gpt-4o-mini
  Temperature: 0.1 (precision extraction)
  max_tokens: 4096
  response_format: json_object
  Input: sanitized transcript (injection-filtered) + speaker list
  Output: structured JSON (requirements, stakeholders, scope, risks, etc.)
  Retry: exponential backoff on 429 / 5xx (up to 5 attempts, 10s–120s)
        │
        ▼
[ generator.py ] — LLM Call 2
  Model: gpt-4o-mini
  Temperature: 0.1
  max_tokens: 8000
  response_format: json_object
  Input: re-serialized validated extracted JSON + today's date
  Output: full BRD JSON (use-case-driven, Polycab house style)
  Retry: exponential backoff on 429 / 5xx (up to 5 attempts, 10s–120s)
        │
        ▼
[ formatter.py ]
  Converts BRD JSON → styled .docx using python-docx
  Title page, Introduction, Scope tables, AS IS flow, TO BE flow,
  Use Cases (full actor-driven blocks), NFR, Risks, Open Questions
  Pure Python — no Node.js, no subprocess
        │
        ▼
  User downloads .docx
```

---

## Module Details

### `keyvault.py`
- Reads `AZURE_KEYVAULT_URL` and `AZURE_API_KEY_SECRET_NAME` from env
- Uses `DefaultAzureCredential` — picks up `az login` session locally,
  Managed Identity on Azure App Service
- Never logs or prints the key value

### `auth.py`
- Two-step check: Key Vault reachable → OpenAI ping
- Returns `AuthResult(ok, client, error)`
- Cached in `st.session_state["auth"]` — does not re-run on Streamlit rerenders
- Specific error messages for: env misconfiguration, vault unreachable,
  wrong API key (401), wrong endpoint (connection error)
- File uploader and all app UI are hidden until auth passes

### `cleaner.py`
- Handles Teams transcript format: `Name\nH:MM:SS\nContent`
- Speaker detection: name-only line followed by timestamp line
- Returns `(cleaned_text: str, speakers: list[str])`
- Word reduction: typically 15–20% token reduction from noise removal

### `prompts.py`
**v2 — use-case-driven (current)**

- `EXTRACTION_SYSTEM`: extracts structured data including `domain_glossary` (every
  acronym/product/system named), `as_is_flow`, `to_be_flow` (with sub-steps and
  worked examples), `in_scope` (module/feature/description objects), and `use_cases`
  (actor, goal, key steps, business rules, exceptions).
  Key rule: **domain fidelity first** — exact terminology, product names, formulas,
  and numbers preserved verbatim as spoken; no paraphrasing technical content.
- `GENERATION_SYSTEM`: expands extracted data into the Polycab BRD format.
  Use Cases are the core — each expressed as `"As a <role>, I want <action> so that <benefit>."`.
  TO BE flow is hierarchical (steps → sub-steps → worked examples with exact numbers).
  Key rules: domain specificity over formality; never genericise to "the system" when
  a specific subsystem is named; every formula/threshold preserved verbatim.
  Produces: `introduction`, `business_objectives` (id/title/description), `stakeholders`,
  `scope` (in/out structured objects), `assumptions` (with impact_if_changed),
  `as_is_business_flow`, `to_be_business_process_flow`, `use_cases`, `non_functional_requirements`,
  `risks`, `open_questions`.
- **Note:** Aggressive security/anti-injection language removed from both prompts.
  Azure content filter was triggering false positives. Structural protection via
  `response_format: json_object` is sufficient.

### `sanitizer.py`
Two responsibilities: injection filtering before LLM calls, and schema enforcement on LLM output.

**Input sanitization — `sanitize_transcript(text)`**
- Scans transcript for 12 injection/jailbreak patterns across four categories:
  - Direct instruction overrides: "ignore previous instructions", "forget everything",
    "new instructions:", "override system"
  - Role/persona hijacking: "pretend you are", "your new role", "DAN", "jailbreak"
  - JSON/schema injection: `"role": "system"`, `<script>`, `<system>` tags
  - Prompt leak attempts: "repeat your system prompt", "what are your instructions"
- Deliberately **excluded** from patterns: "act as", "you are now", "system prompt" —
  these fire on legitimate business speech ("system must act as single source of truth")
  and would corrupt transcript content silently
- Matched patterns are replaced with `[REDACTED]`; all matches are returned as warnings
- `sanitize_speakers(speakers)` — strips non-printable/special chars from speaker names,
  caps at 128 chars each

**Output validation — `validate_extracted()` and `validate_brd()`**

`ExtractedData` schema (LLM Call 1 output) — v2:
- Fields: `project_name`, `meeting_date`, `business_context`, `business_objectives`,
  `stakeholders` (role/responsibility), `domain_glossary` (term/meaning),
  `as_is_flow`, `to_be_flow` (step/sub_steps/example), `in_scope` (module/feature/description),
  `out_of_scope`, `use_cases` (title/actor/goal/key_steps/business_rules/exceptions),
  `assumptions` (assumption/impact_if_changed), `constraints`, `open_questions`,
  `decisions_made`, `risks`, `non_functional_requirements`
- `extra="ignore"` on all models — unexpected LLM keys discarded silently

`BRDDocument` schema (LLM Call 2 output) — v2:
- Models: `DocumentInfo`, `BusinessObjective` (id/title/description), `Stakeholder`,
  `InScopeItem` (module/feature/description/key_outcomes), `OutOfScopeItem`,
  `Scope`, `Assumption` (id/assumption/impact_if_changed), `FlowStep`
  (step/user_action/system_action), `ToBeStep` (step/sub_steps/example),
  `ExceptionRow`, `UseCase` (full actor-driven structure), `NonFunctionalRequirement`,
  `Risk`, `OpenQuestion`
- ID series: `BO-NNN`, `A-NNN`, `UC_NN`, `NFR-NNN`, `R-NNN`, `OQ-NNN`
- Priority/impact: `High`, `Medium`, `Low`
- NFR categories: Performance, Security, Scalability, Usability, Reliability, Auditability, Data Accuracy
- `_coerce_to_be()` field validator: tolerates bare strings in TO BE flow (LLM fallback)
- `renumber_ids()` model validator: silently corrects non-sequential/duplicate IDs, never raises
- `validate_brd()` returns a plain `dict` (not a model instance) — formatter receives raw dict
- Both validate functions wrap `ValidationError` in plain `ValueError` — internals never reach UI

### `extractor.py`
- `extract(cleaned_text, speakers, client, deployment)` → dict
- **Sanitization:** calls `sanitize_transcript()` and `sanitize_speakers()` before
  building the prompt; injection warnings are logged
- **Hard structural delimiters:** transcript wrapped in `<<<TRANSCRIPT_BEGIN>>>` /
  `<<<TRANSCRIPT_END>>>` markers; user message explicitly labels content as untrusted
- `temperature=0.1` — near-zero for consistent extraction
- `max_tokens=4096` — lowered from 8192 to reduce per-request TPM consumption
- `response_format={"type": "json_object"}` — Azure OpenAI JSON mode
- **Retry:** `_call_with_retry()` — exponential backoff, up to **5 attempts**,
  delays **10s / 20s / 40s / 80s / 120s** (capped); reads `Retry-After` header from
  Azure when provided; retries on `RateLimitError` (429) and `APIStatusError` 5xx only
- Validates LLM output via `sanitizer.validate_extracted()` before returning
- Logs use-case count, NFR count, and token usage on completion

### `generator.py`
- `generate(extracted_data, client, deployment)` → dict
- Re-serializes extracted data via `json.dumps(ensure_ascii=True)` — never passes
  raw LLM output as a string directly into the next prompt
- `temperature=0.1`
- `max_tokens=8000`
- `response_format={"type": "json_object"}`
- **Retry:** same `_call_with_retry()` pattern as `extractor.py` (5 attempts, 10s base)
- Calls `sanitizer.validate_brd()` which returns a plain dict — no `.model_dump()` needed
- Logs use-case count and token usage on completion

### `cost_guard.py`
- **Token guard only:** `tiktoken` exact count before any API call. Rejects > 20k tokens
- Rate limiter, daily budget circuit breaker, and usage file removed
- Cost protection delegated entirely to Azure AI Foundry TPM/RPM quotas
- No file persistence — no ephemeral filesystem dependency

### `formatter.py`
**v2 — use-case-driven (current)**

- `build_brd(brd: dict, output_path: str) → str` — main entry point
- `format_docx(brd: dict) → bytes` — convenience wrapper for Streamlit download
  (writes to temp file, reads back as bytes, cleans up)
- `_setup_base_styles()`: Arial font on Normal style
- `_add_footer_page_numbers()`: PAGE field in footer centre via raw XML
- `_add_table()`: styled tables — navy header, white text, zebra body rows
- `_set_cell_bg()` / `_set_cell_margins()`: raw XML (`OxmlElement`) — python-docx
  has no native API for cell shading or margins

**Document sections (numbered):**
1. **Title page** — company name, BRD title, project name, metadata table
2. **Introduction** — intro paragraph + Business Objectives (bold title + description)
   + Stakeholders table (Role / Responsibility)
3. **Scope** — In-Scope table (Module / Feature / Description / Key Outcomes)
   + Out-of-Scope table (Item / Description)
4. **Assumptions** — table (Sr. No. / Assumption / Impact If Changed)
5. **AS IS Business Flow** — bullet list
6. **TO BE Business Process Flow** — numbered steps, indented sub-bullets, italic examples
7. **Use Cases** — per-UC subheading with: Description (`As a…`), Role/Action/Benefit
   bullets, End User, Pre/Post-Condition, Main Flow table (collapses User Action column
   when all steps are system-driven), Business Rules table, Exceptional Flow table,
   per-UC Out of Scope table
8. **Non-Functional Requirements** table
9. **Risks** table
10. **Open Questions** table

### `streamlit_app.py`
- `defusedxml.defuse_stdlib()` called at module import — patches stdlib XML parsers
  before any other code runs
- Auth gate runs first — nothing renders until `auth.ok == True`
- Username resolved at startup: Easy Auth header → az CLI → session fallback
- Sidebar shows username and token limit only — no cost display
- Upload → token guard → pipeline → download button
- `st.status()` shows live step progress: Cleaned → Extracted (use case count) →
  Generated → Formatted
- `--server.maxUploadSize 5` enforced at Streamlit level (5MB cap)
- Three upload guards: raw byte size (`5MB`), decoded char count (`2MB`),
  expansion ratio (`10×`, zip-bomb protection)
- **429 error handling:** detects rate-limit errors from extractor and generator,
  shows actionable message ("increase TPM in Azure AI Foundry") instead of raw API error

---

## Environment Variables

```
AZURE_KEYVAULT_URL            https://your-vault.vault.azure.net/
AZURE_API_KEY_SECRET_NAME     name of the secret storing the OpenAI API key
AZURE_OPENAI_ENDPOINT         https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT       gpt-4o-mini
AZURE_OPENAI_API_VERSION      2024-12-01-preview
```

On Azure App Service: set these under **Configuration → Application Settings**
(not in .env — .env is gitignored and not deployed).

---

## Cost Protection

| Layer | Where | Limit |
|---|---|---|
| Input token guard | cost_guard.py | 20,000 tokens max input |
| Upload size | Streamlit server flag | 5MB max file |
| Azure TPM quota | Azure AI Foundry portal | Set to 40,000 TPM (primary cost wall) |
| Azure RPM quota | Azure AI Foundry portal | Set to 10 RPM |
| Azure Cost Alert | Azure Cost Management | Set monthly budget alert |

Rate limiter and daily budget circuit breaker removed — ephemeral filesystem on
App Service made them unreliable. Azure-side quotas are the cost protection layer.

---

## Deployment — Azure App Service

- Runtime: Python 3.11 Linux
- Startup command: `bash startup.sh`
- `startup.sh` runs: `streamlit run ui/streamlit_app.py --server.port ${PORT:-8000}`
- TLS: Enable **HTTPS Only** in App Service TLS/SSL settings
- Managed Certificate: free via App Service (no Let's Encrypt needed)
- Dependencies: installed via `pip install -r requirements.txt` (Oryx build)
- API key: retrieved from Key Vault at runtime using **Managed Identity**
  (assign Key Vault Secrets User role to the App Service identity in Azure)

---

## Security Notes

- API key never touches disk or logs — fetched from Key Vault at runtime
- `.env` is gitignored — never committed
- Auth gate prevents any file upload or LLM call until Azure credentials verified
- All LLM calls use `response_format: json_object` — structural injection protection
- `sanitizer.py` filters 12 injection patterns before transcript reaches the LLM;
  patterns chosen to avoid false positives on legitimate business language
- Hard structural delimiters (`<<<TRANSCRIPT_BEGIN>>>` / `<<<TRANSCRIPT_END>>>`)
  in extractor prompt explicitly label transcript as untrusted input
- `defusedxml` patches stdlib XML parsers at startup — prevents XML bomb attacks
- Three upload guards in streamlit_app.py: byte size, decoded char count, expansion ratio
- Pydantic output validation (`sanitizer.py`) enforces schema on both LLM outputs;
  unexpected keys discarded, IDs auto-renumbered, raw validation errors never reach UI
- Username resolved from Easy Auth header when deployed on App Service

---

## File Inventory

| File | Status | Notes |
|---|---|---|
| `app/__init__.py` | ✅ | Package marker |
| `app/auth.py` | ✅ | Azure auth gate |
| `app/cleaner.py` | ✅ | Transcript cleaning |
| `app/cost_guard.py` | ✅ | Token guard only (rate limit/budget removed) |
| `app/extractor.py` | ✅ | LLM Call 1 — with retry + hard delimiters |
| `app/formatter.py` | ✅ | BRD JSON → .docx |
| `app/generator.py` | ✅ | LLM Call 2 — with retry |
| `app/keyvault.py` | ✅ | Azure Key Vault client |
| `app/prompts.py` | ✅ | v2 use-case-driven prompts (domain fidelity rules) |
| `app/sanitizer.py` | ✅ | Injection filtering + v2 Pydantic output validation |
| `UI/auth.py` | ⚠️ | Stale duplicate of app/auth.py — safe to delete |
| `UI/streamlit_app.py` | ✅ | Streamlit UI |
| `startup.sh` | ✅ | Azure App Service startup |
| `requirements.txt` | ✅ | pip dependencies (defusedxml added) |
| `pyproject.toml` | ✅ | Poetry config |
| `.env.example` | ✅ | Env var template |
| `.gitignore` | ✅ | Git exclusions |
| `CLAUDE.md` | ✅ | This file |
| `LOG.md` | ✅ | Build log |
