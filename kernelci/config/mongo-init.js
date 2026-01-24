// =============================================================================
// MongoDB Initialization Script for OpenWrt KernelCI
// =============================================================================
//
// This script runs once when the MongoDB container is first initialized.
// It creates the database, collections, and indexes needed for KernelCI.
//
// =============================================================================

// Switch to the openwrt_kernelci database
db = db.getSiblingDB('openwrt_kernelci');

// =============================================================================
// Collections for KernelCI Core
// =============================================================================

// Users collection (managed by kernelci-api)
db.createCollection('users');
db.users.createIndex({ "email": 1 }, { unique: true });
db.users.createIndex({ "username": 1 }, { unique: true });

// Nodes collection (firmware, jobs, tests)
db.createCollection('nodes');
db.nodes.createIndex({ "id": 1 }, { unique: true });
db.nodes.createIndex({ "kind": 1 });
db.nodes.createIndex({ "state": 1 });
db.nodes.createIndex({ "created": -1 });
db.nodes.createIndex({ "owner": 1 });
db.nodes.createIndex({ "parent": 1 });

// =============================================================================
// OpenWrt-Specific Collections
// =============================================================================

// Firmware collection - stores firmware metadata
db.createCollection('firmware');
db.firmware.createIndex({ "id": 1 }, { unique: true });
db.firmware.createIndex({ "source": 1 });
db.firmware.createIndex({ "version": 1 });
db.firmware.createIndex({ "target": 1, "subtarget": 1 });
db.firmware.createIndex({ "profile": 1 });
db.firmware.createIndex({ "created_at": -1 });
db.firmware.createIndex({ "git_commit_hash": 1 });

// Jobs collection - test job queue
db.createCollection('jobs');
db.jobs.createIndex({ "id": 1 }, { unique: true });
db.jobs.createIndex({ "status": 1 });
db.jobs.createIndex({ "priority": -1 });
db.jobs.createIndex({ "device_type": 1 });
db.jobs.createIndex({ "firmware_id": 1 });
db.jobs.createIndex({ "assigned_lab": 1 });
db.jobs.createIndex({ "created_at": -1 });
db.jobs.createIndex({ "status": 1, "priority": -1 });  // Compound for job polling

// Results collection - test results
db.createCollection('results');
db.results.createIndex({ "id": 1 }, { unique: true });
db.results.createIndex({ "job_id": 1 });
db.results.createIndex({ "firmware_id": 1 });
db.results.createIndex({ "device_type": 1 });
db.results.createIndex({ "test_name": 1 });
db.results.createIndex({ "status": 1 });
db.results.createIndex({ "lab_name": 1 });
db.results.createIndex({ "start_time": -1 });
db.results.createIndex({ "firmware_id": 1, "device_type": 1, "test_name": 1 });

// =============================================================================
// Device and Lab Management
// =============================================================================

// Devices collection - device registry
db.createCollection('devices');
db.devices.createIndex({ "id": 1 }, { unique: true });
db.devices.createIndex({ "lab_name": 1 });
db.devices.createIndex({ "status": 1 });
db.devices.createIndex({ "features": 1 });
db.devices.createIndex({ "target": 1, "subtarget": 1 });

// Labs collection - lab registry
db.createCollection('labs');
db.labs.createIndex({ "id": 1 }, { unique: true });
db.labs.createIndex({ "status": 1 });
db.labs.createIndex({ "last_seen": -1 });

// Health checks collection - device health history
db.createCollection('health_checks');
db.health_checks.createIndex({ "device_id": 1 });
db.health_checks.createIndex({ "timestamp": -1 });
db.health_checks.createIndex({ "status": 1 });
db.health_checks.createIndex({ "device_id": 1, "timestamp": -1 });

// =============================================================================
// Events and Notifications
// =============================================================================

// Events collection - pub/sub events (TTL: 7 days)
db.createCollection('events');
db.events.createIndex({ "type": 1 });
db.events.createIndex({ "timestamp": -1 });
db.events.createIndex({ "timestamp": 1 }, { expireAfterSeconds: 604800 });

// Notifications collection - pending notifications
db.createCollection('notifications');
db.notifications.createIndex({ "type": 1 });
db.notifications.createIndex({ "status": 1 });
db.notifications.createIndex({ "created_at": -1 });

// =============================================================================
// Statistics and Aggregations
// =============================================================================

// Daily statistics (for dashboard)
db.createCollection('daily_stats');
db.daily_stats.createIndex({ "date": 1 }, { unique: true });
db.daily_stats.createIndex({ "date": -1 });

// Device statistics
db.createCollection('device_stats');
db.device_stats.createIndex({ "device_id": 1, "date": 1 }, { unique: true });
db.device_stats.createIndex({ "date": -1 });

print('MongoDB initialization complete for OpenWrt KernelCI');
