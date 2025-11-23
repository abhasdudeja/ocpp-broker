"""
OCPP Tag Management Schemas

Simple Pydantic models for basic tag management.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class TagStatus(str, Enum):
    """OCPP Tag Status"""
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    EXPIRED = "Expired"
    INVALID = "Invalid"
    CONCURRENT_TX = "ConcurrentTx"


class TagType(str, Enum):
    """OCPP Tag Type"""
    RFID = "RFID"
    NFC = "NFC"
    QR_CODE = "QRCode"
    MOBILE_APP = "MobileApp"
    USER_ID = "UserId"


class OCPPTag(BaseModel):
    """OCPP Tag definition"""
    id_tag: str = Field(..., min_length=1, max_length=20)
    status: TagStatus
    tag_type: TagType = TagType.RFID
    expiry_date: Optional[str] = None
    parent_id_tag: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TagList(BaseModel):
    """OCPP Tag List"""
    list_version: int = Field(..., ge=0)
    tags: List[OCPPTag]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TagSearchRequest(BaseModel):
    """Tag search filters"""
    id_tag: Optional[str] = None
    status: Optional[TagStatus] = None
    tag_type: Optional[TagType] = None
    parent_id_tag: Optional[str] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class TagSearchResponse(BaseModel):
    """Tag search results"""
    tags: List[OCPPTag]
    total: int
    limit: int
    offset: int
