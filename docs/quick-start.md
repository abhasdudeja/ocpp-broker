# Quick Start Guide

Get your OCPP broker up and running in minutes!

## 🚀 5-Minute Setup

### **Step 1: Install the Broker**

```bash
# Install from PyPI
pip install ocpp-broker

# Or install from source
git clone https://github.com/your-org/ocpp-broker.git
cd ocpp-broker
pip install -e .
```

### **Step 2: Create Configuration**

Create a `config.yaml` file:

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "MyChargingStation"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "CHARGER_001"
```

### **Step 3: Start the Broker**

```bash
python -m ocpp_broker.server
```

### **Step 4: Test Connection**

```bash
# Test health endpoint
curl http://localhost:8765/health

# Test WebSocket connection
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001
```

## 🎯 Basic Usage Examples

### **Example 1: Simple Charger Connection**

```python
# charger_simulator.py
import asyncio
import websockets
import json
import uuid

async def connect_charger():
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("✅ Connected to OCPP broker")
        
        # Send BootNotification
        boot_msg = json.dumps([
            2, str(uuid.uuid4()), "BootNotification",
            {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"}
        ])
        await ws.send(boot_msg)
        print("📤 BootNotification sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"📥 Response: {response}")

# Run the simulator
asyncio.run(connect_charger())
```

### **Example 2: Broker with External Backend**

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

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

### **Example 3: Multiple Organizations**

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "CompanyA"
    connect_to_backend: true
    backends:
      - id: "backend_a"
        url: "ws://company-a-backend.com/ocpp"
        leader: true
        chargers:
          - "COMPANY_A_001"
  
  - name: "CompanyB"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "COMPANY_B_001"
      - "COMPANY_B_002"
```

## 🔧 Configuration Examples

### **Basic Configuration**

```yaml
# Minimal configuration
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "DefaultOrg"
    connect_to_backend: false
    chargers:
      - "CHARGER_001"
```

### **Production Configuration**

```yaml
# Production-ready configuration
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"

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

### **Development Configuration**

```yaml
# Development configuration with OCPP 1.6 features
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
      - firmware_management
    chargers:
      - "DEV_001"
      - "DEV_002"
```

## 🧪 Testing Your Setup

### **1. Health Check**

```bash
# Check if broker is running
curl http://localhost:8765/health

# Expected response
{"status": "ok"}
```

### **2. WebSocket Test**

```bash
# Install wscat for WebSocket testing
npm install -g wscat

# Test WebSocket connection
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001
```

### **3. OCPP Message Test**

```python
# test_ocpp_connection.py
import asyncio
import websockets
import json
import uuid

async def test_ocpp_connection():
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("✅ Connected to OCPP broker")
        
        # Test BootNotification
        boot_msg = json.dumps([
            2, str(uuid.uuid4()), "BootNotification",
            {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"}
        ])
        await ws.send(boot_msg)
        print("📤 BootNotification sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"📥 BootNotification response: {response}")
        
        # Test Heartbeat
        heartbeat_msg = json.dumps([
            2, str(uuid.uuid4()), "Heartbeat", {}
        ])
        await ws.send(heartbeat_msg)
        print("📤 Heartbeat sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"📥 Heartbeat response: {response}")

# Run the test
asyncio.run(test_ocpp_connection())
```

## 🚀 Advanced Features

### **Broker-as-Backend Mode**

```yaml
# config.yaml
organizations:
  - name: "LocalCharging"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "LOCAL_001"
```

**Benefits:**
- No external backend needed
- Local OCPP command processing
- Reduced latency
- Simplified deployment

### **Leader-Follower Logic**

```yaml
# config.yaml
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

**Benefits:**
- Only leader can send commands
- Followers receive status updates
- Prevents command conflicts
- Enhanced security

### **OCPP 1.6 Features**

```yaml
# config.yaml
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

**Supported Commands:**
- **Core Profile**: 19 commands
- **Smart Charging**: 4 commands
- **Firmware Management**: 2 commands
- **Local Auth List**: 2 commands
- **Reservation**: 2 commands

## 🔍 Monitoring and Logs

### **View Logs**

```bash
# Run with debug logging
OCPP_BROKER_LOG_LEVEL=DEBUG python -m ocpp_broker.server

# View logs in real-time
tail -f broker.log
```

### **Monitor Connections**

```bash
# Check active connections
curl http://localhost:8765/api/connections

# Check organization status
curl http://localhost:8765/api/organizations
```

## 🚨 Common Issues

### **Issue 1: Connection Refused**

```bash
# Error: Connection refused
# Solution: Check if broker is running
curl http://localhost:8765/health
```

### **Issue 2: WebSocket Handshake Failed**

```bash
# Error: WebSocket handshake failed
# Solution: Use correct subprotocol
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001 --subprotocol ocpp1.6
```

### **Issue 3: Configuration Not Found**

```bash
# Error: Configuration file not found
# Solution: Create config.yaml or specify path
python -m ocpp_broker.server -c /path/to/config.yaml
```

## 📚 Next Steps

Now that you have the basics working:

1. **Explore OCPP 1.6 Features**: [OCPP 1.6 Features](ocpp16-features.md)
2. **Configure Advanced Settings**: [Configuration Guide](configuration.md)
3. **Learn About Broker-as-Backend**: [Broker-as-Backend Mode](broker-as-backend.md)
4. **Set Up Production**: [Production Deployment](deployment.md)
5. **Explore API**: [API Reference](api-reference.md)

## 🔗 Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [OCPP 1.6 Features](ocpp16-features.md)
- [Broker-as-Backend Mode](broker-as-backend.md)
- [Troubleshooting](troubleshooting.md)

---

*Last updated: October 2024*
