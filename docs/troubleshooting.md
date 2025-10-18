# Troubleshooting Guide

Common issues and solutions for the OCPP broker deployment.

## 🔍 General Troubleshooting

### **Check Service Status**

```bash
# Check if broker is running
sudo systemctl status ocpp-broker

# Check logs
sudo journalctl -u ocpp-broker -f

# Check port binding
sudo netstat -tlnp | grep 8765
```

### **Health Check**

```bash
# Test health endpoint
curl http://localhost:8765/health

# Test WebSocket connection
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001
```

## 🚨 Common Issues

### **Issue 1: Port Already in Use**

**Error:**
```
Address already in use: Port 8765
```

**Solution:**
```bash
# Find process using port
sudo lsof -i :8765

# Kill the process
sudo kill -9 <PID>

# Or change port in config.yaml
broker:
  port: 8766  # Use different port
```

### **Issue 2: Permission Denied**

**Error:**
```
Permission denied: Cannot bind to port 8765
```

**Solution:**
```bash
# Use sudo for ports < 1024
sudo python -m ocpp_broker.server

# Or change to port > 1024
broker:
  port: 8765  # Port > 1024
```

### **Issue 3: Configuration Not Found**

**Error:**
```
Configuration file not found
```

**Solution:**
```bash
# Create config.yaml
touch config.yaml

# Or specify config path
python -m ocpp_broker.server -c /path/to/config.yaml

# Or set environment variable
export OCPP_BROKER_CONFIG="/path/to/config.yaml"
```

### **Issue 4: Module Not Found**

**Error:**
```
ModuleNotFoundError: No module named 'ocpp_broker'
```

**Solution:**
```bash
# Install the package
pip install ocpp-broker

# Or install from source
pip install -e .

# Check Python path
python -c "import sys; print(sys.path)"
```

### **Issue 5: WebSocket Connection Failed**

**Error:**
```
WebSocket connection failed
```

**Solution:**
```bash
# Check if broker is running
curl http://localhost:8765/health

# Check firewall
sudo ufw status

# Check network connectivity
telnet localhost 8765
```

## 🔧 Configuration Issues

### **Issue 1: Invalid YAML Syntax**

**Error:**
```
YAML parsing error: line X, column Y
```

**Solution:**
```yaml
# Check YAML syntax
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Use online YAML validator
# https://www.yamllint.com/
```

### **Issue 2: Missing Required Fields**

**Error:**
```
Organization must have a name
```

**Solution:**
```yaml
# ❌ Wrong
organizations:
  - connect_to_backend: false

# ✅ Correct
organizations:
  - name: "MyOrganization"
    connect_to_backend: false
```

### **Issue 3: Invalid Backend Configuration**

**Error:**
```
Backend configuration invalid
```

**Solution:**
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

## 🌐 Network Issues

### **Issue 1: Connection Refused**

**Error:**
```
Connection refused: Cannot connect to broker
```

**Solution:**
```bash
# Check if broker is running
sudo systemctl status ocpp-broker

# Check port binding
sudo netstat -tlnp | grep 8765

# Check firewall
sudo ufw status
sudo ufw allow 8765/tcp
```

### **Issue 2: WebSocket Handshake Failed**

**Error:**
```
WebSocket handshake failed
```

**Solution:**
```bash
# Check subprotocol
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001 --subprotocol ocpp1.6

# Check CORS settings
curl -H "Origin: http://localhost:3000" http://localhost:8765/health
```

### **Issue 3: Timeout Issues**

**Error:**
```
Connection timeout
```

**Solution:**
```yaml
# Increase timeout in config.yaml
broker:
  timeout: 60  # Increase timeout
```

## 🔌 Backend Connection Issues

### **Issue 1: Backend Connection Failed**

**Error:**
```
Cannot connect to backend
```

**Solution:**
```bash
# Test backend connectivity
telnet backend.example.com 80

# Check backend URL
curl -I http://backend.example.com/health

# Verify WebSocket URL
wscat -c ws://backend.example.com/ocpp
```

### **Issue 2: Leader-Follower Issues**

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
      - id: "leader_backend"
        url: "ws://leader.com/ocpp"
        leader: true  # Must be true for command sending
      - id: "follower_backend"
        url: "ws://follower.com/ocpp"
        leader: false  # Cannot send commands
```

### **Issue 3: Backend Disconnection**

**Error:**
```
Backend connection lost
```

**Solution:**
```bash
# Check backend logs
sudo journalctl -u backend-service -f

# Restart backend connection
sudo systemctl restart ocpp-broker

# Check network connectivity
ping backend.example.com
```

## 📊 Performance Issues

### **Issue 1: High Memory Usage**

**Error:**
```
Out of memory
```

**Solution:**
```bash
# Check memory usage
free -h
ps aux --sort=-%mem | head

# Increase memory limits
echo "vm.max_map_count=262144" >> /etc/sysctl.conf
sysctl -p

# Restart with more memory
docker run -m 2g ocpp-broker:latest
```

### **Issue 2: High CPU Usage**

**Error:**
```
High CPU usage
```

**Solution:**
```bash
# Check CPU usage
top -p $(pgrep -f ocpp_broker)

# Optimize configuration
broker:
  worker_processes: 2  # Reduce worker processes
  worker_connections: 100  # Reduce connections
```

### **Issue 3: Slow Response Times**

**Error:**
```
Slow message processing
```

**Solution:**
```bash
# Check network latency
ping backend.example.com

# Optimize network settings
echo "net.core.somaxconn = 65536" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65536" >> /etc/sysctl.conf
sysctl -p
```

## 🔍 Debugging

### **Enable Debug Logging**

```bash
# Set debug level
export OCPP_BROKER_LOG_LEVEL=DEBUG

# Run with debug logging
python -m ocpp_broker.server --debug
```

### **Check Logs**

```bash
# View application logs
tail -f /opt/ocpp-broker/logs/broker.log

# View system logs
sudo journalctl -u ocpp-broker -f

# View Docker logs
docker logs -f ocpp-broker
```

### **Network Debugging**

```bash
# Check port binding
sudo netstat -tlnp | grep 8765

# Check connections
sudo ss -tulpn | grep 8765

# Test connectivity
telnet localhost 8765
```

### **WebSocket Debugging**

```bash
# Test WebSocket with wscat
wscat -c ws://localhost:8765/MyChargingStation/CHARGER_001

# Test with curl
curl -i -N -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
     -H "Sec-WebSocket-Version: 13" \
     http://localhost:8765/MyChargingStation/CHARGER_001
```

## 🐳 Docker Issues

### **Issue 1: Container Won't Start**

**Error:**
```
Container exited with code 1
```

**Solution:**
```bash
# Check container logs
docker logs ocpp-broker

# Check container status
docker ps -a

# Restart container
docker restart ocpp-broker
```

### **Issue 2: Volume Mount Issues**

**Error:**
```
Permission denied: Cannot access volume
```

**Solution:**
```bash
# Fix volume permissions
sudo chown -R 1000:1000 /path/to/volume

# Use named volumes
docker volume create ocpp-broker-config
```

### **Issue 3: Network Issues**

**Error:**
```
Cannot connect to host
```

**Solution:**
```bash
# Check network mode
docker network ls

# Use host network
docker run --network host ocpp-broker:latest

# Check port mapping
docker port ocpp-broker
```

## 🔧 Performance Tuning

### **System Optimization**

```bash
# Increase file descriptor limits
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# Optimize network settings
echo "net.core.somaxconn = 65536" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65536" >> /etc/sysctl.conf
sysctl -p
```

### **Application Optimization**

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765
  max_connections: 1000
  timeout: 30
  worker_processes: 4
  worker_connections: 1000
```

### **Database Optimization**

```sql
-- PostgreSQL optimization
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET default_statistics_target = 100;
```

## 📊 Monitoring

### **Health Checks**

```bash
# Check service health
curl http://localhost:8765/health

# Check metrics
curl http://localhost:8765/api/metrics

# Check connections
curl http://localhost:8765/api/connections
```

### **Log Analysis**

```bash
# Search for errors
grep -i error /opt/ocpp-broker/logs/broker.log

# Search for specific charger
grep "CHARGER_001" /opt/ocpp-broker/logs/broker.log

# Monitor real-time logs
tail -f /opt/ocpp-broker/logs/broker.log | grep ERROR
```

### **Performance Monitoring**

```bash
# Monitor CPU and memory
htop

# Monitor network connections
netstat -an | grep 8765

# Monitor disk usage
df -h
```

## 🚨 Emergency Procedures

### **Service Recovery**

```bash
# Restart service
sudo systemctl restart ocpp-broker

# Check status
sudo systemctl status ocpp-broker

# View logs
sudo journalctl -u ocpp-broker -f
```

### **Data Recovery**

```bash
# Restore configuration
cp /opt/backups/ocpp-broker/config_20241018.yaml /opt/ocpp-broker/config/config.yaml

# Restart service
sudo systemctl restart ocpp-broker
```

### **Emergency Shutdown**

```bash
# Stop service
sudo systemctl stop ocpp-broker

# Kill all processes
sudo pkill -f ocpp_broker

# Check for remaining processes
ps aux | grep ocpp_broker
```

## 📞 Support

### **Getting Help**

1. **Check Logs**: Review application and system logs
2. **Search Documentation**: Look for similar issues
3. **Community Support**: Post on GitHub Issues
4. **Professional Support**: Contact support team

### **Reporting Issues**

When reporting issues, include:

1. **Error Messages**: Complete error output
2. **Configuration**: Relevant config.yaml sections
3. **Logs**: Application and system logs
4. **Environment**: OS, Python version, deployment method
5. **Steps to Reproduce**: Detailed reproduction steps

### **Useful Commands**

```bash
# System information
uname -a
python --version
pip list | grep ocpp

# Service information
sudo systemctl status ocpp-broker
sudo journalctl -u ocpp-broker --since "1 hour ago"

# Network information
sudo netstat -tlnp | grep 8765
sudo ss -tulpn | grep 8765
```

## 🔗 Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [Quick Start Guide](quick-start.md)
- [Production Deployment](deployment.md)
- [API Reference](api-reference.md)

---

*Last updated: October 2024*
