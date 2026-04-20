# MeetingMind

MeetingMind converts meeting transcripts into actionable outputs:
- concise summary
- action items with owners/deadlines
- follow-up suggestions
- searchable meeting history
- Q&A over stored transcripts
- Google Calendar integration

The app is split into:
- `backend/` — FastAPI, PostgreSQL (psycopg2), Hugging Face **Transformers** (BART-large-CNN summarization, DistilBERT extractive Q&A), **spaCy**, Groq / Google GenAI when configured, plus Google Calendar OAuth helpers
- `frontend/` — React + Vite dashboard UI

## What the app does

### Core features
- Analyze pasted text transcripts or uploaded `.txt` / `.vtt` files
- Generate summary + tasks + follow-ups
- Save meetings and action items in Postgres
- Re-open any past meeting from history
- Ask questions against transcript context (`/ask`)
- Mark action items complete/incomplete
- Create and link Google Calendar events

### Output flow (high-level)
1. User submits transcript (paste or upload).
2. Backend preprocesses text and routes analysis:
   - **Groq** when a Groq API key is set and Groq analyze is enabled (default).
   - Else **Gemini** when a Gemini API key is set and Gemini analyze is enabled.
   - Else **local analyze**: **facebook/bart-large-cnn** (summarization), **spaCy** `en_core_web_sm` (action-item heuristics), and rule-based follow-ups whenever neither Groq nor Gemini is used for `/analyze`.
3. **Q&A** (`/ask`): Groq or Gemini when configured; otherwise **DistilBERT** (extractive question answering over the transcript).
4. Backend returns summary, action items, and follow-ups, persists to Postgres, and the frontend renders results, history, tasks, and Q&A.

## Tech stack

- **Backend:** FastAPI, psycopg2, spaCy, Hugging Face Transformers (**BART-large-CNN** for summarization, **DistilBERT** for extractive Q&A), Groq SDK, Google GenAI SDK
- **Frontend:** React, Vite, Axios, Framer Motion, Tailwind (via Vite plugin)
- **Database:** PostgreSQL

## Prerequisites

- Python 3.10+ (recommended 3.11/3.12)
- Node.js 18+ and npm
- PostgreSQL running locally (or reachable via `DATABASE_URL`)

## Quick start (local dev)

### 1) Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

Create `backend/.env`:

```env
# Required
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/meetingmind

# Recommended (for best analyze quality)
GROQ_API_KEY=your_key_here

# Gemini (if Groq is not used)
# GEMINI_API_KEY=your_key_here

# Behavior flags
# GROQ_ANALYZE=1
# GEMINI_ANALYZE=1
# ALLOW_LOCAL_ANALYZE_WITHOUT_LLM=0
```

Run backend:

```bash
uvicorn main:app --reload --port 8000
```

API docs: `http://127.0.0.1:8000/docs`

### 2) Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://127.0.0.1:5173` (Vite default in this project).
In dev, frontend proxy maps `/api/*` to backend `http://127.0.0.1:8000/*`.

## How to use the app

1. Open the frontend.
2. Enter a meeting title.
3. Paste transcript text or upload `.txt`/`.vtt`.
4. Click analyze.
5. Review summary, tasks, follow-ups.
6. Toggle task completion status.
7. Ask follow-up questions in Q&A panel.
8. Reopen older meetings from history.
9. Use the **Calendar** panel: connect your Google account once, then create an event for the open meeting—the form is **prefilled** from the meeting title, summary, and action-item deadlines (you can edit start time, description, timezone, or add a Meet link before saving).

## Application images

Screenshots in **`docs/images/`**:

| File | What it shows |
|------|----------------|
| `MeetingMindPage.png` | Main dashboard: upload / paste transcript and analyze |
| `ResultsPage.png` | Results: summary, tags, action items, follow-ups |
| `QA_Calendar.png` | Q&A panel and Calendar panel |

![MeetingMind main page](docs/images/MeetingMindPage.png)

![Results page](docs/images/ResultsPage.png)

![Q&A and Calendar](docs/images/QA_Calendar.png)

## Backend endpoints (main)

- `GET /health` — runtime/config status
- `POST /analyze` — analyze pasted transcript JSON
- `POST /analyze/upload` — analyze uploaded file
- `POST /ask` — Q&A for a saved meeting
- `GET /meetings` — list recent meetings
- `GET /meeting/{meeting_id}` — load one meeting + action items
- `PATCH /action-items/{action_id}` — mark completed/uncompleted
- `DELETE /meeting/{meeting_id}` — delete meeting and related data

## Google Calendar

Configure calendar linking:
- set `GOOGLE_CALENDAR_CLIENT_ID`
- set `GOOGLE_CALENDAR_CLIENT_SECRET`
- set `GOOGLE_CALENDAR_REDIRECT_URI`
- ensure redirect URI exactly matches Google Cloud Console OAuth settings

Once connected in the UI, you can create a calendar event from meeting context.

## Project structure

```text
MeetingMind/
  backend/
    main.py
    google_calendar.py
    requirements.txt
  docs/
    images/
  frontend/
    src/
    package.json
```
