"""Local account storage, password hashing, and signed session cookies."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, status

from app.config import settings

SESSION_COOKIE = "meddoc_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
_HASH_ITERATIONS = 310_000
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
_USER_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.sqlite3"


@dataclass(frozen=True)
class CurrentUser:
    id: str
    name: str
    email: str
    phone_number: str
    whatsapp_opt_in: bool
    email_reminder_opt_in: bool
    role: str


def _connect() -> sqlite3.Connection:
    _USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_USER_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_user_store() -> None:
    with _connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                phone_number TEXT NOT NULL DEFAULT '',
                whatsapp_opt_in INTEGER NOT NULL DEFAULT 0,
                email_reminder_opt_in INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL CHECK (role IN ('admin', 'patient')),
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        if "phone_number" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN phone_number TEXT NOT NULL DEFAULT ''"
            )
        if "whatsapp_opt_in" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN whatsapp_opt_in INTEGER NOT NULL DEFAULT 0"
            )
        if "email_reminder_opt_in" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN email_reminder_opt_in INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS medication_reminders (
                id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                medicine_name TEXT NOT NULL,
                dose_pattern TEXT NOT NULL,
                food_instruction TEXT NOT NULL,
                start_date TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                morning_time TEXT,
                afternoon_time TEXT,
                night_time TEXT,
                timezone TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS reminder_deliveries (
                id TEXT PRIMARY KEY,
                reminder_id TEXT NOT NULL,
                scheduled_date TEXT NOT NULL,
                slot TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_message_id TEXT,
                error_code TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (reminder_id, scheduled_date, slot)
            )"""
        )
        delivery_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(reminder_deliveries)")
        }
        if "attempts" not in delivery_columns:
            connection.execute(
                "ALTER TABLE reminder_deliveries ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "next_attempt_at" not in delivery_columns:
            connection.execute(
                "ALTER TABLE reminder_deliveries ADD COLUMN next_attempt_at TEXT"
            )


def _row_to_user(row: sqlite3.Row | None) -> CurrentUser | None:
    if row is None:
        return None
    return CurrentUser(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        phone_number=row["phone_number"],
        whatsapp_opt_in=bool(row["whatsapp_opt_in"]),
        email_reminder_opt_in=bool(row["email_reminder_opt_in"]),
        role=row["role"],
    )


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS)


def _validate_profile(name: str, email: str, phone_number: str) -> tuple[str, str, str]:
    name = name.strip()
    email = email.strip().lower()
    phone_number = phone_number.strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Name must be between 1 and 120 characters.")
    if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    if not _PHONE_PATTERN.fullmatch(phone_number):
        raise ValueError("Enter a phone number in international format, starting with + and the country code.")
    return name, email, phone_number


def register_user(
    name: str,
    email: str,
    phone_number: str,
    password: str,
    whatsapp_opt_in: bool = False,
    email_reminder_opt_in: bool = False,
) -> CurrentUser:
    name, email, phone_number = _validate_profile(name, email, phone_number)
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must be between 12 and 128 characters.")

    user_id = str(uuid.uuid4())
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        role = "admin" if user_count == 0 else "patient"
        try:
            connection.execute(
                "INSERT INTO users (id, name, email, phone_number, whatsapp_opt_in, email_reminder_opt_in, role, password_salt, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, email, phone_number, int(whatsapp_opt_in), int(email_reminder_opt_in), role, salt, password_hash),
            )
        except sqlite3.IntegrityError as error:
            if "email" in str(error).lower():
                raise ValueError("An account with this email already exists.") from error
            raise
    return CurrentUser(
        id=user_id,
        name=name,
        email=email,
        phone_number=phone_number,
        whatsapp_opt_in=whatsapp_opt_in,
        email_reminder_opt_in=email_reminder_opt_in,
        role=role,
    )


def update_profile(
    user_id: str,
    name: str,
    email: str,
    phone_number: str,
    whatsapp_opt_in: bool,
    email_reminder_opt_in: bool,
    current_password: str | None = None,
    new_password: str | None = None,
) -> CurrentUser:
    name, email, phone_number = _validate_profile(name, email, phone_number)
    if bool(current_password) != bool(new_password):
        raise ValueError("Enter both your current and new password to change your password.")
    if new_password and not 12 <= len(new_password) <= 128:
        raise ValueError("New password must be between 12 and 128 characters.")

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("Account not found.")
        password_salt = row["password_salt"]
        password_hash = row["password_hash"]
        if new_password:
            if not hmac.compare_digest(_hash_password(current_password, password_salt), password_hash):
                raise ValueError("Current password is incorrect.")
            password_salt = secrets.token_bytes(16)
            password_hash = _hash_password(new_password, password_salt)
        try:
            connection.execute(
                "UPDATE users SET name = ?, email = ?, phone_number = ?, whatsapp_opt_in = ?, email_reminder_opt_in = ?, password_salt = ?, password_hash = ? "
                "WHERE id = ?",
                (name, email, phone_number, int(whatsapp_opt_in), int(email_reminder_opt_in), password_salt, password_hash, user_id),
            )
        except sqlite3.IntegrityError as error:
            if "email" in str(error).lower():
                raise ValueError("An account with this email already exists.") from error
            raise
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("Account not found.")
    return user


def authenticate(email: str, password: str) -> CurrentUser | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (email.strip(),),
        ).fetchone()
    user = _row_to_user(row)
    if row is None:
        _hash_password(password, b"meddoc-invalid-salt")
        return None
    if not hmac.compare_digest(_hash_password(password, row["password_salt"]), row["password_hash"]):
        return None
    return user


def get_user_by_id(user_id: str) -> CurrentUser | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row)


def list_patients() -> list[CurrentUser]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM users WHERE role = 'patient' ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [user for row in rows if (user := _row_to_user(row)) is not None]


def user_count() -> int:
    with _connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def public_user(user: CurrentUser) -> dict[str, str]:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone_number": user.phone_number,
        "whatsapp_opt_in": user.whatsapp_opt_in,
        "email_reminder_opt_in": user.email_reminder_opt_in,
        "role": user.role,
    }


def create_session_token(user: CurrentUser) -> str:
    payload = json.dumps({
        "sub": user.id,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(settings.AUTH_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def get_session_user(request: Request) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Please sign in to continue.",
    )
    if not token or token.count(".") != 1:
        raise unauthorized

    encoded, supplied_signature = token.split(".", 1)
    expected_signature = base64.urlsafe_b64encode(
        hmac.new(settings.AUTH_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise unauthorized

    try:
        payload_bytes = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(payload_bytes)
        user_id = payload["sub"]
        expires_at = payload["exp"]
        if expires_at <= time.time():
            raise unauthorized
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise unauthorized

    user = get_user_by_id(user_id)
    if user is None:
        raise unauthorized
    return user


initialize_user_store()
