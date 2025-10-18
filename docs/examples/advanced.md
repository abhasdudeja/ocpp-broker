# Advanced Examples

Advanced examples demonstrating complex OCPP broker scenarios and integrations.

## 🚀 Advanced Configuration Examples

### **Example 1: Multi-Organization Setup**

```yaml
# config-advanced.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"
  max_connections: 1000
  timeout: 30

organizations:
  # Organization 1: External Backend
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
  
  # Organization 2: Broker-as-Backend
  - name: "LocalCharging"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
      - firmware_management
    chargers:
      - "LOCAL_001"
      - "LOCAL_002"
  
  # Organization 3: Test Environment
  - name: "TestCharging"
    connect_to_backend: false
    ocpp_features:
      - core_profile
      - smart_charging
      - local_auth_list
      - reservation
    chargers:
      - "TEST_001"
      - "TEST_002"
```

### **Example 2: High Availability Configuration**

```yaml
# config-ha.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"
  metrics:
    enabled: true
    port: 9090
    path: "/metrics"

organizations:
  - name: "HACharging"
    connect_to_backend: true
    backends:
      - id: "ha_backend_1"
        url: "ws://backend-1.example.com/ocpp"
        leader: true
        chargers:
          - "HA_001"
          - "HA_002"
      - id: "ha_backend_2"
        url: "ws://backend-2.example.com/ocpp"
        leader: false
        chargers:
          - "HA_001"
          - "HA_002"
```

## 🔌 Advanced WebSocket Examples

### **Example 1: Multi-Charger Simulator**

```python
# multi_charger_simulator.py
import asyncio
import websockets
import json
import uuid
import datetime
import random

class ChargerSimulator:
    def __init__(self, charger_id, organization):
        self.charger_id = charger_id
        self.organization = organization
        self.uri = f"ws://localhost:8765/{organization}/{charger_id}"
        self.connected = False
        
    async def connect(self):
        """Connect to OCPP broker"""
        try:
            self.websocket = await websockets.connect(
                self.uri, 
                subprotocols=["ocpp1.6"]
            )
            self.connected = True
            print(f"✅ {self.charger_id} connected")
            return True
        except Exception as e:
            print(f"❌ {self.charger_id} connection failed: {e}")
            return False
    
    async def send_boot_notification(self):
        """Send BootNotification"""
        boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
            "chargePointVendor": "Siemens",
            "chargePointModel": "SL/01",
            "chargePointSerialNumber": f"SN{self.charger_id}",
            "firmwareVersion": "1.0.0"
        }]
        
        await self.websocket.send(json.dumps(boot_msg))
        print(f"📤 {self.charger_id} BootNotification sent")
        
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} BootNotification response: {response}")
    
    async def send_heartbeat(self):
        """Send Heartbeat"""
        heartbeat_msg = [2, str(uuid.uuid4()), "Heartbeat", {}]
        await self.websocket.send(json.dumps(heartbeat_msg))
        print(f"💓 {self.charger_id} Heartbeat sent")
        
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} Heartbeat response: {response}")
    
    async def send_status_notification(self, status="Available"):
        """Send StatusNotification"""
        status_msg = [2, str(uuid.uuid4()), "StatusNotification", {
            "connectorId": 1,
            "errorCode": "NoError",
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }]
        
        await self.websocket.send(json.dumps(status_msg))
        print(f"⚡ {self.charger_id} StatusNotification sent: {status}")
        
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} StatusNotification response: {response}")
    
    async def simulate_charging_session(self):
        """Simulate a charging session"""
        print(f"🔋 {self.charger_id} Starting charging session")
        
        # Start transaction
        start_transaction_msg = [2, str(uuid.uuid4()), "StartTransaction", {
            "connectorId": 1,
            "idTag": "TEST1234",
            "meterStart": 0,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }]
        
        await self.websocket.send(json.dumps(start_transaction_msg))
        print(f"📤 {self.charger_id} StartTransaction sent")
        
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} StartTransaction response: {response}")
        
        # Simulate charging for 30 seconds
        await asyncio.sleep(30)
        
        # Stop transaction
        stop_transaction_msg = [2, str(uuid.uuid4()), "StopTransaction", {
            "transactionId": 12345,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "meterStop": 1000,
            "reason": "Local"
        }]
        
        await self.websocket.send(json.dumps(stop_transaction_msg))
        print(f"📤 {self.charger_id} StopTransaction sent")
        
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} StopTransaction response: {response}")
    
    async def run(self):
        """Run the charger simulator"""
        if not await self.connect():
            return
        
        try:
            # Send BootNotification
            await self.send_boot_notification()
            
            # Send StatusNotification
            await self.send_status_notification("Available")
            
            # Simulate charging session
            await self.simulate_charging_session()
            
            # Send periodic heartbeats
            for i in range(10):
                await self.send_heartbeat()
                await asyncio.sleep(5)
                
        except Exception as e:
            print(f"❌ {self.charger_id} error: {e}")
        finally:
            if self.connected:
                await self.websocket.close()
                print(f"🔌 {self.charger_id} disconnected")

async def run_multiple_chargers():
    """Run multiple charger simulators"""
    chargers = [
        ChargerSimulator("CHARGER_001", "MyChargingStation"),
        ChargerSimulator("CHARGER_002", "MyChargingStation"),
        ChargerSimulator("CHARGER_003", "MyChargingStation"),
    ]
    
    tasks = []
    for charger in chargers:
        task = asyncio.create_task(charger.run())
        tasks.append(task)
    
    await asyncio.gather(*tasks, return_exceptions=True)

# Run the simulator
asyncio.run(run_multiple_chargers())
```

### **Example 2: Advanced Message Handling**

```python
# advanced_message_handler.py
import asyncio
import websockets
import json
import uuid
import datetime
from typing import Dict, Any

class AdvancedChargerSimulator:
    def __init__(self, charger_id, organization):
        self.charger_id = charger_id
        self.organization = organization
        self.uri = f"ws://localhost:8765/{organization}/{charger_id}"
        self.connected = False
        self.message_handlers = {
            "BootNotification": self.handle_boot_notification,
            "Heartbeat": self.handle_heartbeat,
            "StatusNotification": self.handle_status_notification,
            "StartTransaction": self.handle_start_transaction,
            "StopTransaction": self.handle_stop_transaction,
            "MeterValues": self.handle_meter_values,
        }
    
    async def connect(self):
        """Connect to OCPP broker"""
        try:
            self.websocket = await websockets.connect(
                self.uri, 
                subprotocols=["ocpp1.6"]
            )
            self.connected = True
            print(f"✅ {self.charger_id} connected")
            return True
        except Exception as e:
            print(f"❌ {self.charger_id} connection failed: {e}")
            return False
    
    async def send_message(self, action: str, payload: Dict[str, Any]):
        """Send OCPP message"""
        message = [2, str(uuid.uuid4()), action, payload]
        await self.websocket.send(json.dumps(message))
        print(f"📤 {self.charger_id} {action} sent")
        
        # Wait for response
        response = await self.websocket.recv()
        print(f"📥 {self.charger_id} {action} response: {response}")
        
        return json.loads(response)
    
    async def handle_boot_notification(self):
        """Handle BootNotification"""
        payload = {
            "chargePointVendor": "Siemens",
            "chargePointModel": "SL/01",
            "chargePointSerialNumber": f"SN{self.charger_id}",
            "chargeBoxSerialNumber": f"BOX{self.charger_id}",
            "firmwareVersion": "1.0.0",
            "iccid": "1234567890123456789",
            "imsi": "123456789012345",
            "meterType": "Single Phase",
            "meterSerialNumber": f"METER{self.charger_id}"
        }
        
        response = await self.send_message("BootNotification", payload)
        return response
    
    async def handle_heartbeat(self):
        """Handle Heartbeat"""
        response = await self.send_message("Heartbeat", {})
        return response
    
    async def handle_status_notification(self, status="Available"):
        """Handle StatusNotification"""
        payload = {
            "connectorId": 1,
            "errorCode": "NoError",
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "vendorId": "Siemens",
            "vendorErrorCode": "0"
        }
        
        response = await self.send_message("StatusNotification", payload)
        return response
    
    async def handle_start_transaction(self, id_tag="TEST1234"):
        """Handle StartTransaction"""
        payload = {
            "connectorId": 1,
            "idTag": id_tag,
            "meterStart": 0,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
        
        response = await self.send_message("StartTransaction", payload)
        return response
    
    async def handle_stop_transaction(self, transaction_id=12345):
        """Handle StopTransaction"""
        payload = {
            "transactionId": transaction_id,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "meterStop": 1000,
            "reason": "Local"
        }
        
        response = await self.send_message("StopTransaction", payload)
        return response
    
    async def handle_meter_values(self, connector_id=1, meter_value=100):
        """Handle MeterValues"""
        payload = {
            "connectorId": connector_id,
            "transactionId": 12345,
            "meterValue": [
                {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "sampledValue": [
                        {
                            "value": str(meter_value),
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Energy.Active.Import.Register",
                            "phase": "L1",
                            "location": "Outlet",
                            "unit": "Wh"
                        }
                    ]
                }
            ]
        }
        
        response = await self.send_message("MeterValues", payload)
        return response
    
    async def run(self):
        """Run the advanced charger simulator"""
        if not await self.connect():
            return
        
        try:
            # Boot sequence
            await self.handle_boot_notification()
            await self.handle_status_notification("Available")
            
            # Simulate charging session
            print(f"🔋 {self.charger_id} Starting charging session")
            
            # Start transaction
            start_response = await self.handle_start_transaction()
            transaction_id = start_response[2].get("transactionId", 12345)
            
            # Send meter values
            for i in range(5):
                await self.handle_meter_values(meter_value=100 + i * 10)
                await asyncio.sleep(2)
            
            # Stop transaction
            await self.handle_stop_transaction(transaction_id)
            
            # Send periodic heartbeats
            for i in range(5):
                await self.handle_heartbeat()
                await asyncio.sleep(5)
                
        except Exception as e:
            print(f"❌ {self.charger_id} error: {e}")
        finally:
            if self.connected:
                await self.websocket.close()
                print(f"🔌 {self.charger_id} disconnected")

# Run the advanced simulator
asyncio.run(AdvancedChargerSimulator("ADVANCED_001", "MyChargingStation").run())
```

## 📊 Advanced REST API Examples

### **Example 1: Monitoring Dashboard**

```python
# monitoring_dashboard.py
import requests
import json
import time
from datetime import datetime

class OCPPBrokerMonitor:
    def __init__(self, base_url="http://localhost:8765"):
        self.base_url = base_url
    
    def get_health(self):
        """Get broker health status"""
        try:
            response = requests.get(f"{self.base_url}/health")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_organizations(self):
        """Get all organizations"""
        try:
            response = requests.get(f"{self.base_url}/api/organizations")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_chargers(self):
        """Get all chargers"""
        try:
            response = requests.get(f"{self.base_url}/api/chargers")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_backends(self):
        """Get all backends"""
        try:
            response = requests.get(f"{self.base_url}/api/backends")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_metrics(self):
        """Get system metrics"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_logs(self, level=None, limit=100):
        """Get system logs"""
        try:
            params = {"limit": limit}
            if level:
                params["level"] = level
            
            response = requests.get(f"{self.base_url}/api/logs", params=params)
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def print_dashboard(self):
        """Print monitoring dashboard"""
        print("=" * 80)
        print("OCPP BROKER MONITORING DASHBOARD")
        print("=" * 80)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print()
        
        # Health status
        health = self.get_health()
        print(f"Health Status: {health.get('status', 'Unknown')}")
        print()
        
        # Organizations
        organizations = self.get_organizations()
        print(f"Organizations: {len(organizations)}")
        for org in organizations:
            print(f"  - {org['name']}: {org['connect_to_backend']}")
        print()
        
        # Chargers
        chargers = self.get_chargers()
        print(f"Chargers: {len(chargers)}")
        connected = sum(1 for c in chargers if c.get('status') == 'connected')
        print(f"  - Connected: {connected}")
        print(f"  - Disconnected: {len(chargers) - connected}")
        print()
        
        # Backends
        backends = self.get_backends()
        print(f"Backends: {len(backends)}")
        for backend in backends:
            print(f"  - {backend['id']}: {backend['status']}")
        print()
        
        # Metrics
        metrics = self.get_metrics()
        if 'system' in metrics:
            print(f"System Uptime: {metrics['system'].get('uptime', 'Unknown')}")
            print(f"Memory Usage: {metrics['system'].get('memory_usage', 'Unknown')}")
            print(f"CPU Usage: {metrics['system'].get('cpu_usage', 'Unknown')}")
        print()
        
        # Recent logs
        logs = self.get_logs(limit=5)
        if 'logs' in logs:
            print("Recent Logs:")
            for log in logs['logs']:
                print(f"  [{log['level']}] {log['message']}")
        print()
    
    def monitor_continuous(self, interval=30):
        """Monitor continuously"""
        print("Starting continuous monitoring...")
        print(f"Refresh interval: {interval} seconds")
        print("Press Ctrl+C to stop")
        print()
        
        try:
            while True:
                self.print_dashboard()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")

# Run the monitor
monitor = OCPPBrokerMonitor()
monitor.print_dashboard()

# Uncomment to run continuous monitoring
# monitor.monitor_continuous(30)
```

### **Example 2: Automated Testing**

```python
# automated_testing.py
import requests
import json
import time
import asyncio
import websockets
import uuid
from datetime import datetime

class OCPPBrokerTester:
    def __init__(self, base_url="http://localhost:8765"):
        self.base_url = base_url
        self.test_results = []
    
    def test_health_endpoint(self):
        """Test health endpoint"""
        try:
            response = requests.get(f"{self.base_url}/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"
            self.test_results.append(("Health Endpoint", "PASS"))
            return True
        except Exception as e:
            self.test_results.append(("Health Endpoint", f"FAIL: {e}"))
            return False
    
    def test_organizations_endpoint(self):
        """Test organizations endpoint"""
        try:
            response = requests.get(f"{self.base_url}/api/organizations")
            assert response.status_code == 200
            organizations = response.json()
            assert isinstance(organizations, list)
            self.test_results.append(("Organizations Endpoint", "PASS"))
            return True
        except Exception as e:
            self.test_results.append(("Organizations Endpoint", f"FAIL: {e}"))
            return False
    
    def test_chargers_endpoint(self):
        """Test chargers endpoint"""
        try:
            response = requests.get(f"{self.base_url}/api/chargers")
            assert response.status_code == 200
            chargers = response.json()
            assert isinstance(chargers, list)
            self.test_results.append(("Chargers Endpoint", "PASS"))
            return True
        except Exception as e:
            self.test_results.append(("Chargers Endpoint", f"FAIL: {e}"))
            return False
    
    def test_metrics_endpoint(self):
        """Test metrics endpoint"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            assert response.status_code == 200
            metrics = response.json()
            assert "system" in metrics
            self.test_results.append(("Metrics Endpoint", "PASS"))
            return True
        except Exception as e:
            self.test_results.append(("Metrics Endpoint", f"FAIL: {e}"))
            return False
    
    async def test_websocket_connection(self):
        """Test WebSocket connection"""
        try:
            uri = "ws://localhost:8765/MyChargingStation/TEST_001"
            async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
                # Send BootNotification
                boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
                    "chargePointVendor": "TestVendor",
                    "chargePointModel": "TestModel"
                }]
                
                await ws.send(json.dumps(boot_msg))
                response = await ws.recv()
                
                data = json.loads(response)
                assert data[0] == 3  # CallResult
                self.test_results.append(("WebSocket Connection", "PASS"))
                return True
        except Exception as e:
            self.test_results.append(("WebSocket Connection", f"FAIL: {e}"))
            return False
    
    def test_configuration_reload(self):
        """Test configuration reload"""
        try:
            response = requests.post(f"{self.base_url}/api/config/reload")
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == "success"
            self.test_results.append(("Configuration Reload", "PASS"))
            return True
        except Exception as e:
            self.test_results.append(("Configuration Reload", f"FAIL: {e}"))
            return False
    
    def run_all_tests(self):
        """Run all tests"""
        print("Running OCPP Broker Tests...")
        print("=" * 50)
        
        # REST API tests
        self.test_health_endpoint()
        self.test_organizations_endpoint()
        self.test_chargers_endpoint()
        self.test_metrics_endpoint()
        self.test_configuration_reload()
        
        # WebSocket test
        asyncio.run(self.test_websocket_connection())
        
        # Print results
        print("\nTest Results:")
        print("-" * 50)
        for test_name, result in self.test_results:
            print(f"{test_name}: {result}")
        
        # Summary
        passed = sum(1 for _, result in self.test_results if result == "PASS")
        total = len(self.test_results)
        print(f"\nSummary: {passed}/{total} tests passed")
        
        return passed == total

# Run the tests
tester = OCPPBrokerTester()
tester.run_all_tests()
```

## 🔧 Integration Examples

### **Example 1: Database Integration**

```python
# database_integration.py
import asyncio
import websockets
import json
import uuid
import sqlite3
from datetime import datetime

class DatabaseIntegration:
    def __init__(self, db_path="ocpp_broker.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chargers (
                id TEXT PRIMARY KEY,
                organization TEXT,
                status TEXT,
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                charger_id TEXT,
                message_type TEXT,
                action TEXT,
                payload TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (charger_id) REFERENCES chargers (id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def log_message(self, charger_id, message_type, action, payload):
        """Log message to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO messages (charger_id, message_type, action, payload)
            VALUES (?, ?, ?, ?)
        """, (charger_id, message_type, action, json.dumps(payload)))
        
        conn.commit()
        conn.close()
    
    def update_charger_status(self, charger_id, status):
        """Update charger status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO chargers (id, status, last_seen)
            VALUES (?, ?, ?)
        """, (charger_id, status, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
    
    async def simulate_charger_with_db(self, charger_id):
        """Simulate charger with database logging"""
        uri = f"ws://localhost:8765/MyChargingStation/{charger_id}"
        
        try:
            async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
                print(f"✅ {charger_id} connected")
                self.update_charger_status(charger_id, "connected")
                
                # Send BootNotification
                boot_msg = [2, str(uuid.uuid4()), "BootNotification", {
                    "chargePointVendor": "TestVendor",
                    "chargePointModel": "TestModel"
                }]
                
                await ws.send(json.dumps(boot_msg))
                self.log_message(charger_id, "Call", "BootNotification", boot_msg[3])
                
                response = await ws.recv()
                response_data = json.loads(response)
                self.log_message(charger_id, "CallResult", "BootNotification", response_data[2])
                
                # Send Heartbeat
                heartbeat_msg = [2, str(uuid.uuid4()), "Heartbeat", {}]
                await ws.send(json.dumps(heartbeat_msg))
                self.log_message(charger_id, "Call", "Heartbeat", heartbeat_msg[3])
                
                response = await ws.recv()
                response_data = json.loads(response)
                self.log_message(charger_id, "CallResult", "Heartbeat", response_data[2])
                
                print(f"📊 {charger_id} messages logged to database")
                
        except Exception as e:
            print(f"❌ {charger_id} error: {e}")
            self.update_charger_status(charger_id, "disconnected")
    
    def get_charger_history(self, charger_id):
        """Get charger message history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT message_type, action, payload, timestamp
            FROM messages
            WHERE charger_id = ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (charger_id,))
        
        results = cursor.fetchall()
        conn.close()
        
        return results

# Run the database integration
db = DatabaseIntegration()
asyncio.run(db.simulate_charger_with_db("DB_CHARGER_001"))

# Get charger history
history = db.get_charger_history("DB_CHARGER_001")
print("Charger History:")
for record in history:
    print(f"  {record[0]} {record[1]}: {record[2]} at {record[3]}")
```

### **Example 2: Message Queue Integration**

```python
# message_queue_integration.py
import asyncio
import websockets
import json
import uuid
import pika
from datetime import datetime

class MessageQueueIntegration:
    def __init__(self, rabbitmq_url="amqp://localhost"):
        self.rabbitmq_url = rabbitmq_url
        self.connection = None
        self.channel = None
        self.setup_rabbitmq()
    
    def setup_rabbitmq(self):
        """Setup RabbitMQ connection"""
        try:
            self.connection = pika.BlockingConnection(
                pika.URLParameters(self.rabbitmq_url)
            )
            self.channel = self.connection.channel()
            
            # Declare queues
            self.channel.queue_declare(queue='ocpp_messages')
            self.channel.queue_declare(queue='ocpp_responses')
            
            print("✅ RabbitMQ connection established")
        except Exception as e:
            print(f"❌ RabbitMQ connection failed: {e}")
    
    def publish_message(self, queue, message):
        """Publish message to queue"""
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=queue,
                body=json.dumps(message)
            )
            print(f"📤 Message published to {queue}")
        except Exception as e:
            print(f"❌ Failed to publish message: {e}")
    
    def consume_messages(self, queue, callback):
        """Consume messages from queue"""
        try:
            self.channel.basic_consume(
                queue=queue,
                on_message_callback=callback,
                auto_ack=True
            )
            print(f"📥 Consuming messages from {queue}")
            self.channel.start_consuming()
        except Exception as e:
            print(f"❌ Failed to consume messages: {e}")
    
    async def simulate_charger_with_mq(self, charger_id):
        """Simulate charger with message queue"""
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
                
                # Publish to message queue
                mq_message = {
                    "charger_id": charger_id,
                    "message_type": "Call",
                    "action": "BootNotification",
                    "payload": boot_msg[3],
                    "timestamp": datetime.now().isoformat()
                }
                self.publish_message('ocpp_messages', mq_message)
                
                response = await ws.recv()
                response_data = json.loads(response)
                
                # Publish response to queue
                mq_response = {
                    "charger_id": charger_id,
                    "message_type": "CallResult",
                    "action": "BootNotification",
                    "payload": response_data[2],
                    "timestamp": datetime.now().isoformat()
                }
                self.publish_message('ocpp_responses', mq_response)
                
                print(f"📊 {charger_id} messages published to queue")
                
        except Exception as e:
            print(f"❌ {charger_id} error: {e}")
    
    def close(self):
        """Close RabbitMQ connection"""
        if self.connection:
            self.connection.close()

# Run the message queue integration
mq = MessageQueueIntegration()
asyncio.run(mq.simulate_charger_with_mq("MQ_CHARGER_001"))
mq.close()
```

## 📚 Next Steps

After exploring these advanced examples:

1. **Learn About OCPP 1.6**: [OCPP 1.6 Features](../ocpp16-features.md)
2. **Configure Production**: [Production Deployment](../deployment.md)
3. **Explore API**: [API Reference](../api-reference.md)
4. **Set Up Monitoring**: [Monitoring & Logging](../monitoring.md)

## 🔗 Related Documentation

- [Basic Examples](basic.md)
- [OCPP 1.6 Features](../ocpp16-features.md)
- [Broker-as-Backend Mode](../broker-as-backend.md)
- [Production Deployment](../deployment.md)
- [API Reference](../api-reference.md)

---

*Last updated: October 2024*
