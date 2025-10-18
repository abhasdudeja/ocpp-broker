# Monitoring & Logging

Comprehensive guide to monitoring and logging the OCPP broker system.

## 📊 Monitoring Overview

### **Monitoring Components**

1. **System Metrics** - CPU, memory, disk usage
2. **Application Metrics** - Connections, messages, errors
3. **OCPP Metrics** - Charger status, backend health
4. **Business Metrics** - Transactions, energy consumption

### **Monitoring Stack**

```
┌─────────────────────────────────────────────────────────────┐
│                    Monitoring Stack                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Prometheus    │  │   Grafana       │  │   Alerting  │ │
│  │   (Metrics)     │  │   (Dashboard)   │  │   (Alerts)  │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   ELK Stack     │  │   Logstash      │  │   Kibana     │ │
│  │   (Logs)        │  │   (Processing)  │  │   (Analysis) │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 System Metrics

### **Basic System Monitoring**

```bash
# Check system resources
htop
free -h
df -h

# Check network connections
netstat -tlnp | grep 8765
ss -tulpn | grep 8765

# Check process status
ps aux | grep ocpp-broker
```

### **System Metrics Collection**

```python
# system_metrics.py
import psutil
import time
import json

class SystemMetrics:
    def __init__(self):
        self.metrics = {}
    
    def collect_metrics(self):
        """Collect system metrics"""
        self.metrics = {
            "timestamp": time.time(),
            "cpu": {
                "percent": psutil.cpu_percent(interval=1),
                "count": psutil.cpu_count(),
                "load_avg": psutil.getloadavg()
            },
            "memory": {
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "percent": psutil.virtual_memory().percent,
                "used": psutil.virtual_memory().used
            },
            "disk": {
                "total": psutil.disk_usage('/').total,
                "used": psutil.disk_usage('/').used,
                "free": psutil.disk_usage('/').free,
                "percent": psutil.disk_usage('/').percent
            },
            "network": {
                "connections": len(psutil.net_connections()),
                "io_counters": psutil.net_io_counters()._asdict()
            }
        }
        return self.metrics
    
    def print_metrics(self):
        """Print metrics in human-readable format"""
        metrics = self.collect_metrics()
        
        print("System Metrics:")
        print(f"  CPU Usage: {metrics['cpu']['percent']}%")
        print(f"  Memory Usage: {metrics['memory']['percent']}%")
        print(f"  Disk Usage: {metrics['disk']['percent']}%")
        print(f"  Network Connections: {metrics['network']['connections']}")

# Run system metrics
metrics = SystemMetrics()
metrics.print_metrics()
```

## 📈 Application Metrics

### **OCPP Broker Metrics**

```python
# ocpp_metrics.py
import time
import json
from collections import defaultdict

class OCPPMetrics:
    def __init__(self):
        self.metrics = {
            "connections": {
                "total_chargers": 0,
                "connected_chargers": 0,
                "total_backends": 0,
                "connected_backends": 0
            },
            "messages": {
                "total_processed": 0,
                "successful": 0,
                "failed": 0,
                "rate_per_minute": 0
            },
            "organizations": {},
            "chargers": {},
            "backends": {}
        }
        self.start_time = time.time()
        self.message_times = []
    
    def record_message(self, success=True):
        """Record a processed message"""
        self.metrics["messages"]["total_processed"] += 1
        if success:
            self.metrics["messages"]["successful"] += 1
        else:
            self.metrics["messages"]["failed"] += 1
        
        # Calculate rate
        current_time = time.time()
        self.message_times.append(current_time)
        
        # Keep only last minute
        minute_ago = current_time - 60
        self.message_times = [t for t in self.message_times if t > minute_ago]
        self.metrics["messages"]["rate_per_minute"] = len(self.message_times)
    
    def update_charger_status(self, charger_id, status):
        """Update charger status"""
        self.metrics["chargers"][charger_id] = {
            "status": status,
            "last_seen": time.time()
        }
    
    def update_backend_status(self, backend_id, status):
        """Update backend status"""
        self.metrics["backends"][backend_id] = {
            "status": status,
            "last_seen": time.time()
        }
    
    def get_uptime(self):
        """Get application uptime"""
        return time.time() - self.start_time
    
    def get_metrics(self):
        """Get all metrics"""
        self.metrics["system"] = {
            "uptime": self.get_uptime(),
            "timestamp": time.time()
        }
        return self.metrics

# Usage example
metrics = OCPPMetrics()
metrics.record_message(success=True)
metrics.update_charger_status("CHARGER_001", "connected")
print(json.dumps(metrics.get_metrics(), indent=2))
```

### **Prometheus Metrics**

```python
# prometheus_metrics.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time

# Define metrics
ocpp_messages_total = Counter('ocpp_messages_total', 'Total OCPP messages processed', ['action', 'status'])
ocpp_message_duration = Histogram('ocpp_message_duration_seconds', 'OCPP message processing duration')
ocpp_connections = Gauge('ocpp_connections', 'Current OCPP connections', ['type'])
ocpp_organizations = Gauge('ocpp_organizations', 'Number of organizations')
ocpp_chargers = Gauge('ocpp_chargers', 'Number of chargers', ['status'])
ocpp_backends = Gauge('ocpp_backends', 'Number of backends', ['status'])

class PrometheusMetrics:
    def __init__(self, port=9090):
        self.port = port
        start_http_server(port)
        print(f"Prometheus metrics server started on port {port}")
    
    def record_message(self, action, success=True):
        """Record a processed message"""
        status = "success" if success else "error"
        ocpp_messages_total.labels(action=action, status=status).inc()
    
    def record_message_duration(self, duration):
        """Record message processing duration"""
        ocpp_message_duration.observe(duration)
    
    def update_connections(self, chargers, backends):
        """Update connection metrics"""
        ocpp_connections.labels(type='chargers').set(chargers)
        ocpp_connections.labels(type='backends').set(backends)
    
    def update_organizations(self, count):
        """Update organization count"""
        ocpp_organizations.set(count)
    
    def update_chargers(self, connected, disconnected):
        """Update charger metrics"""
        ocpp_chargers.labels(status='connected').set(connected)
        ocpp_chargers.labels(status='disconnected').set(disconnected)
    
    def update_backends(self, connected, disconnected):
        """Update backend metrics"""
        ocpp_backends.labels(status='connected').set(connected)
        ocpp_backends.labels(status='disconnected').set(disconnected)

# Usage example
metrics = PrometheusMetrics()
metrics.record_message("BootNotification", success=True)
metrics.update_connections(5, 2)
```

## 📝 Logging Configuration

### **Basic Logging Setup**

```python
# logging_config.py
import logging
import logging.handlers
import os
from datetime import datetime

def setup_logging(log_level="INFO", log_file="/opt/ocpp-broker/logs/broker.log"):
    """Setup comprehensive logging"""
    
    # Create logs directory
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Create file handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return root_logger

# Setup logging
logger = setup_logging("INFO", "/opt/ocpp-broker/logs/broker.log")
```

### **Structured Logging**

```python
# structured_logging.py
import json
import logging
from datetime import datetime

class StructuredLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)
    
    def log_message(self, level, message, **kwargs):
        """Log structured message"""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level.upper(),
            "message": message,
            **kwargs
        }
        
        getattr(self.logger, level.lower())(json.dumps(log_data))
    
    def log_charger_event(self, charger_id, event, **kwargs):
        """Log charger event"""
        self.log_message("info", f"Charger {charger_id} {event}", 
                        charger_id=charger_id, event=event, **kwargs)
    
    def log_backend_event(self, backend_id, event, **kwargs):
        """Log backend event"""
        self.log_message("info", f"Backend {backend_id} {event}",
                        backend_id=backend_id, event=event, **kwargs)
    
    def log_ocpp_message(self, charger_id, action, message_type, **kwargs):
        """Log OCPP message"""
        self.log_message("debug", f"OCPP {message_type} {action}",
                        charger_id=charger_id, action=action, 
                        message_type=message_type, **kwargs)

# Usage example
logger = StructuredLogger("ocpp_broker")
logger.log_charger_event("CHARGER_001", "connected")
logger.log_ocpp_message("CHARGER_001", "BootNotification", "Call")
```

## 📊 Dashboard Setup

### **Grafana Dashboard**

```json
{
  "dashboard": {
    "title": "OCPP Broker Dashboard",
    "panels": [
      {
        "title": "System Overview",
        "type": "stat",
        "targets": [
          {
            "expr": "ocpp_connections{type=\"chargers\"}"
          }
        ]
      },
      {
        "title": "Message Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(ocpp_messages_total[5m])"
          }
        ]
      },
      {
        "title": "Connection Status",
        "type": "table",
        "targets": [
          {
            "expr": "ocpp_chargers"
          }
        ]
      }
    ]
  }
}
```

### **Custom Dashboard**

```python
# custom_dashboard.py
import requests
import json
from datetime import datetime

class OCPPDashboard:
    def __init__(self, base_url="http://localhost:8765"):
        self.base_url = base_url
    
    def get_system_status(self):
        """Get system status"""
        try:
            response = requests.get(f"{self.base_url}/health")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_metrics(self):
        """Get application metrics"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_chargers(self):
        """Get charger status"""
        try:
            response = requests.get(f"{self.base_url}/api/chargers")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_backends(self):
        """Get backend status"""
        try:
            response = requests.get(f"{self.base_url}/api/backends")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def print_dashboard(self):
        """Print dashboard"""
        print("=" * 80)
        print("OCPP BROKER DASHBOARD")
        print("=" * 80)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print()
        
        # System status
        status = self.get_system_status()
        print(f"System Status: {status.get('status', 'Unknown')}")
        print()
        
        # Metrics
        metrics = self.get_metrics()
        if 'system' in metrics:
            print(f"Uptime: {metrics['system'].get('uptime', 'Unknown')}")
            print(f"Memory: {metrics['system'].get('memory_usage', 'Unknown')}")
            print(f"CPU: {metrics['system'].get('cpu_usage', 'Unknown')}")
        print()
        
        # Chargers
        chargers = self.get_chargers()
        print(f"Chargers: {len(chargers)}")
        for charger in chargers:
            print(f"  {charger['id']}: {charger['status']}")
        print()
        
        # Backends
        backends = self.get_backends()
        print(f"Backends: {len(backends)}")
        for backend in backends:
            print(f"  {backend['id']}: {backend['status']}")
        print()

# Run dashboard
dashboard = OCPPDashboard()
dashboard.print_dashboard()
```

## 🚨 Alerting Setup

### **Basic Alerting**

```python
# alerting.py
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class OCPPAlerting:
    def __init__(self, smtp_server, smtp_port, username, password):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
    
    def send_alert(self, subject, message, recipients):
        """Send alert email"""
        msg = MIMEMultipart()
        msg['From'] = self.username
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = subject
        
        msg.attach(MIMEText(message, 'plain'))
        
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            print(f"Alert sent: {subject}")
        except Exception as e:
            print(f"Failed to send alert: {e}")
    
    def check_health(self, base_url="http://localhost:8765"):
        """Check system health and send alerts"""
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            if response.status_code != 200:
                self.send_alert(
                    "OCPP Broker Health Check Failed",
                    f"Health check returned status {response.status_code}",
                    ["admin@example.com"]
                )
        except Exception as e:
            self.send_alert(
                "OCPP Broker Unreachable",
                f"Broker is not responding: {e}",
                ["admin@example.com"]
            )
    
    def check_connections(self, base_url="http://localhost:8765"):
        """Check connection health"""
        try:
            response = requests.get(f"{base_url}/api/metrics")
            metrics = response.json()
            
            if 'connections' in metrics:
                total_chargers = metrics['connections'].get('total_chargers', 0)
                connected_chargers = metrics['connections'].get('connected_chargers', 0)
                
                if connected_chargers == 0 and total_chargers > 0:
                    self.send_alert(
                        "No Chargers Connected",
                        f"Expected {total_chargers} chargers, but 0 are connected",
                        ["admin@example.com"]
                    )
        except Exception as e:
            print(f"Failed to check connections: {e}")

# Usage example
alerting = OCPPAlerting("smtp.gmail.com", 587, "user@gmail.com", "password")
alerting.check_health()
alerting.check_connections()
```

### **Advanced Alerting with Rules**

```python
# advanced_alerting.py
import time
import requests
from datetime import datetime, timedelta

class AdvancedAlerting:
    def __init__(self, base_url="http://localhost:8765"):
        self.base_url = base_url
        self.alert_rules = {
            "high_cpu": {"threshold": 80, "duration": 300},  # 5 minutes
            "high_memory": {"threshold": 90, "duration": 300},
            "no_chargers": {"threshold": 0, "duration": 60},  # 1 minute
            "backend_down": {"threshold": 0, "duration": 120}  # 2 minutes
        }
        self.alert_history = {}
    
    def check_alert_rule(self, rule_name, current_value):
        """Check if alert rule is triggered"""
        rule = self.alert_rules[rule_name]
        threshold = rule["threshold"]
        duration = rule["duration"]
        
        # Check if threshold is exceeded
        if current_value > threshold:
            # Record alert start time
            if rule_name not in self.alert_history:
                self.alert_history[rule_name] = datetime.now()
            
            # Check if duration threshold is met
            alert_start = self.alert_history[rule_name]
            if datetime.now() - alert_start >= timedelta(seconds=duration):
                return True
        else:
            # Reset alert history if threshold is not exceeded
            if rule_name in self.alert_history:
                del self.alert_history[rule_name]
        
        return False
    
    def check_system_alerts(self):
        """Check system-level alerts"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            metrics = response.json()
            
            if 'system' in metrics:
                cpu_usage = float(metrics['system'].get('cpu_usage', '0').replace('%', ''))
                memory_usage = float(metrics['system'].get('memory_usage', '0').replace('%', ''))
                
                if self.check_alert_rule("high_cpu", cpu_usage):
                    self.send_alert("High CPU Usage", f"CPU usage is {cpu_usage}%")
                
                if self.check_alert_rule("high_memory", memory_usage):
                    self.send_alert("High Memory Usage", f"Memory usage is {memory_usage}%")
        except Exception as e:
            print(f"Failed to check system alerts: {e}")
    
    def check_connection_alerts(self):
        """Check connection alerts"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            metrics = response.json()
            
            if 'connections' in metrics:
                connected_chargers = metrics['connections'].get('connected_chargers', 0)
                
                if self.check_alert_rule("no_chargers", connected_chargers):
                    self.send_alert("No Chargers Connected", 
                                  f"Expected chargers but 0 are connected")
        except Exception as e:
            print(f"Failed to check connection alerts: {e}")
    
    def send_alert(self, subject, message):
        """Send alert (implement your preferred method)"""
        print(f"ALERT: {subject} - {message}")
        # Implement your alerting method here

# Usage example
alerting = AdvancedAlerting()
alerting.check_system_alerts()
alerting.check_connection_alerts()
```

## 📊 Log Analysis

### **Log Analysis Script**

```python
# log_analysis.py
import re
import json
from collections import defaultdict, Counter
from datetime import datetime, timedelta

class LogAnalyzer:
    def __init__(self, log_file="/opt/ocpp-broker/logs/broker.log"):
        self.log_file = log_file
        self.log_patterns = {
            "charger_connected": r"Charger (\w+) connected",
            "charger_disconnected": r"Charger (\w+) disconnected",
            "backend_connected": r"Backend (\w+) connected",
            "backend_disconnected": r"Backend (\w+) disconnected",
            "ocpp_message": r"OCPP (\w+) (\w+)",
            "error": r"ERROR.*?(\w+)"
        }
    
    def analyze_logs(self, hours=24):
        """Analyze logs for the last N hours"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        stats = {
            "charger_connections": defaultdict(int),
            "backend_connections": defaultdict(int),
            "ocpp_messages": Counter(),
            "errors": Counter(),
            "total_lines": 0
        }
        
        try:
            with open(self.log_file, 'r') as f:
                for line in f:
                    stats["total_lines"] += 1
                    
                    # Parse timestamp
                    timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    if timestamp_match:
                        log_time = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
                        if log_time < cutoff_time:
                            continue
                    
                    # Analyze patterns
                    for pattern_name, pattern in self.log_patterns.items():
                        matches = re.findall(pattern, line)
                        for match in matches:
                            if pattern_name == "charger_connected":
                                stats["charger_connections"][match] += 1
                            elif pattern_name == "backend_connected":
                                stats["backend_connections"][match] += 1
                            elif pattern_name == "ocpp_message":
                                stats["ocpp_messages"][f"{match[0]} {match[1]}"] += 1
                            elif pattern_name == "error":
                                stats["errors"][match] += 1
        
        except FileNotFoundError:
            print(f"Log file not found: {self.log_file}")
            return None
        
        return stats
    
    def print_analysis(self, hours=24):
        """Print log analysis"""
        stats = self.analyze_logs(hours)
        if not stats:
            return
        
        print(f"Log Analysis for last {hours} hours")
        print("=" * 50)
        print(f"Total log lines: {stats['total_lines']}")
        print()
        
        print("Charger Connections:")
        for charger, count in stats["charger_connections"].items():
            print(f"  {charger}: {count}")
        print()
        
        print("Backend Connections:")
        for backend, count in stats["backend_connections"].items():
            print(f"  {backend}: {count}")
        print()
        
        print("OCPP Messages (Top 10):")
        for message, count in stats["ocpp_messages"].most_common(10):
            print(f"  {message}: {count}")
        print()
        
        print("Errors (Top 10):")
        for error, count in stats["errors"].most_common(10):
            print(f"  {error}: {count}")

# Run analysis
analyzer = LogAnalyzer()
analyzer.print_analysis(24)
```

## 🔧 Monitoring Tools

### **System Monitoring Script**

```bash
#!/bin/bash
# monitor_system.sh

# Check if broker is running
if ! pgrep -f "ocpp_broker" > /dev/null; then
    echo "ALERT: OCPP Broker is not running"
    exit 1
fi

# Check port binding
if ! netstat -tlnp | grep 8765 > /dev/null; then
    echo "ALERT: Port 8765 is not bound"
    exit 1
fi

# Check health endpoint
if ! curl -s http://localhost:8765/health | grep -q "ok"; then
    echo "ALERT: Health check failed"
    exit 1
fi

echo "System is healthy"
```

### **Performance Monitoring**

```python
# performance_monitor.py
import time
import psutil
import requests
from datetime import datetime

class PerformanceMonitor:
    def __init__(self, base_url="http://localhost:8765"):
        self.base_url = base_url
        self.start_time = time.time()
    
    def get_performance_metrics(self):
        """Get performance metrics"""
        metrics = {
            "timestamp": datetime.now().isoformat(),
            "system": {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage('/').percent,
                "load_avg": psutil.getloadavg()
            },
            "application": {
                "uptime": time.time() - self.start_time,
                "connections": self.get_connection_count(),
                "message_rate": self.get_message_rate()
            }
        }
        return metrics
    
    def get_connection_count(self):
        """Get connection count"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            metrics = response.json()
            return metrics.get('connections', {})
        except:
            return {}
    
    def get_message_rate(self):
        """Get message rate"""
        try:
            response = requests.get(f"{self.base_url}/api/metrics")
            metrics = response.json()
            return metrics.get('messages', {}).get('rate_per_minute', 0)
        except:
            return 0
    
    def monitor_performance(self, interval=60):
        """Monitor performance continuously"""
        print("Starting performance monitoring...")
        print(f"Refresh interval: {interval} seconds")
        print("Press Ctrl+C to stop")
        print()
        
        try:
            while True:
                metrics = self.get_performance_metrics()
                
                print(f"Performance Metrics - {metrics['timestamp']}")
                print(f"  CPU: {metrics['system']['cpu_percent']}%")
                print(f"  Memory: {metrics['system']['memory_percent']}%")
                print(f"  Disk: {metrics['system']['disk_percent']}%")
                print(f"  Load: {metrics['system']['load_avg']}")
                print(f"  Uptime: {metrics['application']['uptime']:.0f}s")
                print(f"  Message Rate: {metrics['application']['message_rate']}/min")
                print()
                
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")

# Run performance monitor
monitor = PerformanceMonitor()
monitor.monitor_performance(30)
```

## 📚 Best Practices

### **1. Log Rotation**

```bash
# Configure logrotate
sudo nano /etc/logrotate.d/ocpp-broker

# Content:
/opt/ocpp-broker/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 ocpp-broker ocpp-broker
    postrotate
        systemctl reload ocpp-broker
    endscript
}
```

### **2. Monitoring Alerts**

```yaml
# alerting_rules.yaml
alerts:
  - name: "High CPU Usage"
    condition: "cpu_percent > 80"
    duration: "5m"
    severity: "warning"
  
  - name: "High Memory Usage"
    condition: "memory_percent > 90"
    duration: "5m"
    severity: "critical"
  
  - name: "No Chargers Connected"
    condition: "connected_chargers == 0"
    duration: "1m"
    severity: "critical"
  
  - name: "Backend Down"
    condition: "connected_backends == 0"
    duration: "2m"
    severity: "critical"
```

### **3. Dashboard Configuration**

```json
{
  "dashboard": {
    "title": "OCPP Broker Monitoring",
    "refresh": "30s",
    "panels": [
      {
        "title": "System Overview",
        "type": "stat",
        "targets": [
          {"expr": "cpu_percent"},
          {"expr": "memory_percent"},
          {"expr": "disk_percent"}
        ]
      },
      {
        "title": "Connections",
        "type": "graph",
        "targets": [
          {"expr": "ocpp_connections{type=\"chargers\"}"},
          {"expr": "ocpp_connections{type=\"backends\"}"}
        ]
      },
      {
        "title": "Message Rate",
        "type": "graph",
        "targets": [
          {"expr": "rate(ocpp_messages_total[5m])"}
        ]
      }
    ]
  }
}
```

## 🔗 Related Documentation

- [Production Deployment](deployment.md)
- [Troubleshooting](troubleshooting.md)
- [API Reference](api-reference.md)
- [Configuration Guide](configuration.md)

---

*Last updated: October 2024*
