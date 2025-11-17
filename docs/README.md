# OCPP Broker Documentation

Welcome to the OCPP Broker documentation! This wiki provides comprehensive information about the OCPP 1.6 broker implementation.

## 📚 Documentation Structure

### **Getting Started**
- [Installation Guide](installation.md) - How to install and set up the OCPP broker
- [Quick Start Guide](quick-start.md) - Get up and running in minutes
- [Configuration Guide](configuration.md) - Complete configuration reference

### **Core Features**
- [OCPP 1.6 Support](ocpp16-features.md) - Complete OCPP 1.6 implementation
- [Tag Management](tag-management.md) - Comprehensive tag management for authorization
- [Broker-as-Backend Mode](broker-as-backend.md) - Direct OCPP command processing
- [Leader-Follower Logic](leader-follower.md) - Multi-backend management
- [Message Routing](message-routing.md) - How messages are processed

### **Architecture**
- [Architecture Overview](architecture.md) - Broker, session, and service layout
- [Command Handlers](command-handlers.md) - OCPP command processing
- [Message Validation](message-validation.md) - OCPP message validation
- [WebSocket Management](websocket-management.md) - Connection handling

### **API Reference**
- [REST API](api-reference.md) - REST API endpoints
- [WebSocket API](websocket-api.md) - WebSocket protocol
- [Configuration API](config-api.md) - Configuration management

### **Deployment**
- [Production Deployment](deployment.md) - Production setup guide
- [Docker Deployment](docker.md) - Container deployment
- [Monitoring & Logging](monitoring.md) - System monitoring
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

### **Development**
- [Development Setup](development.md) - Development environment
- [Contributing](contributing.md) - How to contribute
- [Testing](testing.md) - Testing guidelines
- [Code Style](code-style.md) - Coding standards

### **Testing**
- Install dev deps with `pip install .[tests]`
- Run `pytest` to execute async unit tests (e.g., `tests/test_charge_point.py`)

### **Examples**
- [Basic Examples](examples/basic.md) - Simple usage examples
- [Advanced Examples](examples/advanced.md) - Complex scenarios
- [Integration Examples](examples/integration.md) - Third-party integrations

## 🚀 Quick Navigation

| Topic | Description | Link |
|-------|-------------|------|
| **Installation** | Set up the OCPP broker | [Installation Guide](installation.md) |
| **Configuration** | Configure organizations and backends | [Configuration Guide](configuration.md) |
| **OCPP 1.6** | Complete OCPP 1.6 command support | [OCPP 1.6 Features](ocpp16-features.md) |
| **Broker-as-Backend** | Direct OCPP command processing | [Broker-as-Backend Mode](broker-as-backend.md) |
| **API Reference** | REST and WebSocket APIs | [API Reference](api-reference.md) |
| **Deployment** | Production deployment guide | [Production Deployment](deployment.md) |

## 📖 Key Features

### **OCPP 1.6 Support**
- ✅ Powered by the upstream [`ocpp`](https://pypi.org/project/ocpp/) Python library
- ✅ Spec-compliant parsing, validation, and response generation
- ✅ Pass-through (broker-as-proxy) and broker-as-backend modes
- ✅ Config-driven leader/follower backend links
- ✅ Tag management and authorization workflows

### **Architecture**
- ✅ `BrokerChargePoint` subclass for all locally handled chargers
- ✅ Lightweight Starlette → `ocpp` WebSocket adapter
- ✅ Clean separation between pass-through relay and local command handling
- ✅ Backwards-compatible REST/tag management APIs

### **Production Ready**
- ✅ **Error handling**
- ✅ **Logging and monitoring**
- ✅ **Configuration management**
- ✅ **Docker support**

## 🔗 External Links

- [OCPP 1.6 Specification](https://www.openchargealliance.org/protocols/ocpp-16/)
- [Open Charge Alliance](https://www.openchargealliance.org/)
- [GitHub Repository](https://github.com/your-org/ocpp-broker)
- [Issue Tracker](https://github.com/your-org/ocpp-broker/issues)

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Support

- **Documentation**: [GitHub Wiki](https://github.com/your-org/ocpp-broker/wiki)
- **Issues**: [GitHub Issues](https://github.com/your-org/ocpp-broker/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/ocpp-broker/discussions)
- **Email**: support@your-org.com

---

*Last updated: October 2024*
