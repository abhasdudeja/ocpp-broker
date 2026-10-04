# OCPP 1.6 Support

This page lists which OCPP 1.6J messages the broker handles, in which direction, and how to use them. Only OCPP 1.6 over JSON (`ocpp1.6`) is implemented.

The OCPP protocol handling is done by the upstream [`ocpp`](https://github.com/mobilityhouse/ocpp) library (v16 `ChargePoint`): it parses frames, validates every request and response against the OCPP 1.6 JSON schemas and builds CALLERRORs. The broker supplies the business logic on top.

## Who sends what

OCPP has two directions, and the broker supports each differently:

| Direction | Broker mode (`connect_to_backend: false`) | Relay mode (`connect_to_backend: true`) |
|-----------|-------------------------------------------|------------------------------------------|
| **Charger to central system** (requests the charger initiates) | The broker answers all ten of them itself | Forwarded to the leader backend unchanged; followers get a copy. `MeterValues` and `StopTransaction` quote a transaction id, which each follower receives as its own (see [Transaction ids](leader-follower.md#transaction-ids)) |
| **Central system to charger** (commands) | Sent through the broker's REST API; the broker returns the charger's reply | The backend sends them over its own link and they reach the charger; you can also send them through the REST API |

## Charger-initiated messages (broker mode)

These are every message an OCPP 1.6 charger can initiate, and the broker answers all of them:

| Message | Profile | Handling |
|---------|---------|----------|
| `BootNotification` | Core | Always `Accepted`; `interval` from `ocpp.commands.core.heartbeat_interval` (default 300) |
| `Heartbeat` | Core | Returns the current time |
| `StatusNotification` | Core | Logged and stored |
| `MeterValues` | Core | Stored |
| `Authorize` | Core | Answered from the organization's [tags](tag-management.md) |
| `StartTransaction` | Core | Authorizes the tag, returns a `transactionId` |
| `StopTransaction` | Core | Updates the stored transaction |
| `DataTransfer` | Core | Applies the `data_transfer` rules ([Configuration](configuration.md#datatransfer)) |
| `DiagnosticsStatusNotification` | Firmware Management | Acknowledged and stored |
| `FirmwareStatusNotification` | Firmware Management | Acknowledged and stored |

Details of each reply, with real exchanges, are in [Broker-as-Backend](broker_as_backend.md). Any other action a charger sends gets a CALLERROR: `NotImplemented` for a known OCPP 1.6 action, `NotSupported` for an unknown one, and `ProtocolError` (with a `cause`) when the payload breaks the schema.

## Central-system commands

Every OCPP 1.6 action a central system may send can be sent through the REST API (`POST /api/ocpp/organizations/{org}/chargers/{charger}/commands`). The broker supplies a typed route for each:

| Profile | Actions with a typed route (`.../commands/{Action}`) |
|---------|-------------------------------------------------------|
| Core | `ChangeAvailability`, `ChangeConfiguration`, `ClearCache`, `DataTransfer`, `GetConfiguration`, `RemoteStartTransaction`, `RemoteStopTransaction`, `Reset`, `UnlockConnector` |
| Firmware Management | `GetDiagnostics`, `UpdateFirmware` |
| Local Authorization List | `GetLocalListVersion`, `SendLocalList` |
| Reservation | `ReserveNow`, `CancelReservation` |
| Smart Charging | `SetChargingProfile`, `ClearChargingProfile`, `GetCompositeSchedule` |
| Remote Trigger | `TriggerMessage` |

The generic `/commands` route takes `{"action": ..., "payload": {...}, "timeout": ...}` with the OCPP camelCase payload; the typed routes take snake_case fields. Body fields are listed in the [API Reference](api-reference.md#typed-commands).

The call waits for the charger's answer and returns it in `response` (a CALLRESULT) or `error` (a CALLERROR), or HTTP `504` on timeout. In broker mode the request is validated against the schema first and a bad one is `422`; in relay mode it is sent as given.

### Examples

All are valid OCPP 1.6 payloads (checked against the schema). They use the generic route so the payload is the OCPP payload:

```bash
export BROKER=http://localhost:8765
send() {  # usage: send CHARGER ACTION 'PAYLOAD_JSON'
  curl -s -X POST "$BROKER/api/ocpp/organizations/MyOrg/chargers/$1/commands" \
    -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
    -d "{\"action\": \"$2\", \"payload\": $3, \"timeout\": 30}"
}

# Start charging remotely
send CP001 RemoteStartTransaction '{"idTag": "ADMIN001", "connectorId": 1}'

# Limit a connector to 16 A
send CP001 SetChargingProfile '{"connectorId": 1, "csChargingProfiles": {
  "chargingProfileId": 1, "stackLevel": 0, "chargingProfilePurpose": "TxDefaultProfile",
  "chargingProfileKind": "Absolute",
  "chargingSchedule": {"chargingRateUnit": "A", "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16}]}}}'

# Push a local authorization list
send CP001 SendLocalList '{"listVersion": 1, "updateType": "Full", "localAuthorizationList": [
  {"idTag": "ADMIN001", "idTagInfo": {"status": "Accepted", "expiryDate": "2030-12-31T23:59:59Z"}}]}'

# Reserve a connector
send CP001 ReserveNow '{"connectorId": 1, "expiryDate": "2030-01-01T18:00:00Z", "idTag": "ADMIN001", "reservationId": 7}'

# Ask the charger to resend its status
send CP001 TriggerMessage '{"requestedMessage": "StatusNotification", "connectorId": 1}'

# Firmware and diagnostics
send CP001 UpdateFirmware '{"location": "https://example.com/fw.bin", "retrieveDate": "2030-01-01T12:00:00Z", "retryInterval": 300}'
send CP001 GetDiagnostics '{"location": "ftp://example.com/diag", "startTime": "2026-01-01T00:00:00Z", "stopTime": "2026-01-02T00:00:00Z"}'
```

## What the broker does not do

The broker moves and stores OCPP data; it does not implement charging policy:

- **Smart charging:** profiles are only forwarded to chargers. The broker computes no schedules and keeps no profile state.
- **Reservations:** `ReserveNow` / `CancelReservation` are forwarded. The broker does not track or enforce reservations (a `StartTransaction` against a reserved connector is not checked).
- **Local authorization list:** the list you send is the list you build. It is not generated from the tag list automatically, and the broker does not track which version a charger holds.
- **Firmware:** the broker does not host firmware or diagnostics files; `location` is whatever URL you supply.
- **Transactions:** the broker issues ids and stores start/stop records. It does not keep a table of open transactions, enforce one transaction per connector, or compute energy.
- **Other OCPP versions:** the broker answers OCPP 1.6 chargers only. OCPP 2.0.1 and 2.1 chargers are **relayed** to backends that speak the same version ([OCPP 2.0.1 and 2.1 in relay mode](leader-follower.md#ocpp-201-and-21-in-relay-mode)); the broker does not answer them itself and does not translate between versions.

## Error responses a charger can receive

| Code | When |
|------|------|
| `NotImplemented` | The action is valid OCPP 1.6 but not something a central system receives (for example `ReserveNow`) |
| `NotSupported` | The action is not an OCPP 1.6 action |
| `ProtocolError` | The payload is missing a required property (the `cause` names it) |
| `InternalError` | Relay mode: the backend could not take the request (`Backend unavailable, please retry`); or an unexpected error in a handler |

```
[4,"17","ProtocolError","Payload for Action is incomplete",{"cause":"'chargePointModel' is a required property"}]
[4,"18","NotImplemented","Request Action is recognized but not supported by the receiver",{"cause":"No handler for ReserveNow registered."}]
```

## Related documentation

- [Broker-as-Backend Mode](broker_as_backend.md)
- [API Reference](api-reference.md)
- [Tag Management](tag-management.md)
- [Configuration Guide](configuration.md)
