"""
Data models for Labgrid KernelCI Adapter.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TestStatus(str, Enum):
    """Test result status."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class TestResult(BaseModel):
    """Individual test result."""

    id: str
    job_id: str
    firmware_id: str
    device_type: str
    lab_name: str

    test_name: str
    test_path: str | None = None
    status: TestStatus
    duration: float
    start_time: datetime
    end_time: datetime | None = None

    error_message: str | None = None
    log_url: str | None = None
    stdout: str | None = None
    stderr: str | None = None


class JobResult(BaseModel):
    """Complete job result."""

    job_id: str
    firmware_id: str
    device_type: str
    lab_name: str

    status: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    error_tests: int

    started_at: datetime
    completed_at: datetime
    duration: float

    test_results: list[TestResult] = Field(default_factory=list)
    console_log_url: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
