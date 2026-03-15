from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import anthropic, os
from dotenv import load_dotenv
from database import SessionLocal, Exercise, StudentProfile
from datetime import datetime

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

@app.post("/v1/messages")
async def chat(request: Request):
    body = await request.json()
    response = client.messages.create(model=body["model"], max_tokens=body["max_tokens"], system=body["system"], messages=body["messages"])
    return response.model_dump()

@app.post("/api/save-exercise")
async def save_exercise(request: Request):
    body = await request.json()
    db = SessionLocal()
    try:
        exercise = Exercise(
            student_name=body.get("student_name"),
            topic=body.get("topic"),
            difficulty=body.get("difficulty"),
            question=body.get("question"),
            expected_answer=body.get("expected_answer"),
            student_answer=body.get("student_answer"),
            is_correct=body.get("is_correct"),
            feedback=body.get("feedback")
        )
        db.add(exercise)
        profile = db.query(StudentProfile).filter_by(student_name=body.get("student_name")).first()
        if not profile:
            profile = StudentProfile(student_name=body.get("student_name"), total_exercises=0, correct_exercises=0)
            db.add(profile)
            db.flush()
        profile.total_exercises = (profile.total_exercises or 0) + 1
        if body.get("is_correct"):
            profile.correct_exercises = (profile.correct_exercises or 0) + 1
        profile.last_session = datetime.utcnow()
        db.commit()
    except Exception as e:
        db.rollback()
        print("Erreur sauvegarde:", e)
    finally:
        db.close()
    return {"status": "saved"}

@app.get("/api/profile/{student_name}")
async def get_profile(student_name: str):
    db = SessionLocal()
    profile = db.query(StudentProfile).filter_by(student_name=student_name).first()
    db.close()
    if not profile:
        return {"total": 0, "correct": 0, "score": 0}
    score = round((profile.correct_exercises / profile.total_exercises) * 100) if profile.total_exercises > 0 else 0
    return {"total": profile.total_exercises, "correct": profile.correct_exercises, "score": score}