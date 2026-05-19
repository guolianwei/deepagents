"""Tests for the Docker sandbox backend."""

from __future__ import annotations

import io
import tarfile

from deepagents.backends.docker import DockerSandbox


class FakeContainer:
    def __init__(self) -> None:
        self.id = "container-123"
        self.exec_calls: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/workspace"}
        self.put_archive_error: Exception | None = None
        self.get_archive_error: Exception | None = None

    def exec_run(
        self,
        cmd: list[str],
        *,
        workdir: str | None = None,
        demux: bool = False,
    ) -> tuple[int, bytes | None]:
        self.exec_calls.append({"cmd": cmd, "workdir": workdir, "demux": demux})
        command = cmd[-1]
        if command == "pwd":
            return 0, f"{workdir}\n".encode()
        if command.startswith("mkdir -p "):
            self.dirs.add(command.removeprefix("mkdir -p ").strip("'"))
            return 0, b""
        if command.startswith("test -d "):
            path = command.removeprefix("test -d ").strip("'")
            return (0, b"") if path in self.dirs else (1, b"")
        return 0, b"ok"

    def put_archive(self, path: str, data: bytes) -> bool:
        if self.put_archive_error is not None:
            raise self.put_archive_error
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            member = tar.next()
            assert member is not None
            file_obj = tar.extractfile(member)
            assert file_obj is not None
            self.files[f"{path}/{member.name}"] = file_obj.read()
        return True

    def get_archive(self, path: str) -> tuple[list[bytes], dict]:
        if self.get_archive_error is not None:
            raise self.get_archive_error
        if path not in self.files:
            msg = "not found"
            raise RuntimeError(msg)

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            content = self.files[path]
            info = tarfile.TarInfo(name=path.rsplit("/", 1)[-1])
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        return [tar_stream.getvalue()], {}


def test_execute_uses_sh_and_workdir() -> None:
    container = FakeContainer()
    sandbox = DockerSandbox(container=container, workdir="/workspace")

    result = sandbox.execute("pwd")

    assert result.output == "/workspace\n"
    assert result.exit_code == 0
    assert container.exec_calls[0] == {
        "cmd": ["/bin/sh", "-lc", "pwd"],
        "workdir": "/workspace",
        "demux": False,
    }


def test_upload_and_download_file_round_trip() -> None:
    container = FakeContainer()
    sandbox = DockerSandbox(container=container)

    upload = sandbox.upload_files([("/workspace/a.txt", b"hello")])
    download = sandbox.download_files(["/workspace/a.txt"])

    assert upload[0].error is None
    assert download[0].content == b"hello"
    assert download[0].error is None


def test_rejects_relative_paths() -> None:
    sandbox = DockerSandbox(container=FakeContainer())

    assert sandbox.upload_files([("a.txt", b"x")])[0].error == "invalid_path"
    assert sandbox.download_files(["a.txt"])[0].error == "invalid_path"


def test_download_missing_file_maps_to_file_not_found() -> None:
    sandbox = DockerSandbox(container=FakeContainer())

    result = sandbox.download_files(["/workspace/missing.txt"])

    assert result[0].error == "file_not_found"


def test_download_directory_maps_to_is_directory() -> None:
    container = FakeContainer()
    container.dirs.add("/workspace/dir")
    sandbox = DockerSandbox(container=container)

    result = sandbox.download_files(["/workspace/dir"])

    assert result[0].error == "is_directory"


def test_upload_permission_error_maps_to_permission_denied() -> None:
    container = FakeContainer()
    container.put_archive_error = RuntimeError("permission denied")
    sandbox = DockerSandbox(container=container)

    result = sandbox.upload_files([("/workspace/a.txt", b"x")])

    assert result[0].error == "permission_denied"
