"""
sanitizer.py — prompt-injection defence and output schema enforcement.

Two responsibilities:
  1. sanitize_input()   — strip/neutralise injection attempts before they reach the LLM.
  2. validate_extracted() / validate_brd() — enforce Pydantic schemas on LLM output
                                             so malformed responses never propagate downstream.
"""

import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from loguru import logger


# ── NFR categories ────────────────────────────────────────────────────────────

VALID_NFR_CATEGORIES = {
    "Performance", "Security", "Scalability", "Usability", "Reliability",
    "Availability", "Maintainability", "Compliance", "Accessibility",
    "Integration", "Portability", "Interoperability", "Recoverability",
    "Auditability", "Capacity", "Other",
}


# ── Input sanitization ────────────────────────────────────────────────────────

# Patterns that are unambiguous injection / jailbreak markers.
# Removed: "act as", "you are now", "system prompt" — these fire on legitimate
# business speech ("the system must act as a single source of truth") and corrupt
# transcript content without the user knowing why. The threat model here is an
# internal Teams transcript, not a public-facing chatbot.
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
    Warnings are logged by the caller.
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


# ── Output schema — Extraction (LLM Call 1) ───────────────────────────────────

class ActionItemExtracted(BaseModel):
    item: str
    owner: str = "TBD"
    due_date: str = "TBD"


class StakeholderExtracted(BaseModel):
    name: str
    role: str
    interest: str = ""


class ExtractedData(BaseModel):
    project_name: str = "TBD"
    meeting_date: Optional[str] = None
    business_context: Optional[str] = None
    business_objectives: list[str] = Field(default_factory=list)
    stakeholders: list[StakeholderExtracted] = Field(default_factory=list)
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decisions_made: list[str] = Field(default_factory=list)
    action_items: list[ActionItemExtracted] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    # extra="ignore": LLM occasionally returns unexpected keys — discard them
    # rather than failing the entire run.
    model_config = {"extra": "ignore"}

    @field_validator("functional_requirements", "non_functional_requirements", mode="before")
    @classmethod
    def ensure_strings(cls, v):
        if not isinstance(v, list):
            raise ValueError("Must be a list")
        return [str(item) for item in v]

    @field_validator("project_name", mode="before")
    @classmethod
    def clamp_project_name(cls, v):
        return str(v)[:256] if v else "TBD"


def validate_extracted(raw: dict) -> ExtractedData:
    """
    Validate extractor LLM output.
    Raises ValueError with a clean message — never leaks raw Pydantic internals to the caller.
    """
    try:
        return ExtractedData.model_validate(raw)
    except ValidationError as e:
        logger.error(f"Extraction validation failed: {e}")
        raise ValueError(f"Extraction output failed schema validation: {e}") from e


# ── Output schema — BRD Generation (LLM Call 2) ───────────────────────────────

class DocumentInfo(BaseModel):
    project_name: str
    version: str = "1.0"
    status: str = "Draft"
    prepared_by: str = "BRD Agent (AI-assisted)"
    model_config = {"extra": "ignore"}


class BRDScope(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    model_config = {"extra": "ignore"}


class StakeholderBRD(BaseModel):
    name: str
    role: str
    responsibility: str = ""
    model_config = {"extra": "ignore"}


class BusinessObjective(BaseModel):
    id: str
    description: str
    success_criteria: str = ""
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^BO-\d{3}$", v):
            raise ValueError(f"Invalid BO id format: {v!r}")
        return v


class FunctionalRequirement(BaseModel):
    id: str
    description: str
    priority: str
    acceptance_criteria: str = ""
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^FR-\d{3}$", v):
            raise ValueError(f"Invalid FR id format: {v!r}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        if v not in ("High", "Medium", "Low"):
            raise ValueError(f"Invalid priority: {v!r}")
        return v


class NonFunctionalRequirement(BaseModel):
    id: str
    category: str
    description: str
    priority: str
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^NFR-\d{3}$", v):
            raise ValueError(f"Invalid NFR id format: {v!r}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        # Normalise casing before checking — LLM sometimes returns "performance" (lowercase)
        normalised = v.strip().title()
        if normalised not in VALID_NFR_CATEGORIES:
            logger.warning(f"Unrecognised NFR category {v!r} — keeping as-is")
            return v.strip()
        return normalised

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        if v not in ("High", "Medium", "Low"):
            raise ValueError(f"Invalid priority: {v!r}")
        return v


class Risk(BaseModel):
    id: str
    description: str
    impact: str
    mitigation: str = ""
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^R-\d{3}$", v):
            raise ValueError(f"Invalid Risk id format: {v!r}")
        return v

    @field_validator("impact")
    @classmethod
    def validate_impact(cls, v):
        if v not in ("High", "Medium", "Low"):
            raise ValueError(f"Invalid impact: {v!r}")
        return v


class OpenQuestion(BaseModel):
    id: str
    question: str
    owner: str = "TBD"
    target_date: str = "TBD"
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^OQ-\d{3}$", v):
            raise ValueError(f"Invalid OQ id format: {v!r}")
        return v


class ActionItemBRD(BaseModel):
    id: str
    action: str
    owner: str = "TBD"
    due_date: str = "TBD"
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^AI-\d{3}$", v):
            raise ValueError(f"Invalid AI id format: {v!r}")
        return v


class BRDDocument(BaseModel):
    document_info: DocumentInfo
    executive_summary: str
    project_overview: str
    scope: BRDScope
    stakeholders: list[StakeholderBRD] = Field(default_factory=list)
    business_objectives: list[BusinessObjective] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    action_items: list[ActionItemBRD] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def renumber_ids(self) -> "BRDDocument":
        """
        Re-number IDs to be sequential if the LLM skipped or duplicated any.
        Logs a warning per correction — never raises, because a renumbered BRD
        is always better than a failed run.
        """
        def _renumber(items, prefix: str):
            for idx, item in enumerate(items, start=1):
                expected = f"{prefix}-{idx:03d}"
                if item.id != expected:
                    logger.warning(
                        f"Non-sequential ID corrected: {item.id!r} → {expected!r}"
                    )
                    item.id = expected

        _renumber(self.business_objectives,       "BO")
        _renumber(self.functional_requirements,   "FR")
        _renumber(self.non_functional_requirements, "NFR")
        _renumber(self.risks,                     "R")
        _renumber(self.open_questions,            "OQ")
        _renumber(self.action_items,              "AI")
        return self


def validate_brd(raw: dict) -> BRDDocument:
    """
    Validate generator LLM output.
    Raises ValueError with a clean message — never leaks raw Pydantic internals to the caller.
    """
    try:
        return BRDDocument.model_validate(raw)
    except ValidationError as e:
        logger.error(f"BRD validation failed: {e}")
        raise ValueError(f"BRD output failed schema validation: {e}") from e