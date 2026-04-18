import { motion } from 'framer-motion'

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.08 },
  },
}

const item = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0 },
}

export default function ResultsBoard({
  summary,
  actionItems,
  followups,
  transcript,
  onToggleTaskComplete,
}) {
  return (
    <motion.div
      variants={container}
      initial="hidden"
      animate="show"
      className="grid gap-4 lg:grid-cols-2"
    >
      <motion.section
        variants={item}
        className="rounded-2xl border border-white/10 bg-gradient-to-br from-cyan-500/10 via-transparent to-transparent p-6 backdrop-blur-md"
      >
        <h3 className="text-xs font-semibold uppercase tracking-widest text-cyan-300/90">
          Summary
        </h3>
        <p className="mt-3 text-sm leading-relaxed text-slate-200">{summary}</p>
      </motion.section>

      <motion.section
        variants={item}
        className="rounded-2xl border border-white/10 bg-gradient-to-br from-violet-500/10 via-transparent to-transparent p-6 backdrop-blur-md"
      >
        <h3 className="text-xs font-semibold uppercase tracking-widest text-violet-300/90">
          Follow-up suggestions
        </h3>
        <ul className="mt-3 space-y-2 text-sm text-slate-300">
          {followups?.length ? (
            followups.map((f, i) => (
              <li key={`${i}-${String(f).slice(0, 48)}`} className="flex gap-2">
                <span className="text-violet-400">→</span>
                <span>{f}</span>
              </li>
            ))
          ) : (
            <li className="text-slate-500">None listed.</li>
          )}
        </ul>
      </motion.section>

      {transcript ? (
        <motion.section
          variants={item}
          className="lg:col-span-2 rounded-2xl border border-white/10 bg-white/[0.02] p-6 backdrop-blur-md"
        >
          <details open className="group">
            <summary className="cursor-pointer list-none text-xs font-semibold uppercase tracking-widest text-slate-400 [&::-webkit-details-marker]:hidden">
              <span className="inline-flex flex-wrap items-center gap-2">
                Stored transcript
                <span className="text-[10px] font-normal normal-case text-slate-500">
                  (full text — collapse to hide)
                </span>
              </span>
            </summary>
            <pre className="mt-4 rounded-xl border border-white/10 bg-black/25 p-4 text-xs leading-relaxed text-slate-300 whitespace-pre-wrap break-words">
              {transcript}
            </pre>
          </details>
        </motion.section>
      ) : null}

      <motion.section
        variants={item}
        className="lg:col-span-2 rounded-2xl border border-white/10 bg-white/[0.03] p-6 backdrop-blur-md"
      >
        <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Action items
        </h3>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-white/10 text-xs uppercase tracking-wider text-slate-500">
                {onToggleTaskComplete ? (
                  <th className="w-10 pb-3 pr-2 font-medium">Done</th>
                ) : null}
                <th className="pb-3 pr-4 font-medium">Task</th>
                <th className="pb-3 pr-4 font-medium">Owner</th>
                <th className="pb-3 font-medium">Deadline</th>
              </tr>
            </thead>
            <tbody>
              {actionItems?.map((row, i) => (
                <motion.tr
                  key={row.id || i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.04 }}
                  className={`border-b border-white/5 last:border-0 ${
                    row.completed ? 'opacity-60' : ''
                  }`}
                >
                  {onToggleTaskComplete ? (
                    <td className="py-3 pr-2 align-top">
                      {row.id ? (
                        <input
                          type="checkbox"
                          checked={!!row.completed}
                          onChange={(e) =>
                            onToggleTaskComplete(row.id, e.target.checked)
                          }
                          className="h-4 w-4 rounded border-white/20 bg-white/5 text-cyan-500 focus:ring-cyan-500/40"
                          aria-label="Mark task complete"
                        />
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                  ) : null}
                  <td
                    className={`py-3 pr-4 align-top ${
                      row.completed ? 'text-slate-400 line-through' : 'text-slate-200'
                    }`}
                  >
                    {row.task_text}
                  </td>
                  <td className="py-3 pr-4 align-top text-cyan-200/90">
                    {row.owner || '—'}
                  </td>
                  <td className="py-3 align-top text-slate-400">
                    {row.deadline_raw || row.deadline_iso || '—'}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </motion.section>
    </motion.div>
  )
}
