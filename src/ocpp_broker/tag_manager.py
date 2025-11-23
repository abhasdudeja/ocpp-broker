"""
OCPP Tag Manager

Simple service for managing OCPP tags and authorization lists.
Provides basic CRUD operations and search functionality.
Supports both MongoDB persistence and in-memory storage.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from .schemas.tags import (
    OCPPTag, TagList, TagStatus, TagType, TagSearchRequest, 
    TagSearchResponse
)


logger = logging.getLogger("ocpp_broker.tag_manager")


class TagManager:
    """
    Manages OCPP tags and authorization lists.
    
    Features:
    - Basic CRUD operations for tags (with MongoDB persistence)
    - Tag search and listing
    - Tag authorization (for OCPP Authorize command)
    - Configuration-based tag management
    - Hybrid storage: MongoDB when available, in-memory fallback
    """
    
    def __init__(self, config_data: Dict[str, Any] = None, mongodb_service=None):
        """
        Initialize TagManager.
        
        Args:
            config_data: Configuration data for loading initial tags
            mongodb_service: Optional MongoDBService instance for persistence
        """
        self.config_data = config_data or {}
        self.mongodb_service = mongodb_service
        self._tags: Dict[str, OCPPTag] = {}
        self._tag_lists: Dict[str, TagList] = {}
        self._lock = asyncio.Lock()
        self._next_list_version = 1
        self._use_mongodb = mongodb_service is not None and mongodb_service.is_connected()
        
        # Load tags from configuration if available
        self._load_tags_from_config()
        
        # Load tags from MongoDB if available
        if self._use_mongodb:
            # Note: We'll load tags from MongoDB asynchronously when needed
            logger.info("TagManager initialized with MongoDB persistence")
        else:
            logger.info("TagManager initialized with in-memory storage only")
    
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
                    
                    logger.info(f"Loaded {len(tag_list.tags)} tags for organization {org_name} from config")
                    
                    # Note: Tags loaded from config will be saved to MongoDB when first accessed
                    # or can be synced using sync_from_mongodb() method
        
        except Exception as e:
            logger.error(f"Error loading tags from configuration: {e}")
    
    async def add_tag(self, org_name: str, tag: OCPPTag) -> bool:
        """Add a new tag to the organization"""
        async with self._lock:
            try:
                tag_key = f"{org_name}:{tag.id_tag}"
                
                # Check if tag already exists (in memory or MongoDB)
                existing_tag = await self.get_tag(org_name, tag.id_tag)
                if existing_tag:
                    logger.warning(f"Tag {tag.id_tag} already exists in organization {org_name}")
                    return False
                
                # Set timestamps
                now = datetime.now(timezone.utc).isoformat()
                tag.created_at = now
                tag.updated_at = now
                
                # Save to MongoDB if available
                if self._use_mongodb and self.mongodb_service.is_connected():
                    tag_dict = tag.model_dump()
                    success = await self.mongodb_service.save_tag(org_name, tag_dict)
                    if not success:
                        logger.warning(f"Failed to save tag {tag.id_tag} to MongoDB, continuing with in-memory storage")
                
                # Add to tags dictionary (in-memory cache)
                self._tags[tag_key] = tag
                
                # Add to organization's tag list
                if org_name not in self._tag_lists:
                    # Get current list version from MongoDB if available
                    if self._use_mongodb and self.mongodb_service.is_connected():
                        current_version = await self.mongodb_service.get_tag_list_version(org_name)
                        if current_version > 0:
                            self._next_list_version = current_version + 1
                    
                    self._tag_lists[org_name] = TagList(
                        list_version=self._next_list_version,
                        tags=[]
                    )
                    # Update MongoDB list version if available
                    if self._use_mongodb and self.mongodb_service.is_connected():
                        await self.mongodb_service.update_tag_list_version(org_name, self._next_list_version)
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
        
        # Check in-memory cache first
        if tag_key in self._tags:
            return self._tags[tag_key]
        
        # Try MongoDB if available
        if self._use_mongodb and self.mongodb_service.is_connected():
            try:
                tag_dict = await self.mongodb_service.get_tag(org_name, id_tag)
                if tag_dict:
                    # Convert to OCPPTag and cache
                    tag = OCPPTag(**tag_dict)
                    self._tags[tag_key] = tag
                    return tag
            except Exception as e:
                logger.warning(f"Error loading tag from MongoDB: {e}")
        
        return None
    
    async def update_tag(self, org_name: str, id_tag: str, updated_tag: OCPPTag) -> bool:
        """Update an existing tag"""
        async with self._lock:
            try:
                tag_key = f"{org_name}:{id_tag}"
                
                # Get original tag (from cache or MongoDB)
                original_tag = await self.get_tag(org_name, id_tag)
                if not original_tag:
                    logger.warning(f"Tag {id_tag} not found in organization {org_name}")
                    return False
                
                # Preserve creation timestamp
                updated_tag.created_at = original_tag.created_at
                updated_tag.updated_at = datetime.now(timezone.utc).isoformat()
                
                # Update in MongoDB if available
                if self._use_mongodb and self.mongodb_service.is_connected():
                    tag_dict = updated_tag.model_dump()
                    success = await self.mongodb_service.save_tag(org_name, tag_dict)
                    if not success:
                        logger.warning(f"Failed to update tag {id_tag} in MongoDB, continuing with in-memory update")
                
                # Update in-memory cache
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
                
                # Check if tag exists
                existing_tag = await self.get_tag(org_name, id_tag)
                if not existing_tag:
                    logger.warning(f"Tag {id_tag} not found in organization {org_name}")
                    return False
                
                # Delete from MongoDB if available
                if self._use_mongodb and self.mongodb_service.is_connected():
                    success = await self.mongodb_service.delete_tag(org_name, id_tag)
                    if not success:
                        logger.warning(f"Failed to delete tag {id_tag} from MongoDB, continuing with in-memory deletion")
                
                # Remove from tags dictionary
                if tag_key in self._tags:
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
            # Load tags from MongoDB if available and cache is empty
            if self._use_mongodb and self.mongodb_service.is_connected():
                # Check if we need to load from MongoDB
                org_tags_in_cache = [tag for tag_key, tag in self._tags.items() if tag_key.startswith(f"{org_name}:")]
                if not org_tags_in_cache:
                    # Load all tags for this org from MongoDB
                    try:
                        tags_dicts = await self.mongodb_service.list_tags(org_name=org_name)
                        for tag_dict in tags_dicts:
                            tag = OCPPTag(**tag_dict)
                            tag_key = f"{org_name}:{tag.id_tag}"
                            self._tags[tag_key] = tag
                    except Exception as e:
                        logger.warning(f"Error loading tags from MongoDB: {e}")
            
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
        # Load tags from MongoDB if cache is empty
        if self._use_mongodb and self.mongodb_service.is_connected():
            org_tags_in_cache = [tag for tag_key, tag in self._tags.items() if tag_key.startswith(f"{org_name}:")]
            if not org_tags_in_cache:
                try:
                    tags_dicts = await self.mongodb_service.list_tags(org_name=org_name)
                    tags = [OCPPTag(**tag_dict) for tag_dict in tags_dicts]
                    if tags:
                        list_version = await self.mongodb_service.get_tag_list_version(org_name)
                        tag_list = TagList(
                            list_version=list_version,
                            tags=tags,
                            updated_at=datetime.now(timezone.utc).isoformat()
                        )
                        self._tag_lists[org_name] = tag_list
                        # Cache individual tags
                        for tag in tags:
                            tag_key = f"{org_name}:{tag.id_tag}"
                            self._tags[tag_key] = tag
                        return tag_list
                except Exception as e:
                    logger.warning(f"Error loading tag list from MongoDB: {e}")
        
        return self._tag_lists.get(org_name)
    
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
