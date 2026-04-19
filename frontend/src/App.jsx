import { motion } from 'framer-motion'
import { useState, useCallback, useEffect } from 'react'
import {
  analyzeJson,
  analyzeUpload,
  askQuestion,
  getMeeting,
  listMeetings,
  patchActionItemCompleted,
  deleteMeeting as deleteMeetingApi,
} from './api'
import BubbleBackground from './components/BubbleBackground'
import CalendarPanel from './components/CalendarPanel'
import MeetingHistory from './components/MeetingHistory'
import QAPanel from './components/QAPanel'
import ResultsBoard from './components/ResultsBoard'
import UploadPanel from './components/UploadPanel'

function mapActionItems(items) {
  return (items || []).map((a) => ({
    id: a.id,
    task_text: a.task_text,
    owner: a.owner,
    deadline_raw: a.deadline_raw,
    deadline_iso: a.deadline_iso,
    completed: !!a.completed,
    completed_at: a.completed_at,
  }))
}

/** Normalize GET /meeting/:id or POST /analyze response into one result shape. */
function payloadToResult(payload) {
  if (payload.meeting) {
    const m = payload.meeting
    const followups = m.followup_suggestions
    return {
      meeting_id: m.id,
      title: m.title,
      summary: m.summary ?? '',
      transcript: m.transcript ?? '',
      followup_suggestions: Array.isArray(followups) ? followups : [],
      action_items: mapActionItems(payload.action_items),
      analyze_backend: 'saved',
      google_calendar_event_id: m.google_calendar_event_id ?? null,
      google_calendar_html_link: m.google_calendar_html_link ?? null,
    }
  }
  const fu = payload.followup_suggestions
  return {
    meeting_id: payload.meeting_id,
    title: payload.title,
    summary: payload.summary ?? '',
    transcript: payload.transcript ?? '',
    followup_suggestions: Array.isArray(fu) ? fu : [],
    action_items: mapActionItems(payload.action_items),
    analyze_backend: payload.analyze_backend,
    google_calendar_event_id: null,
    google_calendar_html_link: null,
  }
}

export default function App() {
  const [title, setTitle] = useState('My meeting')
  const [transcript, setTranscript] = useState('')
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('paste')
  const [loading, setLoading] = useState(false)
  const [qaLoading, setQaLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [messages, setMessages] = useState([])
  const [meetings, setMeetings] = useState([])
  const [meetingsLoading, setMeetingsLoading] = useState(true)
  const [calendarAuthVersion, setCalendarAuthVersion] = useState(0)

  const refreshMeetings = useCallback(async () => {
    setMeetingsLoading(true)
    try {
      const data = await listMeetings(50)
      setMeetings(data.meetings || [])
    } catch {
      setMeetings([])
    } finally {
      setMeetingsLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshMeetings()
  }, [refreshMeetings])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const cal = params.get('calendar')
    const err = params.get('calendar_error')
    if (!cal && !err) return
    const path = window.location.pathname
    if (cal === 'connected') params.delete('calendar')
    if (err) params.delete('calendar_error')
    const qs = params.toString()
    window.history.replaceState({}, '', `${path}${qs ? `?${qs}` : ''}`)
    if (cal === 'connected') {
      setCalendarAuthVersion((v) => v + 1)
    }
    if (err) {
      alert(decodeURIComponent(err))
    }
  }, [])

  const openMeeting = useCallback(async (meetingId) => {
    setMessages([])
    try {
      const data = await getMeeting(meetingId)
      setResult(payloadToResult(data))
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Could not load meeting'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }, [])

  const deleteMeeting = useCallback(
    async (meetingId) => {
      if (!window.confirm('Delete this meeting, all tasks, and Q&A history for it?')) return
      try {
        await deleteMeetingApi(meetingId)
        if (result?.meeting_id === meetingId) {
          setResult(null)
          setMessages([])
        }
        refreshMeetings()
      } catch (e) {
        const msg = e.response?.data?.detail || e.message || 'Delete failed'
        alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
      }
    },
    [result?.meeting_id, refreshMeetings],
  )

  const toggleTaskComplete = useCallback(async (actionId, completed) => {
    try {
      const { action_item } = await patchActionItemCompleted(actionId, completed)
      setResult((r) => {
        if (!r?.action_items) return r
        return {
          ...r,
          action_items: r.action_items.map((it) =>
            it.id === actionId
              ? {
                  ...it,
                  completed: action_item.completed,
                  completed_at: action_item.completed_at,
                }
              : it,
          ),
        }
      })
      refreshMeetings()
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Update failed'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }, [refreshMeetings])

  const onCalendarLinked = useCallback((fields) => {
    setResult((r) => (r ? { ...r, ...fields } : r))
  }, [])

  const runAnalyze = useCallback(async () => {
    if (mode === 'paste' && !transcript.trim()) {
      alert('Paste a transcript first.')
      return
    }
    if (mode === 'file' && !file) {
      alert('Choose a .txt or .vtt file.')
      return
    }
    setLoading(true)
    setMessages([])
    try {
      let data
      if (mode === 'paste') {
        data = await analyzeJson({ title, transcript })
      } else {
        data = await analyzeUpload({ file, title })
      }
      setResult(payloadToResult(data))
      refreshMeetings()
    } catch (e) {
      const msg =
        e.response?.data?.detail ||
        e.message ||
        'Request failed. Is the API running on port 8000?'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setLoading(false)
    }
  }, [mode, title, transcript, file, refreshMeetings])

  const onSubmitPaste = () => runAnalyze()
  const onSubmitFile = () => runAnalyze()

  const onAsk = async (question) => {
    if (!result?.meeting_id) return
    setMessages((m) => [...m, { role: 'user', text: question }])
    setQaLoading(true)
    const started = performance.now()
    try {
      const data = await askQuestion({ meetingId: result.meeting_id, question })
      const latencyMs = Math.round(performance.now() - started)
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          text: data.answer,
          confidence: data.confidence,
          latencyMs,
        },
      ])
    } catch (e) {
      const latencyMs = Math.round(performance.now() - started)
      const msg = e.response?.data?.detail || e.message || 'Ask failed'
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: String(msg), confidence: 0, latencyMs },
      ])
    } finally {
      setQaLoading(false)
    }
  }

  return (
    <BubbleBackground interactive className="min-h-screen">
      <div className="relative z-10 mx-auto max-w-[1440px] px-4 pb-20 pt-10 md:px-8 md:pt-14">
        <header className="max-w-2xl">
          <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}>
            <p className="text-sm font-medium uppercase tracking-[0.2em] text-cyan-400/90">
              MeetingMind
            </p>
            <h1 className="mt-3 text-4xl font-bold tracking-tight text-white md:text-5xl md:leading-tight">
              NLP intelligence for{' '}
              <span className="bg-gradient-to-r from-cyan-300 to-violet-400 bg-clip-text text-transparent">
                every meeting
              </span>
            </h1>
            <p className="mt-4 text-base leading-relaxed text-slate-400">
              Turn raw conversations into decisions and next steps—powered by{' '}
              <strong className="text-slate-200">BART</strong>, <strong className="text-slate-200">spaCy</strong>,{' '}
              <strong className="text-slate-200">DistilBERT</strong>, and <strong className="text-slate-200">Groq</strong>{' '}
              for summaries, tasks, deadlines, follow-ups, and Q&amp;A.
            </p>
          </motion.div>
        </header>

        {/* lg+: wider center (transcript), narrower calendar; left stays balanced. Mobile: upload first. */}
        <div className="mt-14 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.42fr_0.82fr] lg:items-stretch lg:gap-5">
          <aside className="order-2 flex min-h-0 min-w-0 flex-col gap-4 lg:order-1">
            <MeetingHistory
              meetings={meetings}
              selectedId={result?.meeting_id}
              onSelect={openMeeting}
              onDelete={deleteMeeting}
              loading={meetingsLoading}
              onRefresh={refreshMeetings}
            />
            <div className="flex min-h-0 flex-1 flex-col">
              {result ? (
                <QAPanel
                  meetingId={result.meeting_id}
                  onAsk={onAsk}
                  loading={qaLoading}
                  messages={messages}
                  className="flex min-h-0 flex-1 flex-col"
                />
              ) : (
                <div className="flex flex-1 items-center justify-center rounded-2xl border border-dashed border-white/15 bg-black/30 p-4 text-center text-xs leading-relaxed text-slate-400 backdrop-blur-sm">
                  Run an analysis to unlock Q&amp;A over the stored transcript.
                </div>
              )}
            </div>
          </aside>

          <div className="order-1 flex min-h-0 min-w-0 flex-col lg:order-2">
            <UploadPanel
              fillColumn
              title={title}
              setTitle={setTitle}
              transcript={transcript}
              setTranscript={setTranscript}
              file={file}
              setFile={setFile}
              mode={mode}
              setMode={setMode}
              loading={loading}
              onSubmitPaste={onSubmitPaste}
              onSubmitFile={onSubmitFile}
            />
          </div>

          <aside className="order-3 flex min-h-0 min-w-0 flex-col lg:order-3">
            <CalendarPanel
              className="flex min-h-0 flex-1 flex-col"
              authVersion={calendarAuthVersion}
              meetingId={result?.meeting_id}
              meetingTitle={result?.title}
              meetingSummary={result?.summary}
              calendarHtmlLink={result?.google_calendar_html_link}
              onCalendarLinked={onCalendarLinked}
              actionItems={result?.action_items}
            />
          </aside>
        </div>

        {result && (
          <motion.section
            key={result.meeting_id}
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-12"
          >
            <h2 className="mb-6 text-lg font-semibold text-white">
              Results
              {result.title ? (
                <span className="ml-2 text-base font-normal text-slate-400">· {result.title}</span>
              ) : null}
            </h2>
            <ResultsBoard
              key={result.meeting_id}
              summary={result.summary}
              followups={result.followup_suggestions}
              transcript={result.transcript}
              actionItems={result.action_items}
              onToggleTaskComplete={toggleTaskComplete}
            />
          </motion.section>
        )}
      </div>
    </BubbleBackground>
  )
}
