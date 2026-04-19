import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from dotenv import load_dotenv

import dateparser
import psycopg2
import spacy
import google_calendar
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from transformers import pipeline

# Load variables from backend/.env (.env is gitignored)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


# --------- Config ---------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/meetingmind",
)

# Pre-truncate long transcripts before tokenization (speed); BART encoder max is 1024 tokens.
MAX_SUMMARY_INPUT_CHARS = 32000
MAX_QA_CONTEXT_CHARS = 4000
MIN_QA_CONFIDENCE = 0.20
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# Groq (preferred): OpenAI-compatible chat API — https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip() or None
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Optional smaller/faster model for /ask only (saves cost vs 120B). Empty = same as GROQ_MODEL.
GROQ_QA_MODEL = os.getenv("GROQ_QA_MODEL", "").strip() or None
# On-demand tier TPM is often ~8k; large transcripts + high max_completion_tokens exceed it.
# Raise GROQ_MAX_* only after upgrading tier or if Groq raises your limits.
GROQ_MAX_TRANSCRIPT_CHARS = max(2000, int(os.getenv("GROQ_MAX_TRANSCRIPT_CHARS", "12000")))
# /ask can use a smaller context than /analyze to save tokens per chat turn.
GROQ_MAX_QA_TRANSCRIPT_CHARS = max(1500, int(os.getenv("GROQ_MAX_QA_TRANSCRIPT_CHARS", "8000")))
GROQ_ANALYZE = os.getenv("GROQ_ANALYZE", "1").strip().lower() not in ("0", "false", "no")
GROQ_MAX_RETRIES = max(1, int(os.getenv("GROQ_MAX_RETRIES", "5")))
GROQ_RETRY_BASE_SEC = float(os.getenv("GROQ_RETRY_BASE_SEC", "2"))
GROQ_TEMPERATURE_ANALYZE = float(os.getenv("GROQ_TEMPERATURE_ANALYZE", "0.2"))
GROQ_TEMPERATURE_QA = float(os.getenv("GROQ_TEMPERATURE_QA", "0.2"))
GROQ_MAX_TOKENS_ANALYZE = int(os.getenv("GROQ_MAX_TOKENS_ANALYZE", "2048"))
GROQ_MAX_TOKENS_QA = int(os.getenv("GROQ_MAX_TOKENS_QA", "1024"))
GROQ_TOP_P = float(os.getenv("GROQ_TOP_P", "1"))
# Set to empty to omit (some endpoints/models reject unknown fields)
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "medium").strip() or None

# Gemini (optional fallback if GROQ_API_KEY is not set)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None
# Use a model id your API key supports (see https://ai.google.dev/gemini-api/docs/models )
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-001")
GEMINI_MAX_TRANSCRIPT_CHARS = int(os.getenv("GEMINI_MAX_TRANSCRIPT_CHARS", "100000"))
# Retry transient 429 RESOURCE_EXHAUSTED (rate limits / “retry after N seconds”)
GEMINI_MAX_RETRIES = max(1, int(os.getenv("GEMINI_MAX_RETRIES", "6")))
GEMINI_RETRY_BASE_SEC = float(os.getenv("GEMINI_RETRY_BASE_SEC", "2"))
# /analyze uses Gemini only for summary, action items, deadlines, follow-ups (when key is set).
# Set GEMINI_ANALYZE=0 to force legacy local BART + heuristics (for ablation demos only).
GEMINI_ANALYZE = os.getenv("GEMINI_ANALYZE", "1").strip().lower() not in ("0", "false", "no")
# No GROQ/Gemini key: refuse /analyze unless this is enabled (offline / emergency dev only).
ALLOW_LOCAL_ANALYZE_WITHOUT_LLM = os.getenv(
    "ALLOW_LOCAL_ANALYZE_WITHOUT_LLM",
    os.getenv("ALLOW_LOCAL_ANALYZE_WITHOUT_GEMINI", "0"),
).strip().lower() in ("1", "true", "yes")
# spaCy over huge transcripts is slow; full text still stored in DB for QA
MAX_SPACY_INPUT_CHARS = int(os.getenv("MAX_SPACY_INPUT_CHARS", "150000"))

# --------- App ---------
app = FastAPI(title="MeetingMind API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Load NLP models once at startup ---------
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    raise RuntimeError(
        "spaCy model missing. Run: python -m spacy download en_core_web_sm"
    )

summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
qa_model = pipeline("question-answering", model="distilbert-base-cased-distilled-squad")


# --------- Request/Response models ---------
class AnalyzeRequest(BaseModel):
    title: str
    transcript: str


class AskRequest(BaseModel):
    meeting_id: str
    question: str


class ActionItemCompletedRequest(BaseModel):
    completed: bool


class CreateCalendarEventRequest(BaseModel):
    meeting_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    start_iso: Optional[str] = None
    duration_minutes: int = 60
    timezone: str = "UTC"
    add_meet_link: bool = False


# --------- DB helpers ---------
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _datetime_as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Attach UTC for JSON: naive TIMESTAMP from Postgres is interpreted as UTC wall time."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS meetings (
        id UUID PRIMARY KEY,
        title TEXT NOT NULL,
        transcript TEXT NOT NULL,
        summary TEXT NOT NULL,
        followup_suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS action_items (
        id UUID PRIMARY KEY,
        meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        task_text TEXT NOT NULL,
        owner TEXT,
        deadline_raw TEXT,
        deadline_iso TEXT,
        completed BOOLEAN NOT NULL DEFAULT FALSE,
        completed_at TIMESTAMP NULL
    );

    CREATE TABLE IF NOT EXISTS qa_history (
        id UUID PRIMARY KEY,
        meeting_id UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        score FLOAT,
        asked_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


def migrate_action_items_tracking():
    """Add task completion columns for DBs created before this feature."""
    alters = (
        "ALTER TABLE action_items ADD COLUMN IF NOT EXISTS completed BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE action_items ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL",
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in alters:
                cur.execute(stmt)
        conn.commit()


def migrate_google_calendar():
    """OAuth token store + optional links on meetings."""
    stmts = (
        """
        CREATE TABLE IF NOT EXISTS app_oauth (
            provider TEXT PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS google_calendar_event_id TEXT NULL",
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS google_calendar_html_link TEXT NULL",
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)
        conn.commit()


def get_google_calendar_refresh_token() -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT refresh_token FROM app_oauth WHERE provider = %s",
                ("google_calendar",),
            )
            row = cur.fetchone()
    if not row:
        return None
    t = row.get("refresh_token")
    return str(t).strip() or None


def save_google_calendar_refresh_token(token: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_oauth (provider, refresh_token, updated_at)
                VALUES ('google_calendar', %s, NOW())
                ON CONFLICT (provider) DO UPDATE
                SET refresh_token = EXCLUDED.refresh_token, updated_at = NOW()
                """,
                (token,),
            )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()
    migrate_action_items_tracking()
    migrate_google_calendar()


# --------- NLP helpers ---------
# Spoken disfluencies / hedges — stripped from model inputs and from user-facing task text.
_DISFLUENCY_TOKEN = re.compile(
    r"(?<![A-Za-z])(?:"
    r"uh|um|umm|erm|er|ah|hmm|hm|mm[\s-]?hmm|uh[\s-]?huh"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)
_DISFLUENCY_PHRASE = re.compile(
    r"(?i)\b(?:"
    r"you know|i mean|sort of|kind of|wait once again|once again(?=[\s,.]|$)|"
    r"like(?=\s+(?:i|the|a|an|this|that|we|you|it)\b)"
    r")\b[,.!?:;]*\s*"
)
_LEADING_HEDGE = re.compile(
    r"(?is)^(?:"
    r"(?:yeah|yep|yes|ok|okay|well|so|and|but|no|right)[,.\s]+"
    r")+"
)
_GREETING_ONLY_BODY = re.compile(
    r"(?is)^(?:"
    r"how(?:'s|\s+are)\s+you\b[^.!?]{0,80}|"
    r"howdy\b[^.!?]{0,40}|"
    r"hi\b[^.!?]{0,40}|"
    r"hello\b[^.!?]{0,60}|"
    r"good\s+(?:morning|afternoon|evening)\b[^.!?]{0,40}|"
    r"hey\b[^.!?]{0,40}"
    r")\.?$"
)


def _collapse_ws(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^[,.;:\s]+", "", t)
    return t


def strip_disfluencies(text: str) -> str:
    """Remove common spoken fillers so UI and models see clean English (any messy transcript)."""
    if not text:
        return ""
    t = text
    t = _DISFLUENCY_PHRASE.sub(" ", t)
    t = _DISFLUENCY_TOKEN.sub(" ", t)
    t = _LEADING_HEDGE.sub("", t)
    t = re.sub(r"\s*[,;]\s*[,;]+\s*", ", ", t)
    return _collapse_ws(t)


_WORK_SUBSTANCE = re.compile(
    r"(?i)("
    r"project|deadline|integrat\w*|apis?\w*|backend|frontend|task|assign|report|build|implement\w*|"
    r"discuss|blocker|professor|demo|ship|deploy|bug|feature|requirement|milestone|"
    r"need to|have to|will\b|'ll\b|must\b|should\b|"
    r"by (?:mon|tues|wed|thurs|fri|saturday|sunday)|"
    r"meeting|transcript|zoom|teams"
    r")"
)


def _line_is_small_talk(body: str) -> bool:
    """True for greetings / back-channel only — dropped from summary input, not stored as tasks."""
    c = strip_disfluencies(body).strip()
    if not c:
        return True
    if len(c) <= 8:
        return True
    cl = c.lower()
    if cl in ("howdy", "hey", "hi", "hello", "yeah", "yep", "okay", "ok", "thanks", "thank you"):
        return True
    if len(c) <= 96 and _GREETING_ONLY_BODY.match(c):
        return True
    if _WORK_SUBSTANCE.search(c):
        return False
    if len(c) < 36:
        return True
    return False


def build_summary_source(text: str) -> str:
    """Drop small-talk lines anywhere in the transcript for BART; keep order of substantive lines."""
    kept: List[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^([^:]+):\s*(.+)$", line)
        body = m.group(2).strip() if m else line
        if _line_is_small_talk(body):
            continue
        kept.append(line)
    if not kept:
        return "\n".join(
            strip_disfluencies(ln.strip()) for ln in text.splitlines() if ln.strip()
        )
    return "\n".join(strip_disfluencies(ln) for ln in kept)


def compress_task_description(content: str, max_chars: int = 400) -> str:
    """Turn a noisy speaker turn into a short task line for the UI (no uh/wait once again)."""
    t = strip_disfluencies(content)
    t = _LEADING_HEDGE.sub("", t)
    t = _collapse_ws(t)
    if len(t) <= max_chars:
        return t
    cut = t[: max_chars + 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",; ") + "…"


def preprocess_transcript(text: str) -> str:
    """Normalize whitespace but keep newlines so Zoom closed-caption blocks stay parseable."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_vtt_to_text(raw: str) -> str:
    """Strip WEBVTT timestamps and cues; keep spoken text."""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("WEBVTT"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        if line.startswith("NOTE"):
            continue
        lines.append(line)
    return preprocess_transcript(" ".join(lines))


def _looks_like_zoom_saved_captions(text: str) -> bool:
    return bool(re.search(r"\[[^\]]+\]\s*\d{1,2}:\d{2}:\d{2}", text))


def _format_zoom_speaker_label(label: str) -> str:
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}".strip()
    return parts[0] if parts else "Unknown"


def normalize_zoom_saved_captions(text: str) -> str:
    """Turn Zoom 'Saved closed caption' export into 'First Last: utterance' lines."""
    out: List[str] = []
    current: Optional[str] = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[([^\]]+)\]\s*(\d{1,2}:\d{2}:\d{2})\s*$", line)
        if m:
            current = _format_zoom_speaker_label(m.group(1))
            continue
        if current:
            out.append(f"{current}: {line}")
        else:
            out.append(line)
    return "\n".join(out)


def _is_filler_utterance(content: str) -> bool:
    sl = strip_disfluencies(content).lower().strip()
    if len(sl) < 14:
        return True
    filler = (
        "the thing",
        "not important",
        "too loud",
        "thank god",
        "how are you",
        "how was your",
        "did you party",
        "tea and biscuit",
        "biscuit, okay",
        "let's get back to work",
        "guys, let's get back",
        "can you guys test",
        "oh my go",
        "screen share",
        "want extra stuff",
        "just testing",
        "part of the gig",
        "black skin",
        "inhabited",
        "illegal",
        "okay, bye",
        "he can just start",
        "howdy",
        "wait once again",
    )
    if any(x in sl for x in filler):
        return True
    if re.match(
        r"^(yeah|yep|mm-hmm|uh-huh|okay|ok\.?|hmm\.?|yes\.?|no\.?|thanks\.?|thank you|got it|hello)\b",
        sl,
    ) and len(sl) < 40:
        return True
    return False


def _line_sounds_actionable(content: str) -> bool:
    cl = content.lower()
    hints = (
        "i'll ",
        "i will ",
        "we'll ",
        "i'm ",
        "i am ",
        "i've ",
        "i have ",
        "working on",
        "worked on",
        "look into",
        "looking into",
        "need to",
        "have to",
        "has to",
        "integrate",
        "integration",
        "created",
        "apis",
        "api ",
        "backend",
        "frontend",
        "by monday",
        "by tuesday",
        "by wednesday",
        "by thursday",
        "by friday",
        "report",
        "discuss",
        "meeting",
        "meet on",
        "keep a meet",
        "afternoon",
        "professor",
        "list of things",
        "done by",
        "hopefully",
        "update",
        "transcript",
        "zoom",
        "teams",
    )
    return any(h in cl for h in hints)


def _merge_weekday_lookahead(lines: List[str]) -> List[str]:
    """Attach same-speaker 'Wednesday.' lines that follow a '... done by' style utterance."""
    skip = set()
    enriched: List[str] = []
    for i, line in enumerate(lines):
        if i in skip:
            continue
        m = re.match(r"^([^:]+):\s*(.+)$", line)
        if not m:
            enriched.append(line)
            continue
        sp, cont = m.group(1), m.group(2).strip()
        merged_cont = cont
        if re.search(r"\bby\.?\s*$", cont, re.I):
            for j in range(i + 1, min(i + 10, len(lines))):
                m2 = re.match(r"^([^:]+):\s*(.+)$", lines[j])
                if not m2 or m2.group(1) != sp:
                    continue
                nxt = m2.group(2).strip()
                if re.match(
                    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\.?\s*$",
                    nxt,
                    re.I,
                ):
                    merged_cont = f"{cont} {nxt}"
                    skip.add(j)
                    break
        enriched.append(f"{sp}: {merged_cont}")
    return enriched


def _extract_action_items_from_speaker_lines(text: str) -> List[dict]:
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items: List[dict] = []
    for line in _merge_weekday_lookahead(raw_lines):
        m = re.match(r"^([^:]+):\s*(.+)$", line)
        if not m:
            continue
        speaker, content = m.group(1).strip(), m.group(2).strip()
        if _is_filler_utterance(content):
            continue
        if not _line_sounds_actionable(content):
            continue
        cl = content.lower()
        if "should" in cl and not any(
            x in cl
            for x in (
                "will",
                "'ll",
                "need to",
                "have to",
                "by ",
                "integrat",
                "report",
                "work",
                "meet",
                "finish",
                "api",
                "backend",
                "frontend",
                "zoom",
                "teams",
                "transcript",
                "discuss",
            )
        ):
            continue

        raw_deadline, iso_deadline = normalize_deadline(content)
        polished = compress_task_description(content)
        if len(polished) < 14:
            continue
        items.append(
            {
                "task_text": polished,
                "owner": speaker,
                "deadline_raw": raw_deadline,
                "deadline_iso": iso_deadline,
            }
        )
    return items


def _extract_action_items_spacy_fallback(text: str) -> List[dict]:
    doc = nlp(text)
    items: List[dict] = []
    action_triggers = (
        "will",
        "should",
        "need to",
        "must",
        "please",
        "action item",
        "todo",
        "follow up",
    )

    for sent in doc.sents:
        s = sent.text.strip()
        s_lower = s.lower()
        if any(trigger in s_lower for trigger in action_triggers):
            if _is_filler_utterance(s):
                continue
            if not _line_sounds_actionable(s):
                continue
            owner = find_owner(s, nlp(s))
            raw_deadline, iso_deadline = normalize_deadline(s)
            polished = compress_task_description(s)
            if len(polished) < 14:
                continue
            items.append(
                {
                    "task_text": polished,
                    "owner": owner,
                    "deadline_raw": raw_deadline,
                    "deadline_iso": iso_deadline,
                }
            )
    return items


def _truncate_for_bart_encoder(text: str) -> str:
    """BART max positions = 1024 tokens; long Zoom transcripts must be clipped."""
    tok = summarizer.tokenizer
    cap = getattr(tok, "model_max_length", 1024) or 1024
    cap = min(int(cap), 1024)
    max_tokens = max(128, cap - 64)
    ids = tok.encode(text, max_length=max_tokens, truncation=True, add_special_tokens=True)
    return tok.decode(ids, skip_special_tokens=True)


def generate_summary(text: str) -> str:
    body = (text or "")[:MAX_SUMMARY_INPUT_CHARS].strip()
    if not body:
        return ""
    summary_in = build_summary_source(body)
    if not summary_in.strip():
        summary_in = strip_disfluencies(body)
    short_text = _truncate_for_bart_encoder(summary_in)
    try:
        result = summarizer(
            short_text,
            max_length=160,
            min_length=40,
            do_sample=False,
            truncation=True,
        )
        out = (result[0].get("summary_text") or "").strip()
        return strip_disfluencies(out)
    except Exception:
        # If GPU/CPU still fails, return a cheap extractive fallback
        bits = re.split(r"(?<=[.!?])\s+", summary_in[:4000])
        fallback = " ".join(bits[:5]).strip()
        return strip_disfluencies(fallback) or strip_disfluencies(body[:800])


def find_owner(sentence: str, doc) -> Optional[str]:
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            return ent.text
    # fallback pattern: "X will ...", "X can ..."
    match = re.match(r"^([A-Z][a-z]+)\s+(will|can|should)\b", sentence)
    if match:
        return match.group(1)
    return None


def normalize_deadline(sentence: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract a deadline phrase and, when possible, a concrete calendar date.

    Weekday phrases (e.g. "by Monday", "on Friday afternoon") are resolved to the next
    matching date from *today* (server local time) via dateparser, so the UI shows a
    real date instead of only the day name.
    """
    s = sentence.strip()
    if not s:
        return None, None

    ref_now = datetime.now()
    dp_future = {
        "RELATIVE_BASE": ref_now,
        "PREFER_DATES_FROM": "future",
    }

    wd = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    # (pattern, kind): kind = weekday | relative | numeric | month
    candidates: list[tuple[str, str]] = [
        # "next Monday", "this Friday" (before bare weekday — longer match first)
        (
            rf"\b(?:next|this)\s+({wd})(?:\s+(?:morning|afternoon|evening|night))?\b",
            "weekday",
        ),
        # ASR glitches: "done by. Wednesday", "by. Monday"
        (rf"\bdone\s+by\.?\s*({wd})\b", "weekday"),
        (rf"\bby\.?\s+({wd})(?:\s+(?:morning|afternoon|evening|night))?\b", "weekday"),
        (rf"\bon\s+({wd})(?:\s+(?:morning|afternoon|evening))?\b", "weekday"),
        (rf"\b({wd})(?:\s+(?:morning|afternoon|evening|night))\b", "weekday"),
        (rf"\b(tomorrow|tonight|today)\b", "relative"),
        (rf"\b(next\s+week)\b", "relative"),
        (
            rf"\bby\s+(\d{{1,2}}[/\-]\d{{1,2}}(?:[/\-]\d{{2,4}})?)\b",
            "numeric",
        ),
        (
            rf"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)\b",
            "month",
        ),
    ]

    for pat, kind in candidates:
        m = re.search(pat, s, re.IGNORECASE)
        if not m:
            continue
        phrase = m.group(0).strip()

        if kind == "weekday":
            parsed = None
            for cand in (phrase, m.group(1).strip() if m.lastindex else None):
                if not cand:
                    continue
                parsed = dateparser.parse(cand, settings=dp_future)
                if parsed:
                    break
            if parsed:
                d = parsed.date()
                iso = d.isoformat()
                pretty = d.strftime("%a, %b %d, %Y")
                return pretty, iso
            return phrase, None

        if kind == "relative":
            raw_word = m.group(1)
            parsed = dateparser.parse(raw_word, settings=dp_future)
            iso = parsed.date().isoformat() if parsed else None
            if parsed:
                d = parsed.date()
                pretty = d.strftime("%a, %b %d, %Y")
                return pretty, iso
            return phrase, iso

        # numeric or full month string — safe to attach an ISO when parse works
        raw_date = m.group(1).strip()
        parsed = dateparser.parse(raw_date, settings=dp_future)
        iso = parsed.date().isoformat() if parsed else None
        if parsed:
            d = parsed.date()
            pretty = d.strftime("%a, %b %d, %Y")
            return pretty, iso
        return phrase, iso

    return None, None


def extract_action_items(text: str) -> List[dict]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        speaker_lines = sum(1 for ln in lines if re.match(r"^[^:]+:\s*\S", ln))
        if speaker_lines >= max(2, len(lines) // 4):
            return _extract_action_items_from_speaker_lines(text)
    return _extract_action_items_spacy_fallback(text)


def suggest_followups(text: str, action_items: List[dict]) -> List[str]:
    suggestions = []
    lower_text = text.lower()

    unresolved_signals = ["blocker", "pending", "not decided", "open issue", "follow up"]
    if any(sig in lower_text for sig in unresolved_signals):
        suggestions.append("Schedule a follow-up meeting to resolve pending blockers.")

    no_deadline_count = sum(1 for i in action_items if not i.get("deadline_iso"))
    if no_deadline_count > 0:
        suggestions.append(
            f"{no_deadline_count} action item(s) have no clear deadline. Plan a short alignment meeting."
        )

    if len(action_items) >= 5:
        suggestions.append("Large number of tasks detected. Add a weekly checkpoint meeting.")

    if not suggestions:
        suggestions.append("No urgent follow-up meeting required based on current transcript.")
    return suggestions


def answer_with_rules(question: str, transcript: str) -> Optional[dict]:
    q = question.lower().strip()
    doc = nlp(transcript)
    people_patterns = [
        "how many people",
        "number of people",
        "how many participants",
        "who all are working",
        "who is working",
    ]
    if any(p in q for p in people_patterns):
        people = sorted(
            {ent.text.strip() for ent in doc.ents if ent.label_ == "PERSON" and ent.text.strip()}
        )
        if people:
            return {
                "answer": f"{len(people)} people: {', '.join(people)}",
                "confidence": 1.0,
            }
        return {
            "answer": "I could not confidently identify participant names in this transcript.",
            "confidence": 0.0,
        }
    return None


GEMINI_SYSTEM_INSTRUCTION = (
    "You are MeetingMind, a meeting Q&A assistant. "
    "You MUST answer using ONLY the meeting transcript the user provides in their message. "
    "Do not use outside knowledge, general facts, or the web. "
    "If the question cannot be answered from the transcript alone (including reasonable inferences "
    "explicitly supported by the text), reply with exactly this sentence and nothing else: "
    "The transcript does not contain enough information to answer this question. "
    "Keep answers concise and factual."
)


def _gemini_error_http_detail(exc: Exception) -> tuple[int, str]:
    """Map Gemini client errors to HTTP status and a helpful message."""
    msg = str(exc)
    if (
        "403" in msg
        or "PERMISSION_DENIED" in msg
        or "permission denied" in msg.lower()
        or "not have permission" in msg.lower()
    ):
        return (
            503,
            (
                "Gemini API returned 403 PERMISSION_DENIED (your key is not allowed to call this API). "
                "Try this in order:\n"
                "1) Create a key at https://aistudio.google.com/apikey and set export GEMINI_API_KEY=...\n"
                "2) If the key is from Google Cloud Console: enable the "
                "'Generative Language API' for that project (APIs & Services → Library → "
                "Generative Language API → Enable).\n"
                "3) API key restrictions: for a local/backend server, open the key → Application restrictions → "
                "choose 'None' for testing, or 'IP addresses' and add yours. "
                "Do not use 'HTTP referrers only' for Python on localhost.\n"
                "4) Try another model: export GEMINI_MODEL=gemini-2.0-flash\n"
                f"--- API message: {msg}"
            ),
        )
    if "404" in msg or "NOT_FOUND" in msg or "not found" in msg.lower():
        return (
            503,
            (
                f"Gemini model or endpoint not found. Try GEMINI_MODEL=gemini-2.0-flash or "
                f"gemini-1.5-flash. --- {msg}"
            ),
        )
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return (
            503,
            (
                "Gemini returned 429 RESOURCE_EXHAUSTED (quota / rate limit).\n"
                "What to try:\n"
                "1) Wait a minute and retry — free tier has low per-minute caps.\n"
                "2) If you see limit: 0 for free_tier, enable billing or a paid plan in Google AI Studio / Cloud, "
                "or use a different Google account / project for the API key.\n"
                "3) Switch model in backend/.env, e.g. GEMINI_MODEL=gemini-1.5-flash or gemini-2.0-flash "
                "(see https://ai.google.dev/gemini-api/docs/models ).\n"
                "4) Reduce calls: one /analyze per transcript; avoid rapid Q&A bursts.\n"
                f"--- API message: {msg}"
            ),
        )
    return 502, f"Gemini Q&A failed: {msg}"


def _parse_retry_after_seconds(msg: str) -> Optional[float]:
    m = re.search(r"retry in ([\d.]+)\s*s", msg, re.I)
    if m:
        return min(float(m.group(1)) + 0.25, 120.0)
    return None


def _gemini_generate_content(client, *, model: str, contents: str, config) -> object:
    """Call generate_content with retries on 429 (respect server “retry in Ns” when present)."""
    last_exc: Optional[Exception] = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            last_exc = e
            err = str(e)
            if "429" not in err and "RESOURCE_EXHAUSTED" not in err:
                raise
            if attempt >= GEMINI_MAX_RETRIES - 1:
                break
            wait = _parse_retry_after_seconds(err)
            if wait is None:
                wait = min(GEMINI_RETRY_BASE_SEC * (2**attempt), 90.0)
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini retry loop exited without exception or response")


def _genai_response_text(response) -> str:
    """Extract text from google.genai GenerateContentResponse."""
    t = getattr(response, "text", None)
    if t:
        return str(t).strip()
    parts = []
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            pt = getattr(part, "text", None)
            if pt:
                parts.append(pt)
    return "\n".join(parts).strip()


def answer_with_gemini(transcript: str, question: str) -> str:
    from google import genai
    from google.genai import types

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=GEMINI_API_KEY)
    clipped = transcript[:GEMINI_MAX_TRANSCRIPT_CHARS]
    user_message = (
        "Below is the ONLY source you may use.\n\n"
        "--- MEETING TRANSCRIPT ---\n"
        f"{clipped}\n"
        "--- END TRANSCRIPT ---\n\n"
        f"Question: {question}\n\n"
        "Answer strictly from the transcript above."
    )
    response = _gemini_generate_content(
        client,
        model=GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=GEMINI_SYSTEM_INSTRUCTION,
            temperature=0.2,
            max_output_tokens=1024,
        ),
    )
    out = _genai_response_text(response)
    return out or (
        "The transcript does not contain enough information to answer this question."
    )


GEMINI_ANALYZE_SYSTEM = (
    "You are MeetingMind. You extract structured information from meeting transcripts only. "
    "Do not invent facts. Output a single JSON object only—no markdown, no code fences, no commentary."
)


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            text = text[a : b + 1]
    return json.loads(text)


def _meeting_analysis_from_parsed_json(data: dict) -> tuple[str, List[dict], List[str]]:
    """Shared post-process for Gemini/Groq JSON analyze contract."""
    summary = strip_disfluencies(str(data.get("summary") or "")).strip()
    if not summary:
        summary = "No summary could be extracted from this transcript."

    out_items: List[dict] = []
    for it in data.get("action_items") or []:
        if not isinstance(it, dict):
            continue
        task = strip_disfluencies(str(it.get("task") or "")).strip()
        if len(task) < 10:
            continue
        owner = it.get("owner")
        if owner is not None:
            owner = str(owner).strip() or None
        dh = it.get("deadline_hint")
        if dh is not None:
            dh = str(dh).strip() or None

        combined = f"{task} {dh}" if dh else task
        raw_d, iso_d = normalize_deadline(combined)
        if not raw_d and dh:
            raw_d, iso_d = normalize_deadline(dh)
        out_items.append(
            {
                "task_text": compress_task_description(task, max_chars=500),
                "owner": owner,
                "deadline_raw": raw_d,
                "deadline_iso": iso_d,
            }
        )

    followups: List[str] = []
    for x in data.get("followup_suggestions") or []:
        if isinstance(x, str):
            s = strip_disfluencies(x).strip()
            if s:
                followups.append(s[:500])
    if not followups:
        followups = ["No specific follow-up was suggested beyond the action items above."]

    return summary, out_items, followups


def _groq_error_http_detail(exc: Exception) -> tuple[int, str]:
    msg = str(exc)
    if "401" in msg or "unauthorized" in msg.lower() or "invalid api key" in msg.lower():
        return (
            503,
            "Groq API authentication failed. Check GROQ_API_KEY in backend/.env (https://console.groq.com/keys).",
        )
    if (
        "413" in msg
        or "too large" in msg.lower()
        or "reduce your message" in msg.lower()
        or ("TPM" in msg and "Limit" in msg)
    ):
        return (
            413,
            (
                "Groq rejected the request: prompt + max output tokens exceed your tier limit (often 8000 TPM on-demand). "
                "Fix: lower GROQ_MAX_TRANSCRIPT_CHARS (default 12000) and/or GROQ_MAX_TOKENS_ANALYZE (default 2048) in backend/.env, "
                "or upgrade at https://console.groq.com/settings/billing — "
                f"{msg}"
            ),
        )
    if "429" in msg or "rate" in msg.lower():
        return (
            503,
            f"Groq rate limit or quota. Wait and retry, or check https://console.groq.com/docs/rate-limits — {msg}",
        )
    return 502, f"Groq request failed: {msg}"


def _groq_chat_completion_text(
    messages: list,
    *,
    temperature: float,
    max_completion_tokens: int,
    model: Optional[str] = None,
) -> str:
    from groq import Groq

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    model_id = model or GROQ_MODEL
    client = Groq(api_key=GROQ_API_KEY)

    def _call(extra: dict) -> str:
        completion = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            top_p=GROQ_TOP_P,
            stream=False,
            **extra,
        )
        return (completion.choices[0].message.content or "").strip()

    last_exc: Optional[Exception] = None
    for attempt in range(GROQ_MAX_RETRIES):
        extras: List[dict] = [{}]
        if GROQ_REASONING_EFFORT:
            extras.insert(0, {"reasoning_effort": GROQ_REASONING_EFFORT})
        for extra in extras:
            try:
                return _call(extra)
            except TypeError as e:
                last_exc = e
                continue
            except Exception as e:
                last_exc = e
                err = str(e)
                if "429" in err or "rate" in err.lower():
                    time.sleep(min(GROQ_RETRY_BASE_SEC * (2**attempt), 60.0))
                    break
                if extra:
                    continue
                raise
        else:
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("Groq chat completion failed")


def _clip_transcript_for_groq(transcript: str, max_chars: int) -> str:
    """Fit transcript under Groq TPM limits; keep start + end (decisions often at the end)."""
    t = transcript.strip()
    if len(t) <= max_chars:
        return t
    overhead = 90
    head = max(1500, (max_chars - overhead) // 2)
    tail = max_chars - head - overhead
    if tail < 800:
        return t[:max_chars]
    return (
        t[:head]
        + "\n\n[... middle of transcript omitted to fit API size limits ...]\n\n"
        + t[-tail:]
    )


def _analyze_user_prompt(title: str, clipped: str) -> str:
    return (
        f"Meeting title: {title}\n\n"
        f"--- TRANSCRIPT ---\n{clipped}\n--- END TRANSCRIPT ---\n\n"
        "Return JSON with exactly this shape:\n"
        '{"summary": "<2–6 sentences: topics, progress, decisions. No filler words. No raw transcript dump>",\n'
        '"action_items": [\n'
        '  {"task": "<short imperative, no uh/um/like>", "owner": "<person name or null>", '
        '"deadline_hint": "<e.g. by Monday, Wednesday afternoon, or null>"}\n'
        "],\n"
        '"followup_suggestions": ["<1–4 short bullets: e.g. alignment meeting, risks, next check-in>"]\n'
        "}\n\n"
        "Rules:\n"
        "- summary: synthesize the meeting; do NOT paste opening dialogue or list speakers verbatim.\n"
        "- action_items: only real commitments, scheduled check-ins, or clear deliverables.\n"
        "- Omit status-only updates, questions to the group, and vague fragments.\n"
        "- deadline_hint only when the transcript states a time/day.\n"
        "- followup_suggestions: grounded in the transcript; empty array if none needed.\n"
        "- If there are no action items, use an empty array."
    )


def groq_analyze_meeting(title: str, transcript: str) -> tuple[str, List[dict], List[str]]:
    """Structured meeting analysis via Groq chat completions (non-streaming)."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    size_steps = (
        GROQ_MAX_TRANSCRIPT_CHARS,
        max(4000, GROQ_MAX_TRANSCRIPT_CHARS // 2),
        max(2500, GROQ_MAX_TRANSCRIPT_CHARS // 3),
    )
    tok_steps = (
        GROQ_MAX_TOKENS_ANALYZE,
        max(1024, GROQ_MAX_TOKENS_ANALYZE // 2),
        max(512, GROQ_MAX_TOKENS_ANALYZE // 4),
    )
    last_exc: Optional[Exception] = None
    for max_chars, max_tok in zip(size_steps, tok_steps):
        clipped = _clip_transcript_for_groq(transcript, max_chars)
        messages = [
            {"role": "system", "content": GEMINI_ANALYZE_SYSTEM},
            {"role": "user", "content": _analyze_user_prompt(title, clipped)},
        ]
        try:
            raw = _groq_chat_completion_text(
                messages,
                temperature=GROQ_TEMPERATURE_ANALYZE,
                max_completion_tokens=max_tok,
                model=GROQ_MODEL,
            )
        except Exception as e:
            last_exc = e
            err = str(e)
            if (max_chars, max_tok) == (size_steps[-1], tok_steps[-1]):
                break
            if any(
                x in err
                for x in (
                    "413",
                    "too large",
                    "TPM",
                    "rate_limit_exceeded",
                    "Request too large",
                )
            ):
                continue
            raise
        if not raw:
            raise RuntimeError("Empty response from Groq analyze")
        try:
            data = _parse_json_object(raw)
        except json.JSONDecodeError:
            raise
        return _meeting_analysis_from_parsed_json(data)
    if last_exc:
        raise last_exc
    raise RuntimeError("Groq analyze failed after size retries")


def answer_with_groq(transcript: str, question: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    clipped = _clip_transcript_for_groq(transcript, GROQ_MAX_QA_TRANSCRIPT_CHARS)
    qa_model = GROQ_QA_MODEL or GROQ_MODEL
    messages = [
        {"role": "system", "content": GEMINI_SYSTEM_INSTRUCTION},
        {
            "role": "user",
            "content": (
                "Below is the ONLY source you may use.\n\n"
                "--- MEETING TRANSCRIPT ---\n"
                f"{clipped}\n"
                "--- END TRANSCRIPT ---\n\n"
                f"Question: {question}\n\n"
                "Answer strictly from the transcript above."
            ),
        },
    ]
    out = _groq_chat_completion_text(
        messages,
        temperature=GROQ_TEMPERATURE_QA,
        max_completion_tokens=GROQ_MAX_TOKENS_QA,
        model=qa_model,
    )
    return out or (
        "The transcript does not contain enough information to answer this question."
    )


def gemini_analyze_meeting(title: str, transcript: str) -> tuple[str, List[dict], List[str]]:
    """
    Generative NLP for /analyze: summary, action items, and follow-up suggestions (all from Gemini).
    """
    from google import genai
    from google.genai import types

    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    clipped = transcript[:GEMINI_MAX_TRANSCRIPT_CHARS]
    user_message = _analyze_user_prompt(title, clipped)

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = _gemini_generate_content(
        client,
        model=GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=GEMINI_ANALYZE_SYSTEM,
            temperature=0.15,
            max_output_tokens=4096,
        ),
    )
    raw = _genai_response_text(response)
    if not raw:
        raise RuntimeError("Empty response from Gemini analyze")

    data = _parse_json_object(raw)
    return _meeting_analysis_from_parsed_json(data)


def persist_meeting(
    meeting_id: str,
    title: str,
    cleaned: str,
    summary: str,
    followups: List[str],
    action_items: List[dict],
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meetings (id, title, transcript, summary, followup_suggestions)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (meeting_id, title, cleaned, summary, json.dumps(followups)),
            )
            for item in action_items:
                aid = str(uuid.uuid4())
                item["id"] = aid
                item["completed"] = False
                item["completed_at"] = None
                cur.execute(
                    """
                    INSERT INTO action_items (
                        id, meeting_id, task_text, owner, deadline_raw, deadline_iso, completed, completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE, NULL)
                    """,
                    (
                        aid,
                        meeting_id,
                        item["task_text"],
                        item["owner"],
                        item["deadline_raw"],
                        item["deadline_iso"],
                    ),
                )
        conn.commit()


def run_analyze(title: str, raw_transcript: str) -> dict:
    # Persist the full normalized upload (whitespace only). LLM/spaCy use a Zoom-normalized
    # copy when applicable so analysis matches speaker turns without dropping content.
    stored_transcript = preprocess_transcript(raw_transcript)
    if not stored_transcript:
        raise HTTPException(status_code=400, detail="Transcript cannot be empty.")
    cleaned = (
        normalize_zoom_saved_captions(stored_transcript)
        if _looks_like_zoom_saved_captions(stored_transcript)
        else stored_transcript
    )

    spacy_slice = cleaned[:MAX_SPACY_INPUT_CHARS]

    use_groq = bool(GROQ_API_KEY and GROQ_ANALYZE)
    use_gemini = bool(GEMINI_API_KEY and GEMINI_ANALYZE) and not use_groq

    if use_groq:
        try:
            summary, action_items, followups = groq_analyze_meeting(title, cleaned)
            analyze_backend = "groq"
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Groq returned invalid JSON for analysis. Try again or switch GROQ_MODEL. ({e})",
            ) from e
        except Exception as e:
            status, detail = _groq_error_http_detail(e)
            raise HTTPException(status_code=status, detail=detail) from e
    elif use_gemini:
        try:
            summary, action_items, followups = gemini_analyze_meeting(title, cleaned)
            analyze_backend = "gemini"
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Gemini returned invalid JSON for analysis. Try again or switch GEMINI_MODEL. ({e})",
            ) from e
        except Exception as e:
            status, detail = _gemini_error_http_detail(e)
            raise HTTPException(status_code=status, detail=detail) from e
    else:
        # Local: BART + heuristics (no Groq/Gemini analyze, or no API keys)
        summary = generate_summary(cleaned)
        action_items = extract_action_items(spacy_slice)
        followups = suggest_followups(spacy_slice, action_items)
        analyze_backend = "local_fallback"

    meeting_id = str(uuid.uuid4())
    persist_meeting(meeting_id, title, stored_transcript, summary, followups, action_items)

    return {
        "meeting_id": meeting_id,
        "title": title,
        "summary": summary,
        "action_items": action_items,
        "followup_suggestions": followups,
        "analyze_backend": analyze_backend,
        "transcript": stored_transcript,
    }


# --------- API routes ---------
@app.get("/")
def root():
    return {
        "service": "MeetingMind API",
        "docs": "/docs",
        "health": "/health",
        "calendar_oauth_callback": "/calendar/oauth/callback (also /api/calendar/oauth/callback)",
    }


@app.get("/health")
def health():
    groq_on = bool(GROQ_API_KEY and GROQ_ANALYZE)
    gem_on = bool(GEMINI_API_KEY and GEMINI_ANALYZE) and not groq_on
    hybrid = groq_on or gem_on
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "pipeline": "hybrid" if hybrid else "local",
        "groq_configured": bool(GROQ_API_KEY),
        "groq_model": GROQ_MODEL if GROQ_API_KEY else None,
        "groq_qa_model": (GROQ_QA_MODEL or GROQ_MODEL) if GROQ_API_KEY else None,
        "groq_max_qa_transcript_chars": GROQ_MAX_QA_TRANSCRIPT_CHARS if GROQ_API_KEY else None,
        "groq_analyze_enabled": groq_on,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL if GEMINI_API_KEY else None,
        "gemini_analyze_enabled": gem_on,
        "analyze_path": (
            "groq (summary, action items, deadlines, follow-ups)"
            if groq_on
            else (
                "gemini (summary, action items, deadlines, follow-ups)"
                if gem_on
                else (
                    "local or disabled — set GROQ_API_KEY (or GEMINI_API_KEY)"
                    if not ALLOW_LOCAL_ANALYZE_WITHOUT_LLM
                    else "local allowed without LLM API key (dev)"
                )
            )
        ),
        "local_nlp_loaded": (
            "bart-large-cnn, spaCy en_core_web_sm, distilbert-base QA (Q&A, optional ablation / dev)"
        ),
        "hybrid_description": (
            "Hybrid for reports: Groq or Gemini for /analyze + Q&A when configured; "
            "local BART/spaCy/DistilBERT always loaded."
            if hybrid
            else "Add GROQ_API_KEY (recommended) or GEMINI_API_KEY for LLM meeting analysis."
        ),
        "google_calendar_oauth_configured": google_calendar.oauth_configured(),
        "google_calendar_connected": bool(get_google_calendar_refresh_token()),
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    return run_analyze(req.title, req.transcript)


@app.post("/analyze/upload")
async def analyze_upload(
    file: UploadFile = File(...),
    title: str = Form(default="Uploaded meeting"),
):
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB).")

    name = (file.filename or "").lower()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text.")

    if name.endswith(".vtt"):
        transcript_text = parse_vtt_to_text(text)
    elif name.endswith(".txt") or "." not in name:
        transcript_text = text
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Use .txt or .vtt (Zoom transcript).",
        )

    return run_analyze(title.strip() or "Uploaded meeting", transcript_text)


@app.post("/ask")
def ask(req: AskRequest):
    if GROQ_API_KEY:
        qa_backend = "groq"
    elif GEMINI_API_KEY:
        qa_backend = "gemini"
    else:
        qa_backend = "extractive"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT transcript FROM meetings WHERE id = %s", (req.meeting_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Meeting not found.")

            full_transcript = row["transcript"]

            if GROQ_API_KEY:
                try:
                    answer = answer_with_groq(full_transcript, req.question)
                    score = 1.0
                except Exception as e:
                    status, detail = _groq_error_http_detail(e)
                    raise HTTPException(status_code=status, detail=detail) from e
            elif GEMINI_API_KEY:
                try:
                    answer = answer_with_gemini(full_transcript, req.question)
                    score = 1.0
                except Exception as e:
                    status, detail = _gemini_error_http_detail(e)
                    raise HTTPException(status_code=status, detail=detail) from e
            else:
                transcript = full_transcript[:MAX_QA_CONTEXT_CHARS]
                rule_answer = answer_with_rules(req.question, transcript)
                if rule_answer:
                    answer = rule_answer["answer"]
                    score = float(rule_answer["confidence"])
                else:
                    qa = qa_model(question=req.question, context=transcript)
                    answer = qa["answer"]
                    score = float(qa["score"])
                    if score < MIN_QA_CONFIDENCE:
                        answer = (
                            "I am not confident enough to answer from this transcript. "
                            "Please rephrase the question or provide a more specific one."
                        )

            cur.execute(
                """
                INSERT INTO qa_history (id, meeting_id, question, answer, score)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), req.meeting_id, req.question, answer, score),
            )
        conn.commit()

    return {
        "question": req.question,
        "answer": answer,
        "confidence": score,
        "qa_backend": qa_backend,
    }


@app.get("/meetings")
def list_meetings(limit: int = 50):
    """Past meetings with open/total task counts for the history sidebar."""
    limit = min(max(limit, 1), 100)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.title, m.created_at,
                  COALESCE(
                    COUNT(ai.id) FILTER (WHERE NOT COALESCE(ai.completed, false)), 0
                  )::int AS open_tasks,
                  COALESCE(COUNT(ai.id), 0)::int AS total_tasks
                FROM meetings m
                LEFT JOIN action_items ai ON ai.meeting_id = m.id
                GROUP BY m.id
                ORDER BY m.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    meetings = []
    for row in rows:
        r = dict(row)
        r["created_at"] = _datetime_as_utc(r.get("created_at"))
        meetings.append(r)
    return {"meetings": meetings}


@app.patch("/action-items/{action_id}")
def patch_action_item_completed(action_id: str, body: ActionItemCompletedRequest):
    """Mark an action item done or not done (persists for past meetings)."""
    completed_at: Optional[datetime] = datetime.utcnow() if body.completed else None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE action_items
                SET completed = %s, completed_at = %s
                WHERE id = %s
                RETURNING id, meeting_id, task_text, owner, deadline_raw, deadline_iso, completed, completed_at
                """,
                (body.completed, completed_at, action_id),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Action item not found.")
    return {"action_item": dict(row)}


@app.delete("/meeting/{meeting_id}")
def delete_meeting(meeting_id: str):
    """Remove a meeting and cascaded action items / Q&A history."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM meetings WHERE id = %s RETURNING id", (meeting_id,))
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return {"deleted": True, "meeting_id": meeting_id}


@app.get("/meeting/{meeting_id}")
def get_meeting(meeting_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, transcript, summary, followup_suggestions, created_at,
                  google_calendar_event_id, google_calendar_html_link
                FROM meetings
                WHERE id = %s
                """,
                (meeting_id,),
            )
            meeting = cur.fetchone()
            if not meeting:
                raise HTTPException(status_code=404, detail="Meeting not found.")

            cur.execute(
                """
                SELECT id, task_text, owner, deadline_raw, deadline_iso, completed, completed_at
                FROM action_items
                WHERE meeting_id = %s
                ORDER BY id
                """,
                (meeting_id,),
            )
            actions = cur.fetchall()

    md = dict(meeting)
    md["created_at"] = _datetime_as_utc(md.get("created_at"))
    action_rows = []
    for a in actions:
        ad = dict(a)
        ad["completed_at"] = _datetime_as_utc(ad.get("completed_at"))
        action_rows.append(ad)

    return {"meeting": md, "action_items": action_rows}


def _calendar_parse_start(start_iso: Optional[str], tz_name: str) -> datetime:
    tz_name = (tz_name or "UTC").strip() or "UTC"
    if start_iso and str(start_iso).strip():
        dt = dateparser.parse(
            str(start_iso).strip(),
            settings={
                "TIMEZONE": tz_name,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        if not dt:
            raise HTTPException(status_code=400, detail="Could not parse start time.")
        return dt
    return datetime.now(timezone.utc) + timedelta(hours=1)


calendar_router = APIRouter(prefix="/calendar", tags=["calendar"])


@calendar_router.get("/status")
def calendar_status():
    return {
        "oauth_configured": google_calendar.oauth_configured(),
        "connected": bool(get_google_calendar_refresh_token()),
        # Paste this exact string into Google Cloud → Credentials → OAuth client → Authorized redirect URIs
        "oauth_redirect_uri": google_calendar.get_redirect_uri(),
    }


@calendar_router.get("/oauth/url")
def calendar_oauth_url():
    if not google_calendar.oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Calendar OAuth is not configured (client id, secret, redirect URI).",
        )
    try:
        url = google_calendar.authorization_url()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"url": url}


@calendar_router.get("/oauth/callback")
def calendar_oauth_callback(code: Optional[str] = None, error: Optional[str] = None):
    frontend = os.getenv(
        "GOOGLE_CALENDAR_FRONTEND_URL",
        "http://127.0.0.1:5173",
    ).rstrip("/")
    if error:
        return RedirectResponse(
            url=f"{frontend}/?calendar_error={quote(error)}",
            status_code=302,
        )
    if not code:
        return RedirectResponse(
            url=f"{frontend}/?calendar_error=missing_code",
            status_code=302,
        )
    if not google_calendar.oauth_configured():
        return RedirectResponse(
            url=f"{frontend}/?calendar_error=not_configured",
            status_code=302,
        )
    try:
        creds = google_calendar.exchange_code(code)
        save_google_calendar_refresh_token(creds.refresh_token)
    except Exception as e:
        return RedirectResponse(
            url=f"{frontend}/?calendar_error={quote(str(e))}",
            status_code=302,
        )
    return RedirectResponse(url=f"{frontend}/?calendar=connected", status_code=302)


@calendar_router.delete("/oauth")
def calendar_oauth_disconnect():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app_oauth WHERE provider = %s",
                ("google_calendar",),
            )
        conn.commit()
    return {"disconnected": True}


@calendar_router.get("/events")
def calendar_events(max_results: int = 25):
    token = get_google_calendar_refresh_token()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Google Calendar is not connected. Open /calendar/oauth/url and complete OAuth.",
        )
    try:
        data = google_calendar.list_now_and_upcoming(token, max_results=max_results)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Google Calendar API error: {e}",
        ) from e
    return data


@calendar_router.post("/events")
def calendar_create_event(body: CreateCalendarEventRequest):
    if not google_calendar.oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Calendar OAuth is not configured.",
        )
    token = get_google_calendar_refresh_token()
    if not token:
        raise HTTPException(status_code=401, detail="Google Calendar is not connected.")

    duration = min(max(int(body.duration_minutes), 5), 24 * 60)
    start = _calendar_parse_start(body.start_iso, body.timezone)
    end = start + timedelta(minutes=duration)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, summary FROM meetings WHERE id = %s",
                (body.meeting_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Meeting not found.")

    title = (body.title or row["title"] or "Meeting").strip()
    description = (body.description or row["summary"] or "").strip()

    try:
        created = google_calendar.insert_calendar_event(
            token,
            summary=title,
            description=description,
            start=start,
            end=end,
            timezone_name=body.timezone.strip() or "UTC",
            add_meet_link=body.add_meet_link,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    event_id = created.get("id")
    html_link = created.get("html_link")
    if event_id:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE meetings
                    SET google_calendar_event_id = %s,
                        google_calendar_html_link = %s
                    WHERE id = %s
                    """,
                    (event_id, html_link, body.meeting_id),
                )
            conn.commit()

    return {
        "event_id": event_id,
        "html_link": html_link,
        "hangout_link": created.get("hangout_link"),
    }


app.include_router(calendar_router)
app.include_router(calendar_router, prefix="/api")
