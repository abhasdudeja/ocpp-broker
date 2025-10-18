# System Architecture

Comprehensive overview of the OCPP broker architecture, components, and design principles.

## 🏗️ High-Level Architecture

### **System Overview**

```
┌─────────────────────────────────────────────────────────────┐
│                    OCPP Broker System                       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Chargers      │  │   Broker        │  │   Backends   │ │
│  │   (OCPP 1.6)    │◄─┤   (FastAPI)     │─►│   (OCPP)    │ │
│  │                 │  │                 │  │             │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   WebSocket     │  │   Command       │  │   Backend    │ │
│  │   Connections   │  │   Router        │  │   Manager    │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Core Components**

1. **WebSocket Server** - Handles charger and backend connections
2. **Command Router** - Routes OCPP messages to appropriate handlers
3. **Backend Manager** - Manages backend connections and leader-follower logic
4. **Message Validator** - Validates OCPP messages against schemas
5. **Command Handlers** - Process specific OCPP commands

## 🔧 Component Architecture

### **1. WebSocket Server Layer**

```
┌─────────────────────────────────────────────────────────────┐
│                    WebSocket Server                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   FastAPI       │  │   WebSocket      │  │   CORS       │ │
│  │   Application   │  │   Endpoints      │  │   Middleware │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Charger       │  │   Backend        │  │   Health      │ │
│  │   Endpoints     │  │   Endpoints      │  │   Endpoints  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Responsibilities:**
- Accept WebSocket connections from chargers
- Accept WebSocket connections from backends
- Handle HTTP health checks
- Manage CORS and security

### **2. Command Router Layer**

```
┌─────────────────────────────────────────────────────────────┐
│                    Command Router                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Legacy        │  │   OCPP 1.6      │  │   Message    │ │
│  │   Router        │  │   Router        │  │   Validator  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Message       │  │   Command       │  │   Response   │ │
│  │   Parser        │  │   Dispatcher    │  │   Generator  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Responsibilities:**
- Parse incoming OCPP messages
- Route messages to appropriate handlers
- Validate message format and content
- Generate responses
- Handle errors and exceptions

### **3. Command Handler Layer**

```
┌─────────────────────────────────────────────────────────────┐
│                    Command Handlers                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Core Profile  │  │ Smart Charging │  │   Firmware   │ │
│  │   (19 commands) │  │   (4 commands) │  │   (2 commands)│ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ Local Auth List │  │   Reservation   │                   │
│  │   (2 commands)  │  │   (2 commands) │                   │
│  └─────────────────┘  └─────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

**Responsibilities:**
- Process specific OCPP commands
- Generate appropriate responses
- Handle command-specific logic
- Manage state and data

### **4. Backend Manager Layer**

```
┌─────────────────────────────────────────────────────────────┐
│                    Backend Manager                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Connection    │  │   Leader-        │  │   Message    │ │
│  │   Manager       │  │   Follower      │  │   Relay      │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   WebSocket     │  │   Command       │  │   Error      │ │
│  │   Pool          │  │   Filtering     │  │   Handling   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Responsibilities:**
- Manage backend connections
- Implement leader-follower logic
- Relay messages between chargers and backends
- Handle connection failures
- Manage connection pools

## 🔄 Message Flow Architecture

### **1. Charger to Backend Flow**

```
Charger → Broker → Backend
   │         │        │
   ▼         ▼        ▼
┌─────────┐ ┌──────┐ ┌─────────┐
│ OCPP    │ │ Parse│ │ OCPP    │
│ Message │ │ Route│ │ Message │
└─────────┘ └──────┘ └─────────┘
```

**Steps:**
1. Charger sends OCPP message to broker
2. Broker parses and validates message
3. Broker routes message to appropriate backend
4. Backend processes message and sends response
5. Broker relays response back to charger

### **2. Backend to Charger Flow**

```
Backend → Broker → Charger
   │         │        │
   ▼         ▼        ▼
┌─────────┐ ┌──────┐ ┌─────────┐
│ OCPP    │ │ Parse│ │ OCPP    │
│ Message │ │ Route│ │ Message │
└─────────┘ └──────┘ └─────────┘
```

**Steps:**
1. Backend sends OCPP message to broker
2. Broker validates leader-follower permissions
3. Broker parses and routes message
4. Broker sends message to target charger
5. Charger processes message and sends response
6. Broker relays response back to backend

### **3. Broker-as-Backend Flow**

```
Charger → Broker (acting as backend)
   │         │
   ▼         ▼
┌─────────┐ ┌──────────────┐
│ OCPP    │ │ Local        │
│ Message │ │ Processing   │
└─────────┘ └──────────────┘
```

**Steps:**
1. Charger sends OCPP message to broker
2. Broker processes message locally
3. Broker generates appropriate response
4. Broker sends response back to charger

## 🏛️ Data Architecture

### **Configuration Data**

```yaml
# config.yaml structure
broker:
  host: string
  port: integer
  log_level: string

organizations:
  - name: string
    connect_to_backend: boolean
    backends:
      - id: string
        url: string
        leader: boolean
        chargers: [string]
    chargers: [string]
```

### **Runtime Data**

```python
# Broker runtime state
class OcppBroker:
    org_backends: Dict[str, Dict[str, BackendConnection]]
    active_chargers: Dict[str, WebSocket]
    org_registries: Dict[str, ChargerRegistry]
    config_data: Dict[str, Any]
```

### **Message Data**

```python
# OCPP message structure
class OCPPMessage:
    message_type: int  # 2=Call, 3=CallResult, 4=CallError
    message_id: str
    action: str
    payload: Dict[str, Any]
```

## 🔐 Security Architecture

### **Authentication & Authorization**

```
┌─────────────────────────────────────────────────────────────┐
│                    Security Layer                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   TLS/SSL      │  │   WebSocket     │  │   API       │ │
│  │   Encryption   │  │   Security      │  │   Security  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Certificate   │  │   Subprotocol    │  │   CORS       │ │
│  │   Validation    │  │   Validation     │  │   Headers    │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Leader-Follower Security**

```
┌─────────────────────────────────────────────────────────────┐
│                Leader-Follower Logic                       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Leader        │  │   Follower      │  │   Command    │ │
│  │   Backend       │  │   Backend       │  │   Filtering  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Commands      │  │   Status        │  │   Error      │ │
│  │   Allowed       │  │   Updates       │  │   Handling   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 📊 Monitoring Architecture

### **Logging System**

```
┌─────────────────────────────────────────────────────────────┐
│                    Logging System                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Application   │  │   Access        │  │   Error       │ │
│  │   Logs          │  │   Logs          │  │   Logs        │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Log           │  │   Log           │  │   Log        │ │
│  │   Aggregation   │  │   Rotation      │  │   Analysis   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Metrics System**

```
┌─────────────────────────────────────────────────────────────┐
│                    Metrics System                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Connection    │  │   Message       │  │   Performance│ │
│  │   Metrics       │  │   Metrics       │  │   Metrics    │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Prometheus    │  │   Grafana       │  │   Alerting  │ │
│  │   Endpoint      │  │   Dashboard      │  │   System     │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 🚀 Deployment Architecture

### **Single Instance Deployment**

```
┌─────────────────────────────────────────────────────────────┐
│                    Single Instance                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Load          │  │   OCPP          │  │   Database  │ │
│  │   Balancer      │  │   Broker        │  │   (Optional)│ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **High Availability Deployment**

```
┌─────────────────────────────────────────────────────────────┐
│                High Availability Cluster                    │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Load          │  │   OCPP          │  │   OCPP       │ │
│  │   Balancer      │  │   Broker 1      │  │   Broker 2   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Database      │  │   Redis         │  │   Monitoring │ │
│  │   Cluster       │  │   Cluster      │  │   System     │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Microservices Deployment**

```
┌─────────────────────────────────────────────────────────────┐
│                Microservices Architecture                   │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   API           │  │   WebSocket     │  │   Command    │ │
│  │   Gateway       │  │   Service       │  │   Service    │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Backend       │  │   Message      │  │   Monitoring │ │
│  │   Service       │  │   Service      │  │   Service    │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 Configuration Architecture

### **Configuration Hierarchy**

```
┌─────────────────────────────────────────────────────────────┐
│                Configuration Sources                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Command       │  │   Environment  │  │   File       │ │
│  │   Line Args     │  │   Variables    │  │   Config     │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Priority      │  │   Validation    │  │   Defaults   │ │
│  │   Resolution    │  │   & Schema      │  │   Fallback   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Configuration Flow**

1. **Command Line Arguments** (Highest Priority)
2. **Environment Variables**
3. **Configuration File**
4. **Default Values** (Lowest Priority)

## 📚 API Architecture

### **REST API Structure**

```
┌─────────────────────────────────────────────────────────────┐
│                    REST API Layer                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Health        │  │   Organizations │  │   Chargers   │ │
│  │   Endpoints     │  │   Endpoints     │  │   Endpoints  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Backends      │  │   Metrics       │  │   Logs       │ │
│  │   Endpoints     │  │   Endpoints     │  │   Endpoints  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **WebSocket API Structure**

```
┌─────────────────────────────────────────────────────────────┐
│                  WebSocket API Layer                       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Charger       │  │   Backend       │  │   Protocol   │ │
│  │   Connections   │  │   Connections   │  │   Validation │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   OCPP 1.6      │  │   Message       │  │   Error      │ │
│  │   Protocol      │  │   Routing       │  │   Handling   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 🔗 Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [OCPP 1.6 Features](ocpp16-features.md)
- [Broker-as-Backend Mode](broker-as-backend.md)
- [API Reference](api-reference.md)
- [Production Deployment](deployment.md)

---

*Last updated: October 2024*
