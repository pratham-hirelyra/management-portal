from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime


class BulkMessageRequest(BaseModel):
    mapping_ids: list[uuid.UUID]
    message_text: str


class AgreementMessageRequest(BaseModel):
    message_text: str


class WhatsAppMessageResponse(BaseModel):
    id: uuid.UUID
    client_id: Optional[uuid.UUID] = None
    candidate_id: Optional[uuid.UUID] = None
    mapping_id: Optional[uuid.UUID] = None
    message_type: str
    direction: str
    phone: str
    message_text: Optional[str] = None
    status: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    meta_msg_id: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
