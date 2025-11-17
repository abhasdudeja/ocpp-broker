"""
Schemas exposed by the broker.

The broker now relies on the upstream `ocpp` library for message structures,
so this package only re-exports the tag-management models used by the REST API.
"""

from .tags import (
    OCPPTag,
    TagList,
    TagStatus,
    TagType,
    TagSearchRequest,
    TagSearchResponse,
    TagBulkOperation,
    TagBulkResponse,
    TagStatistics,
    TagValidationResult,
    TagImportRequest,
    TagImportResponse,
    TagExportRequest,
    TagExportResponse,
)

__all__ = [
    "OCPPTag",
    "TagList",
    "TagStatus",
    "TagType",
    "TagSearchRequest",
    "TagSearchResponse",
    "TagBulkOperation",
    "TagBulkResponse",
    "TagStatistics",
    "TagValidationResult",
    "TagImportRequest",
    "TagImportResponse",
    "TagExportRequest",
    "TagExportResponse",
]
