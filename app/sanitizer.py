"""
sanitizer.py — prompt-injection defence and output schema enforcement.

Two responsibilities:
  1. sanitize_input()   — strip/neutralise injection attempts before they reach the LLM.
  2. validate_extract() / validate_brd() — enforce strict Pydantic schemas on LLM output
                                           so manipulated responses never propagate downstream.
"""

import re
import json
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Input sanitization ────────────────────────────────────────────────────────

# Patterns that are canonical injection / jailbreak markers.
# This is a blocklist, not a whitelist — complement it with hard delimiters (see extractor.py).
_INJECTION_PATTERNS: list[re.Pattern] = [
    # Direct instruction overrides
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"forget\s+(everything|all|your\s+instructions?)", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"override\s+(system|instructions?|prompt)", re.I),

    # Role / persona hijacking
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\bact\s+as\b", re.I),
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I),
    re.compile(r"\byour\s+new\s+role\b", re.I),
    re.compile(r"\bsystem\s*prompt\b", re.I),
    re.compile(r"\b(DAN|jailbreak|jail\s*break)\b", re.I),

    # JSON/schema injection — trying to smuggle extra keys into output
    re.compile(r'"role"\s*:\s*"(system|assistant)"', re.I),
    re.compile(r"<\s*/?s(ystem|cript)\s*>", re.I),

    # Prompt leak attempts
    re.compile(r"(repeat|print|output|reveal|show)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"what\s+(are\s+your|is\s+your)\s+(instructions?|prompt|system)", re.I),
]

# Speaker name: only printable word characters, spaces, hyphens, apostrophes.
_SAFE_SPEAKER = re.compile(r"[^\w\s\-\'\.]")


def sanitize_transcript(text: str) -> tuple[str, list[str]]:
    """
    Strip injection patterns from transcript text.
    Returns (sanitized_text, list_of_warnings).
    Warnings are logged by the caller — we don't swallow them silently.
    """
    warnings: list[str] = []
    sanitized = text

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(sanitized):
            warnings.append(f"Injection pattern detected and redacted: {pattern.pattern!r}")
            sanitized = pattern.sub("[REDACTED]", sanitized)

    return sanitized, warnings


def sanitize_speakers(speakers: list[str]) -> list[str]:
    """
    Strip non-printable / special chars from speaker names.
    A name like '[SYSTEM OVERRIDE]' becomes 'SYSTEM OVERRIDE' — harmless in context.
    """
    cleaned = []
    for name in speakers:
        safe = _SAFE_SPEAKER.sub("", name).strip()
        if safe:
            cleaned.append(safe[:128])  # hard length cap
    return cleaned


# ── Output schema enforcement — Extraction ───────────────────────────────────

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

    model_config = {"extra": "forbid"}  # reject any keys not in schema

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
    """Raise ValidationError if the LLM output violates the extraction schema."""
    return ExtractedData.model_validate(raw)


# ── Output schema enforcement — BRD Generation ───────────────────────────────

class DocumentInfo(BaseModel):
    project_name: str
    version: str = "1.0"
    status: str = "Draft"
    prepared_by: str = "BRD Agent (AI-assisted)"
    model_config = {"extra": "forbid"}


class BRDScope(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class StakeholderBRD(BaseModel):
    name: str
    role: str
    responsibility: str = ""
    model_config = {"extra": "forbid"}


class BusinessObjective(BaseModel):
    id: str
    description: str
    success_criteria: str = ""
    model_config = {"extra": "forbid"}

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
    model_config = {"extra": "forbid"}

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
    model_config = {"extra": "forbid"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^NFR-\d{3}$", v):
            raise ValueError(f"Invalid NFR id format: {v!r}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        valid = {"Performance", "Security", "Scalability", "Usability", "Reliability"}
        if v not in valid:
            raise ValueError(f"Invalid NFR category: {v!r}")
        return v

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
    model_config = {"extra": "forbid"}

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
    model_config = {"extra": "forbid"}

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
    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def check_sequential_ids(self) -> "BRDDocument":
        """IDs must be sequential with no gaps — gaps indicate the model hallucinated or skipped items."""
        def _check(items, prefix, attr="id"):
            ids = [getattr(i, attr) for i in items]
            for idx, id_ in enumerate(ids, start=1):
                expected = f"{prefix}-{idx:03d}"
                if id_ != expected:
                    raise ValueError(f"Non-sequential ID: expected {expected}, got {id_!r}")
        _check(self.business_objectives, "BO")
        _check(self.functional_requirements, "FR")
        _check(self.non_functional_requirements, "NFR")
        _check(self.risks, "R")
        _check(self.open_questions, "OQ")
        _check(self.action_items, "AI")
        return self


def validate_brd(raw: dict) -> BRDDocument:
    """Raise ValidationError if the LLM output violates the BRD schema."""
    return BRDDocument.model_validate(raw)
