import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

import dateparser
import psycopg2
import spacy
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from transformers import pipeline

# Load variables from backend/.env (create it from .env.example; .env is gitignored)
load_dotenv(Path(__file__).resolve().parent / ".env")


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

# Gemini Q&A: set GEMINI_API_KEY in backend/.env or in the shell environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None
# Use a model id your API key supports (see https://ai.google.dev/gemini-api/docs/models )
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-001")
GEMINI_MAX_TRANSCRIPT_CHARS = int(os.getenv("GEMINI_MAX_TRANSCRIPT_CHARS", "100000"))
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


# --------- DB helpers ---------
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


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
        deadline_iso TEXT
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


@app.on_event("startup")
def on_startup():
    init_db()


# --------- NLP helpers ---------
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
    sl = content.lower().strip()
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
        items.append(
            {
                "task_text": f"{speaker}: {content}",
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
            items.append(
                {
                    "task_text": s,
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
    short_text = _truncate_for_bart_encoder(body)
    try:
        result = summarizer(
            short_text,
            max_length=160,
            min_length=30,
            do_sample=False,
            truncation=True,
        )
        return (result[0].get("summary_text") or "").strip()
    except Exception:
        # If GPU/CPU still fails, return a cheap extractive fallback
        bits = re.split(r"(?<=[.!?])\s+", body[:4000])
        return " ".join(bits[:5]).strip() or body[:800]


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
    date_patterns = [
        r"\bby\s+([A-Za-z0-9,\s]+)",
        r"\bbefore\s+([A-Za-z0-9,\s]+)",
        r"\bon\s+([A-Za-z0-9,\s]+)",
        r"\bnext\s+[A-Za-z]+\b",
        r"\bthis\s+[A-Za-z]+\b",
        r"\btomorrow\b",
        r"\bfriday\b|\bmonday\b|\btuesday\b|\bwednesday\b|\bthursday\b|\bsaturday\b|\bsunday\b",
    ]
    for pattern in date_patterns:
        m = re.search(pattern, sentence, flags=re.IGNORECASE)
        if m:
            raw_date = m.group(1).strip() if m.groups() else m.group(0).strip()
            parsed = dateparser.parse(raw_date)
            iso_date = parsed.date().isoformat() if parsed else None
            return raw_date, iso_date
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
        return 503, f"Gemini quota or rate limit: {msg}"
    return 502, f"Gemini Q&A failed: {msg}"


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
    response = client.models.generate_content(
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
                cur.execute(
                    """
                    INSERT INTO action_items (id, meeting_id, task_text, owner, deadline_raw, deadline_iso)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        meeting_id,
                        item["task_text"],
                        item["owner"],
                        item["deadline_raw"],
                        item["deadline_iso"],
                    ),
                )
        conn.commit()


def run_analyze(title: str, raw_transcript: str) -> dict:
    cleaned = preprocess_transcript(raw_transcript)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Transcript cannot be empty.")
    if _looks_like_zoom_saved_captions(cleaned):
        cleaned = normalize_zoom_saved_captions(cleaned)

    summary = generate_summary(cleaned)
    spacy_slice = cleaned[:MAX_SPACY_INPUT_CHARS]
    action_items = extract_action_items(spacy_slice)
    followups = suggest_followups(spacy_slice, action_items)
    meeting_id = str(uuid.uuid4())
    persist_meeting(meeting_id, title, cleaned, summary, followups, action_items)

    return {
        "meeting_id": meeting_id,
        "title": title,
        "summary": summary,
        "action_items": action_items,
        "followup_suggestions": followups,
    }


# --------- API routes ---------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL if GEMINI_API_KEY else None,
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
    qa_backend = "gemini" if GEMINI_API_KEY else "extractive"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT transcript FROM meetings WHERE id = %s", (req.meeting_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Meeting not found.")

            full_transcript = row["transcript"]

            if GEMINI_API_KEY:
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


@app.get("/meeting/{meeting_id}")
def get_meeting(meeting_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, summary, followup_suggestions, created_at
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
                SELECT task_text, owner, deadline_raw, deadline_iso
                FROM action_items
                WHERE meeting_id = %s
                """,
                (meeting_id,),
            )
            actions = cur.fetchall()

    return {"meeting": meeting, "action_items": actions}
