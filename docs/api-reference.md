# API Reference

Complete reference for the OCPP broker REST and WebSocket APIs.

## 🌐 REST API

### **Base URL**
```
http://localhost:8765
```

### **Authentication**
Currently, the API does not require authentication. In production deployments, consider implementing API keys or OAuth2.

## 📊 Health Endpoints

### **Health Check**

```http
GET /health
```

**Response:**
```json
{
  "status": "ok"
}
```

**Status Codes:**
- `200 OK` - Service is healthy
- `503 Service Unavailable` - Service is unhealthy

### **Health Check (HEAD)**

```http
HEAD /health
```

**Response:**
- `200 OK` - Service is healthy
- `503 Service Unavailable` - Service is unhealthy

## 🏢 Organization Endpoints

### **List Organizations**

```http
GET /api/organizations
```

**Response:**
```json
[
  {
    "name": "MyChargingStation",
    "connect_to_backend": false,
    "chargers": ["CHARGER_001", "CHARGER_002"],
    "backends": []
  },
  {
    "name": "ProductionCharging",
    "connect_to_backend": true,
    "chargers": [],
    "backends": [
      {
        "id": "production_backend",
        "url": "ws://your-backend.com/ocpp",
        "leader": true,
        "chargers": ["PROD_001", "PROD_002"]
      }
    ]
  }
]
```

### **Get Organization**

```http
GET /api/organizations/{org_name}
```

**Parameters:**
- `org_name` (string) - Name of the organization

**Response:**
```json
{
  "name": "MyChargingStation",
  "connect_to_backend": false,
  "chargers": ["CHARGER_001", "CHARGER_002"],
  "backends": []
}
```

**Status Codes:**
- `200 OK` - Organization found
- `404 Not Found` - Organization not found

## 🔌 Charger Endpoints

### **List Chargers**

```http
GET /api/chargers
```

**Response:**
```json
[
  {
    "id": "CHARGER_001",
    "organization": "MyChargingStation",
    "status": "connected",
    "last_seen": "2024-10-18T20:46:02.911118+00:00"
  },
  {
    "id": "CHARGER_002",
    "organization": "MyChargingStation",
    "status": "disconnected",
    "last_seen": "2024-10-18T20:45:02.911118+00:00"
  }
]
```

### **Get Charger**

```http
GET /api/chargers/{charger_id}
```

**Parameters:**
- `charger_id` (string) - ID of the charger

**Response:**
```json
{
  "id": "CHARGER_001",
  "organization": "MyChargingStation",
  "status": "connected",
  "last_seen": "2024-10-18T20:46:02.911118+00:00",
  "backend_connection": {
    "connected": false,
    "backend_id": null
  }
}
```

**Status Codes:**
- `200 OK` - Charger found
- `404 Not Found` - Charger not found

### **Charger Status**

```http
GET /api/chargers/{charger_id}/status
```

**Response:**
```json
{
  "id": "CHARGER_001",
  "status": "connected",
  "last_seen": "2024-10-18T20:46:02.911118+00:00",
  "message_count": 42,
  "last_message": {
    "type": "Heartbeat",
    "timestamp": "2024-10-18T20:46:02.911118+00:00"
  }
}
```

## 🔗 Backend Endpoints

### **List Backends**

```http
GET /api/backends
```

**Response:**
```json
[
  {
    "id": "production_backend",
    "organization": "ProductionCharging",
    "url": "ws://your-backend.com/ocpp",
    "leader": true,
    "status": "connected",
    "chargers": ["PROD_001", "PROD_002"]
  }
]
```

### **Get Backend**

```http
GET /api/backends/{backend_id}
```

**Parameters:**
- `backend_id` (string) - ID of the backend

**Response:**
```json
{
  "id": "production_backend",
  "organization": "ProductionCharging",
  "url": "ws://your-backend.com/ocpp",
  "leader": true,
  "status": "connected",
  "chargers": ["PROD_001", "PROD_002"],
  "connection_info": {
    "connected_at": "2024-10-18T20:46:02.911118+00:00",
    "last_ping": "2024-10-18T20:46:02.911118+00:00",
    "message_count": 156
  }
}
```

### **Backend Status**

```http
GET /api/backends/{backend_id}/status
```

**Response:**
```json
{
  "id": "production_backend",
  "status": "connected",
  "last_ping": "2024-10-18T20:46:02.911118+00:00",
  "message_count": 156,
  "error_count": 0,
  "uptime": "2h 15m 30s"
}
```

## 📊 Metrics Endpoints

### **System Metrics**

```http
GET /api/metrics
```

**Response:**
```json
{
  "system": {
    "uptime": "2h 15m 30s",
    "memory_usage": "45.2MB",
    "cpu_usage": "12.5%"
  },
  "connections": {
    "total_chargers": 5,
    "connected_chargers": 4,
    "total_backends": 2,
    "connected_backends": 2
  },
  "messages": {
    "total_processed": 1250,
    "successful": 1245,
    "failed": 5,
    "rate_per_minute": 12.5
  }
}
```

### **Organization Metrics**

```http
GET /api/organizations/{org_name}/metrics
```

**Response:**
```json
{
  "organization": "MyChargingStation",
  "chargers": {
    "total": 2,
    "connected": 2,
    "disconnected": 0
  },
  "messages": {
    "total_processed": 450,
    "successful": 448,
    "failed": 2
  },
  "backends": {
    "total": 0,
    "connected": 0
  }
}
```

## 📝 Log Endpoints

### **System Logs**

```http
GET /api/logs
```

**Query Parameters:**
- `level` (string, optional) - Log level filter (DEBUG, INFO, WARNING, ERROR)
- `limit` (integer, optional) - Number of log entries to return (default: 100)
- `offset` (integer, optional) - Number of log entries to skip (default: 0)

**Response:**
```json
{
  "logs": [
    {
      "timestamp": "2024-10-18T20:46:02.911118+00:00",
      "level": "INFO",
      "message": "Charger CHARGER_001 connected",
      "source": "ocpp_broker.broker"
    },
    {
      "timestamp": "2024-10-18T20:45:02.911118+00:00",
      "level": "DEBUG",
      "message": "Processing BootNotification from CHARGER_001",
      "source": "ocpp_broker.command_router"
    }
  ],
  "total": 1250,
  "limit": 100,
  "offset": 0
}
```

### **Charger Logs**

```http
GET /api/chargers/{charger_id}/logs
```

**Response:**
```json
{
  "charger_id": "CHARGER_001",
  "logs": [
    {
      "timestamp": "2024-10-18T20:46:02.911118+00:00",
      "level": "INFO",
      "message": "BootNotification received",
      "source": "ocpp_broker.broker"
    }
  ],
  "total": 25
}
```

## 🔌 WebSocket API

### **Charger Connections**

**Endpoint:**
```
ws://localhost:8765/{org_name}/{charger_id}
```

**Subprotocol:**
```
ocpp1.6
```

**Example:**
```javascript
const ws = new WebSocket('ws://localhost:8765/MyChargingStation/CHARGER_001', 'ocpp1.6');
```

### **Backend Connections**

**Endpoint:**
```
ws://localhost:8765/backend/{org_name}/{backend_id}
```

**Subprotocol:**
```
ocpp1.6
```

**Example:**
```javascript
const ws = new WebSocket('ws://localhost:8765/backend/ProductionCharging/production_backend', 'ocpp1.6');
```

## 📨 OCPP Message Format

### **Call Message (Type 2)**

```json
[2, "unique-message-id", "ActionName", {
  "parameter1": "value1",
  "parameter2": "value2"
}]
```

**Example:**
```json
[2, "12345", "BootNotification", {
  "chargePointVendor": "Siemens",
  "chargePointModel": "SL/01"
}]
```

### **Call Result Message (Type 3)**

```json
[3, "unique-message-id", {
  "parameter1": "value1",
  "parameter2": "value2"
}]
```

**Example:**
```json
[3, "12345", {
  "currentTime": "2024-10-18T20:46:02.911118+00:00",
  "interval": 300,
  "status": "Accepted"
}]
```

### **Call Error Message (Type 4)**

```json
[4, "unique-message-id", "ErrorCode", "ErrorDescription", {
  "errorDetails": "value"
}]
```

**Example:**
```json
[4, "12345", "NotImplemented", "Action not supported", {}]
```

## 🔧 Configuration API

### **Get Configuration**

```http
GET /api/config
```

**Response:**
```json
{
  "broker": {
    "host": "0.0.0.0",
    "port": 8765,
    "log_level": "INFO"
  },
  "organizations": [
    {
      "name": "MyChargingStation",
      "connect_to_backend": false,
      "chargers": ["CHARGER_001", "CHARGER_002"]
    }
  ]
}
```

### **Reload Configuration**

```http
POST /api/config/reload
```

**Response:**
```json
{
  "status": "success",
  "message": "Configuration reloaded successfully"
}
```

**Status Codes:**
- `200 OK` - Configuration reloaded successfully
- `400 Bad Request` - Configuration validation failed
- `500 Internal Server Error` - Configuration reload failed

## 🚨 Error Responses

### **Standard Error Format**

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {
      "field": "Additional error details"
    }
  }
}
```

### **Common Error Codes**

| Code | Description | HTTP Status |
|------|-------------|-------------|
| `INVALID_REQUEST` | Invalid request format | 400 |
| `NOT_FOUND` | Resource not found | 404 |
| `VALIDATION_ERROR` | Request validation failed | 400 |
| `INTERNAL_ERROR` | Internal server error | 500 |
| `SERVICE_UNAVAILABLE` | Service temporarily unavailable | 503 |

### **Error Examples**

**404 Not Found:**
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Charger CHARGER_001 not found",
    "details": {
      "charger_id": "CHARGER_001"
    }
  }
}
```

**400 Bad Request:**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request parameters",
    "details": {
      "field": "organization",
      "message": "Organization name is required"
    }
  }
}
```

## 📊 Rate Limiting

### **Rate Limits**

- **API Requests**: 1000 requests per minute per IP
- **WebSocket Connections**: 100 connections per IP
- **Message Processing**: 100 messages per second per connection

### **Rate Limit Headers**

```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1640995200
```

### **Rate Limit Exceeded**

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json

{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded",
    "details": {
      "limit": 1000,
      "remaining": 0,
      "reset_time": "2024-10-18T21:00:00Z"
    }
  }
}
```

## 🔗 Related Documentation

- [Quick Start Guide](quick-start.md)
- [Configuration Guide](configuration.md)
- [OCPP 1.6 Features](ocpp16-features.md)
- [Broker-as-Backend Mode](broker-as-backend.md)
- [Production Deployment](deployment.md)
- [Troubleshooting](troubleshooting.md)

---

*Last updated: October 2024*
