/**
 * Health Check Dashboard Component
 *
 * Displays device health check status and history:
 * - Current health status per device
 * - Health check history timeline
 * - Failure trends and patterns
 * - Quick actions (trigger check, enable/disable)
 */

import React, { useEffect, useState } from 'react';

// =============================================================================
// Types
// =============================================================================

type DeviceStatus = 'healthy' | 'failing' | 'disabled' | 'unknown';

interface HealthCheckResult {
  id: string;
  device_id: string;
  timestamp: string;
  status: 'pass' | 'fail' | 'timeout';
  duration: number;
  error_message?: string;
  console_log_url?: string;
}

interface DeviceHealth {
  id: string;
  lab_name: string;
  target: string;
  subtarget: string;
  status: DeviceStatus;
  last_check: string | null;
  last_pass: string | null;
  consecutive_failures: number;
  github_issue_url?: string;
}

interface HealthSummary {
  total: number;
  healthy: number;
  failing: number;
  disabled: number;
  unknown: number;
  by_lab: Record<string, {
    total: number;
    healthy: number;
    failing: number;
    disabled: number;
  }>;
}

// =============================================================================
// Status Badge Component
// =============================================================================

interface StatusBadgeProps {
  status: DeviceStatus;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const colors = {
    healthy: 'bg-green-100 text-green-800',
    failing: 'bg-yellow-100 text-yellow-800',
    disabled: 'bg-red-100 text-red-800',
    unknown: 'bg-gray-100 text-gray-800',
  };

  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${colors[status]}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
};

// =============================================================================
// Summary Cards Component
// =============================================================================

interface SummaryCardsProps {
  summary: HealthSummary;
}

const SummaryCards: React.FC<SummaryCardsProps> = ({ summary }) => {
  const cards = [
    { label: 'Total Devices', value: summary.total, color: 'bg-blue-500' },
    { label: 'Healthy', value: summary.healthy, color: 'bg-green-500' },
    { label: 'Failing', value: summary.failing, color: 'bg-yellow-500' },
    { label: 'Disabled', value: summary.disabled, color: 'bg-red-500' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      {cards.map((card) => (
        <div key={card.label} className="bg-white rounded-lg shadow p-4">
          <div className="flex items-center">
            <div className={`w-3 h-3 rounded-full ${card.color} mr-3`} />
            <div>
              <div className="text-2xl font-bold">{card.value}</div>
              <div className="text-sm text-gray-500">{card.label}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

// =============================================================================
// Device Row Component
// =============================================================================

interface DeviceRowProps {
  device: DeviceHealth;
  onTriggerCheck: (deviceId: string) => void;
  onToggleDevice: (deviceId: string, enable: boolean) => void;
}

const DeviceRow: React.FC<DeviceRowProps> = ({
  device,
  onTriggerCheck,
  onToggleDevice,
}) => {
  const formatTime = (timestamp: string | null) => {
    if (!timestamp) return 'Never';
    const date = new Date(timestamp);
    return date.toLocaleString();
  };

  return (
    <tr className="border-b hover:bg-gray-50">
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
      <td className="px-4 py-3 text-sm">
        {formatTime(device.last_check)}
      </td>
      <td className="px-4 py-3">
        {device.consecutive_failures > 0 && (
          <span className="text-red-600 font-medium">
            {device.consecutive_failures}
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <button
            onClick={() => onTriggerCheck(device.id)}
            className="px-3 py-1 text-sm bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
            disabled={device.status === 'disabled'}
          >
            Check
          </button>
          {device.status === 'disabled' ? (
            <button
              onClick={() => onToggleDevice(device.id, true)}
              className="px-3 py-1 text-sm bg-green-100 text-green-700 rounded hover:bg-green-200"
            >
              Enable
            </button>
          ) : (
            <button
              onClick={() => onToggleDevice(device.id, false)}
              className="px-3 py-1 text-sm bg-red-100 text-red-700 rounded hover:bg-red-200"
            >
              Disable
            </button>
          )}
          {device.github_issue_url && (
            <a
              href={device.github_issue_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-1 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
            >
              Issue
            </a>
          )}
        </div>
      </td>
    </tr>
  );
};

// =============================================================================
// History Timeline Component
// =============================================================================

interface HistoryTimelineProps {
  results: HealthCheckResult[];
}

const HistoryTimeline: React.FC<HistoryTimelineProps> = ({ results }) => {
  if (results.length === 0) {
    return (
      <div className="text-center text-gray-500 py-8">
        No health check history available
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {results.map((result) => (
        <div
          key={result.id}
          className={`p-4 rounded-lg border-l-4 ${
            result.status === 'pass'
              ? 'border-green-500 bg-green-50'
              : result.status === 'timeout'
              ? 'border-yellow-500 bg-yellow-50'
              : 'border-red-500 bg-red-50'
          }`}
        >
          <div className="flex justify-between items-start">
            <div>
              <div className="font-medium">{result.device_id}</div>
              <div className="text-sm text-gray-500">
                {new Date(result.timestamp).toLocaleString()}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">
                {result.duration.toFixed(2)}s
              </span>
              <span
                className={`px-2 py-1 rounded text-sm ${
                  result.status === 'pass'
                    ? 'bg-green-100 text-green-800'
                    : result.status === 'timeout'
                    ? 'bg-yellow-100 text-yellow-800'
                    : 'bg-red-100 text-red-800'
                }`}
              >
                {result.status}
              </span>
            </div>
          </div>
          {result.error_message && (
            <div className="mt-2 text-sm text-red-600 font-mono bg-red-100 p-2 rounded">
              {result.error_message}
            </div>
          )}
          {result.console_log_url && (
            <a
              href={result.console_log_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block text-sm text-blue-600 hover:underline"
            >
              View console log
            </a>
          )}
        </div>
      ))}
    </div>
  );
};

// =============================================================================
// Main Component
// =============================================================================

interface HealthCheckDashboardProps {
  apiUrl: string;
}

export const HealthCheckDashboard: React.FC<HealthCheckDashboardProps> = ({
  apiUrl,
}) => {
  const [devices, setDevices] = useState<DeviceHealth[]>([]);
  const [summary, setSummary] = useState<HealthSummary | null>(null);
  const [history, setHistory] = useState<HealthCheckResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLab, setSelectedLab] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<DeviceStatus | 'all'>('all');
  const [activeTab, setActiveTab] = useState<'devices' | 'history'>('devices');

  // Fetch health data
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);

        const [devicesRes, summaryRes, historyRes] = await Promise.all([
          fetch(`${apiUrl}/api/v1/devices`),
          fetch(`${apiUrl}/api/v1/health/summary`),
          fetch(`${apiUrl}/api/v1/health/history?limit=50`),
        ]);

        if (!devicesRes.ok || !summaryRes.ok) {
          throw new Error('Failed to fetch health data');
        }

        const [devicesData, summaryData, historyData] = await Promise.all([
          devicesRes.json(),
          summaryRes.json(),
          historyRes.json(),
        ]);

        setDevices(devicesData.items || devicesData);
        setSummary(summaryData);
        setHistory(historyData.items || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, [apiUrl]);

  // Handle trigger check
  const handleTriggerCheck = async (deviceId: string) => {
    try {
      const response = await fetch(
        `${apiUrl}/api/v1/devices/${deviceId}/health-check`,
        { method: 'POST' }
      );

      if (!response.ok) {
        throw new Error('Failed to trigger health check');
      }

      alert(`Health check triggered for ${deviceId}`);
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  // Handle enable/disable device
  const handleToggleDevice = async (deviceId: string, enable: boolean) => {
    try {
      const response = await fetch(
        `${apiUrl}/api/v1/devices/${deviceId}/${enable ? 'enable' : 'disable'}`,
        { method: 'POST' }
      );

      if (!response.ok) {
        throw new Error(`Failed to ${enable ? 'enable' : 'disable'} device`);
      }

      // Refresh devices
      const devicesRes = await fetch(`${apiUrl}/api/v1/devices`);
      const devicesData = await devicesRes.json();
      setDevices(devicesData.items || devicesData);
    } catch (err) {
      alert(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  // Filter devices
  const filteredDevices = devices.filter((device) => {
    if (selectedLab !== 'all' && device.lab_name !== selectedLab) return false;
    if (statusFilter !== 'all' && device.status !== statusFilter) return false;
    return true;
  });

  // Get unique labs
  const labs = [...new Set(devices.map((d) => d.lab_name))];

  if (loading) {
    return <div className="p-4">Loading health data...</div>;
  }

  if (error) {
    return <div className="p-4 text-red-600">Error: {error}</div>;
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Device Health Dashboard</h1>

      {/* Summary Cards */}
      {summary && <SummaryCards summary={summary} />}

      {/* Tabs */}
      <div className="flex border-b mb-4">
        <button
          onClick={() => setActiveTab('devices')}
          className={`px-4 py-2 font-medium ${
            activeTab === 'devices'
              ? 'border-b-2 border-blue-500 text-blue-600'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          Devices
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`px-4 py-2 font-medium ${
            activeTab === 'history'
              ? 'border-b-2 border-blue-500 text-blue-600'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          Check History
        </button>
      </div>

      {activeTab === 'devices' && (
        <>
          {/* Filters */}
          <div className="flex gap-4 mb-4">
            <select
              value={selectedLab}
              onChange={(e) => setSelectedLab(e.target.value)}
              className="px-3 py-2 border rounded"
            >
              <option value="all">All Labs</option>
              {labs.map((lab) => (
                <option key={lab} value={lab}>
                  {lab}
                </option>
              ))}
            </select>

            <select
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as DeviceStatus | 'all')
              }
              className="px-3 py-2 border rounded"
            >
              <option value="all">All Status</option>
              <option value="healthy">Healthy</option>
              <option value="failing">Failing</option>
              <option value="disabled">Disabled</option>
              <option value="unknown">Unknown</option>
            </select>
          </div>

          {/* Device Table */}
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Device
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Lab
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Last Check
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Failures
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredDevices.map((device) => (
                  <DeviceRow
                    key={device.id}
                    device={device}
                    onTriggerCheck={handleTriggerCheck}
                    onToggleDevice={handleToggleDevice}
                  />
                ))}
              </tbody>
            </table>

            {filteredDevices.length === 0 && (
              <div className="text-center text-gray-500 py-8">
                No devices found matching filters
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === 'history' && <HistoryTimeline results={history} />}
    </div>
  );
};

export default HealthCheckDashboard;
