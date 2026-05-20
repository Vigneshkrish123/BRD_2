import re
from loguru import logger


# ── Regex patterns ────────────────────────────────────────────────────────────

# Teams exports timestamps in these forms:
#   0:00:05        (H:MM:SS)
#   0:00:05.000    (H:MM:SS.ms)
#   00:05          (MM:SS)
_TIMESTAMP = re.compile(
    r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*$",
    re.MULTILINE,
)

# VTT-style timestamps: 00:00:05.000 --> 00:00:10.000
_VTT_TIMESTAMP = re.compile(
    r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
)

# VTT speaker tags: <v John Doe> ... </v>
_VTT_TAGS = re.compile(r"<v[^>]*>|</v>|<[^>]+>")

# Filler words — common spoken noise
_FILLERS = re.compile(
    r"\b(um+|uh+|hmm+|mhm+|you know|i mean|sort of|kind of|"
    r"basically|literally|actually|right\?|okay so|so yeah|"
    r"like i said|you see)\b",
    re.IGNORECASE,
)

# Teams speaker line pattern: a line that is ONLY a person's name
# Heuristic: 1-5 words, each capitalised, no punctuation except hyphens/dots
_SPEAKER_LINE = re.compile(
    r"^([A-Z][a-zA-Z\-\.]+(?:\s[A-Z][a-zA-Z\-\.]+){0,4})\s*$",
    re.MULTILINE,
)


# ── Public API ────────────────────────────────────────────────────────────────

def clean_transcript(raw_text: str) -> tuple[str, list[str]]:
    """
    Clean a Teams meeting transcript.

    Returns:
        cleaned_text : str        — noise removed, ready for LLM
        speakers     : list[str]  — unique speaker names detected
    """
    speakers = _extract_speakers(raw_text)
    text = raw_text

    # 1. Strip WEBVTT header
    text = re.sub(r"^WEBVTT\s*\n?", "", text, flags=re.MULTILINE)

    # 2. Remove VTT-style arrow timestamps
    text = _VTT_TIMESTAMP.sub("", text)

    # 3. Remove VTT speaker tags
    text = _VTT_TAGS.sub("", text)

    # 4. Remove standalone timestamp lines (0:00:05 / 0:00:05.000)
    text = _TIMESTAMP.sub("", text)

    # 5. Remove speaker-name-only lines (they're structural, not content)
    text = _SPEAKER_LINE.sub("", text)

    # 6. Strip filler words
    text = _FILLERS.sub("", text)

    # 7. Collapse leftover whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"^\s+$", "", text, flags=re.MULTILINE)

    cleaned = text.strip()

    original_wc = len(raw_text.split())
    cleaned_wc = len(cleaned.split())
    logger.info(
        f"Cleaned transcript | "
        f"original={original_wc:,} words → cleaned={cleaned_wc:,} words | "
        f"speakers detected: {speakers or ['none']}"
    )

    return cleaned, speakers


# ── Internal ──────────────────────────────────────────────────────────────────

def _extract_speakers(text: str) -> list[str]:
    """
    Find unique speaker names.
    In Teams transcripts a speaker name appears on its own line
    immediately before a timestamp line.
    """
    lines = text.splitlines()
    speakers = []

    for i, line in enumerate(lines):
        line = line.strip()
        # Check if this line looks like a name AND the next non-empty line is a timestamp
        if _SPEAKER_LINE.match(line):
            next_lines = [l.strip() for l in lines[i + 1:i + 3] if l.strip()]
            if next_lines and _TIMESTAMP.match(next_lines[0]):
                speakers.append(line)

    return sorted(set(speakers))
