/**
 * Device Fleet Status Component
 *
 * Displays the health status of all devices across all labs
 * with filtering, grouping, and quick actions.
 */

import React, { useEffect, useState } from 'react';

// =============================================================================
// Types
// =============================================================================

interface Device {
  id: string;
  lab_name: string;
  target: string;
  subtarget: string;
  profile: string | null;
  features: string[];
  status: 'healthy' | 'failing' | 'disabled' | 'unknown';
  last_check: string | null;
  last_pass: string | null;
  consecutive_failures: number;
}

interface Lab {
  id: string;
  name: string;
  status: 'online' | 'offline' | 'maintenance';
  devices: Device[];
}

interface FleetSummary {
  total: number;
  healthy: number;
  failing: number;
  disabled: number;
  unknown: number;
}

// =============================================================================
// Status Badge Component
// =============================================================================

interface StatusBadgeProps {
  status: Device['status'];
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const colors = {
    healthy: 'bg-green-100 text-green-800',
    failing: 'bg-yellow-100 text-yellow-800',
    disabled: 'bg-red-100 text-red-800',
    unknown: 'bg-gray-100 text-gray-800',
  };

  return (
    <span className={`px-2 py-1 rounded-full text-sm font-medium ${colors[status]}`}>
      {status}
    </span>
  );
};

// =============================================================================
// Summary Cards Component
// =============================================================================

interface SummaryCardsProps {
  summary: FleetSummary;
}

const SummaryCards: React.FC<SummaryCardsProps> = ({ summary }) => {
  const cards = [
    { label: 'Total Devices', value: summary.total, color: 'bg-blue-500' },
    { label: 'Healthy', value: summary.healthy, color: 'bg-green-500' },
    { label: 'Failing', value: summary.failing, color: 'bg-yellow-500' },
    { label: 'Disabled', value: summary.disabled, color: 'bg-red-500' },
  ];

  return (
    <div className="grid grid-cols-4 gap-4 mb-6">
      {cards.map((card) => (
        <div key={card.label} className="bg-white rounded-lg shadow p-4">
          <div className={`w-2 h-2 rounded-full ${card.color} mb-2`} />
          <div className="text-2xl font-bold">{card.value}</div>
          <div className="text-gray-500 text-sm">{card.label}</div>
        </div>
      ))}
    </div>
  );
};

// =============================================================================
// Device Row Component
// =============================================================================

interface DeviceRowProps {
  device: Device;
  onHealthCheck: (deviceId: string) => void;
  onViewLogs: (deviceId: string) => void;
}

const DeviceRow: React.FC<DeviceRowProps> = ({ device, onHealthCheck, onViewLogs }) => {
  const formatDate = (date: string | null) => {
    if (!date) return 'Never';
    return new Date(date).toLocaleString();
  };

  return (
    <tr className={`border-b hover:bg-gray-50 ${device.status === 'disabled' ? 'opacity-60' : ''}`}>
      <td className="px-4 py-3">
        <div className="font-medium">{device.id}</div>
        <div className="text-sm text-gray-500">
          {device.target}/{device.subtarget}
        </div>
      </td>
      <td className="px-4 py-3">{device.lab_name}</td>
      <td className="px-4 py-3">
        <StatusBadge status={device.status} />
      </td>
      <td className="px-4 py-3 text-sm">{formatDate(device.last_check)}</td>
      <td className="px-4 py-3 text-sm">{formatDate(device.last_pass)}</td>
      <td className="px-4 py-3">
        {device.consecutive_failures > 0 && (
          <span className="text-red-600 font-medium">{device.consecutive_failures}</span>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1">
          {device.features.map((feature) => (
            <span
              key={feature}
              className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs"
            >
              {feature}
            </span>
          ))}
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <button
            onClick={() => onHealthCheck(device.id)}
            className="px-2 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600"
            disabled={device.status === 'disabled'}
          >
            Check
          </button>
          <button
            onClick={() => onViewLogs(device.id)}
            className="px-2 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Logs
          </button>
        </div>
      </td>
    </tr>
  );
};

// =============================================================================
// Main Component
// =============================================================================

interface DeviceFleetStatusProps {
  apiUrl: string;
}

export const DeviceFleetStatus: React.FC<DeviceFleetStatusProps> = ({ apiUrl }) => {
  const [devices, setDevices] = useState<Device[]>([]);
  const [labs, setLabs] = useState<Lab[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState({
    status: 'all',
    lab: 'all',
    search: '',
  });

  // Fetch devices
  useEffect(() => {
    const fetchDevices = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${apiUrl}/api/v1/devices`);
        if (!response.ok) throw new Error('Failed to fetch devices');
        const data = await response.json();
        setDevices(data.items || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchDevices();
    const interval = setInterval(fetchDevices, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, [apiUrl]);

  // Calculate summary
  const summary: FleetSummary = {
    total: devices.length,
    healthy: devices.filter((d) => d.status === 'healthy').length,
    failing: devices.filter((d) => d.status === 'failing').length,
    disabled: devices.filter((d) => d.status === 'disabled').length,
    unknown: devices.filter((d) => d.status === 'unknown').length,
  };

  // Filter devices
  const filteredDevices = devices.filter((device) => {
    if (filter.status !== 'all' && device.status !== filter.status) return false;
    if (filter.lab !== 'all' && device.lab_name !== filter.lab) return false;
    if (filter.search && !device.id.toLowerCase().includes(filter.search.toLowerCase())) {
      return false;
    }
    return true;
  });

  // Get unique labs
  const uniqueLabs = [...new Set(devices.map((d) => d.lab_name))];

  // Handlers
  const handleHealthCheck = async (deviceId: string) => {
    try {
      await fetch(`${apiUrl}/api/v1/devices/${deviceId}/health-check`, {
        method: 'POST',
      });
      // Refresh devices
      // TODO: Show toast notification
    } catch (err) {
      console.error('Failed to trigger health check:', err);
    }
  };

  const handleViewLogs = (deviceId: string) => {
    window.open(`${apiUrl}/api/v1/devices/${deviceId}/logs`, '_blank');
  };

  if (loading) {
    return <div className="p-4">Loading...</div>;
  }

  if (error) {
    return <div className="p-4 text-red-600">Error: {error}</div>;
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Device Fleet Status</h1>

      <SummaryCards summary={summary} />

      {/* Filters */}
      <div className="flex gap-4 mb-4">
        <input
          type="text"
          placeholder="Search devices..."
          className="px-3 py-2 border rounded-lg"
          value={filter.search}
          onChange={(e) => setFilter({ ...filter, search: e.target.value })}
        />
        <select
          className="px-3 py-2 border rounded-lg"
          value={filter.status}
          onChange={(e) => setFilter({ ...filter, status: e.target.value })}
        >
          <option value="all">All Status</option>
          <option value="healthy">Healthy</option>
          <option value="failing">Failing</option>
          <option value="disabled">Disabled</option>
        </select>
        <select
          className="px-3 py-2 border rounded-lg"
          value={filter.lab}
          onChange={(e) => setFilter({ ...filter, lab: e.target.value })}
        >
          <option value="all">All Labs</option>
          {uniqueLabs.map((lab) => (
            <option key={lab} value={lab}>
              {lab}
            </option>
          ))}
        </select>
      </div>

      {/* Device Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Device</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Lab</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Status</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Last Check</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Last Pass</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Failures</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Features</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredDevices.map((device) => (
              <DeviceRow
                key={device.id}
                device={device}
                onHealthCheck={handleHealthCheck}
                onViewLogs={handleViewLogs}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default DeviceFleetStatus;
