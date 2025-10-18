# Production Deployment

Complete guide for deploying the OCPP broker in production environments.

## 🚀 Deployment Overview

### **Deployment Options**

1. **Single Instance** - Simple deployment for small to medium scale
2. **High Availability** - Multi-instance deployment with load balancing
3. **Microservices** - Containerized deployment with service separation
4. **Cloud Deployment** - Cloud-native deployment with auto-scaling

## 🏗️ Single Instance Deployment

### **System Requirements**

- **CPU**: 2 cores minimum (4 cores recommended)
- **Memory**: 2GB RAM minimum (8GB recommended)
- **Storage**: 10GB free space
- **Network**: 100Mbps bandwidth
- **OS**: Linux (Ubuntu 20.04+), Windows Server 2019+, or macOS 10.15+

### **Installation Steps**

#### **1. System Preparation**

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python 3.8+
sudo apt install python3.8 python3.8-venv python3-pip -y

# Create application user
sudo useradd -m -s /bin/bash ocpp-broker
sudo usermod -aG sudo ocpp-broker
```

#### **2. Application Installation**

```bash
# Switch to application user
sudo su - ocpp-broker

# Create application directory
mkdir -p /opt/ocpp-broker
cd /opt/ocpp-broker

# Create virtual environment
python3.8 -m venv venv
source venv/bin/activate

# Install OCPP broker
pip install ocpp-broker

# Create configuration directory
mkdir -p /opt/ocpp-broker/config
mkdir -p /opt/ocpp-broker/logs
```

#### **3. Configuration Setup**

```yaml
# /opt/ocpp-broker/config/config.yaml
broker:
  host: 0.0.0.0
  port: 8765
  log_level: "INFO"
  log_file: "/opt/ocpp-broker/logs/broker.log"

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

#### **4. Systemd Service**

```ini
# /etc/systemd/system/ocpp-broker.service
[Unit]
Description=OCPP Broker Service
After=network.target

[Service]
Type=simple
User=ocpp-broker
Group=ocpp-broker
WorkingDirectory=/opt/ocpp-broker
Environment=PATH=/opt/ocpp-broker/venv/bin
ExecStart=/opt/ocpp-broker/venv/bin/python -m ocpp_broker.server
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### **5. Service Management**

```bash
# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable ocpp-broker
sudo systemctl start ocpp-broker

# Check service status
sudo systemctl status ocpp-broker

# View logs
sudo journalctl -u ocpp-broker -f
```

## 🔄 High Availability Deployment

### **Architecture Overview**

```
┌─────────────────────────────────────────────────────────────┐
│                High Availability Cluster                    │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Load          │  │   OCPP          │  │   OCPP       │ │
│  │   Balancer      │  │   Broker 1      │  │   Broker 2   │ │
│  │   (HAProxy)     │  │   (Primary)     │  │   (Secondary)│ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │   Database      │  │   Redis         │  │   Monitoring │ │
│  │   Cluster       │  │   Cluster      │  │   System     │ │
│  └─────────────────┘  └─────────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### **Load Balancer Configuration**

#### **HAProxy Configuration**

```haproxy
# /etc/haproxy/haproxy.cfg
global
    daemon
    user haproxy
    group haproxy
    log 127.0.0.1:514 local0

defaults
    mode http
    timeout connect 5000ms
    timeout client 50000ms
    timeout server 50000ms
    option httplog

frontend ocpp_frontend
    bind *:8765
    default_backend ocpp_backend

backend ocpp_backend
    balance roundrobin
    option httpchk GET /health
    server ocpp1 192.168.1.10:8765 check
    server ocpp2 192.168.1.11:8765 check
```

#### **Nginx Configuration**

```nginx
# /etc/nginx/sites-available/ocpp-broker
upstream ocpp_backend {
    server 192.168.1.10:8765;
    server 192.168.1.11:8765;
}

server {
    listen 80;
    server_name ocpp-broker.example.com;

    location / {
        proxy_pass http://ocpp_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### **Database Setup**

#### **PostgreSQL Configuration**

```sql
-- Create database
CREATE DATABASE ocpp_broker;

-- Create user
CREATE USER ocpp_broker WITH PASSWORD 'secure_password';

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE ocpp_broker TO ocpp_broker;
```

#### **Redis Configuration**

```redis
# /etc/redis/redis.conf
bind 127.0.0.1
port 6379
requirepass secure_password
maxmemory 256mb
maxmemory-policy allkeys-lru
```

## 🐳 Docker Deployment

### **Docker Compose Setup**

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
      - ./logs:/app/logs
    restart: unless-stopped
    depends_on:
      - redis
      - postgres

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=ocpp_broker
      - POSTGRES_USER=ocpp_broker
      - POSTGRES_PASSWORD=secure_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    depends_on:
      - ocpp-broker
    restart: unless-stopped

volumes:
  redis_data:
  postgres_data:
```

### **Dockerfile**

```dockerfile
# Dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY config.yaml .

# Create logs directory
RUN mkdir -p logs

# Create non-root user
RUN useradd -m -u 1000 ocpp-broker
USER ocpp-broker

# Expose port
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

# Start application
CMD ["python", "-m", "ocpp_broker.server"]
```

### **Docker Commands**

```bash
# Build image
docker build -t ocpp-broker:latest .

# Run container
docker run -d \
  --name ocpp-broker \
  -p 8765:8765 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  ocpp-broker:latest

# View logs
docker logs -f ocpp-broker

# Stop container
docker stop ocpp-broker
```

## ☁️ Cloud Deployment

### **AWS Deployment**

#### **EC2 Instance Setup**

```bash
# Launch EC2 instance
aws ec2 run-instances \
  --image-id ami-0c02fb55956c7d316 \
  --instance-type t3.medium \
  --key-name your-key-pair \
  --security-group-ids sg-12345678 \
  --subnet-id subnet-12345678

# Install Docker
sudo yum update -y
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
```

#### **ECS Task Definition**

```json
{
  "family": "ocpp-broker",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "ocpp-broker",
      "image": "your-org/ocpp-broker:latest",
      "portMappings": [
        {
          "containerPort": 8765,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "OCPP_BROKER_CONFIG",
          "value": "/app/config.yaml"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/ocpp-broker",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

### **Kubernetes Deployment**

#### **Deployment Manifest**

```yaml
# k8s-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ocpp-broker
  labels:
    app: ocpp-broker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: ocpp-broker
  template:
    metadata:
      labels:
        app: ocpp-broker
    spec:
      containers:
      - name: ocpp-broker
        image: your-org/ocpp-broker:latest
        ports:
        - containerPort: 8765
        env:
        - name: OCPP_BROKER_CONFIG
          value: "/app/config.yaml"
        volumeMounts:
        - name: config-volume
          mountPath: /app/config.yaml
          subPath: config.yaml
      volumes:
      - name: config-volume
        configMap:
          name: ocpp-broker-config
---
apiVersion: v1
kind: Service
metadata:
  name: ocpp-broker-service
spec:
  selector:
    app: ocpp-broker
  ports:
  - port: 8765
    targetPort: 8765
  type: LoadBalancer
```

#### **ConfigMap**

```yaml
# k8s-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ocpp-broker-config
data:
  config.yaml: |
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

## 📊 Monitoring Setup

### **Prometheus Configuration**

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'ocpp-broker'
    static_configs:
      - targets: ['localhost:9090']
    metrics_path: '/metrics'
    scrape_interval: 5s
```

### **Grafana Dashboard**

```json
{
  "dashboard": {
    "title": "OCPP Broker Dashboard",
    "panels": [
      {
        "title": "Active Connections",
        "type": "stat",
        "targets": [
          {
            "expr": "ocpp_broker_active_connections"
          }
        ]
      },
      {
        "title": "Message Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(ocpp_broker_messages_total[5m])"
          }
        ]
      }
    ]
  }
}
```

### **Log Aggregation**

#### **ELK Stack Setup**

```yaml
# docker-compose.logging.yml
version: '3.8'

services:
  elasticsearch:
    image: elasticsearch:8.8.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
    ports:
      - "9200:9200"
    volumes:
      - elasticsearch_data:/usr/share/elasticsearch/data

  logstash:
    image: logstash:8.8.0
    volumes:
      - ./logstash.conf:/usr/share/logstash/pipeline/logstash.conf
    ports:
      - "5044:5044"
    depends_on:
      - elasticsearch

  kibana:
    image: kibana:8.8.0
    ports:
      - "5601:5601"
    depends_on:
      - elasticsearch

volumes:
  elasticsearch_data:
```

## 🔒 Security Configuration

### **SSL/TLS Setup**

```nginx
# nginx-ssl.conf
server {
    listen 443 ssl http2;
    server_name ocpp-broker.example.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;

    location / {
        proxy_pass http://ocpp_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### **Firewall Configuration**

```bash
# UFW configuration
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8765/tcp
sudo ufw enable
```

### **Security Headers**

```nginx
# security-headers.conf
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'" always;
```

## 🚨 Backup and Recovery

### **Configuration Backup**

```bash
#!/bin/bash
# backup-config.sh

BACKUP_DIR="/opt/backups/ocpp-broker"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup configuration
cp /opt/ocpp-broker/config/config.yaml $BACKUP_DIR/config_$DATE.yaml

# Backup logs
tar -czf $BACKUP_DIR/logs_$DATE.tar.gz /opt/ocpp-broker/logs/

# Cleanup old backups (keep 30 days)
find $BACKUP_DIR -name "*.yaml" -mtime +30 -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete
```

### **Database Backup**

```bash
#!/bin/bash
# backup-database.sh

BACKUP_DIR="/opt/backups/database"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup PostgreSQL
pg_dump -h localhost -U ocpp_broker ocpp_broker > $BACKUP_DIR/ocpp_broker_$DATE.sql

# Backup Redis
redis-cli --rdb $BACKUP_DIR/redis_$DATE.rdb
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

### **Application Tuning**

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

## 📚 Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [Monitoring & Logging](monitoring.md)
- [Troubleshooting](troubleshooting.md)
- [API Reference](api-reference.md)

---

*Last updated: October 2024*
