# Configuration Guide

Complete guide to configuring the OCPP broker for various deployment scenarios.

## 📋 Configuration Overview

The OCPP broker uses YAML configuration files to define:
- Broker settings (host, port, logging)
- Organizations and their backends
- Charger assignments
- OCPP 1.6 features
- Leader-follower logic

## 🔧 Basic Configuration

### **Minimal Configuration**

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "DefaultOrg"
    connect_to_backend: false
    chargers:
      - "CHARGER_001"
```

### **Broker Settings**

```yaml
broker:
  host: 0.0.0.0          # Broker host address
  port: 8765             # Broker port
  log_level: "INFO"      # Logging level (DEBUG, INFO, WARNING, ERROR)
  max_connections: 1000  # Maximum concurrent connections
  timeout: 30            # Connection timeout in seconds
```

## 🏢 Organization Configuration

### **Basic Organization**

```yaml
organizations:
  - name: "MyChargingStation"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "CHARGER_001"
      - "CHARGER_002"
```

### **Organization with External Backend**

```yaml
organizations:
  - name: "ProductionCharging"
    connect_to_backend: true
    backends:
      - id: "production_backend"
        url: "ws://your-backend.com/ocpp"
        leader: true
        chargers:
          - "PROD_001"
          - "PROD_002"
```

### **Multiple Backends (Leader-Follower)**

```yaml
organizations:
  - name: "MultiBackendOrg"
    connect_to_backend: true
    backends:
      - id: "leader_backend"
        url: "ws://leader-backend.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_001"
      - id: "follower_backend"
        url: "ws://follower-backend.com/ocpp"
        leader: false
        chargers:
          - "CHARGER_001"
```

## 🎯 Broker-as-Backend Mode

### **Local Processing Configuration**

```yaml
organizations:
  - name: "LocalCharging"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "LOCAL_001"
      - "LOCAL_002"
```

**Benefits:**
- No external backend dependencies
- Local OCPP command processing
- Reduced latency
- Simplified deployment

### **Enhanced OCPP 1.6 Configuration**

```yaml
organizations:
  - name: "AdvancedCharging"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
      - firmware_management
      - local_auth_list
      - reservation
    chargers:
      - "ADVANCED_001"
```

## 🔗 Backend Configuration

### **Single Backend**

```yaml
organizations:
  - name: "SingleBackendOrg"
    connect_to_backend: true
    backends:
      - id: "main_backend"
        url: "ws://backend.example.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
```

### **Multiple Backends**

```yaml
organizations:
  - name: "MultiBackendOrg"
    connect_to_backend: true
    backends:
      - id: "primary_backend"
        url: "ws://primary-backend.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
      - id: "secondary_backend"
        url: "ws://secondary-backend.com/ocpp"
        leader: false
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
```

### **Backend with Authentication**

```yaml
organizations:
  - name: "SecureBackendOrg"
    connect_to_backend: true
    backends:
      - id: "secure_backend"
        url: "ws://secure-backend.com/ocpp"
        leader: true
        auth:
          username: "ocpp_user"
          password: "secure_password"
        chargers:
          - "SECURE_001"
```

## 🚀 OCPP 1.6 Features

### **Core Profile (Default)**

```yaml
organizations:
  - name: "CoreProfileOrg"
    connect_to_backend: false
    ocpp_features:
      - core_profile
    chargers:
      - "CORE_001"
```

**Supported Commands:**
- Authorize, BootNotification, Heartbeat
- StatusNotification, MeterValues
- StartTransaction, StopTransaction
- ChangeAvailability, ChangeConfiguration
- ClearCache, DataTransfer, GetConfiguration
- RemoteStartTransaction, RemoteStopTransaction
- Reset, SendLocalList, SetChargingProfile
- UnlockConnector, UpdateFirmware

### **Smart Charging Profile**

```yaml
organizations:
  - name: "SmartChargingOrg"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
    chargers:
      - "SMART_001"
```

**Additional Commands:**
- ClearChargingProfile
- GetCompositeSchedule
- SetChargingProfile
- TriggerMessage

### **Firmware Management Profile**

```yaml
organizations:
  - name: "FirmwareOrg"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - firmware_management
    chargers:
      - "FIRMWARE_001"
```

**Additional Commands:**
- GetDiagnostics
- UpdateFirmware

### **Local Authorization List Profile**

```yaml
organizations:
  - name: "LocalAuthOrg"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - local_auth_list
    chargers:
      - "LOCAL_AUTH_001"
```

**Additional Commands:**
- GetLocalListVersion
- SendLocalList

### **Reservation Profile**

```yaml
organizations:
  - name: "ReservationOrg"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - reservation
    chargers:
      - "RESERVATION_001"
```

**Additional Commands:**
- CancelReservation
- ReserveNow

### **Complete OCPP 1.6 Configuration**

```yaml
organizations:
  - name: "FullOCPP16Org"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
      - firmware_management
      - local_auth_list
      - reservation
    chargers:
      - "FULL_001"
```

## 🔐 Security Configuration

### **TLS/SSL Configuration**

```yaml
broker:
  host: 0.0.0.0
  port: 8765
  tls:
    enabled: true
    cert_file: "/path/to/cert.pem"
    key_file: "/path/to/key.pem"
    ca_file: "/path/to/ca.pem"
```

### **Authentication**

```yaml
organizations:
  - name: "SecureOrg"
    connect_to_backend: true
    backends:
      - id: "secure_backend"
        url: "wss://secure-backend.com/ocpp"
        leader: true
        auth:
          username: "ocpp_user"
          password: "secure_password"
        chargers:
          - "SECURE_001"
```

## 📊 Monitoring Configuration

### **Logging Configuration**

```yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"
  log_file: "/var/log/ocpp-broker.log"
  log_format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

### **Metrics Configuration**

```yaml
broker:
  host: 0.0.0.0
  port: 8765
  metrics:
    enabled: true
    port: 9090
    path: "/metrics"
```

## 🌐 Network Configuration

### **CORS Configuration**

```yaml
broker:
  host: 0.0.0.0
  port: 8765
  cors:
    enabled: true
    origins: ["*"]
    methods: ["GET", "POST", "PUT", "DELETE"]
    headers: ["*"]
```

### **Proxy Configuration**

```yaml
broker:
  host: 0.0.0.0
  port: 8765
  proxy:
    enabled: true
    trusted_proxies: ["127.0.0.1", "10.0.0.0/8"]
```

## 🔄 Environment Variables

### **Configuration Override**

```bash
# Override configuration file
export OCPP_BROKER_CONFIG="/path/to/config.yaml"

# Override specific settings
export OCPP_BROKER_HOST="0.0.0.0"
export OCPP_BROKER_PORT="8765"
export OCPP_BROKER_LOG_LEVEL="DEBUG"
```

### **Docker Environment**

```yaml
# docker-compose.yml
version: '3.8'
services:
  ocpp-broker:
    image: your-org/ocpp-broker:latest
    ports:
      - "8765:8765"
    environment:
      - OCPP_BROKER_CONFIG=/app/config.yaml
      - OCPP_BROKER_LOG_LEVEL=INFO
    volumes:
      - ./config.yaml:/app/config.yaml
```

## 📝 Configuration Examples

### **Development Configuration**

```yaml
# config-dev.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "DEBUG"

organizations:
  - name: "DevCharging"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
    chargers:
      - "DEV_001"
      - "DEV_002"
```

### **Production Configuration**

```yaml
# config-prod.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO
  metrics:
    enabled: true
    port: 9090

organizations:
  - name: "ProductionCharging"
    connect_to_backend: true
    backends:
      - id: "primary_backend"
        url: "ws://primary-backend.com/ocpp"
        leader: true
        chargers:
          - "PROD_001"
          - "PROD_002"
      - id: "secondary_backend"
        url: "ws://secondary-backend.com/ocpp"
        leader: false
        chargers:
          - "PROD_001"
          - "PROD_002"
```

### **Hybrid Configuration**

```yaml
# config-hybrid.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "ExternalBackend"
    connect_to_backend: true
    backends:
      - id: "external_backend"
        url: "ws://external-backend.com/ocpp"
        leader: true
        chargers:
          - "EXT_001"
  
  - name: "LocalBackend"
    connect_to_backend: false
    chargers:
      - "LOCAL_001"
      - "LOCAL_002"
```

## 🔍 Configuration Validation

### **Validate Configuration**

```bash
# Validate configuration file
python -c "from ocpp_broker.config import load_config; print(load_config())"

# Check configuration syntax
python -m ocpp_broker.server --dry-run
```

### **Configuration Testing**

```bash
# Test configuration with specific file
python -m ocpp_broker.server -c /path/to/config.yaml --dry-run

# Test with environment variables
OCPP_BROKER_CONFIG=/path/to/config.yaml python -m ocpp_broker.server --dry-run
```

## 🚨 Common Configuration Issues

### **Issue 1: Invalid YAML Syntax**

```yaml
# ❌ Wrong - Missing quotes
organizations:
  - name: MyOrg  # Should be "MyOrg"

# ✅ Correct
organizations:
  - name: "MyOrg"
```

### **Issue 2: Missing Required Fields**

```yaml
# ❌ Wrong - Missing name
organizations:
  - connect_to_backend: false

# ✅ Correct
organizations:
  - name: "MyOrg"
    connect_to_backend: false
```

### **Issue 3: Invalid Backend Configuration**

```yaml
# ❌ Wrong - Missing URL
organizations:
  - name: "MyOrg"
    connect_to_backend: true
    backends:
      - id: "backend1"
        leader: true

# ✅ Correct
organizations:
  - name: "MyOrg"
    connect_to_backend: true
    backends:
      - id: "backend1"
        url: "ws://backend.com/ocpp"
        leader: true
```

## 📚 Next Steps

After configuring your broker:

1. **Test Your Configuration**: [Quick Start Guide](quick-start.md)
2. **Explore OCPP 1.6 Features**: [OCPP 1.6 Features](ocpp16-features.md)
3. **Learn About Broker-as-Backend**: [Broker-as-Backend Mode](broker-as-backend.md)
4. **Set Up Production**: [Production Deployment](deployment.md)
5. **Monitor Your System**: [Monitoring & Logging](monitoring.md)

## 🔗 Related Documentation

- [Quick Start Guide](quick-start.md)
- [OCPP 1.6 Features](ocpp16-features.md)
- [Broker-as-Backend Mode](broker-as-backend.md)
- [Production Deployment](deployment.md)
- [Troubleshooting](troubleshooting.md)

---

*Last updated: October 2024*
