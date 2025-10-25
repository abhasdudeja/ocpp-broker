# OCPP Tag Management

Complete guide to managing OCPP tags and authorization lists when the broker acts as a backend.

## 🎯 Overview

The OCPP broker now includes comprehensive tag management capabilities when acting as a backend. This feature allows you to:

- **Manage OCPP Tags**: Create, read, update, and delete authorization tags
- **Configure Tags via YAML**: Define tags directly in the configuration file
- **REST API Management**: Full CRUD operations via REST API
- **Import/Export**: Bulk operations and data migration
- **Real-time Authorization**: Automatic tag validation for OCPP Authorize commands
- **Statistics & Monitoring**: Track tag usage and authorization patterns

## 🔧 Configuration

### **Basic Tag Management Setup**

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765
  enable_tag_management: true  # 🎯 Enable tag management

# Global tag management settings
tag_management:
  global:
    enabled: true
    default_status: "Accepted"
    default_tag_type: "RFID"
    max_tags_per_org: 10000
    auto_cleanup_expired: true

organizations:
  - name: "MyChargingStation"
    connect_to_backend: false  # Broker acts as backend
    tag_management:
      enabled: true
      auto_authorize: true
      validation_strict: true
    # Pre-configured tags
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"
        tag_type: "RFID"
        description: "Administrator access"
        metadata:
          role: "admin"
          department: "IT"
      - id_tag: "USER123456"
        status: "Accepted"
        tag_type: "RFID"
        expiry_date: "2024-12-31T23:59:59Z"
        description: "Employee access"
        metadata:
          role: "employee"
          department: "Engineering"
      - id_tag: "GUEST001"
        status: "Accepted"
        tag_type: "QRCode"
        expiry_date: "2024-01-31T23:59:59Z"
        description: "Guest access"
        metadata:
          role: "guest"
          access_level: "limited"
    chargers:
      - "CHARGER_001"
      - "CHARGER_002"
```

### **Advanced Tag Management Configuration**

```yaml
# Enhanced configuration with full tag management features
tag_management:
  global:
    enabled: true
    default_status: "Accepted"
    default_tag_type: "RFID"
    max_tags_per_org: 10000
    tag_id_length_min: 1
    tag_id_length_max: 20
    auto_cleanup_expired: true
    cleanup_interval: 86400  # 24 hours
  
  validation:
    strict_mode: true
    validate_expiry_dates: true
    validate_parent_tags: true
    allow_duplicate_ids: false
    require_description: false
  
  import_export:
    supported_formats: ["json", "csv", "xml"]
    max_import_size: 10485760  # 10MB
    export_include_metadata: true
    export_include_timestamps: true
  
  monitoring:
    enable_statistics: true
    statistics_interval: 3600  # 1 hour
    log_tag_operations: true
    log_authorization_attempts: true

organizations:
  - name: "ProductionCharging"
    connect_to_backend: false
    tag_management:
      enabled: true
      auto_authorize: true
      validation_strict: true
      cache_timeout: 3600
    tags:
      - id_tag: "PROD_ADMIN"
        status: "Accepted"
        tag_type: "RFID"
        description: "Production administrator"
        metadata:
          role: "admin"
          level: "production"
      - id_tag: "PROD_USER"
        status: "Accepted"
        tag_type: "RFID"
        expiry_date: "2025-12-31T23:59:59Z"
        description: "Production user"
        metadata:
          role: "user"
          level: "production"
      - id_tag: "BLOCKED_USER"
        status: "Blocked"
        tag_type: "RFID"
        description: "Blocked user"
        metadata:
          role: "user"
          reason: "security_violation"
    chargers:
      - "PROD_001"
      - "PROD_002"
```

## 🚀 Features

### **1. Tag Types and Status**

#### **Supported Tag Types**
- `RFID` - Radio Frequency ID cards
- `NFC` - Near Field Communication
- `QRCode` - QR Code scanning
- `MobileApp` - Mobile application
- `UserId` - User ID authentication

#### **Tag Status Values**
- `Accepted` - Tag is authorized for charging
- `Blocked` - Tag is blocked from charging
- `Expired` - Tag has expired
- `Invalid` - Tag is invalid or not found
- `ConcurrentTx` - Tag is already in use

### **2. Tag Configuration**

#### **Basic Tag Definition**
```yaml
tags:
  - id_tag: "USER123456"           # Required: Unique tag identifier
    status: "Accepted"             # Required: Tag status
    tag_type: "RFID"               # Optional: Tag type (default: RFID)
    expiry_date: "2024-12-31T23:59:59Z"  # Optional: Expiry date
    parent_id_tag: "ADMIN001"       # Optional: Parent tag for hierarchy
    description: "Employee access" # Optional: Human-readable description
    metadata:                      # Optional: Additional data
      role: "employee"
      department: "Engineering"
      access_level: "standard"
```

#### **Tag Hierarchy**
```yaml
tags:
  - id_tag: "ADMIN001"
    status: "Accepted"
    tag_type: "RFID"
    description: "Administrator tag"
    metadata:
      role: "admin"
      level: "super"
  
  - id_tag: "USER123456"
    status: "Accepted"
    tag_type: "RFID"
    parent_id_tag: "ADMIN001"  # Child of ADMIN001
    description: "Employee under admin"
    metadata:
      role: "employee"
      parent: "ADMIN001"
```

### **3. REST API Management**

#### **Tag CRUD Operations**

**Add a new tag:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags" \
  -H "Content-Type: application/json" \
  -d '{
    "id_tag": "NEW_USER",
    "status": "Accepted",
    "tag_type": "RFID",
    "description": "New user access",
    "metadata": {
      "role": "user",
      "department": "Sales"
    }
  }'
```

**Get a specific tag:**
```bash
curl "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/USER123456"
```

**Update a tag:**
```bash
curl -X PUT "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/USER123456" \
  -H "Content-Type: application/json" \
  -d '{
    "id_tag": "USER123456",
    "status": "Blocked",
    "tag_type": "RFID",
    "description": "Blocked user access"
  }'
```

**Delete a tag:**
```bash
curl -X DELETE "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/USER123456"
```

#### **Tag Search and Filtering**

**Search tags with filters:**
```bash
curl "http://localhost:8080/api/tags/organizations/MyChargingStation/tags?status=Accepted&tag_type=RFID&limit=50"
```

**Get tag statistics:**
```bash
curl "http://localhost:8080/api/tags/organizations/MyChargingStation/statistics"
```

#### **Bulk Operations**

**Bulk add tags:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/bulk" \
  -H "Content-Type: application/json" \
  -d '{
    "operation": "add",
    "tags": [
      {
        "id_tag": "BULK001",
        "status": "Accepted",
        "tag_type": "RFID",
        "description": "Bulk user 1"
      },
      {
        "id_tag": "BULK002",
        "status": "Accepted",
        "tag_type": "RFID",
        "description": "Bulk user 2"
      }
    ]
  }'
```

#### **Import/Export Operations**

**Import tags from JSON:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/import" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "json",
    "data": "{\"tags\": [{\"id_tag\": \"IMPORT001\", \"status\": \"Accepted\", \"tag_type\": \"RFID\"}]}",
    "overwrite_existing": false
  }'
```

**Export tags to CSV:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/export" \
  -H "Content-Type: application/json" \
  -d '{
    "format": "csv",
    "include_metadata": true
  }' --output tags.csv
```

### **4. OCPP Integration**

#### **Automatic Authorization**

When a charger sends an `Authorize` command, the broker automatically:

1. **Looks up the tag** in the organization's tag list
2. **Validates the tag** (status, expiry, etc.)
3. **Returns authorization result** with proper OCPP response

**Example OCPP Authorize Flow:**
```json
// Charger sends Authorize request
[2, "12345", "Authorize", {
  "idTag": "USER123456"
}]

// Broker responds with tag information
[3, "12345", {
  "idTagInfo": {
    "status": "Accepted",
    "expiryDate": "2024-12-31T23:59:59Z",
    "parentIdTag": "ADMIN001"
  }
}]
```

#### **Tag Validation**

The broker performs comprehensive tag validation:

- **Format validation**: ID tag format and length
- **Status validation**: Check if tag is active
- **Expiry validation**: Check if tag has expired
- **Hierarchy validation**: Validate parent-child relationships
- **Duplicate validation**: Ensure unique tag IDs

### **5. Monitoring and Statistics**

#### **Tag Statistics API**

```bash
curl "http://localhost:8080/api/tags/organizations/MyChargingStation/statistics"
```

**Response:**
```json
{
  "total_tags": 150,
  "active_tags": 120,
  "expired_tags": 20,
  "blocked_tags": 10,
  "tags_by_type": {
    "RFID": 100,
    "QRCode": 30,
    "MobileApp": 20
  },
  "tags_by_status": {
    "Accepted": 120,
    "Blocked": 10,
    "Expired": 20
  }
}
```

#### **Tag Management Status**

```bash
curl "http://localhost:8080/api/tags/status"
```

**Response:**
```json
{
  "enabled": true,
  "message": "Tag management is active",
  "organizations": ["MyChargingStation", "ProductionCharging"]
}
```

## 🔄 Use Cases

### **1. Employee Access Management**

```yaml
organizations:
  - name: "CompanyCharging"
    connect_to_backend: false
    tag_management:
      enabled: true
      auto_authorize: true
    tags:
      - id_tag: "EMP001"
        status: "Accepted"
        tag_type: "RFID"
        description: "Employee John Doe"
        metadata:
          employee_id: "EMP001"
          department: "Engineering"
          access_level: "standard"
      - id_tag: "EMP002"
        status: "Accepted"
        tag_type: "RFID"
        description: "Employee Jane Smith"
        metadata:
          employee_id: "EMP002"
          department: "Sales"
          access_level: "premium"
```

### **2. Guest Access Management**

```yaml
organizations:
  - name: "PublicCharging"
    connect_to_backend: false
    tag_management:
      enabled: true
      auto_authorize: true
    tags:
      - id_tag: "GUEST001"
        status: "Accepted"
        tag_type: "QRCode"
        expiry_date: "2024-01-31T23:59:59Z"
        description: "Guest access - 30 days"
        metadata:
          access_type: "guest"
          duration: "30_days"
          location: "main_entrance"
```

### **3. Fleet Management**

```yaml
organizations:
  - name: "FleetCharging"
    connect_to_backend: false
    tag_management:
      enabled: true
      auto_authorize: true
    tags:
      - id_tag: "FLEET001"
        status: "Accepted"
        tag_type: "RFID"
        description: "Fleet vehicle 001"
        metadata:
          vehicle_id: "FLEET001"
          vehicle_type: "delivery_van"
          driver: "John Doe"
          route: "downtown"
      - id_tag: "FLEET002"
        status: "Accepted"
        tag_type: "RFID"
        description: "Fleet vehicle 002"
        metadata:
          vehicle_id: "FLEET002"
          vehicle_type: "truck"
          driver: "Jane Smith"
          route: "suburbs"
```

## 🛠️ Advanced Features

### **1. Tag Expiry Management**

```yaml
tags:
  - id_tag: "TEMP_USER"
    status: "Accepted"
    tag_type: "RFID"
    expiry_date: "2024-01-31T23:59:59Z"  # Auto-expires
    description: "Temporary access"
    metadata:
      access_type: "temporary"
      duration: "30_days"
```

### **2. Tag Hierarchy**

```yaml
tags:
  - id_tag: "ADMIN_ROOT"
    status: "Accepted"
    tag_type: "RFID"
    description: "Root administrator"
    metadata:
      role: "super_admin"
      level: "root"
  
  - id_tag: "ADMIN_DEPT"
    status: "Accepted"
    tag_type: "RFID"
    parent_id_tag: "ADMIN_ROOT"
    description: "Department administrator"
    metadata:
      role: "dept_admin"
      level: "department"
      parent: "ADMIN_ROOT"
  
  - id_tag: "USER_STANDARD"
    status: "Accepted"
    tag_type: "RFID"
    parent_id_tag: "ADMIN_DEPT"
    description: "Standard user"
    metadata:
      role: "user"
      level: "standard"
      parent: "ADMIN_DEPT"
```

### **3. Bulk Tag Operations**

**Import from CSV:**
```csv
id_tag,status,tag_type,description,metadata
USER001,Accepted,RFID,User 1,"{""role"":""user"",""dept"":""eng""}"
USER002,Accepted,RFID,User 2,"{""role"":""user"",""dept"":""sales""}"
USER003,Blocked,RFID,Blocked User,"{""role"":""user"",""reason"":""violation""}"
```

**Export to JSON:**
```json
{
  "organization": "MyChargingStation",
  "exported_at": "2024-01-15T10:30:00Z",
  "tag_count": 3,
  "tags": [
    {
      "id_tag": "USER001",
      "status": "Accepted",
      "tag_type": "RFID",
      "description": "User 1",
      "metadata": {
        "role": "user",
        "dept": "eng"
      }
    }
  ]
}
```

## 🔍 Troubleshooting

### **Common Issues**

#### **1. Tag Not Found**
```json
{
  "idTagInfo": {
    "status": "Invalid",
    "expiryDate": null,
    "parentIdTag": null
  }
}
```

**Solutions:**
- Check if tag exists in organization
- Verify tag ID spelling
- Ensure organization has tag management enabled

#### **2. Tag Expired**
```json
{
  "idTagInfo": {
    "status": "Expired",
    "expiryDate": "2023-12-31T23:59:59Z",
    "parentIdTag": null
  }
}
```

**Solutions:**
- Update tag expiry date
- Create new tag with valid expiry
- Check system time synchronization

#### **3. Tag Blocked**
```json
{
  "idTagInfo": {
    "status": "Blocked",
    "expiryDate": null,
    "parentIdTag": null
  }
}
```

**Solutions:**
- Check tag status in configuration
- Update tag status to "Accepted"
- Review tag metadata for blocking reason

### **Debugging Commands**

**Check tag management status:**
```bash
curl "http://localhost:8080/api/tags/status"
```

**Validate a specific tag:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "id_tag": "USER123456",
    "status": "Accepted",
    "tag_type": "RFID"
  }'
```

**Test tag authorization:**
```bash
curl -X POST "http://localhost:8080/api/tags/organizations/MyChargingStation/tags/authorize" \
  -H "Content-Type: application/json" \
  -d '"USER123456"'
```

## 📚 Related Documentation

- [Broker-as-Backend Mode](broker-as-backend.md)
- [OCPP 1.6 Features](ocpp16-features.md)
- [API Reference](api-reference.md)
- [Configuration Guide](configuration.md)
- [Troubleshooting](troubleshooting.md)

---

*Last updated: October 2024*
