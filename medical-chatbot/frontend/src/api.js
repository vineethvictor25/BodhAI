export async function apiFetch(path, options = {}) {
  return fetch(`/api${path}`, {
    ...options,
    credentials: 'include',
  })
}

export async function readResponse(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`)
  return data
}