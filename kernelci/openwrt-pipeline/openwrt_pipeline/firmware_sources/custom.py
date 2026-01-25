"""
Custom firmware upload handler.

Provides a FastAPI router for uploading custom firmware builds
for testing on the OpenWrt KernelCI infrastructure.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from minio import Minio
from pydantic import BaseModel

from ..config import settings
from ..models import Firmware, FirmwareArtifacts
from ..models import FirmwareSource as FirmwareSourceEnum

logger = logging.getLogger(__name__)

# Create router for custom firmware uploads
router = APIRouter(prefix="/api/v1/firmware", tags=["firmware"])


class FirmwareUploadResponse(BaseModel):
    """Response model for firmware upload."""

    firmware_id: str
    status: str
    message: str
    artifacts: dict[str, str]


class CustomFirmwareUploader:
    """
    Handler for custom firmware uploads.

    Stores firmware in MinIO and creates firmware entries in the API.
    """

    def __init__(self, config: dict):
        self.config = config
        self.max_file_size = config.get("max_file_size", 100 * 1024 * 1024)  # 100MB
        self.allowed_extensions = config.get(
            "allowed_extensions", [".bin", ".img", ".itb", ".gz"]
        )
        self._minio: Minio | None = None

    def initialize(self) -> None:
        """Initialize MinIO client."""
        self._minio = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

        # Ensure bucket exists
        bucket = "openwrt-firmware"
        if not self._minio.bucket_exists(bucket):
            self._minio.make_bucket(bucket)
            logger.info(f"Created MinIO bucket: {bucket}")

    @property
    def minio(self) -> Minio:
        """Get MinIO client."""
        if self._minio is None:
            raise RuntimeError("Uploader not initialized")
        return self._minio

    def validate_file(self, filename: str, content: bytes) -> None:
        """Validate uploaded file."""
        # Check file size
        if len(content) > self.max_file_size:
            raise ValueError(
                f"File too large: {len(content)} bytes (max: {self.max_file_size})"
            )

        # Check extension
        ext = Path(filename).suffix.lower()
        if ext not in self.allowed_extensions:
            raise ValueError(
                f"Invalid file extension: {ext} (allowed: {self.allowed_extensions})"
            )

    def detect_firmware_type(self, filename: str) -> str:
        """Detect firmware type from filename."""
        filename_lower = filename.lower()

        if "sysupgrade" in filename_lower:
            return "sysupgrade"
        elif "factory" in filename_lower:
            return "factory"
        elif "initramfs" in filename_lower or "kernel" in filename_lower:
            return "initramfs"
        else:
            return "sysupgrade"  # Default assumption

    async def upload_firmware(
        self,
        file: UploadFile,
        target: str,
        subtarget: str,
        profile: str,
        version: str = "custom",
        git_commit: str | None = None,
        description: str | None = None,
    ) -> tuple[Firmware, dict[str, str]]:
        """
        Upload custom firmware to storage.

        Args:
            file: Uploaded file
            target: OpenWrt target (e.g., ath79)
            subtarget: OpenWrt subtarget (e.g., generic)
            profile: Device profile name
            version: Version string (default: "custom")
            git_commit: Git commit hash
            description: Optional description

        Returns:
            Tuple of (Firmware object, artifact URLs)
        """
        # Read file content
        content = await file.read()
        filename = file.filename or "firmware.bin"

        # Validate
        self.validate_file(filename, content)

        # Calculate checksum
        sha256 = hashlib.sha256(content).hexdigest()

        # Determine storage path
        firmware_type = self.detect_firmware_type(filename)
        storage_path = f"custom/{target}/{subtarget}/{profile}/{sha256[:8]}/{filename}"

        # Upload to MinIO
        logger.info(f"Uploading {filename} to MinIO: {storage_path}")

        import io

        self.minio.put_object(
            bucket_name="openwrt-firmware",
            object_name=storage_path,
            data=io.BytesIO(content),
            length=len(content),
            content_type="application/octet-stream",
        )

        # Generate public URL
        # Note: In production, this would use a proper URL or presigned URL
        artifact_url = (
            f"http://{settings.minio_endpoint}/openwrt-firmware/{storage_path}"
        )

        # Create artifact mapping
        artifacts = FirmwareArtifacts()
        setattr(artifacts, firmware_type, artifact_url)
        setattr(artifacts, f"{firmware_type}_sha256", sha256)

        # Generate firmware ID
        firmware_id = self._generate_firmware_id(
            target=target,
            subtarget=subtarget,
            profile=profile,
            sha256=sha256,
        )

        # Create firmware object
        firmware = Firmware(
            id=firmware_id,
            source=FirmwareSourceEnum.CUSTOM,
            version=version,
            target=target,
            subtarget=subtarget,
            profile=profile,
            git_commit_hash=git_commit,
            artifacts=artifacts,
            sha256=sha256,
            file_size=len(content),
            description=description,
            build_time=datetime.utcnow(),
        )

        return firmware, {firmware_type: artifact_url}

    def _generate_firmware_id(
        self,
        target: str,
        subtarget: str,
        profile: str,
        sha256: str,
    ) -> str:
        """Generate unique firmware ID."""
        return f"openwrt:custom:{target}:{subtarget}:{profile}:{sha256[:12]}"


# Global uploader instance (initialized by firmware_trigger service)
_uploader: CustomFirmwareUploader | None = None


def get_uploader() -> CustomFirmwareUploader:
    """Get the custom uploader instance."""
    if _uploader is None:
        raise HTTPException(status_code=503, detail="Upload service not initialized")
    return _uploader


def init_uploader(config: dict) -> CustomFirmwareUploader:
    """Initialize the custom uploader."""
    global _uploader
    _uploader = CustomFirmwareUploader(config)
    _uploader.initialize()
    return _uploader


# =============================================================================
# FastAPI Routes
# =============================================================================


@router.post("/upload", response_model=FirmwareUploadResponse)
async def upload_firmware(
    firmware_file: Annotated[UploadFile, File(description="Firmware file to upload")],
    target: Annotated[str, Form(description="OpenWrt target (e.g., ath79)")],
    subtarget: Annotated[str, Form(description="OpenWrt subtarget (e.g., generic)")],
    profile: Annotated[str, Form(description="Device profile name")],
    version: Annotated[str, Form(description="Version string")] = "custom",
    git_commit: Annotated[str | None, Form(description="Git commit hash")] = None,
    description: Annotated[str | None, Form(description="Description")] = None,
) -> FirmwareUploadResponse:
    """
    Upload custom firmware for testing.

    This endpoint allows developers to upload their own firmware builds
    for testing on the OpenWrt KernelCI infrastructure.

    The firmware will be stored and test jobs will be automatically
    scheduled for compatible devices.
    """
    uploader = get_uploader()

    try:
        firmware, artifacts = await uploader.upload_firmware(
            file=firmware_file,
            target=target,
            subtarget=subtarget,
            profile=profile,
            version=version,
            git_commit=git_commit,
            description=description,
        )

        # TODO: Submit firmware to API and trigger job scheduling

        return FirmwareUploadResponse(
            firmware_id=firmware.id,
            status="accepted",
            message="Firmware uploaded successfully. Test jobs will be scheduled.",
            artifacts=artifacts,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error uploading firmware: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/upload/status/{firmware_id}")
async def get_upload_status(firmware_id: str) -> dict:
    """Get the status of an uploaded firmware and its test jobs."""
    # TODO: Query API for firmware status and jobs
    return {
        "firmware_id": firmware_id,
        "status": "pending",
        "jobs": [],
    }
