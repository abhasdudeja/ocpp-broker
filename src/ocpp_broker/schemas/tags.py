"""
OCPP Tag Management Schemas

Simple Pydantic models for basic tag management.
"""

from typing import Any, Dict, List, Literal, Optional
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

class TagStatistics(BaseModel):
    """Counts of an organization's tags."""
    total_tags: int
    active_tags: int  # Accepted and not past their expiry date
    expired_tags: int  # status Expired, or past their expiry date
    blocked_tags: int
    tags_by_type: Dict[str, int]
    tags_by_status: Dict[str, int]


class TagValidationResult(BaseModel):
    """Outcome of validating one tag. Errors make it invalid; warnings do not."""
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class BulkTagRequest(BaseModel):
    operation: Literal["add", "update", "delete"]
    tags: List[OCPPTag]


class BulkTagItemResult(BaseModel):
    id_tag: str
    success: bool
    error: Optional[str] = None


class BulkTagResult(BaseModel):
    operation: str
    total: int
    succeeded: int
    failed: int
    results: List[BulkTagItemResult]


class TagImportRequest(BaseModel):
    source: Literal["json", "csv"] = "json"
    data: str
    overwrite_existing: bool = False
    validate_only: bool = False  # report what would happen without changing anything


class TagImportError(BaseModel):
    record: int  # 1-based position of the record in the input
    id_tag: Optional[str] = None
    error: str


class TagImportResult(BaseModel):
    source: str
    validate_only: bool
    total: int
    imported: int  # new tags (would be) added
    updated: int  # existing tags (would be) overwritten
    skipped: int  # existing tags left alone because overwrite_existing is false
    errors: List[TagImportError]


class TagExportRequest(BaseModel):
    format: Literal["json", "csv"] = "json"
    include_metadata: bool = True  # created_at, updated_at and metadata
