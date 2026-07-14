from pydantic import BaseModel
from typing import Optional, Any
import uuid
from datetime import datetime


class CandidateCreate(BaseModel):
    name: str
    phone: str
    age_years: Optional[int] = None
    gender: Optional[str] = None
    religion: Optional[str] = None
    email: Optional[str] = None
    experience: Optional[str] = None
    cv_url: Optional[str] = None
    source: Optional[str] = None
    current_location: Optional[str] = None
    working_radius: Optional[int] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    job_type: Optional[str] = None
    industry: Optional[str] = None
    category: Optional[str] = None
    work_preference: Optional[str] = None


class CandidateResponse(BaseModel):
    id: uuid.UUID
    name: str
    age_years: Optional[int] = None
    gender: Optional[str] = None
    religion: Optional[str] = None
    phone: str
    email: Optional[str] = None
    experience: Optional[str] = None
    cv_url: Optional[str] = None
    source: Optional[str] = None
    mentioned_location: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    current_location: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    working_radius: Optional[int] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    job_type: Optional[str] = None
    industry: Optional[str] = None
    call_status: Optional[str] = None
    category: Optional[str] = None
    evaluation_status: Optional[str] = None
    technical_evaluation_score: Optional[float] = None
    total_score: Optional[float] = None
    evaluation_pdf_url: Optional[str] = None
    evaluation_summary: Optional[Any] = None
    hiring_decision_reason: Optional[str] = None
    voice_recording_url: Optional[str] = None
    work_preference: Optional[str] = None
    other_details: Optional[Any] = None
    final_evaluation_report_url: Optional[str] = None
    interview_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
