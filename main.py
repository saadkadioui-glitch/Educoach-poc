from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import anthropic, os, secrets
from dotenv import load_dotenv
from database import SessionLocal, Exercise, StudentProfile
from datetime import datetime, date
import httpx

load_dotenv()

app = FastAPI()

# CORS — allow everything, no credentials (required for wildcard origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://sosykwzdarjwliilakgk.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# ============================================
# CLAUDE PROXY
# ============================================
@app.post("/v1/messages")
async def chat(request: Request):
    try:
        body = await request.json()
        response = client.messages.create(
            model=body["model"],
            max_tokens=body["max_tokens"],
            system=body["system"],
            messages=body["messages"]
        )
        return response.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# SAVE EXERCISE
# ============================================
@app.post("/api/save-exercise")
async def save_exercise(request: Request):
    try:
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
                profile = StudentProfile(
                    student_name=body.get("student_name"),
                    total_exercises=0,
                    correct_exercises=0
                )
                db.add(profile)
                db.flush()
            profile.total_exercises = (profile.total_exercises or 0) + 1
            if body.get("is_correct"):
                profile.correct_exercises = (profile.correct_exercises or 0) + 1
            profile.last_session = datetime.utcnow()
            db.commit()
        except Exception as e:
            db.rollback()
            print("Erreur sauvegarde exercise:", e)
        finally:
            db.close()
        return {"status": "saved"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# SESSION LOG
# ============================================
@app.post("/api/session/log")
async def log_session(request: Request):
    try:
        body = await request.json()
        student_id = body.get("student_id")
        if not student_id:
            return {"status": "skipped"}
        async with httpx.AsyncClient() as hc:
            await hc.post(
                f"{SUPABASE_URL}/rest/v1/sessions_log",
                headers={**supabase_headers(), "Prefer": "return=minimal"},
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
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# PARENT — CREATE
# ============================================
@app.post("/api/parent/create")
async def create_parent(request: Request):
    try:
        body = await request.json()
        user_id = body.get("user_id")
        email = body.get("email", "")
        name = body.get("name", "")

        async with httpx.AsyncClient() as hc:
            # Check if parent already exists
            r = await hc.get(
                f"{SUPABASE_URL}/rest/v1/parents?user_id=eq.{user_id}&select=id",
                headers=supabase_headers()
            )
            existing = r.json()
            if existing and len(existing) > 0:
                return {"parent_id": existing[0]["id"], "status": "exists"}

            # Create parent
            r2 = await hc.post(
                f"{SUPABASE_URL}/rest/v1/parents",
                headers=supabase_headers(),
                json={"user_id": user_id, "email": email, "name": name}
            )
            result = r2.json()
            if not result or len(result) == 0:
                # Try to fetch again in case of race condition
                r3 = await hc.get(
                    f"{SUPABASE_URL}/rest/v1/parents?user_id=eq.{user_id}&select=id",
                    headers=supabase_headers()
                )
                existing2 = r3.json()
                if existing2 and len(existing2) > 0:
                    return {"parent_id": existing2[0]["id"], "status": "exists"}
                return JSONResponse(status_code=500, content={"error": "Failed to create parent", "detail": str(r2.text)})

            return {"parent_id": result[0]["id"], "status": "created"}
    except Exception as e:
        print("create_parent error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# PARENT — INVITE STUDENT
# ============================================
@app.post("/api/parent/invite")
async def invite_student(request: Request):
    try:
        body = await request.json()
        parent_id = body.get("parent_id")
        student_email = body.get("student_email", "")
        student_name = body.get("student_name", "")

        if not parent_id:
            return JSONResponse(status_code=400, content={"error": "parent_id requis"})

        invite_token = secrets.token_urlsafe(16)
        student_app_url = body.get("student_app_url", "https://educoach-poc.vercel.app")
        invite_url = f"{student_app_url}?invite={invite_token}&name={student_name}"

        async with httpx.AsyncClient() as hc:
            # Create student record
            r = await hc.post(
                f"{SUPABASE_URL}/rest/v1/students",
                headers=supabase_headers(),
                json={
                    "parent_id": parent_id,
                    "email": student_email,
                    "name": student_name,
                    "invite_token": invite_token,
                    "invite_accepted": False
                }
            )
            result = r.json()
            if not result or len(result) == 0:
                return JSONResponse(status_code=500, content={"error": "Failed to create student", "detail": str(r.text)})

            student_id = result[0]["id"]

            # Send invite via Supabase (best effort)
            try:
                await hc.post(
                    f"{SUPABASE_URL}/auth/v1/invite",
                    headers=supabase_headers(),
                    json={
                        "email": student_email,
                        "data": {
                            "invite_url": invite_url,
                            "student_name": student_name
                        }
                    }
                )
            except Exception as email_err:
                print("Email invite error (non-blocking):", email_err)

        return {
            "student_id": student_id,
            "invite_token": invite_token,
            "invite_url": invite_url,
            "status": "invited"
        }
    except Exception as e:
        print("invite_student error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# PARENT DASHBOARD
# ============================================
@app.get("/api/parent/dashboard/{parent_id}")
async def get_parent_dashboard(parent_id: str):
    try:
        async with httpx.AsyncClient() as hc:
            # Get students
            r = await hc.get(
                f"{SUPABASE_URL}/rest/v1/students?parent_id=eq.{parent_id}&select=*",
                headers=supabase_headers()
            )
            students = r.json() if r.status_code == 200 else []

            result = []
            for student in students:
                # Get sessions
                r2 = await hc.get(
                    f"{SUPABASE_URL}/rest/v1/sessions_log?student_id=eq.{student['id']}&order=created_at.desc&select=*",
                    headers=supabase_headers()
                )
                sessions = r2.json() if r2.status_code == 200 else []

                total_time = sum(s.get("duration_minutes", 0) for s in sessions)
                avg_score = round(sum(s.get("score", 0) for s in sessions) / len(sessions)) if sessions else 0
                last_session = sessions[0]["created_at"][:10] if sessions else None

                # Check inactivity (3+ days without session)
                inactive = False
                if last_session:
                    try:
                        last = datetime.strptime(last_session, "%Y-%m-%d").date()
                        inactive = (date.today() - last).days >= 3
                    except:
                        pass
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
    except Exception as e:
        print("dashboard error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# ELIO REPORT
# ============================================
@app.get("/api/student/report/{student_id}")
async def generate_report(student_id: str):
    try:
        async with httpx.AsyncClient() as hc:
            r = await hc.get(
                f"{SUPABASE_URL}/rest/v1/students?id=eq.{student_id}&select=*",
                headers=supabase_headers()
            )
            students = r.json() if r.status_code == 200 else []
            student = students[0] if students else {}

            r2 = await hc.get(
                f"{SUPABASE_URL}/rest/v1/sessions_log?student_id=eq.{student_id}&order=created_at.desc&limit=10&select=*",
                headers=supabase_headers()
            )
            sessions = r2.json() if r2.status_code == 200 else []

        if not sessions:
            name = student.get("name", "L'élève")
            return {"report": f"{name} n'a pas encore commencé ses sessions. Encouragez-le à se connecter sur EduCoach !"}

        total_time = sum(s.get("duration_minutes", 0) for s in sessions)
        avg_score = round(sum(s.get("score", 0) for s in sessions) / len(sessions))
        latest_scores = sessions[0].get("scores_snapshot", {})
        name = student.get("name", "l'élève")

        prompt = f"""Tu es Elio. Génère un rapport hebdomadaire bienveillant et professionnel pour les parents de {name}.
Données :
- Sessions complétées : {len(sessions)}
- Temps total : {total_time} minutes
- Score moyen : {avg_score}%
- Scores par thème : Calcul {latest_scores.get('calcul', '?')}%, Algèbre {latest_scores.get('alg', '?')}%, Géométrie {latest_scores.get('geo', '?')}%, Stats {latest_scores.get('stat', '?')}%, Fonctions {latest_scores.get('fonc', '?')}%

Rédige 3-4 phrases :
1. Ce qui va bien
2. Ce qui nécessite du travail
3. Une recommandation concrète pour les parents
Ton neutre, chaleureux, professionnel."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return {"report": response.content[0].text}
    except Exception as e:
        print("report error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# STUDENT — ACCEPT INVITE
# ============================================
@app.post("/api/student/accept-invite")
async def accept_invite(request: Request):
    try:
        body = await request.json()
        token = body.get("invite_token")
        user_id = body.get("user_id")

        async with httpx.AsyncClient() as hc:
            r = await hc.get(
                f"{SUPABASE_URL}/rest/v1/students?invite_token=eq.{token}&select=*",
                headers=supabase_headers()
            )
            students = r.json() if r.status_code == 200 else []
            if not students:
                return JSONResponse(status_code=404, content={"error": "Token invalide"})

            student = students[0]
            await hc.patch(
                f"{SUPABASE_URL}/rest/v1/students?id=eq.{student['id']}",
                headers={**supabase_headers(), "Prefer": "return=minimal"},
                json={"user_id": user_id, "invite_accepted": True}
            )

        return {
            "student_id": student["id"],
            "name": student["name"],
            "parent_id": student["parent_id"]
        }
    except Exception as e:
        print("accept_invite error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================
# STUDENT PROFILE (existing)
# ============================================
@app.get("/api/profile/{student_name}")
async def get_profile(student_name: str):
    try:
        db = SessionLocal()
        profile = db.query(StudentProfile).filter_by(student_name=student_name).first()
        db.close()
        if not profile:
            return {"total": 0, "correct": 0, "score": 0}
        score = round((profile.correct_exercises / profile.total_exercises) * 100) if profile.total_exercises > 0 else 0
        return {"total": profile.total_exercises, "correct": profile.correct_exercises, "score": score}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
