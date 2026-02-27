import os
import secrets
import smtplib
import time
from email.message import EmailMessage

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from database import get_connection, init_db
from models import StudentRegister, AnswerSubmission, FrameData, ViolationLog, OTPRequest, OTPVerify
from questions import QUESTIONS
from yolo_detector import analyze_frame

VIOLATION_DEDUP_SECONDS = int(os.getenv("VIOLATION_DEDUP_SECONDS", "20"))
_last_violation_logged = {}
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
EMAIL_VERIFIED_TTL_SECONDS = int(os.getenv("EMAIL_VERIFIED_TTL_SECONDS", "1800"))
_otp_store = {}
_verified_emails = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="NExpert Proctored Quiz", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _cleanup_auth_state():
    now = time.time()
    for email, record in list(_otp_store.items()):
        if record["expires_at"] <= now:
            _otp_store.pop(email, None)
    for email, expires_at in list(_verified_emails.items()):
        if expires_at <= now:
            _verified_emails.pop(email, None)


def _send_otp_email(email: str, otp: str):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_host or not smtp_user or not smtp_pass or not smtp_from:
        raise HTTPException(
            status_code=500,
            detail="OTP email service not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.",
        )

    msg = EmailMessage()
    msg["Subject"] = "Your NExpert OTP Verification Code"
    msg["From"] = smtp_from
    msg["To"] = email
    msg.set_content(
        f"Your OTP is {otp}. It is valid for {OTP_TTL_SECONDS // 60} minutes.\n\nNExpert Academy"
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send OTP email: {e}")


@app.post("/api/request-otp")
def request_otp(data: OTPRequest):
    _cleanup_auth_state()
    email = _normalize_email(data.email)
    otp = f"{secrets.randbelow(1_000_000):06d}"
    _otp_store[email] = {"otp": otp, "expires_at": time.time() + OTP_TTL_SECONDS}
    _send_otp_email(email, otp)
    return {"message": "OTP sent successfully"}


@app.post("/api/verify-otp")
def verify_otp(data: OTPVerify):
    _cleanup_auth_state()
    email = _normalize_email(data.email)
    otp_input = data.otp.strip()
    record = _otp_store.get(email)
    if not record:
        raise HTTPException(status_code=400, detail="OTP not found or expired")
    if record["otp"] != otp_input:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    _otp_store.pop(email, None)
    _verified_emails[email] = time.time() + EMAIL_VERIFIED_TTL_SECONDS
    return {"verified": True, "message": "Email verified"}


@app.post("/api/register")
def register_student(data: StudentRegister):
    if data.email != data.confirm_email:
        raise HTTPException(status_code=400, detail="Emails do not match")
    _cleanup_auth_state()
    email = _normalize_email(data.email)
    if _verified_emails.get(email, 0) <= time.time():
        raise HTTPException(status_code=400, detail="Please verify your email with OTP first")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM students WHERE email = %s", (email,))
            existing = cur.fetchone()
            if existing:
                return {"student_id": existing["id"], "message": "Already registered"}

            cur.execute(
                "INSERT INTO students (name, email) VALUES (%s, %s)",
                (data.name, email),
            )
            conn.commit()
            _verified_emails.pop(email, None)
            return {"student_id": cur.lastrowid, "message": "Registered successfully"}
    finally:
        conn.close()


@app.get("/api/questions")
def get_questions():
    safe_questions = [
        {"id": q["id"], "question": q["question"], "options": q["options"]}
        for q in QUESTIONS
    ]
    return {"questions": safe_questions, "total": len(safe_questions), "duration_minutes": 60}


@app.post("/api/submit")
def submit_answers(data: AnswerSubmission):
    score = 0
    for q in QUESTIONS:
        submitted = data.answers.get(str(q["id"]))
        if submitted and submitted == q["answer"]:
            score += 1

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM students WHERE id = %s", (data.student_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Student not found")

            cur.execute(
                "UPDATE students SET score = %s WHERE id = %s",
                (score, data.student_id),
            )
            conn.commit()

        return {
            "student_id": data.student_id,
            "score": score,
            "total": len(QUESTIONS),
            "percentage": round(score / len(QUESTIONS) * 100, 2),
        }
    finally:
        conn.close()


# ── YOLO Frame Analysis ──────────────────────────────────────────

@app.post("/api/analyze-frame")
def analyze(data: FrameData):
    result = analyze_frame(data.frame)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            logged_count = 0
            if result["violation_count"] > 0:
                now = time.time()
                for v in result["violations"]:
                    dedup_key = (data.student_id, v)
                    prev = _last_violation_logged.get(dedup_key, 0)
                    if now - prev >= VIOLATION_DEDUP_SECONDS:
                        cur.execute(
                            "INSERT INTO violations (student_id, violation_type) VALUES (%s, %s)",
                            (data.student_id, v),
                        )
                        _last_violation_logged[dedup_key] = now
                        logged_count += 1
            if logged_count > 0:
                cur.execute(
                    "UPDATE students SET violation_count = violation_count + %s WHERE id = %s",
                    (logged_count, data.student_id),
                )

            cur.execute(
                "SELECT violation_count FROM students WHERE id = %s",
                (data.student_id,),
            )
            row = cur.fetchone()
            conn.commit()

        result["total_violations"] = row["violation_count"] if row else 0
    finally:
        conn.close()

    return result


# ── Log Tab Switch ────────────────────────────────────────────────

@app.post("/api/log-violation")
def log_violation(data: ViolationLog):
    violation_text = data.violation_type.strip().lower()
    is_tab_switch = "tab switch" in violation_text
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO violations (student_id, violation_type) VALUES (%s, %s)",
                (data.student_id, data.violation_type),
            )
            if is_tab_switch:
                cur.execute(
                    """
                    UPDATE students
                    SET violation_count = violation_count + 1,
                        tab_switch_count = tab_switch_count + 1
                    WHERE id = %s
                    """,
                    (data.student_id,),
                )
            else:
                cur.execute(
                    "UPDATE students SET violation_count = violation_count + 1 WHERE id = %s",
                    (data.student_id,),
                )
            cur.execute(
                "SELECT violation_count, tab_switch_count FROM students WHERE id = %s",
                (data.student_id,),
            )
            row = cur.fetchone()
            conn.commit()
        return {
            "total_violations": row["violation_count"] if row else 0,
            "tab_switch_count": row["tab_switch_count"] if row else 0,
        }
    finally:
        conn.close()


# ── Result ────────────────────────────────────────────────────────

@app.get("/api/result/{student_id}")
def get_result(student_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM students WHERE id = %s", (student_id,))
            student = cur.fetchone()
            if not student:
                raise HTTPException(status_code=404, detail="Student not found")

            cur.execute(
                "SELECT violation_type, created_at FROM violations WHERE student_id = %s ORDER BY created_at",
                (student_id,),
            )
            violation_logs = cur.fetchall()

        return {
            "student": student,
            "violations": violation_logs,
            "total_questions": len(QUESTIONS),
            "percentage": round(student["score"] / len(QUESTIONS) * 100, 2) if student["score"] else 0,
        }
    finally:
        conn.close()
