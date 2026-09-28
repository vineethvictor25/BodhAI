"""Medication reminder storage, due-slot claiming, and WhatsApp delivery."""
import email.message
import json
import re
import sqlite3
import smtplib
import ssl
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import make_msgid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.auth import _connect
from app.config import settings

SLOT_NAMES = ("morning", "afternoon", "night")
EMAIL_CATCHUP_SECONDS = 12 * 60 * 60
EMAIL_MAX_ATTEMPTS = 3
EMAIL_RETRY_DELAY = timedelta(minutes=5)
TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
REGIMEN_PATTERN = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$")
FOOD_TEXT = {
    "BF": "before food",
    "AF": "after food",
    "WITH": "with food",
    "EMPTY": "on an empty stomach",
    "NONE": "as prescribed",
}


class WhatsAppSendError(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class EmailSendError(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


def is_email_configured() -> bool:
    return bool(
        settings.SMTP_HOST
        and settings.SMTP_FROM_EMAIL
        and settings.SMTP_PORT > 0
        and (bool(settings.SMTP_USERNAME) == bool(settings.SMTP_PASSWORD))
    )


def is_whatsapp_configured() -> bool:
    return bool(
        settings.REMINDER_DELIVERY_MODE == "whatsapp_cloud"
        and settings.WHATSAPP_ACCESS_TOKEN
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_TEMPLATE_NAME
        and settings.WHATSAPP_TEMPLATE_LANGUAGE
    )


def get_manual_due_reminders(now: datetime | None = None) -> list[dict]:
    """Return opted-in reminders due today for an administrator to send manually."""
    if settings.REMINDER_DELIVERY_MODE != "manual_whatsapp":
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("The reminder clock must be timezone-aware.")
    with _connect() as connection:
        rows = connection.execute(
            """SELECT r.*, u.name AS patient_name, u.phone_number, u.whatsapp_opt_in
            FROM medication_reminders AS r
            JOIN users AS u ON u.id = r.patient_id
            WHERE r.active = 1 AND u.whatsapp_opt_in = 1 AND u.phone_number != ''"""
        ).fetchall()

    due = []
    for row in rows:
        try:
            local_now = now.astimezone(ZoneInfo(row["timezone"]))
            course_start = date.fromisoformat(row["start_date"])
        except (ZoneInfoNotFoundError, ValueError):
            continue
        if not course_start <= local_now.date() < course_start + timedelta(days=row["duration_days"]):
            continue
        doses = _parse_regimen(row["dose_pattern"])
        for index, slot in enumerate(SLOT_NAMES):
            scheduled_time = row[f"{slot}_time"]
            if not doses[index] or not scheduled_time:
                continue
            scheduled_at = datetime.combine(
                local_now.date(),
                datetime.strptime(scheduled_time, "%H:%M").time(),
                tzinfo=local_now.tzinfo,
            )
            lateness = (local_now - scheduled_at).total_seconds()
            if not 0 <= lateness < 12 * 60 * 60:
                continue
            scheduled_date = local_now.date().isoformat()
            with _connect() as connection:
                delivery = connection.execute(
                    """SELECT status FROM reminder_deliveries
                    WHERE reminder_id = ? AND scheduled_date = ? AND slot = ?""",
                    (row["id"], scheduled_date, slot),
                ).fetchone()
            if delivery and delivery["status"] in {"sending", "accepted", "manual_sent"}:
                continue
            count = doses[index]
            dose_text = f"{count} scheduled dose" + ("s" if count != 1 else "")
            food_text = FOOD_TEXT[row["food_instruction"]]
            message = (
                f"Medication reminder: {row['medicine_name']}, {dose_text} due now "
                f"({slot}). Take it {food_text}. Follow the prescription from your care team."
            )
            due.append({
                "reminder_id": row["id"],
                "patient_id": row["patient_id"],
                "patient_name": row["patient_name"],
                "patient_phone": row["phone_number"],
                "medicine_name": row["medicine_name"],
                "dose_pattern": row["dose_pattern"],
                "food_instruction": row["food_instruction"],
                "slot": slot,
                "scheduled_date": scheduled_date,
                "message": message,
            })
    return due


def mark_manual_delivery(reminder_id: str, scheduled_date: str, slot: str) -> bool:
    if slot not in SLOT_NAMES:
        raise ValueError("Invalid reminder time slot.")
    try:
        selected_date = date.fromisoformat(scheduled_date)
    except ValueError as error:
        raise ValueError("Invalid reminder date.") from error

    with _connect() as connection:
        reminder = connection.execute(
            """SELECT r.*, u.phone_number, u.whatsapp_opt_in
            FROM medication_reminders AS r
            JOIN users AS u ON u.id = r.patient_id
            WHERE r.id = ?""",
            (reminder_id,),
        ).fetchone()
        if (
            settings.REMINDER_DELIVERY_MODE != "manual_whatsapp"
            or reminder is None
            or not reminder["active"]
            or not reminder["phone_number"]
            or not reminder["whatsapp_opt_in"]
        ):
            return False
        start = date.fromisoformat(reminder["start_date"])
        if not start <= selected_date < start + timedelta(days=reminder["duration_days"]):
            return False
        slot_index = SLOT_NAMES.index(slot)
        scheduled_time = reminder[f"{slot}_time"]
        if not _parse_regimen(reminder["dose_pattern"])[slot_index] or not scheduled_time:
            return False
        local_now = datetime.now(ZoneInfo(reminder["timezone"]))
        if local_now.date() != selected_date:
            return False
        scheduled_at = datetime.combine(
            selected_date,
            datetime.strptime(scheduled_time, "%H:%M").time(),
            tzinfo=local_now.tzinfo,
        )
        elapsed = (local_now - scheduled_at).total_seconds()
        if not 0 <= elapsed < 12 * 60 * 60:
            return False
        result = connection.execute(
            """INSERT INTO reminder_deliveries
            (id, reminder_id, scheduled_date, slot, status)
            VALUES (?, ?, ?, ?, 'manual_sent')
            ON CONFLICT(reminder_id, scheduled_date, slot) DO UPDATE SET status = 'manual_sent'
            WHERE reminder_deliveries.status = 'failed'""",
            (str(uuid.uuid4()), reminder_id, selected_date.isoformat(), slot),
        )
    return result.rowcount > 0


def _parse_regimen(pattern: str) -> tuple[int, int, int]:
    match = REGIMEN_PATTERN.fullmatch(pattern)
    if not match:
        raise ValueError("Enter the prescribed dose pattern as morning-afternoon-night, for example 1-0-1.")
    doses = tuple(int(value) for value in match.groups())
    if not any(doses) or any(value > 20 for value in doses):
        raise ValueError("The prescribed dose pattern must include a dose between 1 and 20 in at least one slot.")
    return doses


def _validate_time(value: str | None, required: bool, slot: str) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"Set a send time for the {slot} dose.")
        return None
    if not TIME_PATTERN.fullmatch(value):
        raise ValueError(f"Use 24-hour HH:MM format for the {slot} time.")
    return value


def create_reminder(
    patient_id: str,
    medicine_name: str,
    dose_pattern: str,
    food_instruction: str,
    start_date: str,
    duration_days: int,
    times: dict[str, str | None],
    timezone_name: str,
) -> str:
    medicine_name = medicine_name.strip()
    if not 1 <= len(medicine_name) <= 120:
        raise ValueError("Medicine name must be between 1 and 120 characters.")
    doses = _parse_regimen(dose_pattern)
    food_instruction = food_instruction.upper()
    if food_instruction not in FOOD_TEXT:
        raise ValueError("Choose BF, AF, or no food instruction.")
    try:
        course_start = date.fromisoformat(start_date)
    except ValueError as error:
        raise ValueError("Enter the prescription start date as YYYY-MM-DD.") from error
    if not 1 <= duration_days <= 3650:
        raise ValueError("Course duration must be between 1 and 3650 days.")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Choose a valid IANA timezone, such as Asia/Kolkata.") from error
    slot_times = {
        slot: _validate_time(times.get(slot), doses[index] > 0, slot)
        for index, slot in enumerate(SLOT_NAMES)
    }

    with _connect() as connection:
        patient = connection.execute(
            "SELECT role, email, phone_number, whatsapp_opt_in, email_reminder_opt_in FROM users WHERE id = ?",
            (patient_id,),
        ).fetchone()
        if patient is None or patient["role"] != "patient":
            raise ValueError("Patient account not found.")
        if settings.REMINDER_DELIVERY_MODE == "email":
            if not patient["email_reminder_opt_in"]:
                raise ValueError("The patient must opt in to email reminders in Profile first.")
        elif settings.REMINDER_DELIVERY_MODE in {"manual_whatsapp", "whatsapp_cloud"}:
            if settings.REMINDER_DELIVERY_MODE == "email":
                if not patient["email_reminder_opt_in"]:
                    raise ValueError("The patient must opt in to email reminders in Profile first.")
            elif settings.REMINDER_DELIVERY_MODE in {"manual_whatsapp", "whatsapp_cloud"}:
                if not patient["phone_number"]:
                    raise ValueError("The patient must add a phone number before WhatsApp reminders can be scheduled.")
                if not patient["whatsapp_opt_in"]:
                    raise ValueError("The patient must opt in to WhatsApp reminders in Profile first.")
            else:
                raise ValueError("The configured reminder delivery mode is invalid.")
        else:
            raise ValueError("The configured reminder delivery mode is invalid.")

        reminder_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO medication_reminders (
                id, patient_id, medicine_name, dose_pattern, food_instruction,
                start_date, duration_days, morning_time, afternoon_time, night_time,
                timezone, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                reminder_id,
                patient_id,
                medicine_name,
                "-".join(str(dose) for dose in doses),
                food_instruction,
                course_start.isoformat(),
                duration_days,
                slot_times["morning"],
                slot_times["afternoon"],
                slot_times["night"],
                timezone_name,
            ),
        )
    return reminder_id


def list_reminders(patient_id: str | None = None) -> list[dict]:
    query = """SELECT r.*, u.name AS patient_name, u.email AS patient_email
        FROM medication_reminders AS r
        JOIN users AS u ON u.id = r.patient_id"""
    parameters: tuple[str, ...] = ()
    if patient_id is not None:
        query += " WHERE r.patient_id = ?"
        parameters = (patient_id,)
    query += " ORDER BY r.created_at DESC"
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    reminders = []
    for row in rows:
        try:
            course_end = date.fromisoformat(row["start_date"]) + timedelta(days=row["duration_days"])
            expired = datetime.now(ZoneInfo(row["timezone"])).date() >= course_end
        except (ZoneInfoNotFoundError, ValueError):
            expired = False
        enabled = bool(row["active"])
        with _connect() as connection:
            deliveries = connection.execute(
                """SELECT scheduled_date, slot, status, error_code, attempts, created_at
                FROM reminder_deliveries WHERE reminder_id = ?
                ORDER BY created_at DESC LIMIT 10""",
                (row["id"],),
            ).fetchall()
        reminders.append({
            "id": row["id"],
            "patient_id": row["patient_id"],
            "patient_name": row["patient_name"],
            "patient_email": row["patient_email"],
            "medicine_name": row["medicine_name"],
            "dose_pattern": row["dose_pattern"],
            "food_instruction": row["food_instruction"],
            "start_date": row["start_date"],
            "duration_days": row["duration_days"],
            "morning_time": row["morning_time"],
            "afternoon_time": row["afternoon_time"],
            "night_time": row["night_time"],
            "timezone": row["timezone"],
            "active": enabled and not expired,
            "status": "stopped" if not enabled else "completed" if expired else "active",
            "delivery_history": [dict(delivery) for delivery in deliveries],
        })
    return reminders


def deactivate_reminder(reminder_id: str, patient_id: str | None = None) -> bool:
    query = "UPDATE medication_reminders SET active = 0 WHERE id = ? AND active = 1"
    parameters: tuple[str, ...] = (reminder_id,)
    if patient_id is not None:
        query += " AND patient_id = ?"
        parameters += (patient_id,)
    with _connect() as connection:
        result = connection.execute(query, parameters)
    return result.rowcount > 0


def claim_due_reminders(
    now: datetime | None = None,
    delivery_mode: str | None = None,
) -> list[dict]:
    """Atomically claim today's matching reminder slots to prevent duplicate sends."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("The scheduler clock must be timezone-aware.")

    delivery_mode = delivery_mode or settings.REMINDER_DELIVERY_MODE
    if delivery_mode == "email":
        consent_filter = "u.email_reminder_opt_in = 1 AND u.email != ''"
    elif delivery_mode in {"manual_whatsapp", "whatsapp_cloud"}:
        consent_filter = "u.whatsapp_opt_in = 1 AND u.phone_number != ''"
    else:
        return []

    with _connect() as connection:
        rows = connection.execute(
            f"""SELECT r.*, u.name AS patient_name, u.email AS patient_email,
                u.phone_number, u.whatsapp_opt_in, u.email_reminder_opt_in
            FROM medication_reminders AS r
            JOIN users AS u ON u.id = r.patient_id
            WHERE r.active = 1 AND {consent_filter}"""
        ).fetchall()

    claims = []
    for row in rows:
        try:
            local_now = now.astimezone(ZoneInfo(row["timezone"]))
            start = date.fromisoformat(row["start_date"])
        except (ZoneInfoNotFoundError, ValueError):
            continue
        if not start <= local_now.date() < start + timedelta(days=row["duration_days"]):
            continue

        doses = _parse_regimen(row["dose_pattern"])
        for index, slot in enumerate(SLOT_NAMES):
            scheduled_time = row[f"{slot}_time"]
            if not doses[index] or not scheduled_time:
                continue
            scheduled_at = datetime.combine(
                local_now.date(),
                datetime.strptime(scheduled_time, "%H:%M").time(),
                tzinfo=local_now.tzinfo,
            )
            lateness_seconds = (local_now - scheduled_at).total_seconds()
            max_lateness = EMAIL_CATCHUP_SECONDS if delivery_mode == "email" else 180
            if not 0 <= lateness_seconds < max_lateness:
                continue

            now_utc = now.astimezone(timezone.utc)
            with _connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                delivery = connection.execute(
                    """SELECT id, status, attempts, next_attempt_at FROM reminder_deliveries
                    WHERE reminder_id = ? AND scheduled_date = ? AND slot = ?""",
                    (row["id"], local_now.date().isoformat(), slot),
                ).fetchone()
                if delivery is None:
                    delivery_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO reminder_deliveries
                        (id, reminder_id, scheduled_date, slot, status, attempts)
                        VALUES (?, ?, ?, ?, 'sending', 1)""",
                        (delivery_id, row["id"], local_now.date().isoformat(), slot),
                    )
                    attempt_number = 1
                elif (
                    delivery_mode == "email"
                    and delivery["status"] == "failed"
                    and delivery["attempts"] < EMAIL_MAX_ATTEMPTS
                    and (
                        not delivery["next_attempt_at"]
                        or datetime.fromisoformat(delivery["next_attempt_at"]) <= now_utc
                    )
                ):
                    delivery_id = delivery["id"]
                    result = connection.execute(
                        """UPDATE reminder_deliveries
                        SET status = 'sending', attempts = attempts + 1,
                            next_attempt_at = NULL, error_code = NULL
                        WHERE id = ? AND status = 'failed' AND attempts < ?""",
                        (delivery_id, EMAIL_MAX_ATTEMPTS),
                    )
                    if result.rowcount == 0:
                        continue
                    attempt_number = delivery["attempts"] + 1
                else:
                    continue
            dose_text = f"{doses[index]} scheduled dose" + ("s" if doses[index] != 1 else "")
            claims.append({
                "delivery_id": delivery_id,
                "reminder_id": row["id"],
                "attempt_number": attempt_number,
                "patient_name": row["patient_name"],
                "patient_email": row["patient_email"],
                "patient_phone": row["phone_number"],
                "medicine_name": row["medicine_name"],
                "dose_text": dose_text,
                "slot": slot,
                "food_text": FOOD_TEXT[row["food_instruction"]],
            })
    return claims


def _send_email_reminder(claim: dict) -> str:
    message = email.message.EmailMessage()
    message["Subject"] = "Medication reminder"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = claim["patient_email"]
    message["Message-ID"] = make_msgid()
    message.set_content(
        "Your scheduled medication reminder:\n\n"
        f"Medicine: {claim['medicine_name']}\n"
        f"Dose: {claim['dose_text']}\n"
        f"Time of day: {claim['slot'].capitalize()}\n"
        f"Food instructions: {claim['food_text']}\n\n"
        "Follow the directions from your prescribing care team. If you have "
        "already taken this dose, no action is needed.\n\n"
        f"Manage your reminders: {settings.APP_PUBLIC_URL}"
    )

    try:
        if settings.SMTP_USE_SSL:
            smtp = smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=20,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
        with smtp:
            if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            refused = smtp.send_message(message)
            if refused:
                raise EmailSendError("recipient_refused")
    except EmailSendError:
        raise
    except (OSError, smtplib.SMTPException, ssl.SSLError):
        raise EmailSendError("smtp_delivery_error") from None
    return message["Message-ID"]


def _send_template(claim: dict) -> str:
    url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_GRAPH_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    parameters = [
        claim["medicine_name"],
        claim["dose_text"],
        claim["slot"],
        claim["food_text"],
    ]
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": claim["patient_phone"],
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in parameters],
            }],
        },
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise WhatsAppSendError(f"http_{error.code}") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise WhatsAppSendError("network_or_response_error") from None
    try:
        return result["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        raise WhatsAppSendError("unexpected_api_response") from None


def process_due_reminders() -> int:
    delivery_mode = settings.REMINDER_DELIVERY_MODE
    if delivery_mode == "email":
        if not is_email_configured():
            return 0
        claims = claim_due_reminders(delivery_mode="email")
        for claim in claims:
            try:
                message_id = _send_email_reminder(claim)
                status_value = "email_accepted"
                error_code = None
            except EmailSendError as error:
                message_id = None
                status_value = "failed"
                error_code = error.error_code
            next_attempt_at = None
            if status_value == "failed" and claim["attempt_number"] < EMAIL_MAX_ATTEMPTS:
                next_attempt_at = (
                    datetime.now(timezone.utc) + EMAIL_RETRY_DELAY
                ).isoformat()
            with _connect() as connection:
                connection.execute(
                    """UPDATE reminder_deliveries
                    SET status = ?, provider_message_id = ?, error_code = ?, next_attempt_at = ?
                    WHERE id = ?""",
                    (status_value, message_id, error_code, next_attempt_at, claim["delivery_id"]),
                )
        return len(claims)

    if not is_whatsapp_configured():
        return 0
    claims = claim_due_reminders(delivery_mode="whatsapp_cloud")
    for claim in claims:
        try:
            message_id = _send_template(claim)
            status_value = "accepted"
            error_code = None
        except WhatsAppSendError as error:
            message_id = None
            status_value = "failed"
            error_code = error.error_code
        with _connect() as connection:
            connection.execute(
                "UPDATE reminder_deliveries SET status = ?, provider_message_id = ?, error_code = ? WHERE id = ?",
                (status_value, message_id, error_code, claim["delivery_id"]),
            )
    return len(claims)
