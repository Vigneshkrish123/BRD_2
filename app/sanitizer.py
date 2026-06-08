"""
sanitizer.py — prompt-injection defence and output schema enforcement.
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

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompt)", re.I),
    re.compile(r"forget\s+(everything|all|your\s+instructions?)", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"override\s+(system|instructions?|prompt)", re.I),
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I),
    re.compile(r"\byour\s+new\s+role\b", re.I),
    re.compile(r"\b(DAN|jailbreak|jail\s*break)\b", re.I),
    re.compile(r'"role"\s*:\s*"(system|assistant)"', re.I),
    re.compile(r"<\s*/?s(ystem|cript)\s*>", re.I),
    re.compile(r"(repeat|print|output|reveal|show)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"what\s+(are\s+your|is\s+your)\s+(instructions?|prompt|system)", re.I),
]

_SAFE_SPEAKER = re.compile(r"[^\w\s\-\'\.]")


def sanitize_transcript(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    sanitized = text
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(sanitized):
            warnings.append(f"Injection pattern detected and redacted: {pattern.pattern!r}")
            sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized, warnings


def sanitize_speakers(speakers: list[str]) -> list[str]:
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
    model_config = {"extra": "ignore"}


class StakeholderExtracted(BaseModel):
    name: str
    role: str
    interest: str = ""
    model_config = {"extra": "ignore"}


class UseCaseExtracted(BaseModel):
    name: str
    actor: str = ""
    description: str = ""
    pre_conditions: list[str] = Field(default_factory=list)
    post_conditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    model_config = {"extra": "ignore"}


class ScopeModuleExtracted(BaseModel):
    module: str
    features: list[str] = Field(default_factory=list)
    key_outcomes: str = ""
    model_config = {"extra": "ignore"}


class OutOfScopeExtracted(BaseModel):
    item: str
    reason: str = ""
    model_config = {"extra": "ignore"}


class NotificationExtracted(BaseModel):
    event: str
    channel: str = ""
    model_config = {"extra": "ignore"}


class AssumptionExtracted(BaseModel):
    assumption: str
    impact_if_changed: str = ""
    model_config = {"extra": "ignore"}


class BusinessObjectiveExtracted(BaseModel):
    title: str
    description: str = ""
    model_config = {"extra": "ignore"}


class ExtractedData(BaseModel):
    project_name: str = "TBD"
    meeting_date: Optional[str] = None
    business_context: Optional[str] = None
    business_objectives: list[BusinessObjectiveExtracted] = Field(default_factory=list)
    stakeholders: list[StakeholderExtracted] = Field(default_factory=list)
    use_cases: list[UseCaseExtracted] = Field(default_factory=list)
    scope_modules: list[ScopeModuleExtracted] = Field(default_factory=list)
    out_of_scope: list[OutOfScopeExtracted] = Field(default_factory=list)
    notifications: list[NotificationExtracted] = Field(default_factory=list)
    assumptions: list[AssumptionExtracted] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decisions_made: list[str] = Field(default_factory=list)
    action_items: list[ActionItemExtracted] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("project_name", mode="before")
    @classmethod
    def clamp_project_name(cls, v):
        return str(v)[:256] if v else "TBD"


def validate_extracted(raw: dict) -> ExtractedData:
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


class BusinessObjectiveBRD(BaseModel):
    id: str
    title: str = ""
    description: str
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^BO-\d{3}$", v):
            raise ValueError(f"Invalid BO id format: {v!r}")
        return v


class StakeholderBRD(BaseModel):
    role: str
    responsibility: str = ""
    model_config = {"extra": "ignore"}


class InScopeItem(BaseModel):
    module: str
    feature: str
    description: str = ""
    key_outcomes: str = ""
    model_config = {"extra": "ignore"}


class OutOfScopeItem(BaseModel):
    item: str
    description: str = ""
    model_config = {"extra": "ignore"}


class ScopeSection(BaseModel):
    in_scope: list[InScopeItem] = Field(default_factory=list)
    out_of_scope: list[OutOfScopeItem] = Field(default_factory=list)
    model_config = {"extra": "ignore"}


class Assumption(BaseModel):
    sr_no: int = 1
    assumption: str
    impact_if_changed: str = ""
    model_config = {"extra": "ignore"}


class MainFlowStep(BaseModel):
    step: int
    user_action: str
    system_action: str
    model_config = {"extra": "ignore"}


class BusinessRule(BaseModel):
    sr_no: int
    rule: str
    model_config = {"extra": "ignore"}


class ExceptionalFlowItem(BaseModel):
    sr_no: int
    exception: str
    error_message: str = ""
    model_config = {"extra": "ignore"}


class UseCase(BaseModel):
    id: str
    name: str
    description: str = ""
    role: str = ""
    pre_conditions: list[str] = Field(default_factory=list)
    post_conditions: list[str] = Field(default_factory=list)
    main_flow: list[MainFlowStep] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    exceptional_flow: list[ExceptionalFlowItem] = Field(default_factory=list)
    model_config = {"extra": "ignore"}

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not re.match(r"^UC_\d{2,}$", v):
            raise ValueError(f"Invalid UC id format: {v!r} — expected UC_01, UC_02, ...")
        return v


class Notification(BaseModel):
    event: str
    trigger: str = ""
    channel: str = ""
    message_template: str = ""
    model_config = {"extra": "ignore"}


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


class AdoptionCriteria(BaseModel):
    success_criteria: str
    metrics_kpis: str = ""
    model_config = {"extra": "ignore"}


class BRDDocument(BaseModel):
    document_info: DocumentInfo
    introduction: str = ""
    business_objectives: list[BusinessObjectiveBRD] = Field(default_factory=list)
    stakeholders: list[StakeholderBRD] = Field(default_factory=list)
    scope: ScopeSection = Field(default_factory=ScopeSection)
    assumptions: list[Assumption] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)
    notifications: list[Notification] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    adoption_criteria: list[AdoptionCriteria] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def renumber_ids(self) -> "BRDDocument":
        def _renumber_prefixed(items, prefix: str, fmt: str):
            for idx, item in enumerate(items, start=1):
                expected = fmt.format(idx)
                if item.id != expected:
                    logger.warning(f"Non-sequential ID corrected: {item.id!r} → {expected!r}")
                    item.id = expected

        _renumber_prefixed(self.business_objectives, "BO", "BO-{:03d}")
        _renumber_prefixed(self.non_functional_requirements, "NFR", "NFR-{:03d}")

        # Use cases: UC_01, UC_02, ...
        for idx, uc in enumerate(self.use_cases, start=1):
            expected = f"UC_{idx:02d}"
            if uc.id != expected:
                logger.warning(f"Non-sequential UC ID corrected: {uc.id!r} → {expected!r}")
                uc.id = expected

        # Re-number assumption sr_no
        for idx, a in enumerate(self.assumptions, start=1):
            a.sr_no = idx

        return self


def validate_brd(raw: dict) -> BRDDocument:
    try:
        return BRDDocument.model_validate(raw)
    except ValidationError as e:
        logger.error(f"BRD validation failed: {e}")
        raise ValueError(f"BRD output failed schema validation: {e}") from e
