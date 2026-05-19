import base64
import io
import posixpath
import shlex
import tarfile
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox


class DockerSandbox(BaseSandbox):
    """Sandbox implementation using a Docker container.

    Executes commands and transfers files using the Docker daemon API.
    """

    def __init__(
        self,
        *,
        container: Any,
        workdir: str = "/workspace",
    ) -> None:
        """Initialize the Docker sandbox.

        Args:
            container: The docker SDK Container object.
            workdir: Default working directory for command execution.
        """
        self._container = container
        self._workdir = workdir
        self._default_timeout = 30 * 60

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance."""
        return self._container.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command in the Docker container.

        Currently, `timeout` is ignored in this implementation. Command execution
        blocks until completion.

        Args:
            command: Shell command to execute.
            timeout: Ignored in the current implementation.

        Returns:
            ExecuteResponse with the combined output and exit code.
        """
        try:
            # We use /bin/sh -lc to ensure a minimal shell environment
            exit_code, output = self._container.exec_run(
                ["/bin/sh", "-lc", command],
                workdir=self._workdir,
                demux=False,
            )
            output_str = output.decode("utf-8", errors="replace") if output else ""

            return ExecuteResponse(
                output=output_str,
                exit_code=exit_code,
                truncated=False,
            )
        except Exception as e:
            return ExecuteResponse(
                output=f"Failed to execute command: {e}",
                exit_code=1,
                truncated=False,
            )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the container.

        Args:
            files: List of (absolute_path, content_bytes) tuples.

        Returns:
            List of FileUploadResponse objects.
        """
        responses = []
        for file_path, file_bytes in files:
            try:
                if not posixpath.isabs(file_path):
                    responses.append(
                        FileUploadResponse(
                            path=file_path,
                            error="invalid_path",
                        )
                    )
                    continue

                parent_dir = posixpath.dirname(file_path)
                file_name = posixpath.basename(file_path)

                # Ensure parent directory exists using execute
                mkdir_cmd = f"mkdir -p {shlex.quote(parent_dir)}"
                mkdir_res = self.execute(mkdir_cmd)
                if mkdir_res.exit_code != 0:
                    responses.append(
                        FileUploadResponse(
                            path=file_path,
                            error="permission_denied",
                        )
                    )
                    continue

                # Create a tar archive in memory containing just this file
                tar_stream = io.BytesIO()
                with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                    tarinfo = tarfile.TarInfo(name=file_name)
                    tarinfo.size = len(file_bytes)
                    tar.addfile(tarinfo, io.BytesIO(file_bytes))
                tar_stream.seek(0)

                success = self._container.put_archive(parent_dir, tar_stream.read())
                if success:
                    responses.append(FileUploadResponse(path=file_path))
                else:
                    responses.append(
                        FileUploadResponse(
                            path=file_path,
                            error="permission_denied",
                        )
                    )
            except Exception as e:
                error_str = str(e).lower()
                if "permission denied" in error_str:
                    responses.append(
                        FileUploadResponse(path=file_path, error="permission_denied")
                    )
                else:
                    responses.append(FileUploadResponse(path=file_path, error=str(e)))

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the container.

        Args:
            paths: List of absolute file paths to download.

        Returns:
            List of FileDownloadResponse objects.
        """
        import docker.errors

        responses = []
        for path in paths:
            try:
                if not posixpath.isabs(path):
                    responses.append(
                        FileDownloadResponse(path=path, error="invalid_path")
                    )
                    continue

                # Check if it's a directory
                stat_cmd = f"test -d {shlex.quote(path)}"
                stat_res = self.execute(stat_cmd)
                if stat_res.exit_code == 0:
                    responses.append(
                        FileDownloadResponse(path=path, error="is_directory")
                    )
                    continue

                # get_archive returns a tuple: (stream, stat)
                stream, _ = self._container.get_archive(path)

                tar_stream = io.BytesIO(b"".join(stream))
                tar_stream.seek(0)

                with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                    member = tar.next()
                    if member is None:
                        responses.append(
                            FileDownloadResponse(path=path, error="file_not_found")
                        )
                        continue
                    file_obj = tar.extractfile(member)
                    if file_obj is None:
                        responses.append(
                            FileDownloadResponse(path=path, error="is_directory")
                        )
                        continue
                    file_content = file_obj.read()

                responses.append(FileDownloadResponse(path=path, content=file_content))
            except docker.errors.NotFound:
                responses.append(
                    FileDownloadResponse(path=path, error="file_not_found")
                )
            except Exception as e:
                error_str = str(e).lower()
                if "permission denied" in error_str:
                    responses.append(
                        FileDownloadResponse(path=path, error="permission_denied")
                    )
                else:
                    responses.append(FileDownloadResponse(path=path, error=str(e)))

        return responses
