from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

engine = create_engine("sqlite:///educoach.db")
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

class Exercise(Base):
    __tablename__ = "exercises"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_name = Column(String)
    topic = Column(String)
    difficulty = Column(Integer)
    question = Column(String)
    expected_answer = Column(String)
    student_answer = Column(String)
    is_correct = Column(Boolean)
    feedback = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class StudentProfile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_name = Column(String, unique=True)
    total_exercises = Column(Integer, default=0)
    correct_exercises = Column(Integer, default=0)
    weak_topics = Column(JSON, default=[])
    strong_topics = Column(JSON, default=[])
    last_session = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)