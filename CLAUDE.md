# CLAUDE.md — Architecture Reference

This file exists so Claude (or any developer) can understand the full architecture
of this project without reading every file. Keep it updated when modules change.

---

## What This Project Does

Converts a Microsoft Teams meeting transcript (`.txt`, 10k–15k words) into a
structured Business Requirements Document (`.docx`) using a two-call LLM pipeline.

**Input:** Teams transcript `.txt` file
**Output:** Styled BRD `.docx` with 11 sections, tables, and headings

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
│   ├── cost_guard.py     Rate limiting, token guard, daily budget circuit breaker
│   ├── extractor.py      LLM Call 1 — structured JSON extraction
│   ├── formatter.py      BRD JSON → styled .docx via python-docx
│   ├── generator.py      LLM Call 2 — formal BRD prose generation
│   ├── keyvault.py       Azure Key Vault secret retrieval
│   └── prompts.py        All LLM system prompts (extraction + generation)
├── ui/
│   └── streamlit_app.py  Streamlit frontend — upload, pipeline, download
├── outputs/              Generated .docx files + .usage.json (gitignored)
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
[ cost_guard.py ] — pre-flight checks
  check_rate_limit()      → max 10 runs/hour (file-based, outputs/.usage.json)
  check_daily_budget()    → circuit breaker at $5.00/day
  check_input_size()      → reject if transcript > 20,000 tokens
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
  Input: cleaned transcript + speaker list
  Output: structured JSON (requirements, stakeholders, scope, risks, etc.)
  Records actual token usage → cost_guard.record_usage()
        │
        ▼
[ generator.py ] — LLM Call 2
  Model: gpt-4o-mini
  Temperature: 0.3 (formal prose needs slight fluency)
  max_tokens: 6000
  response_format: json_object
  Input: extracted JSON + today's date
  Output: full BRD JSON (11 sections, numbered IDs, priorities)
  Records actual token usage → cost_guard.record_usage()
        │
        ▼
[ formatter.py ]
  Converts BRD JSON → styled .docx using python-docx
  Title page, 11 numbered sections, styled tables, bullet lists
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
- `EXTRACTION_SYSTEM`: instructs model to extract only what is explicitly stated.
  Schema includes: project_name, stakeholders, functional_requirements,
  non_functional_requirements, in_scope, out_of_scope, assumptions,
  constraints, open_questions, decisions_made, action_items, risks
- `GENERATION_SYSTEM`: instructs model to expand extracted data into formal
  BRD prose. Schema produces numbered IDs (FR-001, NFR-001, R-001, etc.),
  priorities (High/Medium/Low), and acceptance criteria

### `extractor.py`
- LLM Call 1: `temperature=0.1` — extraction is mechanical, not creative
- Prepends detected speaker names to user message for stakeholder attribution
- Calls `record_usage()` with actual `response.usage` token counts

### `generator.py`
- LLM Call 2: `temperature=0.3` — slightly higher for fluent prose
- Injects today's date for timeline reasoning
- Calls `record_usage()` with actual `response.usage` token counts

### `cost_guard.py`
- **Token guard:** `tiktoken` exact count before any API call. Rejects > 20k tokens
- **Rate limiter:** file-based (not session-based) — persists across browser refreshes.
  Stores timestamps in `outputs/.usage.json`, counts requests in last 3600s
- **Circuit breaker:** cumulative daily USD spend tracked per day.
  Resets at midnight (date comparison on file load)
- **Pricing (gpt-4o-mini):** $0.15/1M input, $0.60/1M output
- **Typical run cost:** ~$0.006 (under 1 cent)
- `get_summary()` returns live stats for sidebar display

### `formatter.py`
- `_setup_styles()`: sets Arial font on Normal, Heading 1 (blue), Heading 2 (navy)
- `_add_table()`: builds styled tables — navy header row, alternating light/white rows,
  cell padding, borders via raw XML (`OxmlElement`)
- `_set_cell_bg()`: sets cell shading via `w:shd` XML — python-docx has no native API for this
- Sections: Title page, Executive Summary (page break after), Project Overview,
  Scope, Stakeholders, Business Objectives, Functional Requirements,
  Non-Functional Requirements, Assumptions & Constraints, Risks,
  Open Questions, Action Items

### `streamlit_app.py`
- Auth gate runs first — nothing renders until `auth.ok == True`
- Cost sidebar always visible (shows spend, remaining, run count)
- Upload → pre-flight guards → pipeline → cost display → download button
- `st.status()` shows live step progress during pipeline execution
- `--server.maxUploadSize 5` enforced at Streamlit level (5MB cap)

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

## Cost Protection (Three Layers)

| Layer | Where | Limit |
|---|---|---|
| Input token guard | cost_guard.py | 20,000 tokens max input |
| Rate limiter | cost_guard.py | 10 runs/hour |
| Daily circuit breaker | cost_guard.py | $5.00/day |
| Upload size | Streamlit server flag | 5MB max file |
| Azure TPM quota | Azure AI Foundry portal | Set to 40,000 TPM |
| Azure Cost Alert | Azure Cost Management | Set monthly budget alert |

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
- `outputs/.usage.json` is gitignored
- Auth gate prevents any file upload or LLM call until Azure credentials verified
- All LLM calls use `response_format: json_object` — prevents prompt injection
  from transcript content affecting JSON parsing
