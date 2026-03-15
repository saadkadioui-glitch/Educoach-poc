from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import anthropic, os, secrets
from dotenv import load_dotenv
from database import SessionLocal, Exercise, StudentProfile
from datetime import datetime
import httpx

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://sosykwzdarjwliilakgk.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ============================================
# CLAUDE PROXY
# ============================================
@app.post("/v1/messages")
async def chat(request: Request):
    body = await request.json()
    response = client.messages.create(
        model=body["model"],
        max_tokens=body["max_tokens"],
        system=body["system"],
        messages=body["messages"]
    )
    return response.model_dump()

# ============================================
# SAVE EXERCISE (existing)
# ============================================
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

# ============================================
# SESSION LOG
# ============================================
@app.post("/api/session/log")
async def log_session(request: Request):
    body = await request.json()
    student_id = body.get("student_id")
    if not student_id:
        return {"status": "skipped"}
    async with httpx.AsyncClient() as hc:
        r = await hc.post(
            f"{SUPABASE_URL}/rest/v1/sessions_log",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            },
            json={
                "student_id": student_id,
                "theme": body.get("theme", ""),
                "exercises_count": body.get("exercises_count", 0),
                "correct_count": body.get("correct_count", 0),
                "score": body.get("score", 0),
                "duration_minutes": body.get("duration_minutes", 0),
                "scores_snapshot": body.get("scores_snapshot", {})
            }
        )
    return {"status": "logged"}

# ============================================
# PARENT — CREATE + INVITE STUDENT
# ============================================
@app.post("/api/parent/create")
async def create_parent(request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    email = body.get("email")
    name = body.get("name")
    async with httpx.AsyncClient() as hc:
        # Check if parent already exists
        r = await hc.get(
            f"{SUPABASE_URL}/rest/v1/parents?user_id=eq.{user_id}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        existing = r.json()
        if existing:
            return {"parent_id": existing[0]["id"], "status": "exists"}
        # Create parent
        r2 = await hc.post(
            f"{SUPABASE_URL}/rest/v1/parents",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={"user_id": user_id, "email": email, "name": name}
        )
        parent = r2.json()[0]
        return {"parent_id": parent["id"], "status": "created"}

@app.post("/api/parent/invite")
async def invite_student(request: Request):
    body = await request.json()
    parent_id = body.get("parent_id")
    student_email = body.get("student_email")
    student_name = body.get("student_name")
    invite_token = secrets.token_urlsafe(16)
    async with httpx.AsyncClient() as hc:
        # Create student record
        r = await hc.post(
            f"{SUPABASE_URL}/rest/v1/students",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            json={
                "parent_id": parent_id,
                "email": student_email,
                "name": student_name,
                "invite_token": invite_token,
                "invite_accepted": False
            }
        )
        student = r.json()[0]
        # Send invite email via Supabase
        invite_url = f"https://educoach-poc.vercel.app?invite={invite_token}&name={student_name}"
        await hc.post(
            f"{SUPABASE_URL}/auth/v1/invite",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            },
            json={"email": student_email, "data": {"invite_url": invite_url, "student_name": student_name}}
        )
        return {"student_id": student["id"], "invite_token": invite_token, "status": "invited"}

# ============================================
# PARENT DASHBOARD DATA
# ============================================
@app.get("/api/parent/dashboard/{parent_id}")
async def get_parent_dashboard(parent_id: str):
    async with httpx.AsyncClient() as hc:
        # Get students
        r = await hc.get(
            f"{SUPABASE_URL}/rest/v1/students?parent_id=eq.{parent_id}&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        students = r.json()
        result = []
        for student in students:
            # Get sessions
            r2 = await hc.get(
                f"{SUPABASE_URL}/rest/v1/sessions_log?student_id=eq.{student['id']}&order=created_at.desc&select=*",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            sessions = r2.json()
            total_time = sum(s.get("duration_minutes", 0) for s in sessions)
            avg_score = round(sum(s.get("score", 0) for s in sessions) / len(sessions)) if sessions else 0
            last_session = sessions[0]["created_at"][:10] if sessions else None
            # Check if inactive (no session in 3 days)
            inactive = False
            if last_session:
                from datetime import date
                last = datetime.strptime(last_session, "%Y-%m-%d").date()
                inactive = (date.today() - last).days >= 3
            elif student.get("invite_accepted"):
                inactive = True
            result.append({
                "student": student,
                "sessions": sessions,
                "total_time_minutes": total_time,
                "avg_score": avg_score,
                "sessions_count": len(sessions),
                "last_session": last_session,
                "inactive_alert": inactive,
                "scores_latest": sessions[0].get("scores_snapshot", {}) if sessions else {}
            })
        return {"students": result}

# ============================================
# ELIO REPORT (AI-generated)
# ============================================
@app.get("/api/student/report/{student_id}")
async def generate_report(student_id: str):
    async with httpx.AsyncClient() as hc:
        r = await hc.get(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        student = r.json()[0] if r.json() else {}
        r2 = await hc.get(
            f"{SUPABASE_URL}/rest/v1/sessions_log?student_id=eq.{student_id}&order=created_at.desc&limit=10&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        sessions = r2.json()

    if not sessions:
        return {"report": f"{student.get('name', 'L\'élève')} n'a pas encore commencé ses sessions. Encouragez-le à se connecter !"}

    total_time = sum(s.get("duration_minutes", 0) for s in sessions)
    avg_score = round(sum(s.get("score", 0) for s in sessions) / len(sessions))
    latest_scores = sessions[0].get("scores_snapshot", {})

    prompt = f"""Tu es Elio. Génère un rapport hebdomadaire bienveillant pour les parents de {student.get('name', 'l\'élève')}.
Données de la semaine :
- Sessions complétées : {len(sessions)}
- Temps total : {total_time} minutes
- Score moyen : {avg_score}%
- Scores par thème : Calcul {latest_scores.get('calcul', '?')}%, Algèbre {latest_scores.get('alg', '?')}%, Géométrie {latest_scores.get('geo', '?')}%, Stats {latest_scores.get('stat', '?')}%, Fonctions {latest_scores.get('fonc', '?')}%

Rédige un avis de 3-4 phrases :
1. Ce qui va bien (points forts)
2. Ce qui nécessite du travail (points faibles)
3. Une recommandation concrète pour les parents
Ton neutre, professionnel mais chaleureux. Pas de jargon."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"report": response.content[0].text}

# ============================================
# STUDENT — ACCEPT INVITE
# ============================================
@app.post("/api/student/accept-invite")
async def accept_invite(request: Request):
    body = await request.json()
    token = body.get("invite_token")
    user_id = body.get("user_id")
    async with httpx.AsyncClient() as hc:
        r = await hc.get(
            f"{SUPABASE_URL}/rest/v1/students?invite_token=eq.{token}&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        )
        students = r.json()
        if not students:
            raise HTTPException(status_code=404, detail="Token invalide")
        student = students[0]
        await hc.patch(
            f"{SUPABASE_URL}/rest/v1/students?id=eq.{student['id']}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            },
            json={"user_id": user_id, "invite_accepted": True}
        )
        return {"student_id": student["id"], "name": student["name"], "parent_id": student["parent_id"]}

@app.get("/api/profile/{student_name}")
async def get_profile(student_name: str):
    db = SessionLocal()
    profile = db.query(StudentProfile).filter_by(student_name=student_name).first()
    db.close()
    if not profile:
        return {"total": 0, "correct": 0, "score": 0}
    score = round((profile.correct_exercises / profile.total_exercises) * 100) if profile.total_exercises > 0 else 0
    return {"total": profile.total_exercises, "correct": profile.correct_exercises, "score": score}