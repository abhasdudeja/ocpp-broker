# OCPP Broker Documentation

Welcome to the OCPP Broker documentation! This wiki provides comprehensive information about the OCPP 1.6 broker implementation.

## 📚 Documentation Structure

### **Getting Started**
- [Installation Guide](installation.md) - How to install and set up the OCPP broker
- [Quick Start Guide](quick-start.md) - Get up and running in minutes
- [Configuration Guide](configuration.md) - Complete configuration reference

### **Core Features**
- [OCPP 1.6 Support](ocpp16-features.md) - Complete OCPP 1.6 implementation
- [Broker-as-Backend Mode](broker-as-backend.md) - Direct OCPP command processing
- [Leader-Follower Logic](leader-follower.md) - Multi-backend management
- [Message Routing](message-routing.md) - How messages are processed

### **Architecture**
- [System Architecture](architecture.md) - High-level system design
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
- ✅ **30 OCPP 1.6 Commands** across all profiles
- ✅ **Complete message validation**
- ✅ **Enhanced command routing**
- ✅ **Leader-follower logic**
- ✅ **Broker-as-backend mode**

### **Architecture**
- ✅ **Modular command handlers**
- ✅ **Extensible validation system**
- ✅ **Dual router support** (legacy + OCPP 1.6)
- ✅ **Backward compatibility**

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
