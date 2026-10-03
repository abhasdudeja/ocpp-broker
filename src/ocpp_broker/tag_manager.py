"""
OCPP Tag Manager

Simple service for managing OCPP tags and authorization lists.
Provides basic CRUD operations and search functionality.
Supports both MongoDB persistence and in-memory storage.
"""

import asyncio
import csv
import io
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .schemas.tags import (
    BulkTagItemResult, BulkTagResult, OCPPTag, TagImportError, TagImportResult,
    TagList, TagSearchRequest, TagSearchResponse, TagStatistics, TagStatus,
    TagValidationResult,
)


logger = logging.getLogger("ocpp_broker.tag_manager")


class TagSyncUnavailable(RuntimeError):
    """MongoDB is not connected, so there is nothing to sync with."""


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
    
    def __init__(self, config_data: Optional[Dict[str, Any]] = None, mongodb_service=None):
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
                    tag = self._tag_from_document(tag_dict)
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
            org_tags = await self._load_org_tags(org_name)
            
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
                    tags = self._tags_from_documents(tags_dicts)
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
    
    # ------------------------------------------------------------------
    # MongoDB documents <-> tags
    # ------------------------------------------------------------------
    @staticmethod
    def _tag_from_document(doc: Dict[str, Any]) -> OCPPTag:
        """
        Build a tag from a stored document. MongoDB adds ``org_name`` and keeps
        timestamps as (naive UTC) datetimes, while OCPPTag holds ISO strings.
        """
        data = {k: v for k, v in doc.items() if k != "org_name"}
        for field in ("created_at", "updated_at"):
            value = data.get(field)
            if isinstance(value, datetime):
                data[field] = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
        return OCPPTag(**data)

    @classmethod
    def _tags_from_documents(cls, docs: List[Dict[str, Any]]) -> List[OCPPTag]:
        """Convert stored documents; an unreadable one is logged and skipped, not fatal."""
        tags = []
        for doc in docs:
            try:
                tags.append(cls._tag_from_document(doc))
            except Exception as e:
                logger.warning(f"Skipping unreadable stored tag {doc.get('id_tag')!r}: {_describe(e)}")
        return tags

    async def sync_from_mongodb(self, org_name: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        """
        Reconcile the in-memory tags with MongoDB (one organization, or all).

        MongoDB is authoritative once it holds tags for an organization: the
        cache (and tag list) is replaced by what is stored, so a tag removed or
        revoked directly in MongoDB disappears here too. An organization that is
        in memory but has *nothing* stored (e.g. tags seeded from config.yaml)
        is bootstrapped the other way, pushed into MongoDB instead.

        Returns ``{org: {"loaded": n, "seeded": n, "dropped": n}}``. Raises
        TagSyncUnavailable if MongoDB is not connected.
        """
        mongo = self.mongodb_service
        if mongo is None or not mongo.is_connected():
            raise TagSyncUnavailable("MongoDB is not connected")

        summary: Dict[str, Dict[str, int]] = {}
        async with self._lock:  # no add/update/delete may interleave with the swap
            documents: Dict[Any, List[Dict[str, Any]]] = {}
            for doc in await mongo.list_tags(org_name=org_name):
                documents.setdefault(doc.get("org_name"), []).append(doc)
            orgs = {org_name} if org_name else set(documents) | set(self._tag_lists)

            for org in sorted(o for o in orgs if o):
                prefix = f"{org}:"
                cached = {key: tag for key, tag in self._tags.items() if key.startswith(prefix)}
                stored = documents.get(org, [])

                if stored:
                    tags = self._tags_from_documents(stored)
                    for key in cached:
                        self._tags.pop(key)
                    for tag in tags:
                        self._tags[f"{org}:{tag.id_tag}"] = tag
                    dropped = len(set(cached) - {f"{org}:{t.id_tag}" for t in tags})
                    self._tag_lists[org] = TagList(
                        list_version=await mongo.get_tag_list_version(org),
                        tags=tags,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if dropped:
                        logger.warning(f"Sync dropped {dropped} in-memory tag(s) for {org} that MongoDB does not have")
                    summary[org] = {"loaded": len(tags), "seeded": 0, "dropped": dropped}
                else:
                    seeded = 0
                    for tag in cached.values():
                        if await mongo.save_tag(org, tag.model_dump()):
                            seeded += 1
                    if seeded and org in self._tag_lists:
                        await mongo.update_tag_list_version(org, self._tag_lists[org].list_version)
                    summary[org] = {"loaded": 0, "seeded": seeded, "dropped": 0}

        logger.info(f"Synced tags with MongoDB: {summary}")
        return summary

    # ------------------------------------------------------------------
    # Helpers shared by statistics, validation, bulk, import and export
    # ------------------------------------------------------------------
    async def _load_org_tags(self, org_name: str) -> List[OCPPTag]:
        """All of an organization's tags, filling the cache from MongoDB if it is empty."""
        prefix = f"{org_name}:"
        if self._use_mongodb and self.mongodb_service.is_connected():
            if not any(key.startswith(prefix) for key in self._tags):
                try:
                    stored = await self.mongodb_service.list_tags(org_name=org_name)
                    for tag in self._tags_from_documents(stored):
                        self._tags[f"{org_name}:{tag.id_tag}"] = tag
                except Exception as e:
                    logger.warning(f"Error loading tags from MongoDB: {e}")
        return [tag for key, tag in self._tags.items() if key.startswith(prefix)]

    @staticmethod
    def _expiry(tag: OCPPTag) -> Optional[datetime]:
        """The tag's expiry as an aware datetime; ValueError if the stored text is not ISO 8601."""
        if not tag.expiry_date:
            return None
        expiry = datetime.fromisoformat(tag.expiry_date.replace("Z", "+00:00"))
        return expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)

    @classmethod
    def _is_expired(cls, tag: OCPPTag) -> bool:
        """Same rule authorize_tag applies: past expiry, or an expiry date we cannot read."""
        try:
            expiry = cls._expiry(tag)
        except ValueError:
            return True
        return expiry is not None and expiry < datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    async def get_tag_statistics(self, org_name: str) -> TagStatistics:
        tags = await self._load_org_tags(org_name)
        return TagStatistics(
            total_tags=len(tags),
            active_tags=sum(1 for t in tags if t.status == TagStatus.ACCEPTED and not self._is_expired(t)),
            expired_tags=sum(1 for t in tags if t.status == TagStatus.EXPIRED or self._is_expired(t)),
            blocked_tags=sum(1 for t in tags if t.status == TagStatus.BLOCKED),
            tags_by_type=dict(Counter(t.tag_type.value for t in tags)),
            tags_by_status=dict(Counter(t.status.value for t in tags)),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    async def validate_tag(
        self,
        org_name: str,
        tag: OCPPTag,
        check_duplicate: bool = True,
        known_ids: Optional[set] = None,
    ) -> TagValidationResult:
        """
        Check a tag against the organization's rules without storing it.

        ``check_duplicate`` makes an already-stored id an error (use False when
        validating an update). ``known_ids`` are extra ids that count as existing
        parents, e.g. other tags in the same import batch.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # OCPP idTag is a CiString20: printable ASCII (length is enforced by the model)
        if not (tag.id_tag.isascii() and tag.id_tag.isprintable()) or tag.id_tag != tag.id_tag.strip():
            errors.append("id_tag must be printable ASCII without leading or trailing spaces")

        if check_duplicate and await self.get_tag(org_name, tag.id_tag):
            errors.append(f"Tag {tag.id_tag} already exists in organization {org_name}")

        if tag.expiry_date:
            try:
                expiry = self._expiry(tag)
            except ValueError:
                errors.append(f"expiry_date '{tag.expiry_date}' is not a valid ISO 8601 date")
            else:
                if expiry is not None and expiry < datetime.now(timezone.utc):
                    warnings.append("expiry_date is in the past; the tag will authorize as Expired")

        if tag.parent_id_tag:
            if tag.parent_id_tag == tag.id_tag:
                errors.append("parent_id_tag cannot be the tag itself")
            elif not (known_ids and tag.parent_id_tag in known_ids) and not await self.get_tag(
                org_name, tag.parent_id_tag
            ):
                errors.append(f"parent_id_tag '{tag.parent_id_tag}' does not exist in organization {org_name}")

        return TagValidationResult(is_valid=not errors, errors=errors, warnings=warnings)

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    async def bulk_operation(self, org_name: str, operation: str, tags: List[OCPPTag]) -> BulkTagResult:
        """Apply add / update / delete to each tag. Items are independent: one failing does not stop the rest."""
        results: List[BulkTagItemResult] = []
        for tag in tags:
            error: Optional[str] = None
            exists = await self.get_tag(org_name, tag.id_tag) is not None
            if operation == "add":
                if exists:
                    error = "already exists"
                elif not await self.add_tag(org_name, tag):
                    error = "could not be added"
            elif operation == "update":
                if not exists:
                    error = "not found"
                elif not await self.update_tag(org_name, tag.id_tag, tag):
                    error = "could not be updated"
            elif operation == "delete":
                if not exists:
                    error = "not found"
                elif not await self.delete_tag(org_name, tag.id_tag):
                    error = "could not be deleted"
            else:
                raise ValueError(f"Unknown bulk operation: {operation}")
            results.append(BulkTagItemResult(id_tag=tag.id_tag, success=error is None, error=error))

        succeeded = sum(1 for r in results if r.success)
        return BulkTagResult(
            operation=operation,
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=results,
        )

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------
    _CSV_FIELDS = ["id_tag", "status", "tag_type", "expiry_date", "parent_id_tag", "description"]
    _CSV_METADATA_FIELDS = ["created_at", "updated_at", "metadata"]

    @staticmethod
    def _parse_import(source: str, data: str) -> List[Dict[str, Any]]:
        """Turn the uploaded text into raw tag dicts; ValueError if it is not parseable at all."""
        if source == "json":
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}") from e
            records = parsed.get("tags") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("JSON must be a list of tags or an object with a 'tags' list")
            return records

        records = []
        for row in csv.DictReader(io.StringIO(data)):
            record: Dict[str, Any] = {k: v for k, v in row.items() if k and v not in (None, "")}
            if "metadata" in record:
                try:
                    record["metadata"] = json.loads(record["metadata"])
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid metadata JSON for tag {record.get('id_tag')}: {e}") from e
            records.append(record)
        return records

    async def import_tags(
        self,
        org_name: str,
        source: str,
        data: str,
        overwrite_existing: bool = False,
        validate_only: bool = False,
    ) -> TagImportResult:
        """
        Import tags from JSON or CSV text. Bad records are reported and skipped;
        good ones still go in. With ``validate_only`` nothing is changed.
        """
        records = self._parse_import(source, data)
        errors: List[TagImportError] = []
        parsed: List[tuple] = []  # (record number, OCPPTag)
        for number, record in enumerate(records, start=1):
            try:
                parsed.append((number, OCPPTag(**record)))
            except Exception as e:
                id_tag = record.get("id_tag") if isinstance(record, dict) else None
                errors.append(TagImportError(record=number, id_tag=id_tag, error=_describe(e)))

        batch_ids = {tag.id_tag for _, tag in parsed}
        imported = updated = skipped = 0
        seen: set = set()
        for number, tag in parsed:
            if tag.id_tag in seen:
                errors.append(TagImportError(record=number, id_tag=tag.id_tag, error="duplicate id_tag in the import"))
                continue
            seen.add(tag.id_tag)

            exists = await self.get_tag(org_name, tag.id_tag) is not None
            if exists and not overwrite_existing:
                skipped += 1
                continue

            check = await self.validate_tag(org_name, tag, check_duplicate=False, known_ids=batch_ids)
            if not check.is_valid:
                errors.append(TagImportError(record=number, id_tag=tag.id_tag, error="; ".join(check.errors)))
                continue

            if validate_only:
                ok = True
            elif exists:
                ok = await self.update_tag(org_name, tag.id_tag, tag)
            else:
                ok = await self.add_tag(org_name, tag)
            if not ok:
                errors.append(TagImportError(record=number, id_tag=tag.id_tag, error="could not be stored"))
            elif exists:
                updated += 1
            else:
                imported += 1

        return TagImportResult(
            source=source,
            validate_only=validate_only,
            total=len(records),
            imported=imported,
            updated=updated,
            skipped=skipped,
            errors=errors,
        )

    async def export_tags(self, org_name: str, fmt: str = "json", include_metadata: bool = True) -> str:
        """Serialise the organization's tags as JSON or CSV text (re-importable with import_tags)."""
        tags = await self._load_org_tags(org_name)
        drop = set() if include_metadata else set(self._CSV_METADATA_FIELDS)
        rows = [{k: v for k, v in tag.model_dump(mode="json").items() if k not in drop} for tag in tags]

        if fmt == "json":
            return json.dumps(
                {
                    "organization": org_name,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "count": len(rows),
                    "tags": rows,
                },
                indent=2,
            )
        if fmt != "csv":
            raise ValueError(f"Unsupported export format: {fmt}")

        fields = self._CSV_FIELDS + ([] if not include_metadata else self._CSV_METADATA_FIELDS)
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if row.get("metadata") is not None:
                row["metadata"] = json.dumps(row["metadata"])
            writer.writerow(row)
        return out.getvalue()

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


def _describe(error: Exception) -> str:
    """Readable one-liner for a bad import record (pydantic's own text is multi-line)."""
    errors = getattr(error, "errors", None)
    if callable(errors):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'record'}: {item['msg']}" for item in errors()
        )
    return str(error) or type(error).__name__