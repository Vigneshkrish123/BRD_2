"""
sanitizer.py — prompt-injection defence and output schema enforcement.

Two responsibilities:
  1. sanitize_input()   — strip/neutralise injection attempts before they reach the LLM.
  2. validate_extracted() / validate_brd() — enforce Pydantic schemas on LLM output
                                             so malformed responses never propagate downstream.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, ValidationError


# ── Input sanitization ────────────────────────────────────────────────────────

# Patterns that are unambiguous injection / jailbreak markers.
# Removed: "act as", "you are now", "system prompt" — these fire on legitimate
# business speech ("the system must act as a single source of truth") and corrupt
# transcript content without the user knowing why.
_INJECTION_PATTERNS: list[re.Pattern] = [
    # Direct instruction overrides
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"forget\s+(everything|all|your\s+instructions?)", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"override\s+(system|instructions?|prompt)", re.I),

    # Role / persona hijacking
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I),
    re.compile(r"\byour\s+new\s+role\b", re.I),
    re.compile(r"\b(DAN|jailbreak|jail\s*break)\b", re.I),

    # JSON / schema injection
    re.compile(r'"role"\s*:\s*"(system|assistant)"', re.I),
    re.compile(r"<\s*/?s(ystem|cript)\s*>", re.I),

    # Prompt leak attempts
    re.compile(r"(repeat|print|output|reveal|show)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"what\s+(are\s+your|is\s+your)\s+(instructions?|prompt|system)", re.I),
]

_SAFE_SPEAKER = re.compile(r"[^\w\s\-\'\.]")


def sanitize_transcript(text: str) -> tuple[str, list[str]]:
    """
    Strip injection patterns from transcript text.
    Returns (sanitized_text, list_of_warnings).
    """
    warnings: list[str] = []
    sanitized = text

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(sanitized):
            warnings.append(f"Injection pattern detected and redacted: {pattern.pattern!r}")
            sanitized = pattern.sub("[REDACTED]", sanitized)

    return sanitized, warnings


def sanitize_speakers(speakers: list[str]) -> list[str]:
    """Strip non-printable / special chars from speaker names."""
    cleaned = []
    for name in speakers:
        safe = _SAFE_SPEAKER.sub("", name).strip()
        if safe:
            cleaned.append(safe[:128])
    return cleaned


# ── Shared base ───────────────────────────────────────────────────────────────

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


Priority = Literal["High", "Medium", "Low"]

_NFR_CATEGORIES = {
    "Performance", "Security", "Scalability", "Usability",
    "Reliability", "Auditability", "Data Accuracy",
}


# ── Output schema — Extraction (LLM Call 1) ───────────────────────────────────

class _StakeholderExtracted(_Base):
    role: str = ""
    responsibility: str = ""


class _GlossaryEntry(_Base):
    term: str = ""
    meaning: str = "TBD"


class _ToBeFlowStep(_Base):
    step: str = ""
    sub_steps: list[str] = Field(default_factory=list)
    example: str = ""


class _InScopeExtracted(_Base):
    module: str = "General"
    feature: str = ""
    description: str = ""


class _UseCaseExtracted(_Base):
    title: str = ""
    actor: str = ""
    goal: str = ""
    key_steps: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)


class _AssumptionExtracted(_Base):
    assumption: str = ""
    impact_if_changed: Optional[str] = None


class ExtractedData(_Base):
    project_name: str = "TBD"
    meeting_date: Optional[str] = None
    business_context: Optional[str] = None
    business_objectives: list[str] = Field(default_factory=list)
    stakeholders: list[_StakeholderExtracted] = Field(default_factory=list)
    domain_glossary: list[_GlossaryEntry] = Field(default_factory=list)
    as_is_flow: list[str] = Field(default_factory=list)
    to_be_flow: list[_ToBeFlowStep] = Field(default_factory=list)
    in_scope: list[_InScopeExtracted] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    use_cases: list[_UseCaseExtracted] = Field(default_factory=list)
    assumptions: list[_AssumptionExtracted] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decisions_made: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)

    @field_validator("project_name", mode="before")
    @classmethod
    def clamp_project_name(cls, v):
        return str(v)[:256] if v else "TBD"


def validate_extracted(raw: dict) -> ExtractedData:
    """
    Validate extractor LLM output.
    Raises ValueError — never leaks raw Pydantic internals to the caller.
    """
    try:
        return ExtractedData.model_validate(raw)
    except ValidationError as e:
        logger.error(f"Extraction validation failed: {e}")
        raise ValueError(f"Extraction output failed schema validation: {e}") from e


# ── Output schema — BRD Generation (LLM Call 2) ───────────────────────────────

class DocumentInfo(_Base):
    project_name: str = "TBD"
    version: str = "1.0"
    status: str = "Draft"
    prepared_by: str = "BRD Agent (AI-assisted)"


class BusinessObjective(_Base):
    id: str = "BO-001"
    title: str = ""
    description: str = ""


class Stakeholder(_Base):
    role: str = ""
    responsibility: str = ""


class InScopeItem(_Base):
    module: str = "General"
    feature: str = ""
    description: str = ""
    key_outcomes: str = ""


class OutOfScopeItem(_Base):
    item: str = ""
    description: str = ""


class Scope(_Base):
    in_scope: list[InScopeItem] = Field(default_factory=list)
    out_of_scope: list[OutOfScopeItem] = Field(default_factory=list)


class Assumption(_Base):
    id: str = "A-001"
    assumption: str = ""
    impact_if_changed: str = ""


class FlowStep(_Base):
    step: str = ""
    user_action: str = ""
    system_action: str = ""


class ToBeStep(_Base):
    step: str = ""
    sub_steps: list[str] = Field(default_factory=list)
    example: str = ""


class ExceptionRow(_Base):
    exception: str = ""
    error_message: str = ""


class UseCase(_Base):
    id: str = "UC_01"
    title: str = ""
    description: str = ""
    role: str = ""
    action: str = ""
    benefit: str = ""
    end_user: str = ""
    pre_conditions: list[str] = Field(default_factory=list)
    post_conditions: list[str] = Field(default_factory=list)
    main_flow: list[FlowStep] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    exceptional_flow: list[ExceptionRow] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(_Base):
    id: str = "NFR-001"
    category: str = "Reliability"
    description: str = ""
    priority: Priority = "Medium"

    @model_validator(mode="after")
    def _normalise_category(self):
        cat = (self.category or "").strip().title()
        if cat not in _NFR_CATEGORIES:
            logger.warning(f"Unknown NFR category kept as-is: {self.category!r}")
        else:
            self.category = cat
        return self


class Risk(_Base):
    id: str = "R-001"
    description: str = ""
    impact: Priority = "Medium"
    mitigation: str = ""


class OpenQuestion(_Base):
    id: str = "OQ-001"
    question: str = ""
    owner: str = "TBD"
    target_date: str = "TBD"


class BRDDocument(_Base):
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    introduction: str = ""
    business_objectives: list[BusinessObjective] = Field(default_factory=list)
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    scope: Scope = Field(default_factory=Scope)
    assumptions: list[Assumption] = Field(default_factory=list)
    as_is_business_flow: list[str] = Field(default_factory=list)
    to_be_business_process_flow: list[ToBeStep] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

    @field_validator("to_be_business_process_flow", mode="before")
    @classmethod
    def _coerce_to_be(cls, v):
        """Tolerate a model that emits bare strings instead of nested objects."""
        if not isinstance(v, list):
            return v
        return [{"step": x} if isinstance(x, str) else x for x in v]

    @model_validator(mode="after")
    def renumber_ids(self):
        """Silently correct non-sequential / duplicate IDs. Never raises."""
        def fix(items, prefix, sep, width):
            for i, item in enumerate(items, start=1):
                want = f"{prefix}{sep}{i:0{width}d}"
                if getattr(item, "id", None) != want:
                    logger.warning(f"Renumbered {getattr(item, 'id', '?')} -> {want}")
                    item.id = want

        fix(self.business_objectives, "BO", "-", 3)
        fix(self.assumptions, "A", "-", 3)
        fix(self.use_cases, "UC", "_", 2)
        fix(self.non_functional_requirements, "NFR", "-", 3)
        fix(self.risks, "R", "-", 3)
        fix(self.open_questions, "OQ", "-", 3)
        return self


def validate_brd(data: dict) -> dict:
    """Validate LLM Call 2 output. Returns a plain dict for the formatter."""
    try:
        model = BRDDocument.model_validate(data)
    except Exception as exc:
        raise ValueError(f"BRD validation failed: {exc}") from exc
    return model.model_dump()
