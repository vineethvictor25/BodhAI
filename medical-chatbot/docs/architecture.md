# Application architecture and flows

This document has two levels: a **high-level architecture** showing how the components connect, followed by **four detailed flows** showing how the application is used:

1. Open the app and sign in.
2. Upload and index a document.
3. Ask a question and receive an answer.
4. Configure and deliver reminders.

## High-level architecture

Use this overview to locate the browser, backend, storage, and external services. Its arrows show component connections and data exchange, not a single execution sequence. For a starting point and step-by-step order, follow the four flow diagrams below.

```mermaid
flowchart TB
    subgraph browser[Browser]
        UI[React UI: accounts, uploads, chat, reminders]
    end
    subgraph local[Local application]
        Vite[Vite development server: port 5173]
        API[FastAPI: port 8000 /api]
        Auth[Session validation and role checks]
        Users[(SQLite: accounts, consent, schedules, delivery ledger)]
        Secret[Local signing secret or environment override]
        Uploads[(Saved original uploads)]
        Loader[PDF / DOCX extraction and Tesseract OCR]
        Split[Overlapping chunks and ownership metadata]
        Embed[Local HuggingFace CPU embeddings]
        Chroma[(Persistent Chroma: text, vectors, metadata)]
        Chat[Chat engine: rewrite, retrieve, answer]
        Memory[Per-user chat history in memory]
        Scheduler[APScheduler: every 30 seconds]
        Reminder[Consent checks, due slots, delivery claims]
        Draft[Manual WhatsApp draft in admin UI]
    end
    subgraph external[External services]
        HF[HuggingFace model download]
        Gemini[Google Gemini API]
        SMTP[SMTP email provider]
        WA[WhatsApp Cloud API]
    end
    UI -->|API requests with session cookie| Vite
    Vite -->|Development proxy| API
    API --> Auth
    Auth <--> Users
    Secret --> Auth
    API -->|Authorized upload| Uploads
    Uploads --> Loader --> Split --> Embed --> Chroma
    HF -.->|Initial model weights| Embed
    API -->|Authorized question| Chat
    Chat <--> Memory
    Chat -->|Question embedding| Embed
    Chat <-->|Patient owner filter or admin-wide retrieval| Chroma
    Chat <-->|Question, excerpts, filenames; history for rewrites| Gemini
    Chat -->|Answer and sources via API| UI
    API -->|Manage schedules and manual delivery| Reminder
    Scheduler --> Reminder
    Reminder <--> Users
    Reminder -->|Email address and medication details| SMTP
    Reminder -->|Phone and medication template fields| WA
    Reminder --> Draft --> UI
    classDef externalService fill:#f3e8ff,stroke:#7e22ce,color:#581c87;
    class HF,Gemini,SMTP,WA externalService;
```

## Four application flows

**Start here:** a person opens the app and signs in through flow 1. They then choose to upload a document (flow 2), ask a question (flow 3), or manage reminders (flow 4). These are separate flows, not one continuous pipeline. Automatic reminders have their own starting point within flow 4: the backend timer.

Read each diagram **from top to bottom**, following the numbered steps and labeled arrows. Rounded **START** and **END** boxes mark the boundaries. Cylinders represent stored data; purple boxes represent external services. All labels are generic and contain no personal data or credentials.

### Flow 1. Entry point: open the app and sign in

```mermaid
flowchart TD
    Start([START: Open the app in a browser]) --> UI[1. React displays sign-up or sign-in]
    UI --> Proxy[2. Vite forwards the request to FastAPI]
    Proxy --> Account[3. Backend creates or verifies the account]
    Account -.-> DB[(SQLite accounts and password hashes)]
    Account --> Session[4. Browser receives a signed session cookie]
    Session --> Action{5. Choose an action}
    Action --> Upload([Upload a document: flow 2])
    Action --> Chat([Ask a question: flow 3])
    Action --> Reminder([Manage reminders: flow 4])
    classDef start fill:#dcfce7,stroke:#15803d,color:#14532d;
    class Start start;
```

Invalid credentials return an error instead of a session. The first registered account becomes the administrator; later accounts are patients. Subsequent protected requests include the session cookie. FastAPI verifies the session and applies role checks before performing the requested action. Vite is the proxy during local development only.

### Flow 2. Upload: turn a document into searchable text

**Starting action:** the patient selects a file, or the administrator selects a patient and a file.

```mermaid
flowchart TD
    Start([START: Select and upload a document]) --> Auth[1. FastAPI checks session and patient access]
    Auth --> Save[2. Save the original file locally]
    Save -.-> Files[(Local upload folder)]
    Save --> Extract[3. Extract PDF or DOCX text; use OCR for images and scanned pages]
    Extract --> Split[4. Split text into overlapping chunks with owner and filename]
    Split --> Embed[5. Local HuggingFace model creates embeddings]
    Embed --> Index[6. Replace index entries for this owner and filename]
    Index -.-> Chroma[(Chroma: text, vectors, and metadata)]
    Index --> Done([END: UI shows indexed status and chunk count])
    classDef start fill:#dcfce7,stroke:#15803d,color:#14532d;
    class Start start;
```

This is the successful upload path. Unsupported files, extraction failures, and empty documents return a status to the UI. Reindex starts from the saved file at step 3. The embedding model is downloaded from HuggingFace on first use and then cached locally. Upload indexing does not call Gemini.

### Flow 3. Chat: retrieve relevant text and generate an answer

**Starting action:** the signed-in user types a question after uploading a document.

```mermaid
flowchart TD
    Start([START: Submit a question]) --> Auth[1. FastAPI checks the session and reads this user's chat history]
    Auth --> Follow{2. Is there previous chat history?}
    Follow -->|Yes| Rewrite[Gemini rewrites the question using up to five recent turns]
    Follow -->|No: use the question as entered| Embed[3. Embed the search question locally]
    Rewrite --> Embed
    Embed --> Search[4. Search Chroma for relevant text chunks]
    DB[(Chroma document index)] -.-> Search
    Search --> Found{5. Were matching chunks found?}
    Found -->|Yes| Generate[6. Gemini receives the current question and retrieved text with filenames]
    Found -->|No| Fallback[6. Return a local message asking for relevant documents]
    Generate --> Result[7. Save the turn in memory and return the response]
    Fallback --> Result
    Result --> End([END: UI displays the answer and any source excerpts])
    classDef start fill:#dcfce7,stroke:#15803d,color:#14532d;
    classDef external fill:#f3e8ff,stroke:#7e22ce,color:#581c87;
    class Start start;
    class Rewrite,Generate external;
```

Patients search only their own documents. Administrator searches span all patients' records, even when a patient is selected for uploading. History holds up to 20 turns per user in backend memory and disappears on reset or process restart. A follow-up can make two Gemini calls: one to rewrite the question and another to generate the answer.

### Flow 4. Reminders: configure a schedule, then deliver when due

**Starting action:** an administrator enters a schedule. **Later trigger:** the backend timer checks schedules every 30 seconds. Manual WhatsApp drafts are requested separately through the administrator UI.

```mermaid
flowchart TD
    Start([START: Administrator submits a reminder schedule]) --> Validate[1. Backend checks admin access, patient, and schedule fields]
    Validate --> Save[2. Save the schedule in SQLite]
    Save --> Configured([END OF SETUP: Schedule saved])
    Save -.-> DB[(SQLite: schedules, patient consent, delivery ledger)]

    Timer([AUTOMATIC START: Backend timer fires every 30 seconds]) --> Check[3. Check configured provider, consent, due time, and delivery ledger]
    DB -.-> Check
    Check --> Due{4. Eligible automatic delivery?}
    Due -->|No| Wait([END THIS CHECK: Wait for the next timer tick])
    Due -->|Yes| Claim[5. Claim the dose slot in SQLite]
    Claim --> Channel{6. Selected automatic channel}
    Channel -->|Email| SMTP[SMTP: send medicine, dose count, time of day, and food instructions]
    Channel -->|WhatsApp Cloud| WA[WhatsApp API: send medication template fields]
    SMTP --> Record[7. Record provider acceptance or failure]
    WA --> Record
    Record -.-> DB
    Record --> End([END: Delivery attempt recorded])

    Manual([MANUAL START: Admin opens due drafts in manual mode]) --> Draft[3. Backend checks consent and returns due drafts]
    DB -.-> Draft
    Draft --> Send[4. Admin sends the message outside the app]
    Send --> Mark[5. Admin marks the dose sent in the app]
    Mark -.-> DB
    Mark --> ManualEnd([END: Manual delivery recorded])

    classDef start fill:#dcfce7,stroke:#15803d,color:#14532d;
    classDef external fill:#f3e8ff,stroke:#7e22ce,color:#581c87;
    class Start,Timer,Manual start;
    class SMTP,WA external;
```

Email and WhatsApp each require separate patient consent. The backend must keep running for automatic sends. The ledger reduces duplicate attempts, but provider acceptance does not confirm receipt or guarantee exactly-once delivery. Patients can stop their own schedules; administrators can stop any schedule.

## Storage and external boundaries

| Component | Role in the flows |
| --- | --- |
| React UI | Starting point for sign-in, uploads, chat, and schedule management |
| Vite / FastAPI | Development request routing / backend processing and access checks |
| SQLite | Accounts, salted password hashes, consent, schedules, and delivery records |
| Upload folder | Original documents used for extraction and reindexing |
| Chroma | Extracted text, embeddings, filenames, timestamps, and owner identifiers |
| Backend memory | Per-user chat history and rate-limit state |
| Local signing secret or environment override | Signs and validates session cookies |
| HuggingFace | Initial embedding-model download; embeddings are computed locally |
| Gemini | External question rewriting and answer generation |
| SMTP / WhatsApp Cloud | Optional external reminder delivery |

The local components do not imply offline operation. Gemini receives questions, retrieved excerpts with filenames, and recent history for rewrites. SMTP receives the recipient email, medicine name, scheduled dose count, time of day, and food instructions; WhatsApp Cloud receives the phone number and medication template parameters. Dependency telemetry is not represented or audited.

Uploads and databases are not encrypted at rest by the application. SQLite and the generated signing secret use `backend/data/`; upload and Chroma paths are configurable. Backups can contain sensitive data and belong outside shared artifacts.

Use one backend process for the current in-memory state and scheduler lifecycle. A deployed static frontend needs API routing equivalent to the development proxy and updates to the backend's allowed origins.

See the [README](../README.md) for setup, configuration, and privacy details.
