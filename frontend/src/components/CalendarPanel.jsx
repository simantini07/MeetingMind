import { motion } from 'framer-motion'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createCalendarEvent,
  disconnectCalendar,
  getCalendarEvents,
  getCalendarOAuthUrl,
  getCalendarStatus,
} from '../api'

function formatShort(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function truncate(s, n) {
  if (!s) return ''
  const t = String(s).trim()
  return t.length <= n ? t : `${t.slice(0, n - 1)}…`
}

/** Parseable deadline for sorting (ms), or null */
function deadlineMs(item) {
  if (!item) return null
  if (item.deadline_iso) {
    const t = Date.parse(item.deadline_iso)
    if (!Number.isNaN(t)) return t
  }
  if (item.deadline_raw) {
    const t = Date.parse(item.deadline_raw)
    if (!Number.isNaN(t)) return t
  }
  return null
}

/** Prefer nearest upcoming deadline among open items; else earliest with any deadline hint */
function pickBestActionItemForSchedule(items) {
  const open = (items || []).filter((a) => !a.completed)
  if (!open.length) return null
  const withHint = open.filter((a) => a.deadline_iso || (a.deadline_raw && String(a.deadline_raw).trim()))
  if (!withHint.length) return null
  const scored = withHint.map((item) => ({ item, ms: deadlineMs(item) }))
  const parsed = scored.filter((x) => x.ms != null)
  const now = Date.now()
  if (parsed.length) {
    const future = parsed.filter((x) => x.ms >= now - 12 * 60 * 60 * 1000)
    const pool = future.length ? future : parsed
    pool.sort((a, b) => a.ms - b.ms)
    return pool[0].item
  }
  return withHint[0]
}

/** Natural-language time string for the API (dateparser); default 9:00am local implication */
function defaultStartTextFromAction(item) {
  if (!item) return ''
  if (item.deadline_raw && String(item.deadline_raw).trim()) {
    return `${String(item.deadline_raw).trim()} 9:00am`
  }
  if (item.deadline_iso) {
    const d = new Date(item.deadline_iso)
    if (!Number.isNaN(d.getTime())) {
      const datePart = d.toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
      return `${datePart} 9:00am`
    }
  }
  return ''
}

function deadlineLabel(item) {
  if (item.deadline_raw && String(item.deadline_raw).trim()) return String(item.deadline_raw).trim()
  if (item.deadline_iso) {
    const d = new Date(item.deadline_iso)
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    }
  }
  return 'no date'
}

export default function CalendarPanel({
  className = '',
  authVersion = 0,
  meetingId,
  meetingTitle,
  meetingSummary,
  calendarHtmlLink,
  onCalendarLinked,
  actionItems: actionItemsProp,
}) {
  const actionItems = actionItemsProp ?? []

  const [status, setStatus] = useState({ oauth_configured: false, connected: false })
  const [eventsLoading, setEventsLoading] = useState(false)
  const [calendarData, setCalendarData] = useState({ events: [], current_event: null })
  /** 'meeting' | action item id */
  const [scheduleSource, setScheduleSource] = useState('meeting')
  const [eventTitleDraft, setEventTitleDraft] = useState('')
  const [startText, setStartText] = useState('')
  const [duration, setDuration] = useState(60)
  const [addMeet, setAddMeet] = useState(false)
  const [creating, setCreating] = useState(false)

  const tz =
    typeof Intl !== 'undefined'
      ? Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      : 'UTC'

  const actionFingerprint = useMemo(
    () =>
      actionItems
        .map((a) => `${a.id}:${a.completed ? 1 : 0}:${a.deadline_iso || ''}:${a.deadline_raw || ''}`)
        .join('|'),
    [actionItems],
  )

  const applyScheduleSource = useCallback(
    (source, items, title) => {
      if (source === 'meeting') {
        setEventTitleDraft(title || '')
        setStartText('')
        return
      }
      const item = items.find((a) => a.id === source)
      if (item) {
        setEventTitleDraft(item.task_text || title || '')
        setStartText(defaultStartTextFromAction(item))
      }
    },
    [],
  )

  useEffect(() => {
    if (!meetingId) {
      setScheduleSource('meeting')
      setEventTitleDraft('')
      setStartText('')
      return
    }
    const best = pickBestActionItemForSchedule(actionItems)
    if (best) {
      setScheduleSource(best.id)
      setEventTitleDraft(best.task_text || meetingTitle || '')
      setStartText(defaultStartTextFromAction(best))
    } else {
      setScheduleSource('meeting')
      setEventTitleDraft(meetingTitle || '')
      setStartText('')
    }
  }, [meetingId, actionFingerprint, meetingTitle])

  const refreshStatus = useCallback(async () => {
    try {
      const s = await getCalendarStatus()
      setStatus(s)
    } catch {
      setStatus({ oauth_configured: false, connected: false })
    }
  }, [])

  const refreshEvents = useCallback(async () => {
    setEventsLoading(true)
    try {
      const d = await getCalendarEvents(25)
      setCalendarData(d)
    } catch {
      setCalendarData({ events: [], current_event: null })
    } finally {
      setEventsLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshStatus()
  }, [refreshStatus, authVersion])

  useEffect(() => {
    if (!status.connected) {
      setCalendarData({ events: [], current_event: null })
      return
    }
    refreshEvents()
    const t = setInterval(refreshEvents, 60000)
    return () => clearInterval(t)
  }, [status.connected, refreshEvents])

  const connect = async () => {
    try {
      const { url } = await getCalendarOAuthUrl()
      window.location.href = url
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Could not start Google login'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }

  const disconnect = async () => {
    if (!window.confirm('Disconnect Google Calendar from MeetingMind?')) return
    try {
      await disconnectCalendar()
      await refreshStatus()
      setCalendarData({ events: [], current_event: null })
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Disconnect failed'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }

  const openItems = useMemo(() => actionItems.filter((a) => !a.completed), [actionItems])

  const schedule = async (e) => {
    e.preventDefault()
    if (!meetingId) return
    setCreating(true)
    const title =
      (eventTitleDraft && eventTitleDraft.trim()) || meetingTitle || 'Meeting'
    const fromItem =
      scheduleSource !== 'meeting' ? actionItems.find((a) => a.id === scheduleSource) : null
    const description = [
      meetingSummary || '',
      fromItem
        ? `\n\n— Action item: ${fromItem.task_text}${fromItem.owner ? ` (${fromItem.owner})` : ''}`
        : '',
    ]
      .join('')
      .trim()

    try {
      const data = await createCalendarEvent({
        meeting_id: meetingId,
        title,
        description: description || undefined,
        start_iso: startText.trim() || undefined,
        duration_minutes: duration,
        timezone: tz,
        add_meet_link: addMeet,
      })
      onCalendarLinked?.({
        google_calendar_event_id: data.event_id,
        google_calendar_html_link: data.html_link,
      })
      applyScheduleSource(scheduleSource, actionItems, meetingTitle)
      await refreshEvents()
      if (data.html_link) {
        window.open(data.html_link, '_blank', 'noopener,noreferrer')
      }
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Could not create event'
      alert(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setCreating(false)
    }
  }

  if (!status.oauth_configured) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className={`rounded-2xl border border-white/10 bg-black/30 p-6 backdrop-blur-xl ${className}`}
      >
        <h3 className="text-sm font-semibold text-white">Google Calendar</h3>
        <p className="mt-2 text-sm text-slate-500">
          Calendar OAuth is not configured on the API. Add client id, secret, and redirect URI to{' '}
          <code className="text-slate-400">backend/.env</code>.
        </p>
      </motion.div>
    )
  }

  const cur = calendarData.current_event
  const upcoming =
    calendarData.events?.filter((ev) => {
      if (cur && ev.id === cur.id) return false
      if (!ev.end) return true
      try {
        return new Date(ev.end).getTime() > Date.now()
      } catch {
        return true
      }
    }) ?? []

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-2xl border border-white/10 bg-black/30 p-6 backdrop-blur-xl ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">Google Calendar</h3>
        {status.connected ? (
          <button
            type="button"
            onClick={disconnect}
            className="text-[11px] uppercase tracking-wider text-slate-500 hover:text-slate-300"
          >
            Disconnect
          </button>
        ) : null}
      </div>

      {!status.connected ? (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-400">
            Connect your Google account to see what is on your calendar now and schedule follow-ups.
          </p>
          <motion.button
            type="button"
            whileTap={{ scale: 0.98 }}
            onClick={connect}
            className="w-full rounded-xl bg-gradient-to-r from-emerald-600/90 to-cyan-600/90 px-4 py-3 text-sm font-medium text-white shadow-lg shadow-emerald-900/20"
          >
            Connect Google Calendar
          </motion.button>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
              Happening now
            </p>
            {eventsLoading && !cur ? (
              <p className="mt-2 text-sm text-slate-500">Loading…</p>
            ) : cur ? (
              <div className="mt-2">
                <p className="text-sm font-medium text-emerald-200/95">{cur.summary}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {formatShort(cur.start)} — {formatShort(cur.end)}
                </p>
                {cur.html_link ? (
                  <a
                    href={cur.html_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-block text-xs text-cyan-400 hover:text-cyan-300"
                  >
                    Open in Google Calendar →
                  </a>
                ) : null}
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-500">No event in progress.</p>
            )}
          </div>

          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
              Next on calendar
            </p>
            <ul className="mt-2 max-h-36 space-y-2 overflow-y-auto text-xs text-slate-400">
              {upcoming.slice(0, 6).map((ev) => (
                <li key={ev.id || ev.start} className="flex flex-col border-b border-white/5 pb-2 last:border-0">
                  <span className="text-slate-200">{ev.summary}</span>
                  <span className="text-slate-500">{formatShort(ev.start)}</span>
                </li>
              ))}
              {!eventsLoading && upcoming.length === 0 ? (
                <li className="text-slate-600">No upcoming items in this window.</li>
              ) : null}
            </ul>
            <button
              type="button"
              onClick={refreshEvents}
              className="mt-2 text-[11px] text-slate-500 hover:text-slate-300"
            >
              Refresh
            </button>
          </div>

          {calendarHtmlLink ? (
            <a
              href={calendarHtmlLink}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-center text-xs text-violet-300 hover:text-violet-200"
            >
              This meeting’s calendar event →
            </a>
          ) : null}

          <form onSubmit={schedule} className="space-y-3 border-t border-white/10 pt-4">
            <p className="text-xs font-medium text-slate-300">Schedule follow-up</p>
            {!meetingId ? (
              <p className="text-xs text-slate-500">Open or analyze a meeting to enable scheduling.</p>
            ) : (
              <>
                <label className="block text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  From action item
                </label>
                <select
                  value={scheduleSource}
                  onChange={(e) => {
                    const v = e.target.value
                    setScheduleSource(v)
                    if (v === 'meeting') {
                      applyScheduleSource('meeting', actionItems, meetingTitle)
                    } else {
                      applyScheduleSource(v, actionItems, meetingTitle)
                    }
                  }}
                  className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
                >
                  <option value="meeting">Meeting only — custom time</option>
                  {openItems.map((a) => (
                    <option key={a.id} value={a.id}>
                      {truncate(a.task_text, 42)} — {deadlineLabel(a)}
                    </option>
                  ))}
                </select>

                <label className="block text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  Event title <span className="font-normal normal-case text-slate-600">(edit to confirm)</span>
                </label>
                <input
                  value={eventTitleDraft}
                  onChange={(e) => setEventTitleDraft(e.target.value)}
                  placeholder={meetingTitle || 'Event title'}
                  className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-emerald-500/40 focus:outline-none focus:ring-2 focus:ring-emerald-500/15"
                />

                <label className="block text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  When
                </label>
                <input
                  value={startText}
                  onChange={(e) => setStartText(e.target.value)}
                  placeholder={`e.g. Wed Apr 22, 2026 9:00am (${tz})`}
                  className="w-full rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-emerald-500/40 focus:outline-none focus:ring-2 focus:ring-emerald-500/15"
                />
                <div className="flex flex-wrap gap-2">
                  <select
                    value={duration}
                    onChange={(e) => setDuration(Number(e.target.value))}
                    className="rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white"
                  >
                    <option value={30}>30 min</option>
                    <option value={60}>1 hour</option>
                    <option value={90}>1.5 hours</option>
                    <option value={120}>2 hours</option>
                  </select>
                  <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-400">
                    <input
                      type="checkbox"
                      checked={addMeet}
                      onChange={(e) => setAddMeet(e.target.checked)}
                      className="rounded border-white/20 bg-white/5 text-emerald-500"
                    />
                    Google Meet link
                  </label>
                </div>
                <motion.button
                  type="submit"
                  disabled={creating}
                  whileTap={{ scale: creating ? 1 : 0.98 }}
                  className="w-full rounded-xl bg-gradient-to-r from-violet-600/85 to-emerald-600/85 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {creating ? 'Creating…' : 'Create calendar event'}
                </motion.button>
                <p className="text-[10px] text-slate-600">
                  Prefills from the next open action item with a deadline (edit time/title, then
                  create). Empty time defaults to one hour from now on the server.
                </p>
              </>
            )}
          </form>
        </div>
      )}
    </motion.div>
  )
}
