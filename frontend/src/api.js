import axios from 'axios'

const base =
  import.meta.env.VITE_API_URL?.replace(/\/$/, '') ||
  (import.meta.env.DEV ? '/api' : 'http://127.0.0.1:8000')

export const api = axios.create({ baseURL: base, timeout: 120000 })

export async function analyzeJson({ title, transcript }) {
  const { data } = await api.post('/analyze', { title, transcript })
  return data
}

export async function analyzeUpload({ file, title }) {
  const form = new FormData()
  form.append('file', file)
  form.append('title', title || 'Uploaded meeting')
  const { data } = await api.post('/analyze/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function askQuestion({ meetingId, question }) {
  const { data } = await api.post('/ask', {
    meeting_id: meetingId,
    question,
  })
  return data
}

export async function getMeeting(meetingId) {
  const { data } = await api.get(`/meeting/${meetingId}`)
  return data
}

export async function listMeetings(limit = 50) {
  const { data } = await api.get('/meetings', { params: { limit } })
  return data
}

export async function patchActionItemCompleted(actionId, completed) {
  const { data } = await api.patch(`/action-items/${actionId}`, { completed })
  return data
}

export async function deleteMeeting(meetingId) {
  const { data } = await api.delete(`/meeting/${meetingId}`)
  return data
}

export async function getCalendarStatus() {
  const { data } = await api.get('/calendar/status')
  return data
}

export async function getCalendarOAuthUrl() {
  const { data } = await api.get('/calendar/oauth/url')
  return data
}

export async function getCalendarEvents(maxResults = 25) {
  const { data } = await api.get('/calendar/events', {
    params: { max_results: maxResults },
  })
  return data
}

export async function createCalendarEvent(payload) {
  const { data } = await api.post('/calendar/events', payload)
  return data
}

export async function disconnectCalendar() {
  const { data } = await api.delete('/calendar/oauth')
  return data
}
