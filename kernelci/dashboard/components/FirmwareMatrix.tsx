/**
 * Firmware Test Matrix Component
 *
 * Displays a matrix of test results with:
 * - Rows: Devices
 * - Columns: Firmware versions
 * - Cells: Pass/fail/skip counts with drill-down
 */

import React, { useEffect, useState } from 'react';

// =============================================================================
// Types
// =============================================================================

interface TestSummary {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  status: 'pass' | 'fail' | 'partial' | 'none';
}

interface MatrixCell {
  device: string;
  firmware_version: string;
  firmware_id: string | null;
  summary: TestSummary;
  last_run: string | null;
}

interface MatrixData {
  devices: string[];
  versions: string[];
  cells: Record<string, Record<string, MatrixCell>>;
}

// =============================================================================
// Cell Status Component
// =============================================================================

interface CellStatusProps {
  summary: TestSummary;
  onClick: () => void;
}

const CellStatus: React.FC<CellStatusProps> = ({ summary, onClick }) => {
  if (summary.status === 'none') {
    return (
      <div className="w-full h-full flex items-center justify-center text-gray-400">
        -
      </div>
    );
  }

  const bgColors = {
    pass: 'bg-green-100 hover:bg-green-200',
    fail: 'bg-red-100 hover:bg-red-200',
    partial: 'bg-yellow-100 hover:bg-yellow-200',
    none: 'bg-gray-50',
  };

  return (
    <button
      onClick={onClick}
      className={`w-full h-full p-2 ${bgColors[summary.status]} transition-colors`}
    >
      <div className="flex flex-col items-center">
        <div className="flex gap-1 text-sm">
          <span className="text-green-600">{summary.passed}</span>
          <span className="text-gray-400">/</span>
          <span className="text-red-600">{summary.failed}</span>
          {summary.skipped > 0 && (
            <>
              <span className="text-gray-400">/</span>
              <span className="text-gray-500">{summary.skipped}</span>
            </>
          )}
        </div>
      </div>
    </button>
  );
};

// =============================================================================
// Detail Modal Component
// =============================================================================

interface DetailModalProps {
  cell: MatrixCell | null;
  onClose: () => void;
  apiUrl: string;
}

const DetailModal: React.FC<DetailModalProps> = ({ cell, onClose, apiUrl }) => {
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!cell || !cell.firmware_id) return;

    const fetchResults = async () => {
      setLoading(true);
      try {
        const response = await fetch(
          `${apiUrl}/api/v1/results?firmware_id=${cell.firmware_id}&device_type=${cell.device}`
        );
        const data = await response.json();
        setResults(data.items || []);
      } catch (err) {
        console.error('Failed to fetch results:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchResults();
  }, [cell, apiUrl]);

  if (!cell) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] overflow-hidden">
        <div className="p-4 border-b flex justify-between items-center">
          <h2 className="text-lg font-semibold">
            {cell.device} - {cell.firmware_version}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            &times;
          </button>
        </div>

        <div className="p-4 overflow-y-auto max-h-[60vh]">
          {loading ? (
            <div>Loading...</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2">Test</th>
                  <th className="text-left py-2">Status</th>
                  <th className="text-left py-2">Duration</th>
                </tr>
              </thead>
              <tbody>
                {results.map((result) => (
                  <tr key={result.id} className="border-b">
                    <td className="py-2">{result.test_name}</td>
                    <td className="py-2">
                      <span
                        className={`px-2 py-1 rounded text-sm ${
                          result.status === 'pass'
                            ? 'bg-green-100 text-green-800'
                            : result.status === 'fail'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}
                      >
                        {result.status}
                      </span>
                    </td>
                    <td className="py-2">{result.duration?.toFixed(2)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="p-4 border-t bg-gray-50">
          <div className="flex justify-between text-sm text-gray-500">
            <span>Last run: {cell.last_run || 'Never'}</span>
            {cell.firmware_id && (
              <a
                href={`${apiUrl}/api/v1/firmware/${cell.firmware_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                View Firmware
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// =============================================================================
// Main Component
// =============================================================================

interface FirmwareMatrixProps {
  apiUrl: string;
}

export const FirmwareMatrix: React.FC<FirmwareMatrixProps> = ({ apiUrl }) => {
  const [matrixData, setMatrixData] = useState<MatrixData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCell, setSelectedCell] = useState<MatrixCell | null>(null);
  const [filter, setFilter] = useState({
    target: 'all',
    hideEmpty: false,
  });

  // Fetch matrix data
  useEffect(() => {
    const fetchMatrix = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${apiUrl}/api/v1/results/matrix`);
        if (!response.ok) throw new Error('Failed to fetch matrix data');
        const data = await response.json();
        setMatrixData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchMatrix();
    const interval = setInterval(fetchMatrix, 300000); // Refresh every 5 minutes
    return () => clearInterval(interval);
  }, [apiUrl]);

  if (loading) {
    return <div className="p-4">Loading matrix...</div>;
  }

  if (error) {
    return <div className="p-4 text-red-600">Error: {error}</div>;
  }

  if (!matrixData) {
    return <div className="p-4">No data available</div>;
  }

  // Filter devices
  let filteredDevices = matrixData.devices;
  if (filter.hideEmpty) {
    filteredDevices = filteredDevices.filter((device) =>
      matrixData.versions.some(
        (version) => matrixData.cells[device]?.[version]?.summary.status !== 'none'
      )
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Firmware Test Matrix</h1>

      {/* Legend */}
      <div className="flex gap-4 mb-4 text-sm">
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 bg-green-100 rounded" />
          <span>All Pass</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 bg-yellow-100 rounded" />
          <span>Partial</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 bg-red-100 rounded" />
          <span>Failures</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-4 h-4 bg-gray-100 rounded" />
          <span>No Results</span>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-4">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={filter.hideEmpty}
            onChange={(e) => setFilter({ ...filter, hideEmpty: e.target.checked })}
          />
          Hide devices without results
        </label>
      </div>

      {/* Matrix */}
      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="sticky left-0 bg-gray-50 px-4 py-3 text-left text-sm font-medium text-gray-500 border-b border-r">
                Device
              </th>
              {matrixData.versions.map((version) => (
                <th
                  key={version}
                  className="px-4 py-3 text-center text-sm font-medium text-gray-500 border-b min-w-[100px]"
                >
                  {version}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredDevices.map((device) => (
              <tr key={device} className="border-b">
                <td className="sticky left-0 bg-white px-4 py-2 font-medium border-r">
                  {device}
                </td>
                {matrixData.versions.map((version) => {
                  const cell = matrixData.cells[device]?.[version] || {
                    device,
                    firmware_version: version,
                    firmware_id: null,
                    summary: { total: 0, passed: 0, failed: 0, skipped: 0, status: 'none' as const },
                    last_run: null,
                  };
                  return (
                    <td key={version} className="p-0 border-r">
                      <CellStatus
                        summary={cell.summary}
                        onClick={() => setSelectedCell(cell)}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Detail Modal */}
      <DetailModal
        cell={selectedCell}
        onClose={() => setSelectedCell(null)}
        apiUrl={apiUrl}
      />
    </div>
  );
};

export default FirmwareMatrix;
