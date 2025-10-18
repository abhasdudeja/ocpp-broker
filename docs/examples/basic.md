# Basic Examples

Simple examples to get started with the OCPP broker.

## 🚀 Quick Start Examples

### **Example 1: Simple Charger Connection**

```python
# simple_charger.py
import asyncio
import websockets
import json
import uuid
import datetime

async def connect_charger():
    """Simple charger simulator"""
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("✅ Connected to OCPP broker")
        
        # Send BootNotification
        boot_msg = json.dumps([
            2, str(uuid.uuid4()), "BootNotification",
            {
                "chargePointVendor": "TestVendor",
                "chargePointModel": "TestModel"
            }
        ])
        await ws.send(boot_msg)
        print("📤 BootNotification sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"📥 BootNotification response: {response}")
        
        # Send Heartbeat
        heartbeat_msg = json.dumps([
            2, str(uuid.uuid4()), "Heartbeat", {}
        ])
        await ws.send(heartbeat_msg)
        print("📤 Heartbeat sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"📥 Heartbeat response: {response}")

# Run the simulator
asyncio.run(connect_charger())
```

### **Example 2: Basic Configuration**

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
      - "CHARGER_002"
```

### **Example 3: Start the Broker**

```bash
# Start the broker
python -m ocpp_broker.server

# Or with custom config
python -m ocpp_broker.server -c /path/to/config.yaml
```

## 🔌 WebSocket Examples

### **Example 1: Basic WebSocket Connection**

```javascript
// webSocketClient.js
const ws = new WebSocket('ws://localhost:8765/MyChargingStation/CHARGER_001', 'ocpp1.6');

ws.onopen = function() {
    console.log('Connected to OCPP broker');
    
    // Send BootNotification
    const bootNotification = [
        2, "12345", "BootNotification",
        {
            "chargePointVendor": "TestVendor",
            "chargePointModel": "TestModel"
        }
    ];
    
    ws.send(JSON.stringify(bootNotification));
    console.log('BootNotification sent');
};

ws.onmessage = function(event) {
    console.log('Received:', event.data);
};

ws.onerror = function(error) {
    console.error('WebSocket error:', error);
};

ws.onclose = function() {
    console.log('Connection closed');
};
```

### **Example 2: Python WebSocket Client**

```python
# webSocketClient.py
import asyncio
import websockets
import json
import uuid

async def webSocketClient():
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("Connected to OCPP broker")
        
        # Send multiple messages
        messages = [
            ["BootNotification", {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"}],
            ["Heartbeat", {}],
            ["StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}]
        ]
        
        for action, payload in messages:
            message = [2, str(uuid.uuid4()), action, payload]
            await ws.send(json.dumps(message))
            print(f"Sent: {action}")
            
            # Wait for response
            response = await ws.recv()
            print(f"Received: {response}")
            
            await asyncio.sleep(1)

asyncio.run(webSocketClient())
```

## 📊 REST API Examples

### **Example 1: Health Check**

```bash
# Check if broker is running
curl http://localhost:8765/health

# Expected response
{"status": "ok"}
```

### **Example 2: List Organizations**

```bash
# Get all organizations
curl http://localhost:8765/api/organizations

# Expected response
[
  {
    "name": "MyChargingStation",
    "connect_to_backend": false,
    "chargers": ["CHARGER_001", "CHARGER_002"],
    "backends": []
  }
]
```

### **Example 3: Get Charger Status**

```bash
# Get charger information
curl http://localhost:8765/api/chargers/CHARGER_001

# Expected response
{
  "id": "CHARGER_001",
  "organization": "MyChargingStation",
  "status": "connected",
  "last_seen": "2024-10-18T20:46:02.911118+00:00"
}
```

### **Example 4: Python REST Client**

```python
# restClient.py
import requests
import json

def test_rest_api():
    base_url = "http://localhost:8765"
    
    # Health check
    response = requests.get(f"{base_url}/health")
    print(f"Health: {response.json()}")
    
    # List organizations
    response = requests.get(f"{base_url}/api/organizations")
    organizations = response.json()
    print(f"Organizations: {organizations}")
    
    # Get charger status
    response = requests.get(f"{base_url}/api/chargers/CHARGER_001")
    charger = response.json()
    print(f"Charger: {charger}")
    
    # Get metrics
    response = requests.get(f"{base_url}/api/metrics")
    metrics = response.json()
    print(f"Metrics: {metrics}")

if __name__ == "__main__":
    test_rest_api()
```

## 🔧 Configuration Examples

### **Example 1: Broker-as-Backend Configuration**

```yaml
# config-broker-as-backend.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"

organizations:
  - name: "LocalCharging"
    connect_to_backend: false  # Broker acts as backend
    chargers:
      - "LOCAL_001"
      - "LOCAL_002"
```

### **Example 2: External Backend Configuration**

```yaml
# config-external-backend.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"

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
# config-multiple-orgs.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"

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

## 🧪 Testing Examples

### **Example 1: Basic Connection Test**

```python
# test_connection.py
import asyncio
import websockets
import json
import uuid

async def test_connection():
    """Test basic WebSocket connection"""
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    try:
        async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
            print("✅ Connection successful")
            
            # Test BootNotification
            boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
                "chargePointVendor": "TestVendor",
                "chargePointModel": "TestModel"
            }]
            
            await ws.send(json.dumps(boot_msg))
            print("📤 BootNotification sent")
            
            response = await ws.recv()
            print(f"📥 Response: {response}")
            
            # Parse response
            data = json.loads(response)
            if data[0] == 3:  # CallResult
                print("✅ BootNotification successful")
            else:
                print("❌ BootNotification failed")
                
    except Exception as e:
        print(f"❌ Connection failed: {e}")

asyncio.run(test_connection())
```

### **Example 2: Load Testing**

```python
# load_test.py
import asyncio
import websockets
import json
import uuid
import time

async def simulate_charger(charger_id):
    """Simulate a single charger"""
    uri = f"ws://localhost:8765/MyChargingStation/{charger_id}"
    
    try:
        async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
            print(f"✅ {charger_id} connected")
            
            # Send BootNotification
            boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
                "chargePointVendor": "TestVendor",
                "chargePointModel": "TestModel"
            }]
            await ws.send(json.dumps(boot_msg))
            
            # Send periodic heartbeats
            for i in range(10):
                heartbeat_msg = [2, str(uuid.uuid4()), "Heartbeat", {}]
                await ws.send(json.dumps(heartbeat_msg))
                await asyncio.sleep(5)
                
    except Exception as e:
        print(f"❌ {charger_id} failed: {e}")

async def load_test(num_chargers=10):
    """Run load test with multiple chargers"""
    print(f"Starting load test with {num_chargers} chargers")
    
    tasks = []
    for i in range(num_chargers):
        charger_id = f"CHARGER_{i:03d}"
        task = asyncio.create_task(simulate_charger(charger_id))
        tasks.append(task)
    
    await asyncio.gather(*tasks, return_exceptions=True)
    print("Load test completed")

# Run load test
asyncio.run(load_test(20))
```

### **Example 3: API Testing**

```python
# api_test.py
import requests
import json

def test_api():
    """Test REST API endpoints"""
    base_url = "http://localhost:8765"
    
    # Test health endpoint
    try:
        response = requests.get(f"{base_url}/health")
        assert response.status_code == 200
        print("✅ Health check passed")
    except Exception as e:
        print(f"❌ Health check failed: {e}")
    
    # Test organizations endpoint
    try:
        response = requests.get(f"{base_url}/api/organizations")
        assert response.status_code == 200
        organizations = response.json()
        print(f"✅ Organizations: {len(organizations)} found")
    except Exception as e:
        print(f"❌ Organizations check failed: {e}")
    
    # Test metrics endpoint
    try:
        response = requests.get(f"{base_url}/api/metrics")
        assert response.status_code == 200
        metrics = response.json()
        print(f"✅ Metrics: {metrics}")
    except Exception as e:
        print(f"❌ Metrics check failed: {e}")

if __name__ == "__main__":
    test_api()
```

## 🔄 Message Flow Examples

### **Example 1: BootNotification Flow**

```python
# bootNotification_flow.py
import asyncio
import websockets
import json
import uuid

async def bootNotification_flow():
    """Demonstrate BootNotification flow"""
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("1. Connected to OCPP broker")
        
        # Send BootNotification
        boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
            "chargePointVendor": "Siemens",
            "chargePointModel": "SL/01",
            "chargePointSerialNumber": "SN123456789",
            "chargeBoxSerialNumber": "SN987654321",
            "firmwareVersion": "1.0.0",
            "iccid": "1234567890123456789",
            "imsi": "123456789012345",
            "meterType": "Single Phase",
            "meterSerialNumber": "METER123456"
        }]
        
        await ws.send(json.dumps(boot_msg))
        print("2. BootNotification sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"3. BootNotification response: {response}")
        
        # Parse response
        data = json.loads(response)
        if data[0] == 3:  # CallResult
            payload = data[2]
            print(f"4. Boot status: {payload.get('status')}")
            print(f"5. Heartbeat interval: {payload.get('interval')} seconds")
            print(f"6. Current time: {payload.get('currentTime')}")

asyncio.run(bootNotification_flow())
```

### **Example 2: Heartbeat Flow**

```python
# heartbeat_flow.py
import asyncio
import websockets
import json
import uuid
import time

async def heartbeat_flow():
    """Demonstrate Heartbeat flow"""
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("1. Connected to OCPP broker")
        
        # Send multiple heartbeats
        for i in range(5):
            heartbeat_msg = [2, str(uuid.uuid4()), "Heartbeat", {}]
            await ws.send(json.dumps(heartbeat_msg))
            print(f"2.{i+1} Heartbeat sent")
            
            # Wait for response
            response = await ws.recv()
            print(f"3.{i+1} Heartbeat response: {response}")
            
            # Parse response
            data = json.loads(response)
            if data[0] == 3:  # CallResult
                payload = data[2]
                print(f"4.{i+1} Current time: {payload.get('currentTime')}")
            
            await asyncio.sleep(2)

asyncio.run(heartbeat_flow())
```

### **Example 3: StatusNotification Flow**

```python
# statusNotification_flow.py
import asyncio
import websockets
import json
import uuid
import datetime

async def statusNotification_flow():
    """Demonstrate StatusNotification flow"""
    uri = "ws://localhost:8765/MyChargingStation/CHARGER_001"
    
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        print("1. Connected to OCPP broker")
        
        # Send StatusNotification
        status_msg = [2, str(uuid.uuid4()), "StatusNotification", {
            "connectorId": 1,
            "errorCode": "NoError",
            "status": "Available",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "vendorId": "Siemens",
            "vendorErrorCode": "0"
        }]
        
        await ws.send(json.dumps(status_msg))
        print("2. StatusNotification sent")
        
        # Wait for response
        response = await ws.recv()
        print(f"3. StatusNotification response: {response}")
        
        # Parse response
        data = json.loads(response)
        if data[0] == 3:  # CallResult
            print("4. StatusNotification acknowledged")

asyncio.run(statusNotification_flow())
```

## 📚 Next Steps

After trying these basic examples:

1. **Explore Advanced Examples**: [Advanced Examples](advanced.md)
2. **Learn About OCPP 1.6**: [OCPP 1.6 Features](ocpp16-features.md)
3. **Configure Production**: [Production Deployment](deployment.md)
4. **Explore API**: [API Reference](api-reference.md)

## 🔗 Related Documentation

- [Quick Start Guide](../quick-start.md)
- [Configuration Guide](../configuration.md)
- [OCPP 1.6 Features](../ocpp16-features.md)
- [Broker-as-Backend Mode](../broker-as-backend.md)
- [API Reference](../api-reference.md)

---

*Last updated: October 2024*
