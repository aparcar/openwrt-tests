/**
 * Pull Request Status View Component
 *
 * Displays GitHub PR testing status:
 * - Active PR test jobs
 * - PR test history
 * - Test results per PR
 * - Direct links to GitHub PRs and test artifacts
 */

import React, { useEffect, useState } from 'react';

// =============================================================================
// Types
// =============================================================================

type JobStatus = 'pending' | 'running' | 'complete' | 'failed' | 'cancelled';
type TestStatus = 'pass' | 'fail' | 'skip' | 'error';

interface PRInfo {
  number: number;
  title: string;
  author: string;
  branch: string;
  url: string;
  head_sha: string;
  created_at: string;
  updated_at: string;
}

interface TestResult {
  test_name: string;
  status: TestStatus;
  duration: number;
  error_message?: string;
}

interface PRTestJob {
  id: string;
  pr: PRInfo;
  firmware_version: string;
  device_type: string;
  status: JobStatus;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  results: TestResult[];
  artifacts_url?: string;
  console_log_url?: string;
}

interface PRSummary {
  pr: PRInfo;
  total_jobs: number;
  completed_jobs: number;
  passed_jobs: number;
  failed_jobs: number;
  pending_jobs: number;
  devices_tested: string[];
  last_updated: string;
}

// =============================================================================
// Status Badge Component
// =============================================================================

interface StatusBadgeProps {
  status: JobStatus | TestStatus;
  size?: 'sm' | 'md';
}

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, size = 'md' }) => {
  const colors: Record<string, string> = {
    pass: 'bg-green-100 text-green-800',
    complete: 'bg-green-100 text-green-800',
    fail: 'bg-red-100 text-red-800',
    failed: 'bg-red-100 text-red-800',
    error: 'bg-red-100 text-red-800',
    skip: 'bg-gray-100 text-gray-800',
    pending: 'bg-yellow-100 text-yellow-800',
    running: 'bg-blue-100 text-blue-800',
    cancelled: 'bg-gray-100 text-gray-800',
  };

  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-sm';

  return (
    <span className={`${sizeClasses} rounded font-medium ${colors[status] || 'bg-gray-100'}`}>
      {status}
    </span>
  );
};

// =============================================================================
// PR Summary Card Component
// =============================================================================

interface PRSummaryCardProps {
  summary: PRSummary;
  onClick: () => void;
  isSelected: boolean;
}

const PRSummaryCard: React.FC<PRSummaryCardProps> = ({
  summary,
  onClick,
  isSelected,
}) => {
  const { pr, total_jobs, completed_jobs, passed_jobs, failed_jobs } = summary;
  const progress = total_jobs > 0 ? (completed_jobs / total_jobs) * 100 : 0;

  const overallStatus =
    failed_jobs > 0
      ? 'failed'
      : completed_jobs === total_jobs
      ? 'complete'
      : 'running';

  return (
    <div
      onClick={onClick}
      className={`p-4 rounded-lg border cursor-pointer transition-all ${
        isSelected
          ? 'border-blue-500 bg-blue-50'
          : 'border-gray-200 hover:border-gray-300 bg-white'
      }`}
    >
      <div className="flex justify-between items-start mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-blue-600">#{pr.number}</span>
            <StatusBadge status={overallStatus} size="sm" />
          </div>
          <h3 className="font-medium text-gray-900 truncate" title={pr.title}>
            {pr.title}
          </h3>
          <div className="text-sm text-gray-500">
            by {pr.author} · {pr.branch}
          </div>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mt-3">
        <div className="flex justify-between text-sm text-gray-500 mb-1">
          <span>{completed_jobs} / {total_jobs} jobs</span>
          <span>
            <span className="text-green-600">{passed_jobs} pass</span>
            {failed_jobs > 0 && (
              <span className="text-red-600 ml-2">{failed_jobs} fail</span>
            )}
          </span>
        </div>
        <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all ${
              failed_jobs > 0 ? 'bg-red-500' : 'bg-green-500'
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
    </div>
  );
};

// =============================================================================
// Job Detail Component
// =============================================================================

interface JobDetailProps {
  job: PRTestJob;
}

const JobDetail: React.FC<JobDetailProps> = ({ job }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border rounded-lg overflow-hidden">
      <div
        onClick={() => setExpanded(!expanded)}
        className="p-4 bg-gray-50 cursor-pointer hover:bg-gray-100 flex justify-between items-center"
      >
        <div className="flex items-center gap-3">
          <StatusBadge status={job.status} />
          <div>
            <div className="font-medium">{job.device_type}</div>
            <div className="text-sm text-gray-500">
              {job.firmware_version}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {job.results.length > 0 && (
            <div className="text-sm">
              <span className="text-green-600">
                {job.results.filter((r) => r.status === 'pass').length} pass
              </span>
              {' / '}
              <span className="text-red-600">
                {job.results.filter((r) => r.status === 'fail').length} fail
              </span>
            </div>
          )}
          <span className="text-gray-400">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {expanded && (
        <div className="p-4 bg-white">
          {/* Test Results */}
          {job.results.length > 0 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2">Test</th>
                  <th className="text-left py-2">Status</th>
                  <th className="text-left py-2">Duration</th>
                </tr>
              </thead>
              <tbody>
                {job.results.map((result, idx) => (
                  <tr key={idx} className="border-b last:border-0">
                    <td className="py-2 font-mono">{result.test_name}</td>
                    <td className="py-2">
                      <StatusBadge status={result.status} size="sm" />
                    </td>
                    <td className="py-2">{result.duration.toFixed(2)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="text-gray-500 text-center py-4">
              {job.status === 'pending'
                ? 'Waiting to start...'
                : job.status === 'running'
                ? 'Tests in progress...'
                : 'No test results available'}
            </div>
          )}

          {/* Links */}
          <div className="flex gap-4 mt-4 pt-4 border-t">
            {job.console_log_url && (
              <a
                href={job.console_log_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-blue-600 hover:underline"
              >
                Console Log
              </a>
            )}
            {job.artifacts_url && (
              <a
                href={job.artifacts_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-blue-600 hover:underline"
              >
                Artifacts
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// =============================================================================
// PR Detail View Component
// =============================================================================

interface PRDetailViewProps {
  summary: PRSummary;
  jobs: PRTestJob[];
  onClose: () => void;
}

const PRDetailView: React.FC<PRDetailViewProps> = ({
  summary,
  jobs,
  onClose,
}) => {
  const { pr } = summary;

  return (
    <div className="bg-white rounded-lg shadow-lg overflow-hidden">
      {/* Header */}
      <div className="p-4 bg-gray-50 border-b">
        <div className="flex justify-between items-start">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <a
                href={pr.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xl font-mono text-blue-600 hover:underline"
              >
                #{pr.number}
              </a>
              <span className="text-sm text-gray-500">
                {pr.head_sha.substring(0, 7)}
              </span>
            </div>
            <h2 className="text-lg font-semibold">{pr.title}</h2>
            <div className="text-sm text-gray-500 mt-1">
              by {pr.author} · {pr.branch}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 text-2xl"
          >
            &times;
          </button>
        </div>
      </div>

      {/* Jobs list */}
      <div className="p-4 space-y-3 max-h-[60vh] overflow-y-auto">
        <h3 className="font-medium text-gray-700 mb-2">
          Test Jobs ({jobs.length})
        </h3>
        {jobs.map((job) => (
          <JobDetail key={job.id} job={job} />
        ))}
      </div>

      {/* Footer */}
      <div className="p-4 bg-gray-50 border-t flex justify-between items-center">
        <div className="text-sm text-gray-500">
          Last updated: {new Date(summary.last_updated).toLocaleString()}
        </div>
        <a
          href={pr.url}
          target="_blank"
          rel="noopener noreferrer"
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          View on GitHub
        </a>
      </div>
    </div>
  );
};

// =============================================================================
// Main Component
// =============================================================================

interface PRStatusViewProps {
  apiUrl: string;
}

export const PRStatusView: React.FC<PRStatusViewProps> = ({ apiUrl }) => {
  const [summaries, setSummaries] = useState<PRSummary[]>([]);
  const [selectedPR, setSelectedPR] = useState<number | null>(null);
  const [prJobs, setPrJobs] = useState<PRTestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'complete'>('all');

  // Fetch PR summaries
  useEffect(() => {
    const fetchSummaries = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${apiUrl}/api/v1/pr/summaries`);
        if (!response.ok) throw new Error('Failed to fetch PR summaries');
        const data = await response.json();
        setSummaries(data.items || data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchSummaries();
    const interval = setInterval(fetchSummaries, 30000); // Refresh every 30 seconds
    return () => clearInterval(interval);
  }, [apiUrl]);

  // Fetch jobs for selected PR
  useEffect(() => {
    if (selectedPR === null) {
      setPrJobs([]);
      return;
    }

    const fetchJobs = async () => {
      try {
        const response = await fetch(
          `${apiUrl}/api/v1/pr/${selectedPR}/jobs`
        );
        if (!response.ok) throw new Error('Failed to fetch PR jobs');
        const data = await response.json();
        setPrJobs(data.items || data);
      } catch (err) {
        console.error('Failed to fetch PR jobs:', err);
      }
    };

    fetchJobs();
    const interval = setInterval(fetchJobs, 10000); // Refresh every 10 seconds when viewing
    return () => clearInterval(interval);
  }, [selectedPR, apiUrl]);

  // Filter summaries
  const filteredSummaries = summaries.filter((s) => {
    if (statusFilter === 'active') {
      return s.completed_jobs < s.total_jobs;
    }
    if (statusFilter === 'complete') {
      return s.completed_jobs === s.total_jobs;
    }
    return true;
  });

  const selectedSummary = summaries.find((s) => s.pr.number === selectedPR);

  if (loading) {
    return <div className="p-4">Loading PR status...</div>;
  }

  if (error) {
    return <div className="p-4 text-red-600">Error: {error}</div>;
  }

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Pull Request Testing</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setStatusFilter('all')}
            className={`px-3 py-1 rounded ${
              statusFilter === 'all'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            All ({summaries.length})
          </button>
          <button
            onClick={() => setStatusFilter('active')}
            className={`px-3 py-1 rounded ${
              statusFilter === 'active'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Active ({summaries.filter((s) => s.completed_jobs < s.total_jobs).length})
          </button>
          <button
            onClick={() => setStatusFilter('complete')}
            className={`px-3 py-1 rounded ${
              statusFilter === 'complete'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Complete ({summaries.filter((s) => s.completed_jobs === s.total_jobs).length})
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* PR List */}
        <div className={`space-y-4 ${selectedPR ? 'lg:col-span-1' : 'lg:col-span-3'}`}>
          {filteredSummaries.length === 0 ? (
            <div className="text-center text-gray-500 py-8 bg-white rounded-lg">
              No pull requests found
            </div>
          ) : (
            <div className={`grid gap-4 ${selectedPR ? '' : 'md:grid-cols-2 xl:grid-cols-3'}`}>
              {filteredSummaries.map((summary) => (
                <PRSummaryCard
                  key={summary.pr.number}
                  summary={summary}
                  onClick={() => setSelectedPR(summary.pr.number)}
                  isSelected={selectedPR === summary.pr.number}
                />
              ))}
            </div>
          )}
        </div>

        {/* PR Detail */}
        {selectedPR && selectedSummary && (
          <div className="lg:col-span-2">
            <PRDetailView
              summary={selectedSummary}
              jobs={prJobs}
              onClose={() => setSelectedPR(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default PRStatusView;
