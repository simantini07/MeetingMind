import { motion } from 'framer-motion'
import { useRef, useState } from 'react'

const tabs = [
  { id: 'paste', label: 'Paste text' },
  { id: 'file', label: 'Upload file' },
]

export default function UploadPanel({
  title,
  setTitle,
  transcript,
  setTranscript,
  file,
  setFile,
  mode,
  setMode,
  loading,
  onSubmitPaste,
  onSubmitFile,
}) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="rounded-2xl border border-white/10 bg-white/[0.04] p-6 shadow-2xl shadow-cyan-500/5 backdrop-blur-xl md:p-8"
    >
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-white md:text-xl">
            New analysis
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            Paste a transcript or upload <span className="text-cyan-300">.txt</span> /{' '}
            <span className="text-violet-300">.vtt</span> (Zoom).
          </p>
        </div>
        <div className="flex rounded-full border border-white/10 bg-black/30 p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setMode(t.id)}
              className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                mode === t.id
                  ? 'bg-gradient-to-r from-cyan-500/30 to-violet-600/30 text-white shadow-inner shadow-cyan-500/20'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <label className="mb-3 block text-xs font-medium uppercase tracking-wider text-slate-500">
        Meeting title
      </label>
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="e.g. Sprint planning — Apr 17"
        className="mb-6 w-full rounded-xl border border-white/10 bg-black/40 px-4 py-3 text-white placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
      />

      {mode === 'paste' ? (
        <>
          <label className="mb-3 block text-xs font-medium uppercase tracking-wider text-slate-500">
            Transcript
          </label>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={12}
            placeholder="Paste meeting transcript here..."
            className="mb-6 w-full resize-y rounded-xl border border-white/10 bg-black/40 px-4 py-3 font-mono text-sm leading-relaxed text-slate-200 placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none focus:ring-2 focus:ring-cyan-500/20"
          />
          <motion.button
            type="button"
            disabled={loading}
            whileHover={{ scale: loading ? 1 : 1.02 }}
            whileTap={{ scale: loading ? 1 : 0.98 }}
            onClick={onSubmitPaste}
            className="w-full rounded-xl bg-gradient-to-r from-cyan-500 to-violet-600 px-6 py-4 font-semibold text-white shadow-lg shadow-cyan-500/25 transition hover:shadow-cyan-500/40 disabled:opacity-50"
          >
            {loading ? 'Analyzing…' : 'Run NLP pipeline'}
          </motion.button>
        </>
      ) : (
        <>
          <div
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDrag(true)
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDrag(false)
              const f = e.dataTransfer.files?.[0]
              if (f) setFile(f)
            }}
            className={`mb-6 flex min-h-[200px] cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-12 transition ${
              drag
                ? 'border-cyan-400/60 bg-cyan-500/10'
                : 'border-white/15 bg-black/25 hover:border-white/25'
            }`}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".txt,.vtt,text/plain"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <span className="text-4xl">📄</span>
            <p className="mt-3 text-center text-sm text-slate-300">
              {file ? (
                <>
                  <span className="font-medium text-cyan-300">{file.name}</span>
                  <span className="block text-xs text-slate-500">
                    {(file.size / 1024).toFixed(1)} KB
                  </span>
                </>
              ) : (
                <>
                  Drop <strong>.txt</strong> or <strong>.vtt</strong> here, or click to browse
                </>
              )}
            </p>
          </div>
          <motion.button
            type="button"
            disabled={loading || !file}
            whileHover={{ scale: loading || !file ? 1 : 1.02 }}
            whileTap={{ scale: loading || !file ? 1 : 0.98 }}
            onClick={onSubmitFile}
            className="w-full rounded-xl bg-gradient-to-r from-cyan-500 to-violet-600 px-6 py-4 font-semibold text-white shadow-lg shadow-violet-500/25 transition hover:shadow-violet-500/40 disabled:opacity-50"
          >
            {loading ? 'Analyzing…' : 'Upload & analyze'}
          </motion.button>
        </>
      )}
    </motion.div>
  )
}
