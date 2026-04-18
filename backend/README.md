# MeetingMind — backend API

FastAPI service: transcript upload/paste → summary, action items, follow-ups, Q&A.

## Hybrid NLP (report) / Gemini analyze (runtime)

With **`GROQ_API_KEY`** and **`GROQ_ANALYZE=1`** (default), **`/analyze` uses Groq** (chat completions, non-streaming): summary, action items, deadlines, follow-ups. If Groq is unset, **`GEMINI_API_KEY`** is used the same way.

**BART**, **spaCy**, and **DistilBERT** stay loaded for **extractive Q&A**, heuristics, and **ablation** (`GROQ_ANALYZE=0` / `GEMINI_ANALYZE=0` or `ALLOW_LOCAL_ANALYZE_WITHOUT_LLM=1`).

Longer **report-ready wording**: see [`HYBRID_NLP_REPORT.md`](./HYBRID_NLP_REPORT.md).

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
cp .env.example .env   # add GEMINI_API_KEY for hybrid analyze
uvicorn main:app --reload --port 8000
```

## Health check

`GET /health` — includes `gemini_analyze_enabled`, `gemini_configured`, and `local_nlp` description.
