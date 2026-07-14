from pydantic import BaseModel
from typing import Optional, Any
from enum import Enum
import uuid
from datetime import datetime


class MappingStage(str, Enum):
    matched = "matched"
    intent_ask = "intent_ask"
    interested = "interested"
    not_interested = "not_interested"
    slot_booked = "slot_booked"
    interview_done = "interview_done"
    placed = "placed"
    rejected = "rejected"


class MappingCreate(BaseModel):
    candidate_id: uuid.UUID
    match_score: Optional[int] = None
    notes: Optional[str] = None


class MappingStageUpdate(BaseModel):
    stage: MappingStage
    interview_slot: Optional[datetime] = None
    notes: Optional[str] = None


class MappingResponse(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    candidate_id: uuid.UUID
    stage: MappingStage
    match_score: Optional[int] = None
    wa_sent_at: Optional[datetime] = None
    intent_received_at: Optional[datetime] = None
    slot_sent_at: Optional[datetime] = None
    interview_slot: Optional[datetime] = None
    interview_done: bool = False
    interview_done_at: Optional[datetime] = None
    placement_confirmed: bool = False
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
