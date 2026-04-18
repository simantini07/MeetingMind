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
