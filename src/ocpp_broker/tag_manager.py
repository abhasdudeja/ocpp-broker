"""
OCPP Tag Manager

Service for managing OCPP tags and authorization lists.
Provides CRUD operations, validation, and search functionality.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import csv
import xml.etree.ElementTree as ET

from .schemas.tags import (
    OCPPTag, TagList, TagStatus, TagType, TagSearchRequest, 
    TagSearchResponse, TagBulkOperation, TagBulkResponse,
    TagStatistics, TagValidationResult, TagImportRequest,
    TagImportResponse, TagExportRequest, TagExportResponse
)


logger = logging.getLogger("ocpp_broker.tag_manager")


class TagManager:
    """
    Manages OCPP tags and authorization lists.
    
    Features:
    - CRUD operations for tags
    - Tag validation and search
    - Bulk operations
    - Import/export functionality
    - Statistics and reporting
    - Configuration-based tag management
    """
    
    def __init__(self, config_data: Dict[str, Any] = None):
        self.config_data = config_data or {}
        self._tags: Dict[str, OCPPTag] = {}
        self._tag_lists: Dict[str, TagList] = {}
        self._lock = asyncio.Lock()
        self._next_list_version = 1
        
        # Load tags from configuration if available
        self._load_tags_from_config()
    
    def is_enabled(self) -> bool:
        """Check if tag management is enabled for any organization"""
        return len(self._tag_lists) > 0
    
    def _load_tags_from_config(self):
        """Load tags from configuration data"""
        try:
            organizations = self.config_data.get('organizations', [])
            for org in organizations:
                org_name = org.get('name', 'default')
                tags_config = org.get('tags', [])
                
                if tags_config:
                    tag_list = TagList(
                        list_version=self._next_list_version,
                        tags=[]
                    )
                    
                    for tag_config in tags_config:
                        tag = OCPPTag(**tag_config)
                        tag_list.tags.append(tag)
                        self._tags[f"{org_name}:{tag.id_tag}"] = tag
                    
                    self._tag_lists[org_name] = tag_list
                    self._next_list_version += 1
                    
                    logger.info(f"Loaded {len(tag_list.tags)} tags for organization {org_name}")
        
        except Exception as e:
            logger.error(f"Error loading tags from configuration: {e}")
    
    async def add_tag(self, org_name: str, tag: OCPPTag) -> bool:
        """Add a new tag to the organization"""
        async with self._lock:
            try:
                tag_key = f"{org_name}:{tag.id_tag}"
                
                if tag_key in self._tags:
                    logger.warning(f"Tag {tag.id_tag} already exists in organization {org_name}")
                    return False
                
                # Set timestamps
                now = datetime.now(timezone.utc).isoformat()
                tag.created_at = now
                tag.updated_at = now
                
                # Add to tags dictionary
                self._tags[tag_key] = tag
                
                # Add to organization's tag list
                if org_name not in self._tag_lists:
                    self._tag_lists[org_name] = TagList(
                        list_version=self._next_list_version,
                        tags=[]
                    )
                    self._next_list_version += 1
                
                self._tag_lists[org_name].tags.append(tag)
                self._tag_lists[org_name].updated_at = now
                
                logger.info(f"Added tag {tag.id_tag} to organization {org_name}")
                return True
                
            except Exception as e:
                logger.error(f"Error adding tag {tag.id_tag}: {e}")
                return False
    
    async def get_tag(self, org_name: str, id_tag: str) -> Optional[OCPPTag]:
        """Get a specific tag"""
        tag_key = f"{org_name}:{id_tag}"
        return self._tags.get(tag_key)
    
    async def update_tag(self, org_name: str, id_tag: str, updated_tag: OCPPTag) -> bool:
        """Update an existing tag"""
        async with self._lock:
            try:
                tag_key = f"{org_name}:{id_tag}"
                
                if tag_key not in self._tags:
                    logger.warning(f"Tag {id_tag} not found in organization {org_name}")
                    return False
                
                # Preserve creation timestamp
                original_tag = self._tags[tag_key]
                updated_tag.created_at = original_tag.created_at
                updated_tag.updated_at = datetime.now(timezone.utc).isoformat()
                
                # Update tag
                self._tags[tag_key] = updated_tag
                
                # Update in organization's tag list
                if org_name in self._tag_lists:
                    for i, tag in enumerate(self._tag_lists[org_name].tags):
                        if tag.id_tag == id_tag:
                            self._tag_lists[org_name].tags[i] = updated_tag
                            self._tag_lists[org_name].updated_at = updated_tag.updated_at
                            break
                
                logger.info(f"Updated tag {id_tag} in organization {org_name}")
                return True
                
            except Exception as e:
                logger.error(f"Error updating tag {id_tag}: {e}")
                return False
    
    async def delete_tag(self, org_name: str, id_tag: str) -> bool:
        """Delete a tag"""
        async with self._lock:
            try:
                tag_key = f"{org_name}:{id_tag}"
                
                if tag_key not in self._tags:
                    logger.warning(f"Tag {id_tag} not found in organization {org_name}")
                    return False
                
                # Remove from tags dictionary
                del self._tags[tag_key]
                
                # Remove from organization's tag list
                if org_name in self._tag_lists:
                    self._tag_lists[org_name].tags = [
                        tag for tag in self._tag_lists[org_name].tags 
                        if tag.id_tag != id_tag
                    ]
                    self._tag_lists[org_name].updated_at = datetime.now(timezone.utc).isoformat()
                
                logger.info(f"Deleted tag {id_tag} from organization {org_name}")
                return True
                
            except Exception as e:
                logger.error(f"Error deleting tag {id_tag}: {e}")
                return False
    
    async def search_tags(self, org_name: str, search_request: TagSearchRequest) -> TagSearchResponse:
        """Search tags with filters"""
        try:
            # Get organization's tags
            org_tags = []
            for tag_key, tag in self._tags.items():
                if tag_key.startswith(f"{org_name}:"):
                    org_tags.append(tag)
            
            # Apply filters
            filtered_tags = org_tags
            
            if search_request.id_tag:
                filtered_tags = [tag for tag in filtered_tags 
                               if search_request.id_tag.upper() in tag.id_tag.upper()]
            
            if search_request.status:
                filtered_tags = [tag for tag in filtered_tags 
                               if tag.status == search_request.status]
            
            if search_request.tag_type:
                filtered_tags = [tag for tag in filtered_tags 
                               if tag.tag_type == search_request.tag_type]
            
            if search_request.parent_id_tag:
                filtered_tags = [tag for tag in filtered_tags 
                               if tag.parent_id_tag == search_request.parent_id_tag]
            
            # Apply pagination
            total = len(filtered_tags)
            start = search_request.offset or 0
            end = start + (search_request.limit or 100)
            paginated_tags = filtered_tags[start:end]
            
            return TagSearchResponse(
                tags=paginated_tags,
                total=total,
                limit=search_request.limit or 100,
                offset=search_request.offset or 0
            )
            
        except Exception as e:
            logger.error(f"Error searching tags: {e}")
            return TagSearchResponse(tags=[], total=0, limit=0, offset=0)
    
    async def get_tag_list(self, org_name: str) -> Optional[TagList]:
        """Get the complete tag list for an organization"""
        return self._tag_lists.get(org_name)
    
    async def get_tag_statistics(self, org_name: str) -> TagStatistics:
        """Get tag statistics for an organization"""
        try:
            org_tags = []
            for tag_key, tag in self._tags.items():
                if tag_key.startswith(f"{org_name}:"):
                    org_tags.append(tag)
            
            total_tags = len(org_tags)
            active_tags = len([tag for tag in org_tags if tag.status == TagStatus.ACCEPTED])
            expired_tags = len([tag for tag in org_tags if tag.status == TagStatus.EXPIRED])
            blocked_tags = len([tag for tag in org_tags if tag.status == TagStatus.BLOCKED])
            
            # Count by type
            tags_by_type = {}
            for tag in org_tags:
                tag_type = tag.tag_type.value
                tags_by_type[tag_type] = tags_by_type.get(tag_type, 0) + 1
            
            # Count by status
            tags_by_status = {}
            for tag in org_tags:
                status = tag.status.value
                tags_by_status[status] = tags_by_status.get(status, 0) + 1
            
            return TagStatistics(
                total_tags=total_tags,
                active_tags=active_tags,
                expired_tags=expired_tags,
                blocked_tags=blocked_tags,
                tags_by_type=tags_by_type,
                tags_by_status=tags_by_status
            )
            
        except Exception as e:
            logger.error(f"Error getting tag statistics: {e}")
            return TagStatistics(
                total_tags=0, active_tags=0, expired_tags=0, blocked_tags=0,
                tags_by_type={}, tags_by_status={}
            )
    
    async def validate_tag(self, tag: OCPPTag) -> TagValidationResult:
        """Validate a tag"""
        errors = []
        warnings = []
        
        try:
            # Check ID tag format
            if not tag.id_tag or len(tag.id_tag.strip()) == 0:
                errors.append("ID tag cannot be empty")
            elif len(tag.id_tag) > 20:
                errors.append("ID tag cannot exceed 20 characters")
            
            # Check expiry date
            if tag.expiry_date:
                try:
                    expiry_dt = datetime.fromisoformat(tag.expiry_date.replace('Z', '+00:00'))
                    if expiry_dt < datetime.now(timezone.utc):
                        warnings.append("Tag has already expired")
                except ValueError:
                    errors.append("Invalid expiry date format")
            
            # Check parent tag exists if specified
            if tag.parent_id_tag:
                parent_exists = any(
                    t.parent_id_tag == tag.parent_id_tag 
                    for t in self._tags.values()
                )
                if not parent_exists:
                    warnings.append("Parent tag not found in system")
            
            # Check for duplicate ID tag
            duplicate_exists = any(
                t.id_tag == tag.id_tag and t != tag 
                for t in self._tags.values()
            )
            if duplicate_exists:
                errors.append("Tag with this ID already exists")
            
            return TagValidationResult(
                valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                tag=tag if len(errors) == 0 else None
            )
            
        except Exception as e:
            logger.error(f"Error validating tag: {e}")
            return TagValidationResult(
                valid=False,
                errors=[f"Validation error: {str(e)}"],
                warnings=[]
            )
    
    async def bulk_operation(self, org_name: str, operation: TagBulkOperation) -> TagBulkResponse:
        """Perform bulk operations on tags"""
        success_count = 0
        error_count = 0
        errors = []
        updated_tags = []
        
        for tag in operation.tags:
            try:
                if operation.operation == 'add':
                    success = await self.add_tag(org_name, tag)
                elif operation.operation == 'update':
                    success = await self.update_tag(org_name, tag.id_tag, tag)
                elif operation.operation == 'delete':
                    success = await self.delete_tag(org_name, tag.id_tag)
                else:
                    success = False
                
                if success:
                    success_count += 1
                    updated_tags.append(tag)
                else:
                    error_count += 1
                    errors.append({
                        "tag": tag.id_tag,
                        "error": f"Failed to {operation.operation} tag"
                    })
                    
            except Exception as e:
                error_count += 1
                errors.append({
                    "tag": tag.id_tag,
                    "error": str(e)
                })
        
        return TagBulkResponse(
            success_count=success_count,
            error_count=error_count,
            errors=errors,
            updated_tags=updated_tags
        )
    
    async def authorize_tag(self, org_name: str, id_tag: str) -> Dict[str, Any]:
        """
        Authorize a tag for charging.
        This is the core method used by the OCPP Authorize command.
        """
        try:
            tag = await self.get_tag(org_name, id_tag)
            
            if not tag:
                return {
                    "status": TagStatus.INVALID.value,
                    "expiry_date": None,
                    "parent_id_tag": None
                }
            
            # Check if tag is expired
            if tag.expiry_date:
                try:
                    expiry_dt = datetime.fromisoformat(tag.expiry_date.replace('Z', '+00:00'))
                    if expiry_dt < datetime.now(timezone.utc):
                        return {
                            "status": TagStatus.EXPIRED.value,
                            "expiry_date": tag.expiry_date,
                            "parent_id_tag": tag.parent_id_tag
                        }
                except ValueError:
                    # Invalid expiry date format, treat as expired
                    return {
                        "status": TagStatus.EXPIRED.value,
                        "expiry_date": tag.expiry_date,
                        "parent_id_tag": tag.parent_id_tag
                    }
            
            # Return tag authorization info
            return {
                "status": tag.status.value,
                "expiry_date": tag.expiry_date,
                "parent_id_tag": tag.parent_id_tag
            }
            
        except Exception as e:
            logger.error(f"Error authorizing tag {id_tag}: {e}")
            return {
                "status": TagStatus.INVALID.value,
                "expiry_date": None,
                "parent_id_tag": None
            }
    
    async def import_tags(self, org_name: str, import_request: TagImportRequest) -> TagImportResponse:
        """Import tags from external source"""
        imported_count = 0
        skipped_count = 0
        error_count = 0
        errors = []
        imported_tags = []
        
        try:
            if import_request.source == 'json':
                data = json.loads(import_request.data)
                tags_data = data if isinstance(data, list) else data.get('tags', [])
            elif import_request.source == 'csv':
                # Parse CSV data
                csv_reader = csv.DictReader(import_request.data.split('\n'))
                tags_data = list(csv_reader)
            else:
                raise ValueError(f"Unsupported import source: {import_request.source}")
            
            for tag_data in tags_data:
                try:
                    # Convert to OCPPTag
                    tag = OCPPTag(**tag_data)
                    
                    # Validate tag
                    validation = await self.validate_tag(tag)
                    if not validation.valid:
                        error_count += 1
                        errors.append({
                            "tag": tag.id_tag,
                            "error": "; ".join(validation.errors)
                        })
                        continue
                    
                    # Check if tag already exists
                    existing_tag = await self.get_tag(org_name, tag.id_tag)
                    if existing_tag and not import_request.overwrite_existing:
                        skipped_count += 1
                        continue
                    
                    # Add or update tag
                    if existing_tag and import_request.overwrite_existing:
                        success = await self.update_tag(org_name, tag.id_tag, tag)
                    else:
                        success = await self.add_tag(org_name, tag)
                    
                    if success:
                        imported_count += 1
                        imported_tags.append(tag)
                    else:
                        error_count += 1
                        errors.append({
                            "tag": tag.id_tag,
                            "error": "Failed to import tag"
                        })
                        
                except Exception as e:
                    error_count += 1
                    errors.append({
                        "tag": tag_data.get('id_tag', 'unknown'),
                        "error": str(e)
                    })
            
            return TagImportResponse(
                imported_count=imported_count,
                skipped_count=skipped_count,
                error_count=error_count,
                errors=errors,
                imported_tags=imported_tags
            )
            
        except Exception as e:
            logger.error(f"Error importing tags: {e}")
            return TagImportResponse(
                imported_count=0,
                skipped_count=0,
                error_count=1,
                errors=[{"tag": "all", "error": str(e)}],
                imported_tags=[]
            )
    
    async def export_tags(self, org_name: str, export_request: TagExportRequest) -> TagExportResponse:
        """Export tags to external format"""
        try:
            # Get organization's tags
            org_tags = []
            for tag_key, tag in self._tags.items():
                if tag_key.startswith(f"{org_name}:"):
                    # Apply filters
                    if export_request.filter_status and tag.status not in export_request.filter_status:
                        continue
                    if export_request.filter_type and tag.tag_type not in export_request.filter_type:
                        continue
                    org_tags.append(tag)
            
            if export_request.format == 'json':
                export_data = {
                    "organization": org_name,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "tag_count": len(org_tags),
                    "tags": [tag.dict() for tag in org_tags]
                }
                data = json.dumps(export_data, indent=2)
                
            elif export_request.format == 'csv':
                if not org_tags:
                    data = "id_tag,status,tag_type,expiry_date,parent_id_tag,description\n"
                else:
                    # Create CSV
                    fieldnames = ['id_tag', 'status', 'tag_type', 'expiry_date', 'parent_id_tag', 'description']
                    if export_request.include_metadata:
                        fieldnames.extend(['created_at', 'updated_at'])
                    
                    output = []
                    output.append(','.join(fieldnames))
                    
                    for tag in org_tags:
                        row = [
                            tag.id_tag,
                            tag.status.value,
                            tag.tag_type.value,
                            tag.expiry_date or '',
                            tag.parent_id_tag or '',
                            tag.description or ''
                        ]
                        if export_request.include_metadata:
                            row.extend([
                                tag.created_at or '',
                                tag.updated_at or ''
                            ])
                        output.append(','.join(f'"{str(field)}"' for field in row))
                    
                    data = '\n'.join(output)
            
            else:
                raise ValueError(f"Unsupported export format: {export_request.format}")
            
            return TagExportResponse(
                format=export_request.format,
                data=data,
                tag_count=len(org_tags),
                file_size=len(data.encode('utf-8'))
            )
            
        except Exception as e:
            logger.error(f"Error exporting tags: {e}")
            return TagExportResponse(
                format=export_request.format,
                data="",
                tag_count=0,
                file_size=0
            )
