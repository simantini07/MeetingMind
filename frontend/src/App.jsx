import { motion } from 'framer-motion'
import { useState, useCallback } from 'react'
import { analyzeJson, analyzeUpload, askQuestion } from './api'
import BubbleBackground from './components/BubbleBackground'
import QAPanel from './components/QAPanel'
import ResultsBoard from './components/ResultsBoard'
import UploadPanel from './components/UploadPanel'

export default function App() {
  const [title, setTitle] = useState('My meeting')
  const [transcript, setTranscript] = useState('')
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('paste')
  const [loading, setLoading] = useState(false)
  const [qaLoading, setQaLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [messages, setMessages] = useState([])

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
      setResult(data)
    } catch (e) {
      const msg =
        e.response?.data?.detail ||
        e.message ||
        'Request failed. Is the API running on port 8000?'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setLoading(false)
    }
  }, [mode, title, transcript, file])

  const onSubmitPaste = () => runAnalyze()
  const onSubmitFile = () => runAnalyze()

  const onAsk = async (question) => {
    if (!result?.meeting_id) return
    setMessages((m) => [...m, { role: 'user', text: question }])
    setQaLoading(true)
    try {
      const data = await askQuestion({ meetingId: result.meeting_id, question })
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          text: data.answer,
          confidence: data.confidence,
        },
      ])
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Ask failed'
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: String(msg), confidence: 0 },
      ])
    } finally {
      setQaLoading(false)
    }
  }

  return (
    <BubbleBackground interactive className="min-h-screen">
      <div className="relative z-10 mx-auto max-w-6xl px-4 pb-20 pt-10 md:px-8 md:pt-14">
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
              Upload or paste a transcript. We summarize, extract action items, surface follow-ups,
              and answer questions — powered by BART, DistilBERT QA, and spaCy.
            </p>
          </motion.div>
        </header>

        <div className="mt-14 grid gap-10 lg:grid-cols-[1fr_380px]">
          <UploadPanel
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

          <div className="space-y-6">
            {result && (
              <>
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200/90"
                >
                  Analysis saved · use the same backend PostgreSQL for demos and reports.
                </motion.div>
                <QAPanel
                  meetingId={result.meeting_id}
                  onAsk={onAsk}
                  loading={qaLoading}
                  messages={messages}
                />
              </>
            )}
            {!result && (
              <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.02] p-8 text-center text-sm text-slate-500">
                Run an analysis to unlock Q&amp;A over the stored transcript.
              </div>
            )}
          </div>
        </div>

        {result && (
          <motion.section
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-12"
          >
            <h2 className="mb-6 text-lg font-semibold text-white">Results</h2>
            <ResultsBoard
              summary={result.summary}
              actionItems={result.action_items}
              followups={result.followup_suggestions}
            />
          </motion.section>
        )}
      </div>
    </BubbleBackground>
  )
}
