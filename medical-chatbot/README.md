# MedDoc Assistant

A React and FastAPI application for uploading medical documents, asking questions with source excerpts, and managing medication reminders. Text extraction, embeddings, and storage run locally. Answer generation uses Google's Gemini API; optional reminders use SMTP or WhatsApp.

See the [architecture diagram and data flows](docs/architecture.md).

## Features and access

- Upload PDF, DOCX, PNG, JPG, and JPEG files. Scanned PDF pages and images use Tesseract OCR; PDFium renders PDFs without Poppler. Convert legacy `.doc` files to `.docx`: the API accepts the suffix, but the parser cannot read legacy Word binaries.
- Reindex saved uploads or replace documents with the same filename for the same patient. Answers display retrieved source excerpts.
- The first registered account becomes the administrator; later accounts are patients. Create the administrator before exposing the app to others.
- Patients access their own documents. Administrators can list and query all patients' documents and must select a patient for uploads. Administrator chat searches are not limited to the patient selected for upload.
- Profiles support contact details, password changes, and separate email and WhatsApp reminder consent. Sign-up requires an international E.164 phone number and a password of at least 12 characters.
- Administrators enter reminder schedules; patients can stop their own schedules. Schedules are not extracted automatically from prescriptions.

## Prerequisites

- Python 3.10 or newer is required by the source syntax. Compatibility of the pinned dependencies with every newer Python release has not been verified.
- Node.js `^20.19.0 || >=22.12.0` and npm, matching Vite's requirement in the lockfile.
- A [Google AI Studio API key](https://aistudio.google.com/app/apikey) and a Gemini model available to the account and compatible with the installed SDK.
- Tesseract OCR installed separately and available on `PATH`, or configured with `TESSERACT_CMD` in the local environment file. PDFium is installed by the Python requirements; Poppler is unnecessary.
- Internet access for installation, the initial embedding-model download, Gemini calls, and configured reminder providers.

## Backend setup

From the project root, in PowerShell:

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Alternatively, on macOS/Linux:

```sh
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For a new setup, fill in **both** `GOOGLE_API_KEY` and `GEMINI_MODEL` in `.env`. If `.env` already exists, merge missing settings instead of overwriting it. The code retains a legacy model default; do not assume that model is still available. Keep credentials only in the ignored local file.

Start from `backend/`:

```sh
uvicorn app.main:app --reload --port 8000
```

The API runs at [localhost:8000](http://localhost:8000), with endpoint schemas at [/docs](http://localhost:8000/docs). Write requests require an allowed Origin, so use the frontend for normal interactive operations. The first operation requiring embeddings downloads the configured model into the local HuggingFace cache.

## Frontend setup

In a second terminal, from the project root:

```sh
cd frontend
npm ci
npm run dev
```

Open [localhost:5173](http://localhost:5173). Vite proxies `/api` to the backend on port 8000. Use port 5173: the backend currently allows only `localhost:5173` and `127.0.0.1:5173` as browser origins.

## Configuration

The [environment template](backend/.env.example) has empty credential fields and generic settings.

| Settings | Purpose |
| --- | --- |
| `GOOGLE_API_KEY`, `GEMINI_MODEL` | Gemini credentials and model selection |
| `EMBEDDING_MODEL` | Local CPU model; defaults to `sentence-transformers/all-MiniLM-L6-v2` |
| `CHUNK_SIZE`, `CHUNK_OVERLAP`, `RETRIEVAL_K` | Defaults: 1000 characters, 150 characters, and 5 chunks |
| `UPLOAD_DIR`, `CHROMA_DIR` | Storage paths relative to `backend/`, unless absolute |
| `AUTH_SECRET`, `AUTH_COOKIE_SECURE` | Optional signing secret and HTTPS cookie flag |
| `TESSERACT_CMD` | Optional OCR executable path |
| `REMINDER_DELIVERY_MODE` | `email`, `manual_whatsapp`, or `whatsapp_cloud` |
| `SMTP_*`, `APP_PUBLIC_URL` | Email transport and portal link |
| `WHATSAPP_*` | Cloud API credentials, API version, and template settings |

Changing the embedding model requires rebuilding the index because existing vectors were produced by the previous model.

## Medication reminders

The administrator enters the prescribed medicine, dose pattern, food instruction, start date, duration, timezone, and send times. Email and WhatsApp each require separate patient consent.

- **Email (default):** configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_EMAIL`, and credentials if required by the server. TLS defaults to enabled. Messages include the medicine name, scheduled dose count, time of day, and food instruction (before food, after food, with food, on an empty stomach, or as prescribed). The portal link is optional for managing reminders; reading the instructions does not require signing in. `email_accepted` means server acceptance, not confirmed inbox delivery.
- **Manual WhatsApp:** administrators view due drafts, send them manually, then mark them sent in the app.
- **WhatsApp Cloud:** configure the token, phone-number ID, API version, and approved template. Four body parameters are supplied in order: medicine name, dose count, day part, and food instruction. Verify provider availability and template requirements in the provider account before enabling delivery.

The scheduler checks every 30 seconds while the backend runs. A SQLite ledger tracks dose slots and attempts. Email has a 12-hour catch-up window and up to three attempts separated by five minutes; automatic WhatsApp has a three-minute window. Delivery and exactly-once receipt are not guaranteed. Backend downtime can cause missed reminders.

## Data handling and privacy

Documentation and examples use generic values rather than real records or credentials. The running application handles sensitive information:

| Location or service | Data handled |
| --- | --- |
| `backend/data/uploads/` | Original files; saved filenames include generated identifiers and original filenames |
| `backend/data/chroma_db/` | Extracted text, embeddings, filenames, timestamps, and owner IDs |
| `backend/data/users.sqlite3` | Contact details, roles, consent, salted password hashes, schedules, and delivery records |
| `backend/data/.auth_secret` | Generated signing secret, unless provided through the environment |
| Backend memory | Up to 20 chat turns per user; cleared on reset or process restart |
| Gemini API | Questions and retrieved text with filenames; follow-up rewriting also sends up to five recent question/answer turns |
| SMTP provider | Recipient email, medicine name, scheduled dose count, time of day, food instructions, and portal link |
| WhatsApp Cloud API | Recipient phone number and medication template parameters |

Original documents are not sent to Gemini by this pipeline, but excerpts and history can contain personal or medical information. Follow-ups can make two Gemini calls. Library telemetry and provider retention settings are separate from these explicit application data flows and have not been audited here.

Runtime data, environment files, database sidecars, and logs are ignored by Git. Ignore rules do not remove previously tracked files or history. Keep records, backups, credentials, and identifying screenshots out of commits and shared bug reports. The application does not encrypt uploads or databases at rest.

**Clear documents** removes the patient's documents and chat history; for an administrator it removes all documents and histories. It does not remove accounts or reminder schedules. **Reset chat** clears only the current user's history. Local deletion does not delete information already sent to providers.

## Project structure

```text
backend/
  app/
    main.py             API routes, access checks, chat memory, scheduler lifecycle
    auth.py             SQLite accounts and signed session cookies
    config.py           Environment settings and storage configuration
    document_loader.py  PDF, DOCX, and image extraction / OCR
    chunking.py         Text chunks and ownership metadata
    embeddings.py       Cached local CPU embedding model
    vectorstore.py      Persistent Chroma index and owner filters
    chat_engine.py      Follow-up rewriting, retrieval, and Gemini calls
    prompts.py          Answer and rewrite prompts
    reminders.py        Schedules, delivery ledger, SMTP and WhatsApp adapters
  .env.example          Shareable configuration template
  requirements.txt     Backend dependencies
frontend/
  src/App.jsx           Account, profile, document, and reminder UI
  src/api.js            Same-origin API requests with cookies
  src/components/       Upload and chat components
  vite.config.js        Development server and API proxy
  package-lock.json     Frontend dependency lockfile
docs/architecture.md    Architecture diagram and data boundaries
```

## Verification and deployment limits

```sh
# From backend/, inside the virtual environment
python -m pip check
python -m compileall -q app
python -m unittest discover -s tests

# From frontend/
npm run build
```

Focused reminder-email tests cover message details and all food instructions using a mocked SMTP server, without loading local credentials or accessing patient records. A functional smoke check should also use synthetic documents and cover sign-up, patient isolation, upload/reindex, chat sources, and reminder consent with delivery disabled or a test provider.

Use one backend process for the current design: history and rate limiting are in memory, and each process starts a scheduler. A production frontend build needs a server that serves static files and routes `/api` to FastAPI; Vite's development proxy is not part of that build. Another deployment origin requires updating both CORS and the write-request origin allowlist in `main.py`, configuring the public portal URL, and enabling secure cookies for HTTPS.

Existing dependency pins are retained, with Pydantic declared directly and the unused direct `langchain-community` dependency removed. This is not a dependency security audit or a verified upgrade to current releases. Doctor-summary generation is not implemented.
