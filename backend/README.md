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
# Create backend/.env with API keys as needed (see README env vars; .env is gitignored)
uvicorn main:app --reload --port 8000
```

## Health check

`GET /health` — includes `gemini_analyze_enabled`, `gemini_configured`, and `local_nlp` description.

## Google Calendar (optional)

In [Google Cloud Console](https://console.cloud.google.com/), enable **Google Calendar API**, create OAuth **Web client** credentials, and set **Authorized redirect URI** to match `GOOGLE_CALENDAR_REDIRECT_URI` in `.env` (e.g. `http://127.0.0.1:8000/calendar/oauth/callback`). Put **`GOOGLE_CALENDAR_CLIENT_ID`** and **`GOOGLE_CALENDAR_CLIENT_SECRET`** from the console into `backend/.env`. After connecting in the UI, the sidebar shows the **current** calendar event (refreshed every minute) and can **create** an event linked to the open meeting.

If Google shows **Error 400: redirect_uri_mismatch**, the value in **`GOOGLE_CALENDAR_REDIRECT_URI`** must appear **character-for-character** under **Authorized redirect URIs** for that OAuth client (including `localhost` vs `127.0.0.1` — they are different to Google). You can add both URLs in the console, or use one everywhere. Call **`GET /calendar/status`** and copy **`oauth_redirect_uri`** into the console.

If the browser shows **`invalid_grant` / Missing code verifier**, the backend disables PKCE for the web client flow (see `google_calendar.make_flow`) so the callback can exchange the code using the client secret alone.
