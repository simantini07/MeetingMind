function formatDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 6h18" />
      <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
      <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
      <line x1="10" x2="10" y1="11" y2="17" />
      <line x1="14" x2="14" y1="11" y2="17" />
    </svg>
  )
}

export default function MeetingHistory({
  meetings,
  selectedId,
  onSelect,
  onDelete,
  loading,
  onRefresh,
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur-md">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Past meetings
        </h3>
        <button
          type="button"
          onClick={onRefresh}
          className="text-xs text-cyan-400/90 hover:text-cyan-300"
        >
          Refresh
        </button>
      </div>
      {loading ? (
        <p className="mt-3 text-sm text-slate-500">Loading…</p>
      ) : !meetings?.length ? (
        <p className="mt-3 text-sm text-slate-500">No saved meetings yet. Run an analysis first.</p>
      ) : (
        <ul className="mt-3 max-h-64 space-y-1 overflow-y-auto pr-1 text-sm">
          {meetings.map((m) => {
            const open = m.open_tasks ?? 0
            const total = m.total_tasks ?? 0
            const active = m.id === selectedId
            return (
              <li key={m.id} className="flex items-stretch gap-1">
                <button
                  type="button"
                  onClick={() => onSelect(m.id)}
                  className={`min-w-0 flex-1 flex-col rounded-lg px-3 py-2 text-left transition-colors ${
                    active
                      ? 'bg-cyan-500/20 text-white'
                      : 'text-slate-300 hover:bg-white/5'
                  }`}
                >
                  <span className="truncate font-medium">{m.title || 'Untitled'}</span>
                  <span className="text-xs text-slate-500">
                    {formatDate(m.created_at)}
                    {total > 0 ? (
                      <span className="ml-2 text-slate-400">
                        · {open} open / {total} tasks
                      </span>
                    ) : null}
                  </span>
                </button>
                {onDelete ? (
                  <button
                    type="button"
                    title="Delete meeting"
                    aria-label="Delete meeting"
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onDelete(m.id)
                    }}
                    className="flex shrink-0 items-center justify-center rounded-lg px-2 text-rose-400/80 opacity-80 transition hover:bg-rose-500/15 hover:text-rose-300 hover:opacity-100"
                  >
                    <TrashIcon />
                  </button>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
