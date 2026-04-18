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

export default function ResultsBoard({ summary, actionItems, followups }) {
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
          {followups?.map((f, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-violet-400">→</span>
              <span>{f}</span>
            </li>
          ))}
        </ul>
      </motion.section>

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
                <th className="pb-3 pr-4 font-medium">Task</th>
                <th className="pb-3 pr-4 font-medium">Owner</th>
                <th className="pb-3 font-medium">Deadline</th>
              </tr>
            </thead>
            <tbody>
              {actionItems?.map((row, i) => (
                <motion.tr
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.04 }}
                  className="border-b border-white/5 last:border-0"
                >
                  <td className="py-3 pr-4 align-top text-slate-200">{row.task_text}</td>
                  <td className="py-3 pr-4 align-top text-cyan-200/90">
                    {row.owner || '—'}
                  </td>
                  <td className="py-3 align-top text-slate-400">
                    {row.deadline_iso || row.deadline_raw || '—'}
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
