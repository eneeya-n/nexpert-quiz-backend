from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from database import get_connection, init_db
from models import StudentRegister, AnswerSubmission, FrameData, ViolationLog
from questions import QUESTIONS
from yolo_detector import analyze_frame


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


@app.post("/api/register")
def register_student(data: StudentRegister):
    if data.email != data.confirm_email:
        raise HTTPException(status_code=400, detail="Emails do not match")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM students WHERE email = %s", (data.email,))
            existing = cur.fetchone()
            if existing:
                return {"student_id": existing["id"], "message": "Already registered"}

            cur.execute(
                "INSERT INTO students (name, email) VALUES (%s, %s)",
                (data.name, data.email),
            )
            conn.commit()
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
            if result["violation_count"] > 0:
                for v in result["violations"]:
                    cur.execute(
                        "INSERT INTO violations (student_id, violation_type) VALUES (%s, %s)",
                        (data.student_id, v),
                    )
                cur.execute(
                    "UPDATE students SET violation_count = violation_count + %s WHERE id = %s",
                    (result["violation_count"], data.student_id),
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
