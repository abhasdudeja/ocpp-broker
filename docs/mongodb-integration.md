# MongoDB Integration for OCPP Broker

This document describes the MongoDB integration for saving OCPP messages, statuses, metervalues, configurations, and other data when the broker is acting as the leader (backend).

## Overview

When the broker is configured to act as the backend (i.e., `connect_to_backend: false` in organization config), all OCPP messages from chargers are automatically saved to MongoDB. The data is organized by organization and charger in separate collections.

## Configuration

You can configure MongoDB either via `config.yaml` or using a `.env` file. Environment variables take precedence over YAML configuration.

### Option 1: Using config.yaml

Add the following to your `config.yaml`:

```yaml
mongodb:
  enabled: true  # Set to false to disable MongoDB integration
  connection_string: "mongodb://localhost:27017"  # MongoDB connection string
  database_name: "ocpp_broker"  # Database name to use
```

### Option 2: Using .env file

Create a `.env` file in your project root:

```env
# Enable MongoDB integration
MONGODB_ENABLED=true

# MongoDB connection string
MONGODB_CONNECTION_STRING=mongodb://localhost:27017

# MongoDB database name
MONGODB_DATABASE_NAME=ocpp_broker
```

### Environment Variables

The following environment variables are supported:

- `MONGODB_ENABLED` - Enable/disable MongoDB (true/false/1/0/yes/no/on/off)
- `MONGODB_CONNECTION_STRING` - MongoDB connection string
- `MONGODB_DATABASE_NAME` - Database name (default: "ocpp_broker")

### Connection String Examples

```env
# Local MongoDB
MONGODB_CONNECTION_STRING=mongodb://localhost:27017

# With authentication
MONGODB_CONNECTION_STRING=mongodb://username:password@localhost:27017

# Replica set
MONGODB_CONNECTION_STRING=mongodb://host1:27017,host2:27017,host3:27017/?replicaSet=rs0

# MongoDB Atlas
MONGODB_CONNECTION_STRING=mongodb+srv://username:password@cluster.mongodb.net/

# With database name in connection string
MONGODB_CONNECTION_STRING=mongodb://localhost:27017/ocpp_broker
```

## Collections

Each OCPP 1.6 message type is stored in its own collection. The following MongoDB collections are created:

### From Charge Point to Central System (Requests)

1. **authorize_messages** - Authorize messages
2. **boot_notifications** - BootNotification messages
3. **data_transfers** - DataTransfer messages
4. **diagnostics_status_notifications** - DiagnosticsStatusNotification messages
5. **firmware_status_notifications** - FirmwareStatusNotification messages
6. **charger_heartbeats_latest** - Latest heartbeat timestamp for each charger (updated on each heartbeat, no individual messages stored)
7. **meter_values** - MeterValues messages
8. **start_transactions** - StartTransaction messages
9. **status_notifications** - StatusNotification messages
10. **stop_transactions** - StopTransaction messages

### From Central System to Charge Point (Commands)

11. **cancel_reservations** - CancelReservation commands
12. **change_availabilities** - ChangeAvailability commands
13. **change_configurations** - ChangeConfiguration commands
14. **clear_caches** - ClearCache commands
15. **clear_charging_profiles** - ClearChargingProfile commands
16. **get_composite_schedules** - GetCompositeSchedule commands
17. **get_configurations** - GetConfiguration commands
18. **get_diagnostics** - GetDiagnostics commands
19. **get_local_list_versions** - GetLocalListVersion commands
20. **remote_start_transactions** - RemoteStartTransaction commands
21. **remote_stop_transactions** - RemoteStopTransaction commands
22. **reserve_nows** - ReserveNow commands
23. **resets** - Reset commands
24. **send_local_lists** - SendLocalList commands
25. **set_charging_profiles** - SetChargingProfile commands
26. **trigger_messages** - TriggerMessage commands
27. **unlock_connectors** - UnlockConnector commands
28. **update_firmwares** - UpdateFirmware commands

### Additional Collections (Detailed Records)

29. **charger_statuses** - StatusNotification messages (detailed records)
30. **charger_statuses_latest** - Latest status for each connector (updated on each status change)
31. **charger_heartbeats_latest** - Latest heartbeat timestamp for each charger (indicates last availability)
32. **transactions** - Transaction records (for compatibility)
33. **authorizations** - Authorize command data (detailed records)
34. **charger_configurations** - BootNotification data (charger configuration)

### Heartbeat Collection Structure

The `charger_heartbeats_latest` collection stores only the latest timestamp for each charger:

```json
{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "last_heartbeat": "2024-01-01T12:00:00Z",
  "updated_at": "2024-01-01T12:00:00Z"
}
```

This allows you to quickly check when each charger was last available without storing individual heartbeat messages.

**Note:** All OCPP 1.6 messages (calls, call_results, and call_errors) are saved to their respective collections based on the action name.

## Automatic Saving

When the broker is the leader (acting as backend), **ALL OCPP 1.6 messages** are automatically saved:

### From Charge Point to Central System (Requests)
- **Authorize** - Saved to `authorize_messages` and `authorizations`
- **BootNotification** - Saved to `boot_notifications` and `charger_configurations`
- **DataTransfer** - Saved to `data_transfers`
- **DiagnosticsStatusNotification** - Saved to `diagnostics_status_notifications`
- **FirmwareStatusNotification** - Saved to `firmware_status_notifications`
- **Heartbeat** - Updates `charger_heartbeats_latest` with latest timestamp (individual messages not stored)
- **MeterValues** - Saved to `meter_values`
- **StartTransaction** - Saved to `start_transactions` and `transactions`
- **StatusNotification** - Saved to `status_notifications`, `charger_statuses`, and `charger_statuses_latest`
- **StopTransaction** - Saved to `stop_transactions` and `transactions`

### From Central System to Charge Point (Commands)
All commands sent from the broker to chargers are saved:
- **CancelReservation** - Saved to `cancel_reservations`
- **ChangeAvailability** - Saved to `change_availabilities`
- **ChangeConfiguration** - Saved to `change_configurations`
- **ClearCache** - Saved to `clear_caches`
- **ClearChargingProfile** - Saved to `clear_charging_profiles`
- **GetCompositeSchedule** - Saved to `get_composite_schedules`
- **GetConfiguration** - Saved to `get_configurations`
- **GetDiagnostics** - Saved to `get_diagnostics`
- **GetLocalListVersion** - Saved to `get_local_list_versions`
- **RemoteStartTransaction** - Saved to `remote_start_transactions`
- **RemoteStopTransaction** - Saved to `remote_stop_transactions`
- **ReserveNow** - Saved to `reserve_nows`
- **Reset** - Saved to `resets`
- **SendLocalList** - Saved to `send_local_lists`
- **SetChargingProfile** - Saved to `set_charging_profiles`
- **TriggerMessage** - Saved to `trigger_messages`
- **UnlockConnector** - Saved to `unlock_connectors`
- **UpdateFirmware** - Saved to `update_firmwares`

### Call Results and Errors
- **CallResult** (Type 3) - Saved to action-specific collections with message_type="call_result"
- **CallError** (Type 4) - Saved to action-specific collections with message_type="call_error"

Each message type has its own dedicated collection for better organization and querying. This covers the complete OCPP 1.6 specification.

## REST APIs

The following REST APIs are available for manually saving OCPP data to MongoDB:

### Base URL
All APIs are available at `/api/mongodb/`

### Endpoints

#### 1. Save StatusNotification
```http
POST /api/mongodb/status-notification
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "connector_id": 1,
  "status": "Available",
  "error_code": null,
  "info": null,
  "vendor_id": null,
  "vendor_error_code": null
}
```

#### 2. Save MeterValues
```http
POST /api/mongodb/meter-values
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "connector_id": 1,
  "transaction_id": 12345,
  "meter_value": [
    {
      "timestamp": "2024-01-01T12:00:00Z",
      "sampledValue": [
        {
          "value": "1000",
          "context": "Sample.Periodic",
          "format": "Raw",
          "measurand": "Energy.Active.Import.Register",
          "location": "Outlet",
          "unit": "Wh"
        }
      ]
    }
  ]
}
```

#### 3. Save BootNotification
```http
POST /api/mongodb/boot-notification
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "charge_point_model": "Model X",
  "charge_point_vendor": "Vendor Y",
  "firmware_version": "1.0.0",
  "iccid": "123456789",
  "imsi": "987654321",
  "meter_type": "Type A",
  "meter_serial_number": "SN123456"
}
```

#### 4. Save Transaction
```http
POST /api/mongodb/transaction
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "transaction_id": 12345,
  "connector_id": 1,
  "id_tag": "USER123",
  "meter_start": 1000,
  "reservation_id": null,
  "transaction_type": "start"
}
```

#### 5. Save Authorization
```http
POST /api/mongodb/authorization
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "id_tag": "USER123",
  "status": "Accepted",
  "expiry_date": "2024-12-31T23:59:59Z",
  "parent_id_tag": null
}
```

#### 6. Save DataTransfer
```http
POST /api/mongodb/data-transfer
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "vendor_id": "VendorA",
  "message_id": "MSG001",
  "data": "{\"key\": \"value\"}",
  "status": "Accepted"
}
```

#### 7. Save Generic OCPP Message
```http
POST /api/mongodb/ocpp-message
Content-Type: application/json

{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "message_type": "call",
  "action": "Heartbeat",
  "payload": {},
  "direction": "charger_to_broker",
  "message_id": "msg123"
}
```

#### 8. Check MongoDB Health
```http
GET /api/mongodb/health
```

Returns:
```json
{
  "status": "connected",
  "connected": true,
  "database": "ocpp_broker"
}
```

## Data Structure

All documents include:
- `org_name` - Organization name
- `charger_id` - Charger ID
- `timestamp` - Timestamp (UTC)

### StatusNotification Document
```json
{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "connector_id": 1,
  "status": "Available",
  "error_code": null,
  "info": null,
  "timestamp": "2024-01-01T12:00:00Z",
  "vendor_id": null,
  "vendor_error_code": null
}
```

### MeterValues Document
```json
{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "connector_id": 1,
  "transaction_id": 12345,
  "meter_value": [...],
  "timestamp": "2024-01-01T12:00:00Z"
}
```

### Transaction Document
```json
{
  "org_name": "orgA",
  "charger_id": "CHARGER_001",
  "transaction_id": 12345,
  "connector_id": 1,
  "id_tag": "USER123",
  "meter_start": 1000,
  "reservation_id": null,
  "transaction_type": "start",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Usage Examples

### Example: Organization with Broker as Backend

```yaml
organizations:
  - name: "orgB"
    connect_to_backend: false  # Broker acts as backend
    ocpp_subprotocol: "ocpp1.6"
```

When a charger connects to this organization, all OCPP messages will be automatically saved to MongoDB.

### Example: Using REST APIs

```python
import requests

# Save a status notification
response = requests.post(
    "http://localhost:8080/api/mongodb/status-notification",
    json={
        "org_name": "orgA",
        "charger_id": "CHARGER_001",
        "connector_id": 1,
        "status": "Charging"
    }
)
print(response.json())
```

## Error Handling

- If MongoDB is not connected, the APIs will return a 503 status code
- If MongoDB connection fails during initialization, the broker will continue to operate but MongoDB saving will be disabled
- All MongoDB operations are non-blocking - if saving fails, it's logged but doesn't affect OCPP message processing

## Dependencies

The following Python packages are required:
- `motor>=3.3.0` - Async MongoDB driver
- `pymongo>=4.6.0` - MongoDB driver

These are automatically included when installing the broker.

## Notes

- MongoDB saving only occurs when the broker is acting as the leader (backend)
- When `connect_to_backend: true`, messages are relayed to external backends and not saved to MongoDB
- The `charger_statuses_latest` collection is automatically updated with the latest status for each connector
- All timestamps are stored in UTC

