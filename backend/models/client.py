from pydantic import BaseModel, field_validator
from typing import Optional
from enum import Enum
import uuid
from datetime import datetime


def _normalize_job_title(v: str | None) -> str | None:
    if not v:
        return v
    jt = v.lower()
    if "junior" in jt or "data entry" in jt or "deo" in jt:
        return "Junior Accountant"
    return "Senior Accountant"


class ClientStage(str, Enum):
    lead = "lead"
    scraping = "scraping"
    scraped = "scraped"
    reachout_sent = "reachout_sent"
    interested = "interested"
    agreement_sent = "agreement_sent"  # kept for DB compat; not shown in UI
    onboarded = "onboarded"
    disqualified = "disqualified"
    churned = "churned"
    deal_won = "deal_won"


class ClientCreate(BaseModel):
    company_name: str
    initial_stage: Optional[str] = None

    @field_validator("job_title", mode="before")
    @classmethod
    def normalise_job_title(cls, v):
        return _normalize_job_title(v)
    location: Optional[str] = None
    job_location: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    district: Optional[str] = None
    poc_name: Optional[str] = None
    poc_position: Optional[str] = None
    poc_phone: Optional[str] = None
    job_title: Optional[str] = None
    industry: Optional[str] = None
    num_employees: Optional[int] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    key_skills: Optional[list[str]] = None
    job_timings: Optional[str] = None
    gst_tds: Optional[dict] = None
    other_details: Optional[str] = None
    age_requirements: Optional[str] = None
    gender_requirements: Optional[str] = None
    tools_requirements: Optional[str] = None
    religion_requirement: Optional[str] = None
    payment_terms: Optional[str] = None
    salary_deductions: Optional[str] = None
    job_type: Optional[str] = None
    fee_amount: Optional[float] = None
    replacement_period: Optional[str] = None
    payment_due_days: Optional[int] = None
    backdoor_period: Optional[str] = None
    agreement_url: Optional[str] = None
    source: Optional[str] = None
    phone_numbers: Optional[list[dict]] = None
    agreed_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_description: Optional[str] = None
    source_job_id: Optional[str] = None
    female_employee_pct: Optional[float] = None
    diwali_bonus: Optional[str] = None
    business_type: Optional[str] = None
    segment: Optional[str] = None
    is_dnd: Optional[bool] = None
    open_positions: Optional[int] = None
    labels: Optional[list[str]] = None
    is_ca_firm: Optional[bool] = None
    travel_radius: Optional[int] = None
    skip_agreement: bool = False


class ClientUpdate(BaseModel):
    company_name: Optional[str] = None

    @field_validator("job_title", mode="before")
    @classmethod
    def normalise_job_title(cls, v):
        return _normalize_job_title(v)
    location: Optional[str] = None
    job_location: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    district: Optional[str] = None
    poc_name: Optional[str] = None
    poc_position: Optional[str] = None
    poc_phone: Optional[str] = None
    job_title: Optional[str] = None
    industry: Optional[str] = None
    num_employees: Optional[int] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    key_skills: Optional[list[str]] = None
    job_timings: Optional[str] = None
    gst_tds: Optional[dict] = None
    other_details: Optional[str] = None
    age_requirements: Optional[str] = None
    gender_requirements: Optional[str] = None
    tools_requirements: Optional[str] = None
    religion_requirement: Optional[str] = None
    payment_terms: Optional[str] = None
    salary_deductions: Optional[str] = None
    job_type: Optional[str] = None
    fee_amount: Optional[float] = None
    replacement_period: Optional[str] = None
    payment_due_days: Optional[int] = None
    backdoor_period: Optional[str] = None
    agreement_url: Optional[str] = None
    source: Optional[str] = None
    phone_numbers: Optional[list[dict]] = None
    agreed_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_description: Optional[str] = None
    source_job_id: Optional[str] = None
    female_employee_pct: Optional[float] = None
    diwali_bonus: Optional[str] = None
    business_type: Optional[str] = None
    segment: Optional[str] = None
    is_dnd: Optional[bool] = None
    open_positions: Optional[int] = None
    labels: Optional[list[str]] = None
    is_ca_firm: Optional[bool] = None
    travel_radius: Optional[int] = None
    sourcing_count_needed: Optional[int] = None
    onboarding_reminder_count: Optional[int] = None
    agreement_reminder_count: Optional[int] = None
    available_slots: Optional[list[dict]] = None
    stop_sourcing: Optional[bool] = None
    is_bot: Optional[bool] = None


class StageUpdateRequest(BaseModel):
    to_stage: ClientStage
    note: Optional[str] = None
    created_by: str = "system"


class ClientResponse(BaseModel):
    id: uuid.UUID
    company_name: str
    location: Optional[str] = None
    job_location: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    poc_name: Optional[str] = None
    poc_position: Optional[str] = None
    poc_phone: Optional[str] = None
    stage: ClientStage
    job_title: Optional[str] = None
    industry: Optional[str] = None
    num_employees: Optional[int] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    key_skills: Optional[list[str]] = None
    job_timings: Optional[str] = None
    gst_tds: Optional[dict] = None
    other_details: Optional[str] = None
    age_requirements: Optional[str] = None
    gender_requirements: Optional[str] = None
    tools_requirements: Optional[str] = None
    religion_requirement: Optional[str] = None
    payment_terms: Optional[str] = None
    salary_deductions: Optional[str] = None
    job_type: Optional[str] = None
    fee_amount: Optional[float] = None
    replacement_period: Optional[str] = None
    payment_due_days: Optional[int] = None
    backdoor_period: Optional[str] = None
    agreement_url: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
