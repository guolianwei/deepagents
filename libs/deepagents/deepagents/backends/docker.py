"""Docker sandbox backend implementation."""

from __future__ import annotations

import io
import posixpath
import shlex
import tarfile
from typing import TYPE_CHECKING, Protocol

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileOperationError,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

if TYPE_CHECKING:
    from collections.abc import Iterable


class DockerContainer(Protocol):
    """Minimal Docker SDK container surface used by `DockerSandbox`."""

    id: str

    def exec_run(
        self,
        cmd: list[str],
        *,
        workdir: str | None = None,
        demux: bool = False,
    ) -> tuple[int, bytes | tuple[bytes | None, bytes | None] | None]:
        """Run a command in the container."""

    def put_archive(self, path: str, data: bytes) -> bool:
        """Upload a tar archive into the container."""

    def get_archive(self, path: str) -> tuple[Iterable[bytes], dict]:
        """Download a tar archive from the container."""


def _standard_error_from_message(message: str) -> FileOperationError:
    """Map Docker/OS error text to the standard sandbox error vocabulary."""
    text = message.lower()
    if "permission denied" in text:
        return "permission_denied"
    if "is a directory" in text:
        return "is_directory"
    if "not found" in text or "no such file" in text:
        return "file_not_found"
    return "invalid_path"


def _extract_archive_bytes(chunks: Iterable[bytes]) -> bytes | None:
    """Return the first regular file payload from a Docker archive stream."""
    tar_stream = io.BytesIO(b"".join(chunks))
    tar_stream.seek(0)
    with tarfile.open(fileobj=tar_stream, mode="r") as tar:
        for member in tar:
            if member.isdir():
                return None
            file_obj = tar.extractfile(member)
            if file_obj is None:
                return None
            return file_obj.read()
    return None


class DockerSandbox(BaseSandbox):
    """Sandbox implementation using a Docker container.

    Executes commands and transfers files using the Docker daemon API.
    """

    def __init__(
        self,
        *,
        container: DockerContainer,
        workdir: str = "/workspace",
    ) -> None:
        """Initialize the Docker sandbox.

        Args:
            container: Docker SDK container object to execute against.
            workdir: Default working directory for command execution.
        """
        self._container = container
        self._workdir = workdir

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance."""
        return self._container.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ARG002
    ) -> ExecuteResponse:
        """Execute a shell command in the Docker container.

        Docker SDK `exec_run` does not expose a portable command timeout in the
        sync API used here. The `timeout` parameter is accepted for protocol
        compatibility and currently ignored; execution blocks until completion.

        Args:
            command: Shell command to execute.
            timeout: Accepted for protocol compatibility, currently ignored.

        Returns:
            `ExecuteResponse` with combined output and exit code.
        """
        try:
            exit_code, output = self._container.exec_run(
                ["/bin/sh", "-lc", command],
                workdir=self._workdir,
                demux=False,
            )
        except (OSError, RuntimeError, ValueError) as e:
            return ExecuteResponse(
                output=f"Failed to execute command: {e}",
                exit_code=1,
                truncated=False,
            )

        if isinstance(output, tuple):
            stdout, stderr = output
            output_bytes = (stdout or b"") + (stderr or b"")
        else:
            output_bytes = output or b""

        return ExecuteResponse(
            output=output_bytes.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the container.

        Args:
            files: List of `(absolute_path, content_bytes)` tuples.

        Returns:
            List of `FileUploadResponse` objects.
        """
        responses: list[FileUploadResponse] = []
        for file_path, file_bytes in files:
            if not posixpath.isabs(file_path):
                responses.append(FileUploadResponse(path=file_path, error="invalid_path"))
                continue

            parent_dir = posixpath.dirname(file_path)
            file_name = posixpath.basename(file_path)
            mkdir_res = self.execute(f"mkdir -p {shlex.quote(parent_dir)}")
            if mkdir_res.exit_code != 0:
                responses.append(
                    FileUploadResponse(path=file_path, error="permission_denied")
                )
                continue

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name=file_name)
                tarinfo.size = len(file_bytes)
                tar.addfile(tarinfo, io.BytesIO(file_bytes))
            tar_stream.seek(0)

            try:
                success = self._container.put_archive(parent_dir, tar_stream.read())
            except (OSError, RuntimeError, ValueError) as e:
                responses.append(
                    FileUploadResponse(
                        path=file_path,
                        error=_standard_error_from_message(str(e)),
                    )
                )
                continue

            error = None if success else "permission_denied"
            responses.append(FileUploadResponse(path=file_path, error=error))

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the container.

        Args:
            paths: List of absolute file paths to download.

        Returns:
            List of `FileDownloadResponse` objects.
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not posixpath.isabs(path):
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue

            stat_res = self.execute(f"test -d {shlex.quote(path)}")
            if stat_res.exit_code == 0:
                responses.append(FileDownloadResponse(path=path, error="is_directory"))
                continue

            try:
                stream, _ = self._container.get_archive(path)
                content = _extract_archive_bytes(stream)
            except (OSError, RuntimeError, ValueError) as e:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        error=_standard_error_from_message(str(e)),
                    )
                )
                continue

            if content is None:
                responses.append(FileDownloadResponse(path=path, error="is_directory"))
            else:
                responses.append(FileDownloadResponse(path=path, content=content))

        return responses
