"""
OCPP Tag Management API

REST API endpoints for managing OCPP tags and authorization lists.
Provides CRUD operations, search, import/export, and statistics.
"""

import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query, Path, Body, Depends
from fastapi.responses import JSONResponse, StreamingResponse
import json
import io

from .schemas.tags import (
    OCPPTag, TagList, TagSearchRequest, TagSearchResponse,
    TagBulkOperation, TagBulkResponse, TagStatistics,
    TagValidationResult, TagImportRequest, TagImportResponse,
    TagExportRequest, TagExportResponse, TagStatus, TagType
)
from .tag_manager import TagManager

logger = logging.getLogger("ocpp_broker.tag_api")


def create_tag_api(broker) -> APIRouter:
    """Create tag management API router"""
    router = APIRouter(prefix="/api/tags", tags=["Tag Management"])
    
    # Get tag manager from broker
    tag_manager = broker.tag_manager if broker and hasattr(broker, 'tag_manager') else None
    
    if not tag_manager:
        @router.get("/status")
        async def tag_management_status():
            return {"enabled": False, "message": "Tag management not enabled"}
        
        # Return router with disabled endpoints
        return router
    
    @router.get("/status")
    async def tag_management_status():
        """Get tag management status"""
        return {
            "enabled": True,
            "message": "Tag management is active",
            "organizations": list(tag_manager._tag_lists.keys())
        }
    
    @router.post("/organizations/{org_name}/tags")
    async def add_tag(
        org_name: str = Path(..., description="Organization name"),
        tag: OCPPTag = Body(..., description="Tag to add")
    ):
        """Add a new tag to an organization"""
        try:
            success = await tag_manager.add_tag(org_name, tag)
            if success:
                return {"success": True, "message": f"Tag {tag.id_tag} added successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to add tag")
        except Exception as e:
            logger.error(f"Error adding tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/tags/{id_tag}")
    async def get_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID")
    ):
        """Get a specific tag"""
        try:
            tag = await tag_manager.get_tag(org_name, id_tag)
            if tag:
                return tag.model_dump()
            else:
                raise HTTPException(status_code=404, detail="Tag not found")
        except Exception as e:
            logger.error(f"Error getting tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.put("/organizations/{org_name}/tags/{id_tag}")
    async def update_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID"),
        tag: OCPPTag = Body(..., description="Updated tag data")
    ):
        """Update an existing tag"""
        try:
            success = await tag_manager.update_tag(org_name, id_tag, tag)
            if success:
                return {"success": True, "message": f"Tag {id_tag} updated successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to update tag")
        except Exception as e:
            logger.error(f"Error updating tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.delete("/organizations/{org_name}/tags/{id_tag}")
    async def delete_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID")
    ):
        """Delete a tag"""
        try:
            success = await tag_manager.delete_tag(org_name, id_tag)
            if success:
                return {"success": True, "message": f"Tag {id_tag} deleted successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to delete tag")
        except Exception as e:
            logger.error(f"Error deleting tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/tags")
    async def search_tags(
        org_name: str = Path(..., description="Organization name"),
        id_tag: Optional[str] = Query(None, description="Filter by ID tag"),
        status: Optional[TagStatus] = Query(None, description="Filter by status"),
        tag_type: Optional[TagType] = Query(None, description="Filter by tag type"),
        parent_id_tag: Optional[str] = Query(None, description="Filter by parent tag"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        offset: int = Query(0, ge=0, description="Number of results to skip")
    ):
        """Search tags with filters"""
        try:
            search_request = TagSearchRequest(
                id_tag=id_tag,
                status=status,
                tag_type=tag_type,
                parent_id_tag=parent_id_tag,
                limit=limit,
                offset=offset
            )
            result = await tag_manager.search_tags(org_name, search_request)
            return result.dict()
        except Exception as e:
            logger.error(f"Error searching tags: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/list")
    async def get_tag_list(
        org_name: str = Path(..., description="Organization name")
    ):
        """Get complete tag list for an organization"""
        try:
            tag_list = await tag_manager.get_tag_list(org_name)
            if tag_list:
                return tag_list.dict()
            else:
                return {"listVersion": 0, "tags": []}
        except Exception as e:
            logger.error(f"Error getting tag list: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/statistics")
    async def get_tag_statistics(
        org_name: str = Path(..., description="Organization name")
    ):
        """Get tag statistics for an organization"""
        try:
            stats = await tag_manager.get_tag_statistics(org_name)
            return stats.dict()
        except Exception as e:
            logger.error(f"Error getting tag statistics: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/validate")
    async def validate_tag(
        org_name: str = Path(..., description="Organization name"),
        tag: OCPPTag = Body(..., description="Tag to validate")
    ):
        """Validate a tag"""
        try:
            result = await tag_manager.validate_tag(tag)
            return result.dict()
        except Exception as e:
            logger.error(f"Error validating tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/bulk")
    async def bulk_operation(
        org_name: str = Path(..., description="Organization name"),
        operation: TagBulkOperation = Body(..., description="Bulk operation to perform")
    ):
        """Perform bulk operations on tags"""
        try:
            result = await tag_manager.bulk_operation(org_name, operation)
            return result.dict()
        except Exception as e:
            logger.error(f"Error performing bulk operation: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/import")
    async def import_tags(
        org_name: str = Path(..., description="Organization name"),
        import_request: TagImportRequest = Body(..., description="Import request")
    ):
        """Import tags from external source"""
        try:
            result = await tag_manager.import_tags(org_name, import_request)
            return result.dict()
        except Exception as e:
            logger.error(f"Error importing tags: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/export")
    async def export_tags(
        org_name: str = Path(..., description="Organization name"),
        export_request: TagExportRequest = Body(..., description="Export request")
    ):
        """Export tags to external format"""
        try:
            result = await tag_manager.export_tags(org_name, export_request)
            
            # Return as downloadable file
            if export_request.format == 'json':
                media_type = 'application/json'
                filename = f"{org_name}_tags.json"
            elif export_request.format == 'csv':
                media_type = 'text/csv'
                filename = f"{org_name}_tags.csv"
            else:
                media_type = 'application/octet-stream'
                filename = f"{org_name}_tags.{export_request.format}"
            
            return StreamingResponse(
                io.BytesIO(result.data.encode('utf-8')),
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        except Exception as e:
            logger.error(f"Error exporting tags: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/authorize")
    async def authorize_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Body(..., description="Tag ID to authorize")
    ):
        """Authorize a tag (same as OCPP Authorize command)"""
        try:
            result = await tag_manager.authorize_tag(org_name, id_tag)
            return {
                "idTag": id_tag,
                "idTagInfo": {
                    "status": result["status"],
                    "expiryDate": result.get("expiry_date"),
                    "parentIdTag": result.get("parent_id_tag")
                }
            }
        except Exception as e:
            logger.error(f"Error authorizing tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations")
    async def list_organizations():
        """List all organizations with tag management"""
        try:
            organizations = list(tag_manager._tag_lists.keys())
            return {"organizations": organizations}
        except Exception as e:
            logger.error(f"Error listing organizations: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return router
