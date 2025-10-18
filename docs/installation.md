# Installation Guide

This guide will help you install and set up the OCPP Broker on your system.

## 📋 Prerequisites

### **System Requirements**
- **Python**: 3.8 or higher
- **Operating System**: Windows, macOS, or Linux
- **Memory**: 512MB RAM minimum (2GB recommended)
- **Storage**: 100MB free space

### **Dependencies**
- Python 3.8+
- pip (Python package manager)
- Git (for development)

## 🚀 Installation Methods

### **Method 1: PyPI Installation (Recommended)**

```bash
# Install from PyPI
pip install ocpp-broker

# Verify installation
python -m ocpp_broker.server --help
```

### **Method 2: Source Installation**

```bash
# Clone the repository
git clone https://github.com/your-org/ocpp-broker.git
cd ocpp-broker

# Install in development mode
pip install -e .

# Or install dependencies manually
pip install -r requirements.txt
```

### **Method 3: Docker Installation**

```bash
# Pull the Docker image
docker pull your-org/ocpp-broker:latest

# Run the container
docker run -p 8765:8765 -v $(pwd)/config.yaml:/app/config.yaml your-org/ocpp-broker
```

## 🔧 Configuration Setup

### **1. Create Configuration File**

Create a `config.yaml` file in your working directory:

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "MyOrganization"
    connect_to_backend: true
    backends:
      - id: "backend1"
        url: "ws://your-backend.com/ocpp"
        leader: true
        chargers:
          - "CHARGER_001"
          - "CHARGER_002"
```

### **2. Environment Variables (Optional)**

```bash
# Set custom configuration path
export OCPP_BROKER_CONFIG="/path/to/your/config.yaml"

# Set log level
export OCPP_BROKER_LOG_LEVEL="INFO"
```

## 🏃‍♂️ Quick Start

### **1. Start the Broker**

```bash
# Using Python module
python -m ocpp_broker.server

# Using custom config
python -m ocpp_broker.server -c /path/to/config.yaml

# Using Docker
docker run -p 8765:8765 -v $(pwd)/config.yaml:/app/config.yaml your-org/ocpp-broker
```

### **2. Verify Installation**

```bash
# Check if broker is running
curl http://localhost:8765/health

# Expected response
{"status": "ok"}
```

### **3. Test Connection**

```bash
# Test WebSocket connection
wscat -c ws://localhost:8765/orgA/CHARGER_001
```

## 🐳 Docker Installation

### **Docker Compose Setup**

Create a `docker-compose.yml` file:

```yaml
version: '3.8'
services:
  ocpp-broker:
    image: your-org/ocpp-broker:latest
    ports:
      - "8765:8765"
    volumes:
      - ./config.yaml:/app/config.yaml
    environment:
      - OCPP_BROKER_LOG_LEVEL=INFO
    restart: unless-stopped

  # Optional: Add a backend service
  backend:
    image: your-org/ocpp-backend:latest
    ports:
      - "9000:9000"
    environment:
      - BACKEND_PORT=9000
```

Run with Docker Compose:

```bash
docker-compose up -d
```

### **Docker Build (Development)**

```bash
# Build from source
docker build -t ocpp-broker:dev .

# Run development container
docker run -p 8765:8765 -v $(pwd):/app ocpp-broker:dev
```

## 🔧 Development Installation

### **1. Clone Repository**

```bash
git clone https://github.com/your-org/ocpp-broker.git
cd ocpp-broker
```

### **2. Create Virtual Environment**

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### **3. Install Dependencies**

```bash
# Install development dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Install in development mode
pip install -e .
```

### **4. Run Tests**

```bash
# Run all tests
python -m pytest

# Run specific test
python -m pytest tests/test_broker.py

# Run with coverage
python -m pytest --cov=src/ocpp_broker
```

## 📦 Package Dependencies

### **Core Dependencies**
```
fastapi>=0.100.0
uvicorn>=0.20.0
websockets>=11.0.0
pydantic>=2.0.0
pyyaml>=6.0
```

### **Development Dependencies**
```
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0.0
black>=23.0.0
flake8>=6.0.0
mypy>=1.0.0
```

## 🔍 Verification

### **Check Installation**

```bash
# Check Python version
python --version

# Check installed packages
pip list | grep ocpp-broker

# Check broker version
python -m ocpp_broker.server --version
```

### **Test Configuration**

```bash
# Validate configuration
python -c "from ocpp_broker.config import load_config; print(load_config())"

# Test broker startup
python -m ocpp_broker.server --dry-run
```

## 🚨 Troubleshooting

### **Common Issues**

#### **1. Port Already in Use**
```bash
# Error: Address already in use
# Solution: Change port in config.yaml
broker:
  port: 8766  # Use different port
```

#### **2. Permission Denied**
```bash
# Error: Permission denied
# Solution: Use sudo or change port
sudo python -m ocpp_broker.server
# Or change to port > 1024
```

#### **3. Module Not Found**
```bash
# Error: No module named 'ocpp_broker'
# Solution: Install the package
pip install -e .
```

#### **4. Configuration Not Found**
```bash
# Error: Configuration file not found
# Solution: Create config.yaml or specify path
python -m ocpp_broker.server -c /path/to/config.yaml
```

### **Debug Mode**

```bash
# Run with debug logging
OCPP_BROKER_LOG_LEVEL=DEBUG python -m ocpp_broker.server

# Run with verbose output
python -m ocpp_broker.server --verbose
```

## 📚 Next Steps

After installation, proceed to:

1. [Configuration Guide](configuration.md) - Set up your broker configuration
2. [Quick Start Guide](quick-start.md) - Get your first charger connected
3. [OCPP 1.6 Features](ocpp16-features.md) - Learn about OCPP 1.6 support
4. [API Reference](api-reference.md) - Explore the API endpoints

## 🔗 Related Documentation

- [Configuration Guide](configuration.md)
- [Quick Start Guide](quick-start.md)
- [Troubleshooting](troubleshooting.md)
- [Development Setup](development.md)

---

*Last updated: October 2024*
