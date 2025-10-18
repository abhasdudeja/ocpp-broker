# Leader-Follower Logic

Comprehensive guide to the leader-follower logic implementation in the OCPP broker.

## 🎯 Overview

The leader-follower logic ensures that only designated leader backends can send commands to chargers, while follower backends receive status updates but cannot send commands. This prevents command conflicts and ensures proper control hierarchy.

## 🏗️ Architecture

### **Leader-Follower Model**

```
┌─────────────────────────────────────────────────────────────┐
│                Leader-Follower Architecture                  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Leader        │  │   Follower       │  │   Charger    │ │
│  │   Backend       │  │   Backend        │  │              │ │
│  │                 │  │                 │  │              │ │
│  │ ✅ Send Commands│  │ ❌ No Commands  │  │              │ │
│  │ ✅ Receive      │  │ ✅ Receive      │  │              │ │
│  │    Status       │  │    Status       │  │              │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│           │                     │                     │     │
│           ▼                     ▼                     ▼     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Command       │  │   Status        │  │   OCPP       │ │
│  │   Processing    │  │   Updates       │  │   Messages   │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Message Flow**

1. **Charger → Broker**: OCPP message from charger
2. **Broker → All Backends**: Status updates to all backends
3. **Leader Backend → Broker**: Commands from leader backend
4. **Broker → Charger**: Commands forwarded to charger
5. **Follower Backend → Broker**: Commands blocked (ignored)

## 🔧 Configuration

### **Basic Leader-Follower Setup**

```yaml
# config.yaml
organizations:
  - name: "MultiBackendOrg"
    connect_to_backend: true
    backends:
      - id: "leader_backend"
        url: "ws://leader-backend.com/ocpp"
        leader: true  # This backend can send commands
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
      - id: "follower_backend"
        url: "ws://follower-backend.com/ocpp"
        leader: false  # This backend cannot send commands
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
```

### **Multiple Leaders (Not Recommended)**

```yaml
# config.yaml
organizations:
  - name: "MultiLeaderOrg"
    connect_to_backend: true
    backends:
      - id: "leader_1"
        url: "ws://leader-1.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_001"
      - id: "leader_2"
        url: "ws://leader-2.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_002"
```

**Note**: Multiple leaders are not recommended as they can cause command conflicts.

### **Auto-Leader Assignment**

```yaml
# config.yaml
organizations:
  - name: "AutoLeaderOrg"
    connect_to_backend: true
    backends:
      - id: "backend_1"
        url: "ws://backend-1.com/ocpp"
        # No leader specified - will be auto-assigned as leader
        chargers:
          - "CHARGER_001"
      - id: "backend_2"
        url: "ws://backend-2.com/ocpp"
        leader: false
        chargers:
          - "CHARGER_001"
```

## 🚀 Implementation Details

### **Leader-Follower Logic in Code**

```python
# In command_router_v2.py
async def route_backend_message(self, backend, message: str):
    """
    Route a message from a backend to the appropriate charger.
    """
    try:
        # Check if backend is leader - only leader backends can send commands
        if not getattr(backend, 'is_leader', False):
            logger.debug(f"Ignoring follower message from {backend.id} ({backend.org})")
            return
        
        # Parse backend message
        parsed_message = self._parse_backend_message(message)
        if not parsed_message:
            return
        
        # Extract target charger
        target_charger = self._extract_target_charger(parsed_message)
        if not target_charger:
            logger.warning(f"No target charger found in backend message: {message[:200]}")
            return
        
        # Get charger WebSocket connection
        charger_ws = self.broker.active_chargers.get(target_charger)
        if not charger_ws or charger_ws.closed:
            logger.warning(f"Cannot deliver message to charger {target_charger}: not connected")
            return
        
        # Send message to charger
        await charger_ws.send_text(message)
        logger.info(f"Delivered leader command from {backend.id} ({backend.org}) to charger {target_charger}")
        
    except Exception as e:
        logger.error(f"Error routing backend message: {e}")
```

### **Backend Connection Setup**

```python
# In broker.py
async def handle_charger(self, websocket, path):
    # ... existing code ...
    
    if connect_to_backend:
        backend_config = org_entry["backends"][0]  # Get first backend config
        backend_url = backend_config["url"]
        is_leader = backend_config.get("leader", False)  # Get leader status from config
        backend_conn = BackendConnection(
            broker=self,
            charger_id=charger_id,
            url=backend_url,
            org=org_name,
            is_leader=is_leader  # Pass leader status to BackendConnection
        )
```

### **BackendConnection Class**

```python
# In backend_manager.py
class BackendConnection:
    def __init__(self, broker, charger_id: str, url: str, org: str = "default", is_leader: bool = False):
        self.broker = broker
        self.id = charger_id
        self.url = url.rstrip("/")
        self.org = org
        self.is_leader = is_leader  # Leader/follower status
        self.websocket = None
        # ... rest of the implementation
```

## 📊 Message Processing

### **Leader Backend Messages**

```python
# Leader backend can send commands
if backend.is_leader:
    # Process command
    await charger_ws.send_text(message)
    logger.info(f"Delivered leader command from {backend.id}")
```

### **Follower Backend Messages**

```python
# Follower backend messages are ignored
if not backend.is_leader:
    logger.debug(f"Ignoring follower message from {backend.id}")
    return
```

### **Status Updates to All Backends**

```python
# Status updates are sent to all backends (leaders and followers)
for backend in backends:
    if backend.websocket and not backend.websocket.closed:
        await backend.websocket.send_text(status_message)
        logger.info(f"Status update sent to {backend.id}")
```

## 🧪 Testing Leader-Follower Logic

### **Test Script**

```python
# test_leader_follower.py
import asyncio
import websockets
import json
import uuid
from unittest.mock import Mock, AsyncMock

async def test_leader_follower_logic():
    """Test leader-follower logic implementation"""
    
    print("Testing Leader-Follower Logic")
    print("=" * 50)
    
    # Create broker instance
    broker = OcppBroker()
    broker._use_ocpp_router = True
    
    # Mock configuration
    broker.config_data = {
        "organizations": [
            {
                "name": "TestOrg",
                "connect_to_backend": True,
                "backends": [
                    {
                        "id": "leader_backend",
                        "url": "ws://leader.com/ocpp",
                        "leader": True,
                        "chargers": ["CHARGER_001"]
                    },
                    {
                        "id": "follower_backend",
                        "url": "ws://follower.com/ocpp",
                        "leader": False,
                        "chargers": ["CHARGER_001"]
                    }
                ]
            }
        ]
    }
    
    # Test 1: Leader backend can send commands
    print("\nTest 1: Leader backend command")
    print("-" * 30)
    
    leader_backend = Mock()
    leader_backend.id = "leader_backend"
    leader_backend.org = "TestOrg"
    leader_backend.is_leader = True
    
    # Mock charger WebSocket
    mock_charger_ws = Mock()
    mock_charger_ws.send_text = AsyncMock()
    mock_charger_ws.closed = False
    
    broker.active_chargers["CHARGER_001"] = mock_charger_ws
    
    # Test leader command
    leader_command = json.dumps([
        2, "12345", "RemoteStartTransaction",
        {"connectorId": 1, "idTag": "TEST1234"}
    ])
    
    await broker.ocpp_router.route_backend_message(leader_backend, leader_command)
    
    # Verify command was sent
    mock_charger_ws.send_text.assert_called_once()
    print("✅ Leader command processed successfully")
    
    # Test 2: Follower backend command is ignored
    print("\nTest 2: Follower backend command")
    print("-" * 30)
    
    follower_backend = Mock()
    follower_backend.id = "follower_backend"
    follower_backend.org = "TestOrg"
    follower_backend.is_leader = False
    
    # Reset mock
    mock_charger_ws.send_text.reset_mock()
    
    # Test follower command
    follower_command = json.dumps([
        2, "12346", "RemoteStartTransaction",
        {"connectorId": 1, "idTag": "TEST1234"}
    ])
    
    await broker.ocpp_router.route_backend_message(follower_backend, follower_command)
    
    # Verify command was NOT sent
    mock_charger_ws.send_text.assert_not_called()
    print("✅ Follower command ignored successfully")
    
    print("\n" + "=" * 50)
    print("Leader-Follower Logic Test Complete!")
    print("✅ Leader commands are processed")
    print("✅ Follower commands are ignored")

# Run the test
asyncio.run(test_leader_follower_logic())
```

## 🔍 Monitoring and Logging

### **Leader-Follower Logs**

```bash
# View leader-follower logs
grep -i "leader\|follower" /opt/ocpp-broker/logs/broker.log

# View ignored follower messages
grep "Ignoring follower message" /opt/ocpp-broker/logs/broker.log

# View leader command processing
grep "Delivered leader command" /opt/ocpp-broker/logs/broker.log
```

### **Monitoring Dashboard**

```python
# leader_follower_monitor.py
import requests
import json

def monitor_leader_follower():
    """Monitor leader-follower status"""
    base_url = "http://localhost:8765"
    
    # Get backends
    response = requests.get(f"{base_url}/api/backends")
    backends = response.json()
    
    print("Leader-Follower Status:")
    print("=" * 40)
    
    for backend in backends:
        status = "Leader" if backend.get("leader", False) else "Follower"
        print(f"{backend['id']}: {status} ({backend['status']})")
    
    # Get metrics
    response = requests.get(f"{base_url}/api/metrics")
    metrics = response.json()
    
    if 'backends' in metrics:
        print(f"\nTotal Backends: {metrics['backends']['total']}")
        print(f"Connected Backends: {metrics['backends']['connected']}")

# Run the monitor
monitor_leader_follower()
```

## 🚨 Common Issues

### **Issue 1: No Leader Assigned**

**Error:**
```
No leader backend found for organization
```

**Solution:**
```yaml
# Ensure at least one backend is marked as leader
organizations:
  - name: "MyOrg"
    connect_to_backend: true
    backends:
      - id: "backend1"
        url: "ws://backend1.com/ocpp"
        leader: true  # Mark as leader
        chargers:
          - "CHARGER_001"
```

### **Issue 2: Multiple Leaders**

**Warning:**
```
Organization has multiple leaders; using first one only
```

**Solution:**
```yaml
# Ensure only one backend is marked as leader
organizations:
  - name: "MyOrg"
    connect_to_backend: true
    backends:
      - id: "leader_backend"
        url: "ws://leader.com/ocpp"
        leader: true  # Only one leader
        chargers:
          - "CHARGER_001"
      - id: "follower_backend"
        url: "ws://follower.com/ocpp"
        leader: false  # Mark as follower
        chargers:
          - "CHARGER_001"
```

### **Issue 3: Follower Commands Not Working**

**Error:**
```
Follower backend cannot send commands
```

**Solution:**
```yaml
# Check leader configuration
organizations:
  - name: "MyOrg"
    connect_to_backend: true
    backends:
      - id: "backend1"
        url: "ws://backend1.com/ocpp"
        leader: true  # Must be true to send commands
        chargers:
          - "CHARGER_001"
```

## 🔧 Advanced Configuration

### **Dynamic Leader Assignment**

```python
# dynamic_leader_assignment.py
class DynamicLeaderManager:
    def __init__(self, broker):
        self.broker = broker
        self.leader_backend = None
    
    def assign_leader(self, backend_id):
        """Assign a new leader backend"""
        # Remove leader status from current leader
        if self.leader_backend:
            self.leader_backend.is_leader = False
        
        # Assign new leader
        for backend in self.broker.org_backends.values():
            if backend.id == backend_id:
                backend.is_leader = True
                self.leader_backend = backend
                break
    
    def get_leader(self):
        """Get current leader backend"""
        return self.leader_backend
    
    def is_leader(self, backend_id):
        """Check if backend is leader"""
        return self.leader_backend and self.leader_backend.id == backend_id
```

### **Leader Health Monitoring**

```python
# leader_health_monitor.py
class LeaderHealthMonitor:
    def __init__(self, broker):
        self.broker = broker
        self.leader_timeout = 30  # seconds
    
    async def monitor_leader_health(self):
        """Monitor leader backend health"""
        while True:
            leader = self.get_leader()
            if leader and not self.is_leader_healthy(leader):
                await self.failover_to_follower()
            await asyncio.sleep(5)
    
    def is_leader_healthy(self, leader):
        """Check if leader is healthy"""
        return (leader.websocket and 
                not leader.websocket.closed and
                time.time() - leader.last_ping < self.leader_timeout)
    
    async def failover_to_follower(self):
        """Failover to follower backend"""
        followers = [b for b in self.broker.org_backends.values() 
                    if not b.is_leader and b.websocket and not b.websocket.closed]
        
        if followers:
            new_leader = followers[0]
            new_leader.is_leader = True
            self.leader_backend.is_leader = False
            self.leader_backend = new_leader
            print(f"Leader failover to {new_leader.id}")
```

## 📊 Performance Considerations

### **Leader-Follower Overhead**

- **Leader Processing**: Minimal overhead for leader commands
- **Follower Filtering**: Very low overhead for ignoring follower commands
- **Status Updates**: All backends receive status updates (expected behavior)

### **Optimization Tips**

1. **Minimize Follower Commands**: Avoid sending commands from follower backends
2. **Monitor Leader Health**: Implement health checks for leader backends
3. **Load Balancing**: Distribute chargers across multiple leader backends
4. **Failover Planning**: Implement automatic failover mechanisms

## 📚 Best Practices

### **1. Single Leader per Organization**

```yaml
# ✅ Good - Single leader
organizations:
  - name: "MyOrg"
    backends:
      - id: "leader"
        leader: true
      - id: "follower"
        leader: false

# ❌ Bad - Multiple leaders
organizations:
  - name: "MyOrg"
    backends:
      - id: "leader1"
        leader: true
      - id: "leader2"
        leader: true
```

### **2. Clear Leader Assignment**

```yaml
# ✅ Good - Explicit leader assignment
organizations:
  - name: "MyOrg"
    backends:
      - id: "primary"
        leader: true
      - id: "secondary"
        leader: false

# ❌ Bad - Ambiguous assignment
organizations:
  - name: "MyOrg"
    backends:
      - id: "backend1"
        # No leader specified
      - id: "backend2"
        # No leader specified
```

### **3. Proper Error Handling**

```python
# ✅ Good - Proper error handling
try:
    if backend.is_leader:
        await process_leader_command(backend, message)
    else:
        logger.debug(f"Ignoring follower command from {backend.id}")
except Exception as e:
    logger.error(f"Error processing backend message: {e}")

# ❌ Bad - No error handling
if backend.is_leader:
    await process_leader_command(backend, message)
```

## 🔗 Related Documentation

- [Configuration Guide](configuration.md)
- [Broker-as-Backend Mode](broker-as-backend.md)
- [API Reference](api-reference.md)
- [Troubleshooting](troubleshooting.md)
- [Production Deployment](deployment.md)

---

*Last updated: October 2024*
