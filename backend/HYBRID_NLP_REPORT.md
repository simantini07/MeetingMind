# Hybrid NLP stack — text for reports & presentations

You can copy the sections below into your report, slides, or README.

## Short summary (abstract-style)

**MeetingMind** uses a **hybrid NLP architecture** for coursework and documentation: **Groq** (OpenAI-compatible chat API; default model e.g. `openai/gpt-oss-120b`) or optionally **Google Gemini** performs **end-to-end meeting analysis**—abstractive summary, structured action items with deadlines, and follow-up suggestions—from noisy transcripts. In parallel, the system **loads established local models**—**BART-large-CNN**, **spaCy** (`en_core_web_sm`), and **DistilBERT** (SQuAD)—for **extractive question answering**, entity-aware heuristics, and optional **ablation / offline** runs (`GROQ_ANALYZE=0` / `GEMINI_ANALYZE=0` or `ALLOW_LOCAL_ANALYZE_WITHOUT_LLM=1`). This is a deliberate **hybrid**: hosted generative NLP for structured extraction, plus reproducible open-model components for QA and reporting.

## One paragraph (methods section)

**Preprocessing** normalizes whitespace and optional Zoom closed-caption formats. The primary **`/analyze` path** uses **Groq** when `GROQ_API_KEY` is set (else **Gemini**), with the same strict JSON contract: `summary`, `action_items` (task, owner, deadline hint), and `followup_suggestions`, grounded in the transcript only. **BART**, **spaCy**, and rule-based extractors remain for **development without an LLM key**, **ablation**, and **extractive Q&A** when Groq/Gemini is not used for answering. **PostgreSQL** stores meetings, action rows, and Q&A history.

## Bullet list (components)

| Component | Role |
|-----------|------|
| **Groq** (API) | Primary `/analyze` + chat Q&A when `GROQ_API_KEY` is set |
| **Gemini** (API) | Same contract when Groq is unset and `GEMINI_API_KEY` is set |
| **BART-large-CNN** | Optional local summarization (ablation / dev without LLM key) |
| **spaCy** `en_core_web_sm` | NER, segmentation, heuristic action path when not using Gemini analyze |
| **DistilBERT** (SQuAD) | Extractive Q&A over transcript when Gemini Q&A is not used |
| **PostgreSQL** | Persistence of meetings, action rows, Q&A history |

## Configuration (reproducibility)

- Create `backend/.env` (gitignored) for local secrets.
- Set **`GROQ_API_KEY`** for default LLM `/analyze` + Q&A.
- **`GEMINI_API_KEY`**: optional fallback if Groq is not configured.
- **`GROQ_ANALYZE=0` / `GEMINI_ANALYZE=0`**: force local BART + heuristics for `/analyze`.
- **`ALLOW_LOCAL_ANALYZE_WITHOUT_LLM=1`**: allow `/analyze` without any LLM key (offline only).

## Terminology

- **Hybrid NLP**: Multiple model families in one system (local transformers + spaCy + hosted LLM), not a single fused checkpoint.
- **Gemini** is standard **NLP** (neural language modeling); pairing it with BART/spaCy/DistilBERT is accurate for a “hybrid stack” narrative.
