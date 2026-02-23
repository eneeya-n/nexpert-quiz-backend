import base64
import os
import urllib.request
import numpy as np
import cv2

_yolo_available = False
_mediapipe_available = False

try:
    from ultralytics import YOLO
    yolo_model = YOLO("yolov8n.pt")
    _yolo_available = True
except Exception:
    yolo_model = None

try:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import (
        FaceLandmarker,
        FaceLandmarkerOptions,
        RunningMode,
    )

    MODEL_PATH = os.path.join(os.path.dirname(__file__), "face_landmarker.task")
    _MODEL_URL = (
        "https://storage.googleapis.com/mediapipe-models/"
        "face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
    )

    if not os.path.exists(MODEL_PATH):
        urllib.request.urlretrieve(_MODEL_URL, MODEL_PATH)

    _landmarker_options = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=RunningMode.IMAGE,
        num_faces=2,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    face_landmarker = FaceLandmarker.create_from_options(_landmarker_options)
    _mediapipe_available = True
except Exception:
    face_landmarker = None

PERSON_CLASS = 0
PHONE_CLASS = 67

LEFT_IRIS = [468, 469, 470, 471, 472]
RIGHT_IRIS = [473, 474, 475, 476, 477]
LEFT_EYE_INNER = 133
LEFT_EYE_OUTER = 33
RIGHT_EYE_INNER = 362
RIGHT_EYE_OUTER = 263


def _decode_frame(b64_frame: str) -> np.ndarray:
    img_bytes = base64.b64decode(b64_frame)
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _iris_ratio(landmarks, eye_inner, eye_outer, iris_ids):
    inner = np.array([landmarks[eye_inner].x, landmarks[eye_inner].y])
    outer = np.array([landmarks[eye_outer].x, landmarks[eye_outer].y])
    width = np.linalg.norm(inner - outer)
    if width < 1e-6:
        return 0.5
    center = np.mean([[landmarks[i].x, landmarks[i].y] for i in iris_ids], axis=0)
    return np.linalg.norm(center - outer) / width


def _check_gaze(landmarks) -> bool:
    left = _iris_ratio(landmarks, LEFT_EYE_INNER, LEFT_EYE_OUTER, LEFT_IRIS)
    right = _iris_ratio(landmarks, RIGHT_EYE_INNER, RIGHT_EYE_OUTER, RIGHT_IRIS)
    avg = (left + right) / 2.0
    return avg < 0.30 or avg > 0.70


def analyze_frame(b64_frame: str) -> dict:
    if not _yolo_available and not _mediapipe_available:
        return {
            "person_count": 0,
            "phone_detected": False,
            "face_detected": True,
            "violations": [],
            "violation_count": 0,
        }

    frame = _decode_frame(b64_frame)
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    violations = []

    person_count = 0
    phone_detected = False
    face_detected = True
    looking_away = False

    if _yolo_available:
        yolo_results = yolo_model(frame, verbose=False)[0]
        for box in yolo_results.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            if cls_id == PERSON_CLASS and conf > 0.45:
                person_count += 1
            if cls_id == PHONE_CLASS and conf > 0.35:
                phone_detected = True

    if _mediapipe_available:
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = face_landmarker.detect(mp_image)
        face_detected = len(result.face_landmarks) > 0
        if face_detected and len(result.face_landmarks[0]) >= 478:
            looking_away = _check_gaze(result.face_landmarks[0])

    if person_count > 1:
        violations.append("Multiple persons detected")
    if phone_detected:
        violations.append("Phone detected")
    if not face_detected:
        violations.append("No face detected")
    elif looking_away:
        violations.append("Looking away from screen")

    return {
        "person_count": person_count,
        "phone_detected": phone_detected,
        "face_detected": face_detected,
        "violations": violations,
        "violation_count": len(violations),
    }
