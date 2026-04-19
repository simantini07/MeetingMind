import { motion, AnimatePresence } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'

export default function QAPanel({ meetingId, onAsk, loading, messages, className = '' }) {
  const [q, setQ] = useState('')
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const submit = async (e) => {
    e.preventDefault()
    if (!q.trim() || loading) return
    await onAsk(q.trim())
    setQ('')
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex min-h-0 flex-col rounded-2xl border border-white/10 bg-black/30 p-6 backdrop-blur-xl ${className}`}
    >
      <h3 className="shrink-0 text-sm font-semibold text-white">Ask about this meeting</h3>

      <div
        ref={scrollRef}
        className="mt-4 min-h-[7rem] max-h-[min(20rem,42vh)] flex-1 space-y-3 overflow-y-auto overflow-x-hidden overscroll-contain rounded-xl border border-white/5 bg-black/40 p-3"
        role="log"
        aria-relevant="additions"
        aria-label="Chat messages"
      >
        <AnimatePresence initial={false}>
          {messages.length === 0 && (
            <p className="text-center text-sm text-slate-500">Ask a question to see answers here.</p>
          )}
          {messages.map((m, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: m.role === 'user' ? 12 : -12 }}
              animate={{ opacity: 1, x: 0 }}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[90%] rounded-2xl px-4 py-2 text-sm ${
                  m.role === 'user'
                    ? 'bg-gradient-to-r from-cyan-600/80 to-violet-600/80 text-white'
                    : 'border border-white/10 bg-white/5 text-slate-200'
                }`}
              >
                {m.role === 'assistant' && m.confidence != null && (
                  <span className="mb-0.5 block text-[10px] uppercase tracking-wider text-slate-500">
                    confidence {(m.confidence * 100).toFixed(0)}%
                  </span>
                )}
                {m.role === 'assistant' && m.latencyMs != null && (
                  <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
                    latency {m.latencyMs} ms
                  </span>
                )}
                {m.text}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      <form onSubmit={submit} className="mt-4 flex shrink-0 gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. Who owns the API deadline?"
          className="min-w-0 flex-1 rounded-xl border border-white/10 bg-black/50 px-4 py-3 text-sm text-white placeholder:text-slate-600 focus:border-violet-500/50 focus:outline-none focus:ring-2 focus:ring-violet-500/20"
        />
        <motion.button
          type="submit"
          disabled={loading || !meetingId}
          whileTap={{ scale: 0.97 }}
          className="rounded-xl bg-white/10 px-5 py-3 text-sm font-medium text-white transition hover:bg-white/15 disabled:opacity-40"
        >
          {loading ? '…' : 'Ask'}
        </motion.button>
      </form>
    </motion.div>
  )
}
