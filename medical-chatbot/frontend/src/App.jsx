import { useEffect, useState, useCallback, useRef } from 'react'
import FileUpload from './components/FileUpload.jsx'
import ChatWindow from './components/ChatWindow.jsx'
import { apiFetch, readResponse } from './api.js'

function documentKey(document) {
  return `${document.owner_id}:${document.filename}`
}


function doseForSlot(pattern, index) {
  const match = pattern.match(/^\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*$/)
  return match ? Number(match[index + 1]) : 0
}


const FOOD_LABELS = {
  NONE: 'As prescribed',
  BF: 'Before food',
  AF: 'After food',
  WITH: 'With food',
  EMPTY: 'On an empty stomach',
}

function DocumentRow({ document, admin, busy, working, notice, onReindex, onReplace }) {
  const fileInputRef = useRef(null)

  return (
    <li className="document-row">
      <div className="document-name">
        <span className="doc-icon" aria-hidden="true">📄</span>
        <span>{document.filename}</span>
      </div>
      <div className="document-meta">
        {document.chunk_count} {document.chunk_count === 1 ? 'chunk' : 'chunks'}
        {document.doc_type && document.doc_type !== 'unknown' ? ` · ${document.doc_type.toUpperCase()}` : ''}
        {admin && <span> · Patient: {document.patient_name}{document.patient_email ? ` (${document.patient_email})` : ''}</span>}
      </div>
      <div className="document-actions">
        <button
          type="button"
          onClick={() => onReindex(document)}
          disabled={busy || !document.can_reindex}
          title={document.can_reindex ? 'Rebuild the index from the saved upload' : 'Saved upload is unavailable'}
        >
          {working ? 'Indexing…' : 'Reindex'}
        </button>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
        >
          {working ? 'Updating…' : 'Replace file'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.png,.jpg,.jpeg"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onReplace(file, document)
            event.target.value = ''
          }}
        />
      </div>
      {notice && <p className={`document-notice ${notice.type}`}>{notice.text}</p>}
    </li>
  )
}

export default function App() {
  const [user, setUser] = useState(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [phoneNumber, setPhoneNumber] = useState('')
  const [whatsappOptIn, setWhatsappOptIn] = useState(false)
  const [emailReminderOptIn, setEmailReminderOptIn] = useState(false)
  const [password, setPassword] = useState('')
  const [signupMode, setSignupMode] = useState(false)
  const [needsAdminSetup, setNeedsAdminSetup] = useState(false)
  const [documents, setDocuments] = useState([])
  const [patients, setPatients] = useState([])
  const [selectedPatientId, setSelectedPatientId] = useState('')
  const [reminders, setReminders] = useState([])
  const [dueReminders, setDueReminders] = useState([])
  const [whatsappConfigured, setWhatsappConfigured] = useState(false)
  const [emailConfigured, setEmailConfigured] = useState(false)
  const [deliveryMode, setDeliveryMode] = useState('manual')
  const [reminderBusy, setReminderBusy] = useState(false)
  const [reminderNotice, setReminderNotice] = useState('')
  const [reminderForm, setReminderForm] = useState({
    medicine_name: '',
    dose_pattern: '1-0-1',
    food_instruction: 'NONE',
    start_date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10),
    duration_days: 7,
    morning_time: '08:00',
    afternoon_time: '13:00',
    night_time: '20:00',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  })
  const [profileOpen, setProfileOpen] = useState(false)
  const [profileName, setProfileName] = useState('')
  const [profileEmail, setProfileEmail] = useState('')
  const [profilePhone, setProfilePhone] = useState('')
  const [profileWhatsAppOptIn, setProfileWhatsAppOptIn] = useState(false)
  const [profileEmailReminderOptIn, setProfileEmailReminderOptIn] = useState(false)
  const [profileCurrentPassword, setProfileCurrentPassword] = useState('')
  const [profileNewPassword, setProfileNewPassword] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileMessage, setProfileMessage] = useState(null)
  const [busyDocument, setBusyDocument] = useState(null)
  const [documentNotice, setDocumentNotice] = useState(null)

  const refreshDocuments = useCallback(async () => {
    if (!user) return
    try {
      const res = await apiFetch('/documents')
      const data = await readResponse(res)
      setDocuments(data.documents || [])
    } catch {
      setDocuments([])
    }
  }, [user])

  const refreshReminders = useCallback(async () => {
    if (!user) return
    try {
      const data = await readResponse(await apiFetch('/reminders'))
      setReminders(data.reminders || [])
      setWhatsappConfigured(Boolean(data.whatsapp_configured))
      setEmailConfigured(Boolean(data.email_configured))
      setDeliveryMode(data.delivery_mode || 'manual')
      if (user.role === 'admin') {
        const due = await readResponse(await apiFetch('/reminders/due'))
        setDueReminders(due.reminders || [])
      } else {
        setDueReminders([])
      }
    } catch {
      setReminders([])
      setDueReminders([])
      setWhatsappConfigured(false)
      setEmailConfigured(false)
      setDeliveryMode('manual')
    }
  }, [user])

  useEffect(() => {
    Promise.all([
      apiFetch('/auth/me').then(readResponse).catch(() => null),
      apiFetch('/auth/status').then(readResponse),
    ])
      .then(([session, status]) => {
        setUser(session)
        setNeedsAdminSetup(status.needs_admin_setup)
        setSignupMode(status.needs_admin_setup)
      })
      .catch(() => setUser(null))
      .finally(() => setCheckingSession(false))
  }, [])

  useEffect(() => {
    if (user?.role !== 'admin') {
      setPatients([])
      setSelectedPatientId('')
      return
    }
    apiFetch('/admin/patients')
      .then(readResponse)
      .then((data) => {
        const nextPatients = data.patients || []
        setPatients(nextPatients)
        setSelectedPatientId((current) =>
          nextPatients.some((patient) => patient.id === current) ? current : nextPatients[0]?.id || '',
        )
      })
      .catch(() => setPatients([]))
  }, [user])

  useEffect(() => {
    if (!user) return
    setProfileName(user.name)
    setProfileEmail(user.email)
    setProfilePhone(user.phone_number || '')
    setProfileWhatsAppOptIn(Boolean(user.whatsapp_opt_in))
    setProfileEmailReminderOptIn(Boolean(user.email_reminder_opt_in))
  }, [user])

  useEffect(() => {
    refreshDocuments()
  }, [refreshDocuments])

  useEffect(() => {
    refreshReminders()
  }, [refreshReminders])

  useEffect(() => {
    if (user?.role !== 'admin') return undefined
    const interval = window.setInterval(refreshReminders, 30000)
    return () => window.clearInterval(interval)
  }, [user, refreshReminders])

  async function submitAuth(event) {
    event.preventDefault()
    setLoginBusy(true)
    setLoginError('')
    try {
      const response = await apiFetch(signupMode ? '/auth/signup' : '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          signupMode
            ? {
                name,
                email,
                phone_number: phoneNumber,
                whatsapp_opt_in: whatsappOptIn,
                email_reminder_opt_in: emailReminderOptIn,
                password,
              }
            : { email, password },
        ),
      })
      setUser(await readResponse(response))
      setPassword('')
    } catch (error) {
      setLoginError(error.message)
    } finally {
      setLoginBusy(false)
    }
  }

  async function signOut() {
    try {
      await apiFetch('/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
      setDocuments([])
      setPatients([])
      setReminders([])
    }
  }

  async function saveProfile(event) {
    event.preventDefault()
    setProfileSaving(true)
    setProfileMessage(null)
    const body = {
      name: profileName,
      email: profileEmail,
      phone_number: profilePhone,
      whatsapp_opt_in: profileWhatsAppOptIn,
      email_reminder_opt_in: profileEmailReminderOptIn,
      ...(profileNewPassword ? {
        current_password: profileCurrentPassword,
        new_password: profileNewPassword,
      } : {}),
    }
    try {
      const response = await apiFetch('/auth/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setUser(await readResponse(response))
      setProfileCurrentPassword('')
      setProfileNewPassword('')
      setProfileMessage({ type: 'success', text: 'Profile updated.' })
    } catch (error) {
      setProfileMessage({ type: 'error', text: error.message })
    } finally {
      setProfileSaving(false)
    }
  }

  async function createMedicationReminder(event) {
    event.preventDefault()
    setReminderBusy(true)
    setReminderNotice('')
    try {
      const payload = {
        ...reminderForm,
        patient_id: selectedPatientId,
        duration_days: Number(reminderForm.duration_days),
      }
      await readResponse(await apiFetch('/reminders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }))
      setReminderForm((form) => ({ ...form, medicine_name: '' }))
      setReminderNotice('Medication schedule created.')
      await refreshReminders()
    } catch (error) {
      setReminderNotice(error.message)
    } finally {
      setReminderBusy(false)
    }
  }

  async function stopMedicationReminder(reminderId) {
    setReminderBusy(true)
    setReminderNotice('')
    try {
      await readResponse(await apiFetch(`/reminders/${encodeURIComponent(reminderId)}`, { method: 'DELETE' }))
      setReminderNotice('Medication reminders stopped.')
      await refreshReminders()
    } catch (error) {
      setReminderNotice(error.message)
    } finally {
      setReminderBusy(false)
    }
  }

  async function copyDueReminder(message) {
    try {
      await navigator.clipboard.writeText(message)
      setReminderNotice('Message copied. Open the patient chat, paste it, and send from WhatsApp.')
    } catch {
      setReminderNotice('Clipboard access is unavailable. Select and copy the message text manually.')
    }
  }

  async function markDueReminderSent(reminder) {
    setReminderBusy(true)
    setReminderNotice('')
    try {
      await readResponse(await apiFetch(`/reminders/${encodeURIComponent(reminder.reminder_id)}/manual-sent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scheduled_date: reminder.scheduled_date, slot: reminder.slot }),
      }))
      setReminderNotice('Reminder marked as sent.')
      await refreshReminders()
    } catch (error) {
      setReminderNotice(error.message)
    } finally {
      setReminderBusy(false)
    }
  }

  async function reindexDocument(document) {
    const key = documentKey(document)
    setBusyDocument(key)
    setDocumentNotice(null)
    try {
      const owner = user.role === 'admin' ? `?owner_id=${encodeURIComponent(document.owner_id)}` : ''
      const res = await apiFetch(`/documents/${encodeURIComponent(document.filename)}/reindex${owner}`, {
        method: 'POST',
      })
      const data = await readResponse(res)
      setDocumentNotice({
        filename: document.filename,
        owner_id: document.owner_id,
        type: 'success',
        text: `Reindexed · ${data.chunks_added} chunks · ${Number(data.characters_extracted || 0).toLocaleString()} characters`,
      })
      await refreshDocuments()
    } catch (error) {
      setDocumentNotice({ filename: document.filename, owner_id: document.owner_id, type: 'error', text: error.message })
    } finally {
      setBusyDocument(null)
    }
  }

  async function replaceDocument(file, document) {
    const filename = document.filename
    const fileExtension = file.name.split('.').pop()?.toLowerCase()
    const documentExtension = filename.split('.').pop()?.toLowerCase()
    if (fileExtension !== documentExtension) {
      setDocumentNotice({
        filename,
        type: 'error',
        text: 'Choose a replacement with the same file type.',
      })
      return
    }

    setBusyDocument(documentKey(document))
    setDocumentNotice(null)
    const formData = new FormData()
    formData.append('files', file, filename)
    if (user.role === 'admin') formData.append('owner_id', document.owner_id)
    try {
      const res = await apiFetch('/upload', { method: 'POST', body: formData })
      const data = await readResponse(res)
      const result = data.results?.[0]
      if (!result || result.status !== 'indexed') {
        throw new Error(result?.reason || `Replacement status: ${result?.status || 'unknown'}`)
      }
      setDocumentNotice({
        filename,
        owner_id: document.owner_id,
        type: 'success',
        text: `Updated · ${result.chunks_added} chunks · ${Number(result.characters_extracted || 0).toLocaleString()} characters`,
      })
      await refreshDocuments()
    } catch (error) {
      setDocumentNotice({ filename, type: 'error', text: error.message })
    } finally {
      setBusyDocument(null)
    }
  }

  async function clearAll() {
    const scope = user.role === 'admin' ? 'all patient records' : 'your records'
    if (!confirm(`This will delete ${scope} and chat history. Continue?`)) return
    await apiFetch('/clear-documents', { method: 'POST' })
    setDocuments([])
    window.location.reload()
  }

  const failedDeliveryCount = user?.role === 'admin'
    ? reminders.reduce(
        (count, reminder) => count + (reminder.delivery_history || []).filter((delivery) => delivery.status === 'failed').length,
        0,
      )
    : 0

  if (checkingSession) {
    return <main className="session-check" aria-live="polite">Checking session…</main>
  }

  if (!user) {
    return (
      <main className="login-screen">
        <form className="login-panel" onSubmit={submitAuth}>
          <p className="login-kicker">MEDDOC ASSISTANT</p>
          <h1>{signupMode ? (needsAdminSetup ? 'Create administrator' : 'Create patient account') : 'Sign in'}</h1>
          {signupMode && (
            <>
              <label htmlFor="name">Full name</label>
              <input
                id="name"
                autoComplete="name"
                maxLength={120}
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
              <label htmlFor="phone-number">Phone number</label>
              <input
                id="phone-number"
                type="tel"
                autoComplete="tel"
                inputMode="tel"
                placeholder="+<country code><phone number>"
                pattern="\+[1-9][0-9]{7,14}"
                title="Use international format with country code, for example +<country code><phone number>"
                maxLength={16}
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
                required
              />
              <label className="consent-control">
                <input
                  type="checkbox"
                  checked={whatsappOptIn}
                  onChange={(event) => setWhatsappOptIn(event.target.checked)}
                />
                <span>I agree to receive medication reminders from this app on WhatsApp. I can turn this off in Profile.</span>
              </label>
              <label className="consent-control">
                <input
                  type="checkbox"
                  checked={emailReminderOptIn}
                  onChange={(event) => setEmailReminderOptIn(event.target.checked)}
                />
                <span>I agree to receive medication reminders at this email address, including medicine names, scheduled dose counts, time of day, and food instructions.</span>
              </label>
            </>
          )}
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            maxLength={254}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete={signupMode ? 'new-password' : 'current-password'}
            minLength={signupMode ? 12 : undefined}
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
          {loginError && <p className="login-error" role="alert">{loginError}</p>}
          <button type="submit" disabled={loginBusy}>
            {loginBusy ? 'Please wait…' : signupMode ? 'Create account' : 'Sign in'}
          </button>
          {!needsAdminSetup && (
            <button
              className="auth-mode-toggle"
              type="button"
              onClick={() => { setSignupMode((mode) => !mode); setLoginError('') }}
            >
              {signupMode ? 'Already have an account? Sign in' : 'Create a patient account'}
            </button>
          )}
        </form>
      </main>
    )
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>🩺 MedDoc Assistant</h1>
        <p className="subtitle">Understand your prescriptions & reports, in plain language.</p>

        <div className="account-bar">
          <span>{user.name} · {user.role === 'admin' ? 'Admin' : 'Patient'}</span>
          <button type="button" onClick={() => { setProfileOpen((open) => !open); setProfileMessage(null) }}>
            {profileOpen ? 'Close profile' : 'Profile'}
          </button>
          <button type="button" onClick={signOut}>Sign out</button>
        </div>

        {profileOpen && (
          <form className="profile-panel" onSubmit={saveProfile}>
            <h2>Profile</h2>
            <label htmlFor="profile-name">Full name</label>
            <input
              id="profile-name"
              autoComplete="name"
              maxLength={120}
              value={profileName}
              onChange={(event) => setProfileName(event.target.value)}
              required
            />
            <label htmlFor="profile-email">Email address</label>
            <input
              id="profile-email"
              type="email"
              autoComplete="email"
              maxLength={254}
              value={profileEmail}
              onChange={(event) => setProfileEmail(event.target.value)}
              required
            />
            <label htmlFor="profile-phone">Phone number</label>
            <input
              id="profile-phone"
              type="tel"
              autoComplete="tel"
              inputMode="tel"
              placeholder="+<country code><phone number>"
              pattern="\+[1-9][0-9]{7,14}"
              title="Use international format with country code"
              maxLength={16}
              value={profilePhone}
              onChange={(event) => setProfilePhone(event.target.value)}
              required
            />
            <label className="consent-control">
              <input
                type="checkbox"
                checked={profileWhatsAppOptIn}
                onChange={(event) => setProfileWhatsAppOptIn(event.target.checked)}
              />
              <span>Allow medication reminders via WhatsApp</span>
            </label>
            <label className="consent-control">
              <input
                type="checkbox"
                checked={profileEmailReminderOptIn}
                onChange={(event) => setProfileEmailReminderOptIn(event.target.checked)}
              />
              <span>Allow email reminders with medicine names, scheduled dose counts, time of day, and food instructions</span>
            </label>
            <label htmlFor="profile-current-password">Current password</label>
            <input
              id="profile-current-password"
              type="password"
              autoComplete="current-password"
              maxLength={128}
              value={profileCurrentPassword}
              onChange={(event) => setProfileCurrentPassword(event.target.value)}
              required={Boolean(profileNewPassword)}
              placeholder="Required only to change password"
            />
            <label htmlFor="profile-new-password">New password</label>
            <input
              id="profile-new-password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              value={profileNewPassword}
              onChange={(event) => setProfileNewPassword(event.target.value)}
              placeholder="Leave blank to keep current password"
            />
            {profileMessage && <p className={`profile-message ${profileMessage.type}`} role="status">{profileMessage.text}</p>}
            <button type="submit" disabled={profileSaving}>
              {profileSaving ? 'Saving…' : 'Save profile'}
            </button>
          </form>
        )}

        {user.role === 'admin' && (
          <label className="patient-picker">
            <span>Patient record</span>
            <select
              value={selectedPatientId}
              onChange={(event) => setSelectedPatientId(event.target.value)}
              disabled={patients.length === 0}
            >
              {patients.length === 0 ? (
                <option value="">No patient accounts yet</option>
              ) : patients.map((patient) => (
                <option key={patient.id} value={patient.id}>{patient.name} · {patient.email}</option>
              ))}
            </select>
          </label>
        )}

        <FileUpload
          ownerId={user.role === 'admin' ? selectedPatientId : null}
          disabled={user.role === 'admin' && !selectedPatientId}
          onUploaded={refreshDocuments}
        />

        <section className="reminder-section">
          <h2>Medication reminders</h2>
          {failedDeliveryCount > 0 && (
            <div className="delivery-failure-alert" role="alert">
              <strong>{failedDeliveryCount} reminder delivery{failedDeliveryCount === 1 ? '' : 'ies'} failed.</strong>
              <span>Retries run automatically. Check Recent delivery activity for the affected schedule.</span>
            </div>
          )}
          {user.role === 'admin' && (
            <>
              {deliveryMode === 'email' ? (
                <p className="reminder-config-note">
                  {emailConfigured
                    ? 'Email reminders include the medicine, scheduled dose count, time of day, and food instructions. SMTP acceptance is recorded, but inbox delivery is not confirmed.'
                    : 'Email mode is selected. Configure SMTP in backend/.env and have the patient opt in under Profile before sending.'}
                </p>
              ) : deliveryMode === 'manual_whatsapp' ? (
                <p className="reminder-config-note">Manual mode: copy each due message, send it from your WhatsApp, then mark it sent.</p>
              ) : !whatsappConfigured ? (
                <p className="reminder-config-note">WhatsApp Cloud API is selected but not configured. Switch to email or manual WhatsApp, or add Meta credentials.</p>
              ) : null}
              <form className="reminder-form" onSubmit={createMedicationReminder}>
                <label htmlFor="reminder-medicine">Medicine</label>
                <input
                  id="reminder-medicine"
                  maxLength={120}
                  value={reminderForm.medicine_name}
                  onChange={(event) => setReminderForm((form) => ({ ...form, medicine_name: event.target.value }))}
                  required
                />
                <label htmlFor="reminder-pattern">Dose pattern (morning-afternoon-night)</label>
                <input
                  id="reminder-pattern"
                  pattern="[0-9]{1,2}-[0-9]{1,2}-[0-9]{1,2}"
                  value={reminderForm.dose_pattern}
                  onChange={(event) => setReminderForm((form) => ({ ...form, dose_pattern: event.target.value }))}
                  required
                />
                <label htmlFor="reminder-food">Food instruction</label>
                <select
                  id="reminder-food"
                  value={reminderForm.food_instruction}
                  onChange={(event) => setReminderForm((form) => ({ ...form, food_instruction: event.target.value }))}
                >
                  <option value="NONE">As prescribed</option>
                  <option value="BF">Before food (BF)</option>
                  <option value="AF">After food (AF)</option>
                  <option value="WITH">With food</option>
                  <option value="EMPTY">On an empty stomach</option>
                </select>
                <label htmlFor="reminder-start">Prescription start date</label>
                <input
                  id="reminder-start"
                  type="date"
                  value={reminderForm.start_date}
                  onChange={(event) => setReminderForm((form) => ({ ...form, start_date: event.target.value }))}
                  required
                />
                <label htmlFor="reminder-days">Course length (days)</label>
                <input
                  id="reminder-days"
                  type="number"
                  min="1"
                  max="3650"
                  value={reminderForm.duration_days}
                  onChange={(event) => setReminderForm((form) => ({ ...form, duration_days: event.target.value }))}
                  required
                />
                {['morning', 'afternoon', 'night'].map((slot, index) => doseForSlot(reminderForm.dose_pattern, index) > 0 && (
                  <label className="reminder-time" key={slot} htmlFor={`reminder-${slot}`}>
                    {slot[0].toUpperCase() + slot.slice(1)} time
                    <input
                      id={`reminder-${slot}`}
                      type="time"
                      value={reminderForm[`${slot}_time`]}
                      onChange={(event) => setReminderForm((form) => ({ ...form, [`${slot}_time`]: event.target.value }))}
                      required
                    />
                  </label>
                ))}
                <label htmlFor="reminder-timezone">Patient timezone</label>
                <input
                  id="reminder-timezone"
                  maxLength={64}
                  value={reminderForm.timezone}
                  onChange={(event) => setReminderForm((form) => ({ ...form, timezone: event.target.value }))}
                  required
                />
                <button type="submit" disabled={reminderBusy || !selectedPatientId}>
                  {reminderBusy ? 'Saving…' : 'Schedule reminders'}
                </button>
              </form>
            </>
          )}
          {reminderNotice && <p className="reminder-notice" role="status">{reminderNotice}</p>}
          {user.role === 'admin' && dueReminders.length > 0 && (
            <div className="due-reminders">
              <h3>Due today ({dueReminders.length})</h3>
              {dueReminders.map((reminder) => (
                <article className="due-reminder" key={`${reminder.reminder_id}:${reminder.scheduled_date}:${reminder.slot}`}>
                  <strong>{reminder.patient_name} · {reminder.medicine_name}</strong>
                  <span>{reminder.slot} · {reminder.patient_phone}</span>
                  <textarea aria-label={`WhatsApp message for ${reminder.patient_name}`} readOnly rows={3} value={reminder.message} />
                  <button type="button" disabled={reminderBusy} onClick={() => copyDueReminder(reminder.message)}>
                    Copy message
                  </button>
                  <a href={`https://wa.me/${reminder.patient_phone.replace(/\D/g, '')}`} target="_blank" rel="noreferrer">
                    Open WhatsApp chat
                  </a>
                  <button type="button" disabled={reminderBusy} onClick={() => markDueReminderSent(reminder)}>
                    Mark sent
                  </button>
                </article>
              ))}
            </div>
          )}
          {reminders.length === 0 ? (
            <p className="hint">No medication schedules yet.</p>
          ) : (
            <ul className="reminder-list">
              {reminders.map((reminder) => (
                <li key={reminder.id}>
                  <strong>{reminder.medicine_name}</strong>
                  <span>{reminder.dose_pattern} · {FOOD_LABELS[reminder.food_instruction] || reminder.food_instruction}</span>
                  <span>{reminder.start_date} · {reminder.duration_days} days</span>
                  <span>{['morning', 'afternoon', 'night'].filter((slot) => reminder[`${slot}_time`]).map((slot) => `${slot} ${reminder[`${slot}_time`]}`).join(' · ')} · {reminder.timezone}</span>
                  {user.role === 'admin' && <span>{reminder.patient_name} · {reminder.patient_email}</span>}
                  {reminder.delivery_history?.length > 0 && (
                    <details className="delivery-history">
                      <summary>Recent delivery activity</summary>
                      <ul>
                        {reminder.delivery_history.map((delivery) => (
                          <li key={`${delivery.scheduled_date}:${delivery.slot}`}>
                            {delivery.scheduled_date} · {delivery.slot} · {
                              delivery.status === 'email_accepted'
                                ? 'Email accepted by SMTP (delivery not confirmed)'
                                : delivery.status === 'accepted'
                                  ? 'WhatsApp API accepted (delivery not confirmed)'
                                  : delivery.status === 'manual_sent'
                                    ? 'Admin marked sent'
                                    : delivery.status === 'failed'
                                      ? 'Failed'
                                      : delivery.status
                            }
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {reminder.status !== 'active' && (
                    <span className="reminder-stopped">{reminder.status === 'completed' ? 'Course completed' : 'Stopped'}</span>
                  )}
                  {reminder.active && (
                    <button type="button" disabled={reminderBusy} onClick={() => stopMedicationReminder(reminder.id)}>
                      Stop reminders
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="doc-list">
          <h3>{user.role === 'admin' ? 'All patient documents' : 'Your documents'} ({documents.length})</h3>
          {documents.length === 0 ? (
            <p className="hint">No documents uploaded yet.</p>
          ) : (
            <ul>
              {documents.map((document) => (
                <DocumentRow
                  key={documentKey(document)}
                  admin={user.role === 'admin'}
                  document={document}
                  busy={Boolean(busyDocument)}
                  working={busyDocument === documentKey(document)}
                  notice={documentNotice?.filename === document.filename && documentNotice?.owner_id === document.owner_id ? documentNotice : null}
                  onReindex={reindexDocument}
                  onReplace={replaceDocument}
                />
              ))}
            </ul>
          )}
        </div>

        {documents.length > 0 && (
          <button className="clear-btn" onClick={clearAll}>
            {user.role === 'admin' ? 'Clear all patient documents' : 'Clear my documents'}
          </button>
        )}

        <p className="disclaimer">
          This tool helps you understand your own records. It does not
          replace medical advice from your doctor.
        </p>
      </aside>

      <main className="main">
          <ChatWindow key={user.id} hasDocuments={documents.length > 0} />
      </main>
    </div>
  )
}
