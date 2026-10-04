"""
Read-only endpoints for what the broker has recorded (see history.py): transactions, meter readings,
connector status changes, commands and (when switched on) the OCPP message log.

Lists are newest first and paged with an opaque cursor, so a page is stable while new records arrive.
All of it comes from MongoDB; without it every endpoint answers ``available: false`` and a reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Path, Query

from .history import (
    DEFAULT_PAGE,
    MAX_PAGE,
    BadCursor,
    decode_cursor,
    encode_cursor,
    flatten_meter_values,
    transaction_row,
)
from .schemas.history import (
    CommandPage,
    CommandRecord,
    HistoryInfo,
    HistoryTransaction,
    MessageError,
    MessagePage,
    MessageRecord,
    MeterReading,
    MeterReadingPage,
    StatusPage,
    StatusRecord,
    TransactionDetail,
    TransactionPage,
)

DETAIL_READINGS = 5000  # MeterValues documents read for one transaction


def _utc(value: Any) -> Optional[datetime]:
    """A time read back from MongoDB (naive UTC), or a time given in a query, as an aware UTC datetime."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _range(field: str, since: Optional[datetime], until: Optional[datetime]) -> Dict[str, Any]:
    window: Dict[str, Any] = {}
    if since is not None:
        window["$gte"] = _utc(since)
    if until is not None:
        window["$lt"] = _utc(until)
    return {field: window} if window else {}


def _filters(org: Optional[str], charger_id: Optional[str]) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if org:
        query["org_name"] = org
    if charger_id:
        query["charger_id"] = charger_id
    return query


def _transaction(document: Dict[str, Any]) -> HistoryTransaction:
    row = transaction_row(document)
    row["started_at"] = _utc(row["started_at"])
    row["stopped_at"] = _utc(row["stopped_at"])
    return HistoryTransaction(**row)


def _reading(row: Dict[str, Any]) -> MeterReading:
    stamp = row["timestamp"]
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            stamp = None
    return MeterReading(**{**row, "timestamp": _utc(stamp)})


def create_history_api(broker: Any) -> APIRouter:
    router = APIRouter(prefix="/api/history", tags=["History"])

    def _service() -> Tuple[Any, Optional[str]]:
        """The MongoDB service, or None and the reason there is none."""
        service = getattr(broker, "mongodb_service", None)
        if service is None:
            return None, "MongoDB is not configured, so nothing is recorded"
        if not service.is_connected():
            return None, "MongoDB is not connected"
        return service, None

    def _cursor(text: Optional[str]) -> Optional[Tuple[datetime, str]]:
        if text is None:
            return None
        try:
            return decode_cursor(text)
        except BadCursor as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    async def _page(collection: str, query: Dict[str, Any], time_field: str, limit: int, cursor: Optional[str]):
        """(rows, next cursor text, None), or (None, None, the reason there are no rows)."""
        service, reason = _service()
        if service is None:
            return None, None, reason
        position = _cursor(cursor)
        try:
            rows, more = await service.history_page(collection, query, time_field, limit, position)
        except Exception as exc:
            return None, None, f"MongoDB did not answer: {exc}"
        return rows, (encode_cursor(*more) if more else None), None

    @router.get("/info", response_model=HistoryInfo)
    async def info() -> HistoryInfo:
        """Whether history is available, what is being recorded and for how long it is kept."""
        config = broker.history
        service, reason = _service()
        counts: Dict[str, int] = {}
        if service is not None:
            try:
                counts = await service.history_counts()
            except Exception as exc:
                service, reason = None, f"MongoDB did not answer: {exc}"
        return HistoryInfo(
            available=service is not None,
            reason=reason,
            messages_enabled=config.messages,
            heartbeats_enabled=config.heartbeats,
            retention_days=config.retention_days,
            counts=counts,
        )

    @router.get("/transactions", response_model=TransactionPage)
    async def transactions(
        org: Optional[str] = Query(None),
        charger_id: Optional[str] = Query(None),
        id_tag: Optional[str] = Query(None),
        state: Optional[str] = Query(None, pattern="^(open|closed)$", description="Only transactions still running, or only ended ones"),
        since: Optional[datetime] = Query(None, description="Started at or after this time (ISO 8601; no zone means UTC)"),
        until: Optional[datetime] = Query(None, description="Started before this time"),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        cursor: Optional[str] = Query(None),
    ) -> TransactionPage:
        """Charging sessions, newest first. Recorded when the broker answers the charger (broker mode, local leader)."""
        query = {**_filters(org, charger_id), **_range("timestamp", since, until)}
        if id_tag:
            query["id_tag"] = id_tag
        if state == "open":
            query["stop_timestamp"] = None
        elif state == "closed":
            query["stop_timestamp"] = {"$ne": None}
        rows, next_cursor, reason = await _page("transactions", query, "timestamp", limit, cursor)
        if rows is None:
            return TransactionPage(available=False, reason=reason, next_cursor=None, items=[])
        return TransactionPage(available=True, reason=None, next_cursor=next_cursor, items=[_transaction(row) for row in rows])

    @router.get("/transactions/{org}/{charger_id}/{transaction_id}", response_model=TransactionDetail)
    async def transaction(
        org: str = Path(...), charger_id: str = Path(...), transaction_id: int = Path(...)
    ) -> TransactionDetail:
        """One transaction and the meter readings recorded for it, oldest first."""
        service, reason = _service()
        if service is None:
            return TransactionDetail(available=False, reason=reason, transaction=None, readings=[], readings_truncated=False)
        key = {"org_name": org, "charger_id": charger_id, "transaction_id": transaction_id}
        try:
            found = await service.history_get("transactions", key, "timestamp", 1, ascending=False)
            documents = await service.history_get("meter_values", key, "timestamp", DETAIL_READINGS + 1)
        except Exception as exc:
            return TransactionDetail(available=False, reason=f"MongoDB did not answer: {exc}", transaction=None, readings=[], readings_truncated=False)
        if not found:
            raise HTTPException(status_code=404, detail="Transaction not found")
        truncated = len(documents) > DETAIL_READINGS
        readings = [_reading(row) for document in documents[:DETAIL_READINGS] for row in flatten_meter_values(document)]
        readings.sort(key=lambda r: r.timestamp or datetime.min.replace(tzinfo=timezone.utc))
        return TransactionDetail(
            available=True, reason=None, transaction=_transaction(found[0]), readings=readings, readings_truncated=truncated
        )

    @router.get("/meter-values", response_model=MeterReadingPage)
    async def meter_values(
        org: Optional[str] = Query(None),
        charger_id: Optional[str] = Query(None),
        transaction_id: Optional[int] = Query(None),
        connector_id: Optional[int] = Query(None),
        measurand: Optional[str] = Query(None, description="Only this measurand, for example Power.Active.Import"),
        since: Optional[datetime] = Query(None, description="Received at or after this time"),
        until: Optional[datetime] = Query(None, description="Received before this time"),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE, description="MeterValues messages per page; each holds several readings"),
        cursor: Optional[str] = Query(None),
    ) -> MeterReadingPage:
        """Meter readings, one row per measured value, newest message first."""
        query = {**_filters(org, charger_id), **_range("timestamp", since, until)}
        if transaction_id is not None:
            query["transaction_id"] = transaction_id
        if connector_id is not None:
            query["connector_id"] = connector_id
        rows, next_cursor, reason = await _page("meter_values", query, "timestamp", limit, cursor)
        if rows is None:
            return MeterReadingPage(available=False, reason=reason, next_cursor=None, items=[])
        items = [_reading(r) for document in rows for r in flatten_meter_values(document)]
        if measurand:
            items = [item for item in items if item.measurand == measurand]
        return MeterReadingPage(available=True, reason=None, next_cursor=next_cursor, items=items)

    @router.get("/statuses", response_model=StatusPage)
    async def statuses(
        org: Optional[str] = Query(None),
        charger_id: Optional[str] = Query(None),
        connector_id: Optional[int] = Query(None),
        status: Optional[str] = Query(None, description="For example Charging or Faulted"),
        since: Optional[datetime] = Query(None),
        until: Optional[datetime] = Query(None),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        cursor: Optional[str] = Query(None),
    ) -> StatusPage:
        """Connector status changes, newest first (the charger's own timestamp when it gave one)."""
        query = {**_filters(org, charger_id), **_range("timestamp", since, until)}
        if connector_id is not None:
            query["connector_id"] = connector_id
        if status:
            query["status"] = status
        rows, next_cursor, reason = await _page("charger_statuses", query, "timestamp", limit, cursor)
        if rows is None:
            return StatusPage(available=False, reason=reason, next_cursor=None, items=[])
        items = [
            StatusRecord(
                org=row.get("org_name", ""),
                charger_id=row.get("charger_id", ""),
                connector_id=row.get("connector_id"),
                status=row.get("status", ""),
                error_code=row.get("error_code"),
                info=row.get("info"),
                vendor_id=row.get("vendor_id"),
                vendor_error_code=row.get("vendor_error_code"),
                timestamp=_utc(row.get("timestamp")),
            )
            for row in rows
        ]
        return StatusPage(available=True, reason=None, next_cursor=next_cursor, items=items)

    @router.get("/commands", response_model=CommandPage)
    async def commands(
        org: Optional[str] = Query(None),
        charger_id: Optional[str] = Query(None),
        action: Optional[str] = Query(None, description="For example Reset"),
        status: Optional[str] = Query(None, pattern="^(success|error|timeout|cancelled|pending)$"),
        since: Optional[datetime] = Query(None),
        until: Optional[datetime] = Query(None),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        cursor: Optional[str] = Query(None),
    ) -> CommandPage:
        """Commands sent through the API or the console, newest first, with the outcome."""
        query = {**_filters(org, charger_id), **_range("sent_at", since, until)}
        if action:
            query["action"] = action
        if status:
            query["status"] = status
        rows, next_cursor, reason = await _page("commands", query, "sent_at", limit, cursor)
        if rows is None:
            return CommandPage(available=False, reason=reason, next_cursor=None, items=[])
        items = [
            CommandRecord(
                org=row.get("org_name", ""),
                charger_id=row.get("charger_id", ""),
                message_id=row.get("message_id", ""),
                action=row.get("action", ""),
                payload=row.get("payload"),
                status=row.get("status", "error"),
                response=row.get("response"),
                error=row.get("error"),
                sent_at=_utc(row.get("sent_at")),
                finished_at=_utc(row.get("finished_at")),
                duration_ms=row.get("duration_ms"),
            )
            for row in rows
        ]
        return CommandPage(available=True, reason=None, next_cursor=next_cursor, items=items)

    @router.get("/messages", response_model=MessagePage)
    async def messages(
        org: Optional[str] = Query(None),
        charger_id: Optional[str] = Query(None),
        action: Optional[str] = Query(None, description="For example StatusNotification (replies carry the action they answer)"),
        direction: Optional[str] = Query(None, pattern="^(in|out)$"),
        message_id: Optional[str] = Query(None, description="A request and its reply share an id"),
        since: Optional[datetime] = Query(None),
        until: Optional[datetime] = Query(None),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        cursor: Optional[str] = Query(None),
    ) -> MessagePage:
        """
        The OCPP frames to and from chargers, newest first. Empty unless `mongodb.history.messages` is on
        (see `GET /api/history/info`). Payloads are stored as sent: they can hold id tags.
        """
        query = {**_filters(org, charger_id), **_range("timestamp", since, until)}
        if action:
            query["action"] = action
        if direction:
            query["direction"] = direction
        if message_id:
            query["message_id"] = message_id
        rows, next_cursor, reason = await _page("ocpp_messages", query, "timestamp", limit, cursor)
        if rows is None:
            return MessagePage(available=False, reason=reason, next_cursor=None, items=[])
        items = [
            MessageRecord(
                org=row.get("org_name", ""),
                charger_id=row.get("charger_id", ""),
                direction=row.get("direction", "in"),
                type=row.get("type", "call"),
                action=row.get("action"),
                message_id=row.get("message_id", ""),
                payload=row.get("payload"),
                error=MessageError(**row["error"]) if row.get("error") else None,
                truncated=bool(row.get("truncated", False)),
                size=row.get("size"),
                timestamp=_utc(row.get("timestamp")),
            )
            for row in rows
        ]
        return MessagePage(available=True, reason=None, next_cursor=next_cursor, items=items)

    return router
