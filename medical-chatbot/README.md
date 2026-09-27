# 🩺 MedDoc Assistant

A local, lightweight chatbot that reads your prescriptions and medical
reports (PDF, Word, or photos) and answers your questions about them in
plain, friendly language — powered by **Google AI Studio (Gemini)** for
generation and **local HuggingFace embeddings + ChromaDB** for retrieval,
so nothing about your documents is sent anywhere except the final
question + relevant excerpts to Gemini.

## How it works (RAG pipeline)

```
 Upload (PDF/DOCX/image)
        │
        ▼
 1. Document Loader   → extracts text (OCR fallback for scanned/handwritten docs)
        │
        ▼
 2. Chunking          → RecursiveCharacterTextSplitter (LangChain)
        │
        ▼
 3. Embedding         → HuggingFace "all-MiniLM-L6-v2" (runs locally, CPU)
        │
        ▼
 4. Vector DB         → ChromaDB (persisted locally in backend/data/chroma_db)
        │
        ▼
 5. User asks a question in the chat UI
        │
        ▼
 6. Retriever finds the most relevant chunks
        │
        ▼
 7. Prompting         → Gemini (Google AI Studio) answers using ONLY
                         those chunks, in simple language, with sources
        │
        ▼
 8. Friendly chat UI  → React, shows answer + expandable sources
```

## Project structure

```
medical-chatbot/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app & endpoints
│   │   ├── config.py          # settings from .env
│   │   ├── document_loader.py # PDF/DOCX/image → text (+ OCR fallback)
│   │   ├── chunking.py        # RecursiveCharacterTextSplitter
│   │   ├── embeddings.py      # HuggingFace embedding model
│   │   ├── vectorstore.py     # ChromaDB wrapper
│   │   ├── prompts.py         # prompt templates
│   │   └── chat_engine.py     # retrieval + Gemini call
│   ├── data/
│   │   ├── uploads/           # saved uploaded files
│   │   └── chroma_db/         # persisted vector DB
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── App.jsx             # layout: sidebar (upload) + chat
    │   ├── components/
    │   │   ├── FileUpload.jsx
    │   │   ├── ChatWindow.jsx
    │   │   └── MessageBubble.jsx
    │   └── App.css
    └── package.json
```

## 1. Prerequisites

- **Python 3.10+**
- **Node.js 18+** and npm
- A free **Google AI Studio API key**: https://aistudio.google.com/app/apikey
- **Tesseract OCR** (needed for scanned/photographed prescriptions):
       - macOS: `brew install tesseract`
       - Ubuntu/Debian: `sudo apt install tesseract-ocr`
       - Windows: install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
              and add it to your PATH. The default executable path is
              `C:\Program Files\Tesseract-OCR\tesseract.exe`.

If Tesseract is installed elsewhere, set `TESSERACT_CMD` in `backend/.env`.
Use forward slashes in Windows `.env` paths, for example
`C:/Program Files/Tesseract-OCR/tesseract.exe`.

PDF pages are rendered by `pypdfium2`; Poppler is not required.

## 2. Backend setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# open .env and paste your GOOGLE_API_KEY

uvicorn app.main:app --reload --port 8000
```

The first run will download the embedding model (~80MB) automatically.
Backend runs at **http://localhost:8000**.

## 3. Frontend setup

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at **http://localhost:5173** — open it in your browser.

## 4. Using it

1. Drag & drop (or click to browse) your prescriptions/reports — PDF,
   `.docx`, or a photo (`.jpg`/`.png`) of a handwritten prescription.
2. Wait for the "indexed" status and chunk count per file. The document
       list shows the current number of indexed chunks.
3. For an existing document, choose **Reindex** to rebuild its index from
       the saved upload, or **Replace file** to index an updated file of the
       same type without duplicating its chunks.
4. Ask questions in the chat, e.g.:
   - "What medicines was I prescribed and how should I take them?"
   - "Is my cholesterol level normal?"
   - "Summarize my last blood test."
   - Follow-ups work too: "What about the one before that?"
5. Click **Sources** under any answer to see exactly which document/text
   it was based on.
6. Use **Clear all documents** in the sidebar to wipe everything and
   start fresh.

## Notes on running this locally / lightweight

- Embeddings run **on your CPU**, no API key or internet needed for them.
- The vector DB (Chroma) is just a folder on disk — no server to run.
- Only your **question + the small number of retrieved text snippets**
  are sent to Gemini via the Google AI Studio API — not your full
  documents.
- Everything (documents, embeddings, chat) stays on your laptop except
  that one API call per question.

### OCR and UI troubleshooting

The backend OCR dependencies in `backend/requirements.txt` are:

- `pytesseract==0.3.13`: Python wrapper that calls the separately installed
       Tesseract OCR engine.
- `pypdfium2==5.13.0`: renders scanned PDF pages inside Python without
       launching Poppler executables.
- `Pillow==10.4.0`: supplies image handling for OCR.

If a scanned PDF upload reports `Could not OCR scanned PDF pages: Unable to
get page count`, an older version of the loader was invoking Poppler's
`pdfinfo.exe` and `pdftoppm.exe`. Windows Smart App Control blocked those
executables. The loader now uses `pypdfium2`, so Poppler does not need to be
installed or allowed through Smart App Control. Install the Python
dependencies with `pip install -r requirements.txt` from `backend/`, then
restart the backend.

If the page is blank after documents load, check the browser console for
`useRef is not defined`. The document list uses React's `useRef` hook; the
frontend now imports it in `src/App.jsx`.

## Extending this project

- **Multi-document history & summarization**: add a `/api/summarize`
  endpoint that pulls all chunks for a user across time and asks Gemini
  to produce a longitudinal summary for their doctor (see "Future
  implementation" below).
- Swap ChromaDB for FAISS if you prefer (the `vectorstore.py` module is
  the only place that would need to change).
- Add authentication if this ever needs to support multiple people.

### Planned: Doctor summary view

A future `/api/summarize-for-doctor` endpoint that retrieves *all*
chunks (not just top-k for a question), groups them by document date,
and asks Gemini to produce a structured longitudinal summary — trends in
key values, medication history, and flagged abnormalities — for a
person's doctor to quickly review.
