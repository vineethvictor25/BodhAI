"""
FastAPI backend for the Medical Document Chatbot.

Endpoints:
  POST /api/upload      -> upload one or more PDF/DOCX/image files
  POST /api/chat        -> ask a question about uploaded documents
  GET  /api/documents    -> list currently indexed documents
  POST /api/reset       -> clear chat memory (keeps documents)
  POST /api/clear-documents -> wipe all uploaded documents + vector DB

Run with:  uvicorn app.main:app --reload --port 8000
"""
import shutil
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import settings
from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    CurrentUser,
    authenticate,
    create_session_token,
    get_session_user,
    get_user_by_id,
    list_patients,
    public_user,
    register_user,
    update_profile,
    user_count,
)
from app.document_loader import load_document
from app.chunking import chunk_document
from app.vectorstore import replace_chunks, list_sources, clear_all, clear_owner
from app.chat_engine import answer_question
from app.reminders import (
    create_reminder,
    deactivate_reminder,
    get_manual_due_reminders,
    is_email_configured,
    is_whatsapp_configured,
    list_reminders,
    mark_manual_delivery,
    process_due_reminders,
)

settings.validate()

app = FastAPI(title="Medical Document Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep conversation context isolated by stable user ID.
CHAT_HISTORY: dict[str, list[dict]] = {}
LOGIN_ATTEMPTS: dict[str, tuple[float, int]] = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 10
ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}


class ChatRequest(BaseModel):
    message: str


def _saved_uploads(filename: str, owner_id: str | None = None) -> list[Path]:
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid document filename.")
    suffix = f"_{filename}"
    return [
        path for path in settings.UPLOAD_DIR.iterdir()
        if path.is_file()
        and path.name.endswith(suffix)
        and (owner_id is None or path.name.startswith(f"{owner_id}_"))
    ]


@app.middleware("http")
async def validate_request_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        if request.headers.get("origin") not in ALLOWED_ORIGINS:
            return Response(status_code=403, content="Untrusted request origin.")
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status():
    return {"needs_admin_setup": user_count() == 0}


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class SignupRequest(BaseModel):
    name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    phone_number: str = Field(max_length=16)
    whatsapp_opt_in: bool
    email_reminder_opt_in: bool = False
    password: str = Field(min_length=12, max_length=128)


class ProfileUpdate(BaseModel):
    name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    phone_number: str = Field(max_length=16)
    whatsapp_opt_in: bool
    email_reminder_opt_in: bool = False
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str | None = Field(default=None, min_length=12, max_length=128)


class ReminderCreate(BaseModel):
    patient_id: str | None = None
    medicine_name: str = Field(max_length=120)
    dose_pattern: str = Field(max_length=20)
    food_instruction: Literal["BF", "AF", "WITH", "EMPTY", "NONE"]
    start_date: date
    duration_days: int = Field(ge=1, le=3650)
    morning_time: str | None = None
    afternoon_time: str | None = None
    night_time: str | None = None
    timezone: str = Field(max_length=64)


class ManualDelivery(BaseModel):
    scheduled_date: date
    slot: Literal["morning", "afternoon", "night"]


reminder_scheduler = BackgroundScheduler(timezone="UTC")


@app.on_event("startup")
def start_reminder_scheduler():
    if not reminder_scheduler.running:
        reminder_scheduler.add_job(
            process_due_reminders,
            "interval",
            seconds=30,
            id="medication-reminders",
            max_instances=1,
            coalesce=True,
        )
        reminder_scheduler.start()


@app.on_event("shutdown")
def stop_reminder_scheduler():
    if reminder_scheduler.running:
        reminder_scheduler.shutdown(wait=False)


@app.post("/api/auth/signup")
def signup(details: SignupRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    rate_key = f"signup:{client_ip}"
    window_start, failures = LOGIN_ATTEMPTS.get(rate_key, (now, 0))
    if now - window_start >= LOGIN_WINDOW_SECONDS:
        window_start, failures = now, 0
    if failures >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many account creation attempts. Try again later.")
    try:
        user = register_user(
            details.name,
            details.email,
            details.phone_number,
            details.password,
            details.whatsapp_opt_in,
            details.email_reminder_opt_in,
        )
    except ValueError as error:
        LOGIN_ATTEMPTS[rate_key] = (window_start, failures + 1)
        raise HTTPException(status_code=400, detail=str(error)) from error
    LOGIN_ATTEMPTS.pop(rate_key, None)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(user),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/api",
    )
    return public_user(user)


@app.post("/api/auth/login")
def login(credentials: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window_start, failures = LOGIN_ATTEMPTS.get(client_ip, (now, 0))
    if now - window_start >= LOGIN_WINDOW_SECONDS:
        window_start, failures = now, 0
    if failures >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")

    user = authenticate(credentials.email, credentials.password)
    if user is None:
        LOGIN_ATTEMPTS[client_ip] = (window_start, failures + 1)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    LOGIN_ATTEMPTS.pop(client_ip, None)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(user),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/api",
    )
    return public_user(user)


@app.get("/api/auth/me")
def current_session(user: CurrentUser = Depends(get_session_user)):
    return public_user(user)


@app.patch("/api/auth/profile")
def update_account_profile(
    details: ProfileUpdate,
    response: Response,
    user: CurrentUser = Depends(get_session_user),
):
    try:
        updated_user = update_profile(
            user.id,
            details.name,
            details.email,
            details.phone_number,
            details.whatsapp_opt_in,
            details.email_reminder_opt_in,
            details.current_password,
            details.new_password,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(updated_user),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/api",
    )
    return public_user(updated_user)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True, samesite="strict")
    return {"status": "signed out"}


@app.post("/api/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    owner_id: str | None = Form(None),
    user: CurrentUser = Depends(get_session_user),
):
    if user.role == "admin":
        target_patient = get_user_by_id(owner_id) if owner_id else None
        if target_patient is None or target_patient.role != "patient":
            raise HTTPException(status_code=404, detail="Select a valid patient account.")
        target_owner = target_patient.id
    else:
        if owner_id not in (None, user.id):
            raise HTTPException(status_code=403, detail="You can only upload to your own records.")
        target_owner = user.id
    results = []

    for file in files:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            results.append({
                "filename": file.filename,
                "status": "skipped",
                "reason": f"Unsupported file type '{suffix}'",
            })
            continue

        # Save with a unique name on disk to avoid collisions, but keep
        # the original name as metadata for a friendly display.
        safe_name = f"{target_owner}_{uuid.uuid4().hex}_{file.filename}"
        dest_path = settings.UPLOAD_DIR / safe_name
        previous_files = _saved_uploads(file.filename, target_owner)

        try:
            with dest_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)

            text = load_document(dest_path)
            doc_type = "image" if suffix in (".png", ".jpg", ".jpeg") else suffix.strip(".")
            chunks = chunk_document(
                text,
                filename=file.filename,
                doc_type=doc_type,
                owner_id=target_owner,
            )
            if not chunks:
                dest_path.unlink(missing_ok=True)
                results.append({
                    "filename": file.filename,
                    "status": "empty",
                    "chunks_added": 0,
                    "characters_extracted": len(text),
                    "reason": "No text could be extracted; the existing index was left unchanged.",
                })
                continue

            added = replace_chunks(chunks, source=file.filename, owner_id=target_owner)
            for previous_file in previous_files:
                previous_file.unlink(missing_ok=True)

            results.append({
                "filename": file.filename,
                "status": "indexed" if added else "empty",
                "chunks_added": added,
                "characters_extracted": len(text),
            })
        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "error",
                "reason": str(e),
            })

    return {"results": results}


@app.get("/api/documents")
def get_documents(user: CurrentUser = Depends(get_session_user)):
    documents = list_sources(owner_id=None if user.role == "admin" else user.id)
    for document in documents:
        patient = get_user_by_id(document["owner_id"])
        document["patient_name"] = patient.name if patient else "Unassigned legacy record"
        document["patient_email"] = patient.email if patient else ""
        document["can_reindex"] = bool(
            _saved_uploads(document["filename"], document["owner_id"])
        )
    return {"documents": documents}


@app.get("/api/admin/patients")
def get_patients(user: CurrentUser = Depends(get_session_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return {"patients": [public_user(patient) for patient in list_patients()]}


@app.get("/api/reminders")
def get_reminders(user: CurrentUser = Depends(get_session_user)):
    return {
        "reminders": list_reminders(patient_id=None if user.role == "admin" else user.id),
        "whatsapp_configured": is_whatsapp_configured(),
        "email_configured": is_email_configured(),
        "delivery_mode": settings.REMINDER_DELIVERY_MODE,
    }


@app.get("/api/reminders/due")
def get_due_reminders(user: CurrentUser = Depends(get_session_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return {"reminders": get_manual_due_reminders()}


@app.post("/api/reminders/{reminder_id}/manual-sent")
def mark_reminder_manually_sent(
    reminder_id: str,
    details: ManualDelivery,
    user: CurrentUser = Depends(get_session_user),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    try:
        marked = mark_manual_delivery(
            reminder_id,
            details.scheduled_date.isoformat(),
            details.slot,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not marked:
        raise HTTPException(status_code=404, detail="Due reminder was already sent or is no longer active.")
    return {"status": "marked sent"}


@app.post("/api/reminders", status_code=201)
def add_reminder(details: ReminderCreate, user: CurrentUser = Depends(get_session_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only an administrator can configure medication schedules.")
    patient = get_user_by_id(details.patient_id) if details.patient_id else None
    if patient is None or patient.role != "patient":
        raise HTTPException(status_code=404, detail="Select a valid patient account.")
    try:
        reminder_id = create_reminder(
            patient_id=patient.id,
            medicine_name=details.medicine_name,
            dose_pattern=details.dose_pattern,
            food_instruction=details.food_instruction,
            start_date=details.start_date.isoformat(),
            duration_days=details.duration_days,
            times={
                "morning": details.morning_time,
                "afternoon": details.afternoon_time,
                "night": details.night_time,
            },
            timezone_name=details.timezone,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": reminder_id}


@app.delete("/api/reminders/{reminder_id}")
def stop_reminder(reminder_id: str, user: CurrentUser = Depends(get_session_user)):
    patient_id = None if user.role == "admin" else user.id
    if not deactivate_reminder(reminder_id, patient_id=patient_id):
        raise HTTPException(status_code=404, detail="Reminder not found or already stopped.")
    return {"status": "stopped"}


@app.post("/api/documents/{filename}/reindex")
def reindex_document(
    filename: str,
    owner_id: str | None = None,
    user: CurrentUser = Depends(get_session_user),
):
    if user.role != "admin" and owner_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="You can only reindex your own records.")
    target_owner = owner_id if user.role == "admin" else user.id
    if user.role == "admin":
        target_patient = get_user_by_id(target_owner) if target_owner else None
        if target_patient is None or target_patient.role != "patient":
            raise HTTPException(status_code=404, detail="Patient account not found.")
    saved_files = _saved_uploads(filename, target_owner)
    if not saved_files:
        raise HTTPException(status_code=404, detail="Saved upload not found for this document.")

    source_path = max(saved_files, key=lambda path: path.stat().st_mtime_ns)
    try:
        text = load_document(source_path)
        suffix = source_path.suffix.lower()
        doc_type = "image" if suffix in (".png", ".jpg", ".jpeg") else suffix.strip(".")
        chunks = chunk_document(
            text,
            filename=filename,
            doc_type=doc_type,
            owner_id=target_owner,
        )
        if not chunks:
            raise HTTPException(status_code=422, detail="No text could be extracted; the existing index was left unchanged.")
        added = replace_chunks(chunks, source=filename, owner_id=target_owner)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Reindex failed: {e}") from e

    return {
        "filename": filename,
        "status": "indexed",
        "chunks_added": added,
        "characters_extracted": len(text),
    }


@app.post("/api/chat")
def chat(req: ChatRequest, user: CurrentUser = Depends(get_session_user)):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    history = CHAT_HISTORY.setdefault(user.id, [])
    result = answer_question(
        req.message.strip(),
        history=history,
        owner_id=None if user.role == "admin" else user.id,
    )

    history.append({"question": req.message.strip(), "answer": result["answer"]})
    # Keep history from growing unbounded
    if len(history) > 20:
        del history[: len(history) - 20]

    return result


@app.post("/api/reset")
def reset_chat(user: CurrentUser = Depends(get_session_user)):
    CHAT_HISTORY.pop(user.id, None)
    return {"status": "chat history cleared"}


@app.post("/api/clear-documents")
def clear_documents(user: CurrentUser = Depends(get_session_user)):
    if user.role == "admin":
        clear_all()
        CHAT_HISTORY.clear()
        files = settings.UPLOAD_DIR.glob("*")
        status_message = "all documents and chat history cleared"
    else:
        clear_owner(user.id)
        CHAT_HISTORY.pop(user.id, None)
        files = settings.UPLOAD_DIR.glob(f"{user.id}_*")
        status_message = "your documents and chat history cleared"
    for file_path in files:
        if file_path.is_file():
            file_path.unlink()
    return {"status": status_message}
