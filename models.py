from pydantic import BaseModel, EmailStr


class StudentRegister(BaseModel):
    name: str
    email: EmailStr
    confirm_email: EmailStr


class AnswerSubmission(BaseModel):
    student_id: int
    answers: dict


class FrameData(BaseModel):
    student_id: int
    frame: str  # base64 JPEG


class ViolationLog(BaseModel):
    student_id: int
    violation_type: str
