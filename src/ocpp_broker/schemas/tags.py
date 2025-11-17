"""
OCPP Tag Management Schemas

Pydantic models for OCPP tag management, including authorization lists,
tag definitions, and tag operations.
"""

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime
from enum import Enum


class TagStatus(str, Enum):
    """OCPP Tag Status enumeration"""
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    EXPIRED = "Expired"
    INVALID = "Invalid"
    CONCURRENT_TX = "ConcurrentTx"


class TagType(str, Enum):
    """OCPP Tag Type enumeration"""
    RFID = "RFID"
    NFC = "NFC"
    QR_CODE = "QRCode"
    MOBILE_APP = "MobileApp"
    USER_ID = "UserId"


class OCPPTag(BaseModel):
    """OCPP Tag definition"""
    id_tag: str = Field(..., description="Unique tag identifier", min_length=1, max_length=20)
    status: TagStatus = Field(..., description="Tag authorization status")
    tag_type: TagType = Field(default=TagType.RFID, description="Type of tag")
    expiry_date: Optional[str] = Field(None, description="Tag expiry date (ISO 8601 format)")
    parent_id_tag: Optional[str] = Field(None, description="Parent tag ID for hierarchical tags")
    description: Optional[str] = Field(None, description="Human-readable description")
    created_at: Optional[str] = Field(None, description="Tag creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional tag metadata")
    
    @field_validator('id_tag')
    @classmethod
    def validate_id_tag(cls, v):
        """Validate ID tag format"""
        if not v or not v.strip():
            raise ValueError("ID tag cannot be empty")
        # Remove any whitespace and convert to uppercase
        return v.strip().upper()
    
    @field_validator('expiry_date')
    @classmethod
    def validate_expiry_date(cls, v):
        """Validate expiry date format"""
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError("Expiry date must be in ISO 8601 format")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "idTag": "USER123456",
                "status": "Accepted",
                "tagType": "RFID",
                "expiryDate": "2024-12-31T23:59:59Z",
                "parentIdTag": "ADMIN001",
                "description": "Employee access card",
                "metadata": {
                    "department": "Engineering",
                    "access_level": "standard"
                }
            }
        }
    )


class TagList(BaseModel):
    """OCPP Tag List definition"""
    list_version: int = Field(..., description="Version number of the tag list", ge=0)
    tags: List[OCPPTag] = Field(..., description="List of tags")
    created_at: Optional[str] = Field(None, description="List creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "listVersion": 1,
                "tags": [
                    {
                        "idTag": "USER123456",
                        "status": "Accepted",
                        "tagType": "RFID",
                        "expiryDate": "2024-12-31T23:59:59Z"
                    }
                ]
            }
        }
    )


class TagSearchRequest(BaseModel):
    """Tag search request"""
    id_tag: Optional[str] = Field(None, description="Search by ID tag")
    status: Optional[TagStatus] = Field(None, description="Filter by status")
    tag_type: Optional[TagType] = Field(None, description="Filter by tag type")
    parent_id_tag: Optional[str] = Field(None, description="Filter by parent tag")
    limit: Optional[int] = Field(100, description="Maximum number of results", ge=1, le=1000)
    offset: Optional[int] = Field(0, description="Number of results to skip", ge=0)


class TagSearchResponse(BaseModel):
    """Tag search response"""
    tags: List[OCPPTag] = Field(..., description="Matching tags")
    total: int = Field(..., description="Total number of matching tags")
    limit: int = Field(..., description="Applied limit")
    offset: int = Field(..., description="Applied offset")


class TagBulkOperation(BaseModel):
    """Bulk tag operation request"""
    operation: str = Field(..., description="Operation type: add, update, delete")
    tags: List[OCPPTag] = Field(..., description="Tags to operate on")
    
    @field_validator('operation')
    @classmethod
    def validate_operation(cls, v):
        """Validate operation type"""
        if v not in ['add', 'update', 'delete']:
            raise ValueError("Operation must be one of: add, update, delete")
        return v


class TagBulkResponse(BaseModel):
    """Bulk operation response"""
    success_count: int = Field(..., description="Number of successful operations")
    error_count: int = Field(..., description="Number of failed operations")
    errors: List[Dict[str, Any]] = Field(..., description="Error details for failed operations")
    updated_tags: List[OCPPTag] = Field(..., description="Successfully processed tags")


class TagStatistics(BaseModel):
    """Tag statistics"""
    total_tags: int = Field(..., description="Total number of tags")
    active_tags: int = Field(..., description="Number of active tags")
    expired_tags: int = Field(..., description="Number of expired tags")
    blocked_tags: int = Field(..., description="Number of blocked tags")
    tags_by_type: Dict[str, int] = Field(..., description="Tag count by type")
    tags_by_status: Dict[str, int] = Field(..., description="Tag count by status")


class TagValidationResult(BaseModel):
    """Tag validation result"""
    valid: bool = Field(..., description="Whether tag is valid")
    errors: List[str] = Field(..., description="Validation errors")
    warnings: List[str] = Field(..., description="Validation warnings")
    tag: Optional[OCPPTag] = Field(None, description="Validated tag if valid")


class TagImportRequest(BaseModel):
    """Tag import request"""
    source: str = Field(..., description="Import source: csv, json, xml")
    data: str = Field(..., description="Tag data to import")
    overwrite_existing: bool = Field(False, description="Whether to overwrite existing tags")
    validate_only: bool = Field(False, description="Whether to only validate without importing")


class TagImportResponse(BaseModel):
    """Tag import response"""
    imported_count: int = Field(..., description="Number of tags imported")
    skipped_count: int = Field(..., description="Number of tags skipped")
    error_count: int = Field(..., description="Number of import errors")
    errors: List[Dict[str, Any]] = Field(..., description="Import error details")
    imported_tags: List[OCPPTag] = Field(..., description="Successfully imported tags")


class TagExportRequest(BaseModel):
    """Tag export request"""
    format: str = Field(..., description="Export format: csv, json, xml")
    filter_status: Optional[List[TagStatus]] = Field(None, description="Filter by status")
    filter_type: Optional[List[TagType]] = Field(None, description="Filter by type")
    include_metadata: bool = Field(True, description="Whether to include metadata")
    
    @field_validator('format')
    @classmethod
    def validate_format(cls, v):
        """Validate export format"""
        if v not in ['csv', 'json', 'xml']:
            raise ValueError("Format must be one of: csv, json, xml")
        return v


class TagExportResponse(BaseModel):
    """Tag export response"""
    format: str = Field(..., description="Export format used")
    data: str = Field(..., description="Exported tag data")
    tag_count: int = Field(..., description="Number of tags exported")
    file_size: int = Field(..., description="Size of exported data in bytes")
