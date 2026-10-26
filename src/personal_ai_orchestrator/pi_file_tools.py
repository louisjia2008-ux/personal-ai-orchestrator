"""Descriptor-based filesystem operations for the six host-owned Pi tools.

This module is also an isolated, one-request helper. It imports no project code,
executes no project programs and never follows links. The worker has no shell or
directory-rename tool; an unrelated process with the owner's OS permissions is
outside this boundary. Ignored Git metadata is never searched.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import sys
from functools import lru_cache

from pathspec import GitIgnoreSpec

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ENTRIES = 20000


class ConfinedFiles:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def close(self) -> None:
        os.close(self.fd)

    def parts(self, path: str) -> list[str]:
        absolute = os.path.abspath(os.path.join(self.root, path))
        if os.path.commonpath((self.root, absolute)) != self.root:
            raise ValueError("path escapes assigned worktree")
        relative = os.path.relpath(absolute, self.root)
        parts = [] if relative == "." else relative.split(os.sep)
        if any(part.casefold() in {".git", ".pao"} for part in parts):
            raise ValueError("protected host path")
        return parts

    def directory(self, parts: list[str], *, create: bool = False) -> int:
        current = os.dup(self.fd)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=current)
                    except FileExistsError:
                        pass
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                os.close(current)
                current = following
            return current
        except BaseException:
            os.close(current)
            raise

    def info(self, path: str) -> os.stat_result:
        parts = self.parts(path)
        if not parts:
            return os.fstat(self.fd)
        parent = self.directory(parts[:-1])
        try:
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("links and special files are unavailable")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError("hard-linked files are unavailable")
            return info
        finally:
            os.close(parent)

    def read(self, path: str) -> bytes:
        parts = self.parts(path)
        if not parts:
            raise ValueError("expected a file")
        parent = self.directory(parts[:-1])
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("expected an unlinked regular file")
            if before.st_size > MAX_FILE_BYTES:
                raise ValueError("file exceeds tool size limit")
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (
                len(data) > MAX_FILE_BYTES
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError("file changed while reading")
            return data

    def write(self, path: str, content: bytes) -> None:
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("file exceeds tool size limit")
        parts = self.parts(path)
        if not parts:
            raise ValueError("expected a file")
        parent = self.directory(parts[:-1], create=True)
        try:
            fd = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o644,
                dir_fd=parent,
            )
        finally:
            os.close(parent)
        with os.fdopen(fd, "wb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("expected an unlinked regular file")
            stream.truncate(0)
            stream.write(content)

    def entries(self, path: str) -> list[str]:
        directory = self.directory(self.parts(path))
        try:
            return sorted(
                name for name in os.listdir(directory) if name.casefold() not in {".git", ".pao"}
            )
        finally:
            os.close(directory)

    def walk(self, path: str) -> list[str]:
        found: list[str] = []
        inherited: list[tuple[str, GitIgnoreSpec]] = []
        components = self.parts(path)
        for depth in range(len(components)):
            origin = os.path.join(self.root, *components[:depth])
            try:
                ignore = self.read(os.path.join(origin, ".gitignore")).decode("utf-8")
                inherited.append((origin, GitIgnoreSpec.from_lines(ignore.splitlines())))
            except (OSError, ValueError, UnicodeError):
                pass
        pending: list[tuple[str, list[tuple[str, GitIgnoreSpec]]]] = [(path, inherited)]
        visited = 0
        while pending:
            directory, inherited = pending.pop()
            rules = list(inherited)
            try:
                ignore = self.read(os.path.join(directory, ".gitignore")).decode("utf-8")
                rules.append((directory, GitIgnoreSpec.from_lines(ignore.splitlines())))
            except (OSError, ValueError, UnicodeError):
                pass
            for name in self.entries(directory):
                visited += 1
                if visited > MAX_ENTRIES:
                    raise ValueError("search exceeds tool entry limit; narrow the path")
                child = os.path.join(directory, name)
                try:
                    info = self.info(child)
                except (OSError, ValueError):
                    continue
                is_directory = stat.S_ISDIR(info.st_mode)
                ignored = False
                for origin, specification in rules:
                    relative = os.path.relpath(child, origin) + ("/" if is_directory else "")
                    match = specification.check_file(relative)
                    if match.include is not None:
                        ignored = match.include
                if ignored:
                    continue
                if is_directory:
                    pending.append((child, rules))
                else:
                    found.append(child)
        return sorted(found)


@lru_cache(maxsize=128)
def _glob_spec(pattern: str) -> GitIgnoreSpec:
    # Treat leading comment/negation markers as literal file-name characters.
    literal = "\\" + pattern if pattern.startswith(("#", "!")) else pattern
    return GitIgnoreSpec.from_lines([literal])


def _matches(path: str, pattern: str) -> bool:
    return _glob_spec(pattern).match_file(path)


def operate(files: ConfinedFiles, request: dict) -> object:
    operation = request["operation"]
    path = request.get("path", ".")
    if not isinstance(path, str):
        raise ValueError("missing path")
    if operation == "read":
        return base64.b64encode(files.read(path)).decode("ascii")
    if operation == "write":
        files.write(path, request["content"].encode("utf-8"))
        return None
    if operation == "mkdir":
        directory = files.directory(files.parts(path), create=True)
        os.close(directory)
        return None
    if operation == "stat":
        return {"directory": stat.S_ISDIR(files.info(path).st_mode)}
    if operation == "ls":
        return files.entries(path)
    if operation == "find":
        limit = max(1, min(10000, int(request.get("limit", 1000))))
        return [
            os.path.relpath(item, path)
            for item in files.walk(path)
            if _matches(os.path.relpath(item, path), request["pattern"])
        ][:limit]
    if operation == "grep":
        pattern = request["pattern"]
        if request.get("literal"):
            pattern = re.escape(pattern)
        expression = re.compile(pattern, re.IGNORECASE if request.get("ignoreCase") else 0)
        limit = max(1, min(1000, int(request.get("limit", 100))))
        context = max(0, min(20, int(request.get("context", 0))))
        directory = stat.S_ISDIR(files.info(path).st_mode)
        candidates = files.walk(path) if directory else [path]
        output: list[str] = []
        count = 0
        size = 0
        for item in candidates:
            label = os.path.relpath(item, path) if directory else os.path.basename(item)
            if request.get("glob") and not _matches(label, request["glob"]):
                continue
            try:
                raw = files.read(item)
            except (OSError, ValueError):
                continue
            if b"\0" in raw:
                continue
            lines = raw.decode("utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines):
                if not expression.search(line):
                    continue
                for at in range(max(0, index - context), min(len(lines), index + context + 1)):
                    result = f"{label}:{at + 1}:{lines[at][:500]}"
                    size += len(result.encode("utf-8"))
                    if size > MAX_RESPONSE_BYTES:
                        return "\n".join(output) + "\n[output limit reached]"
                    output.append(result)
                count += 1
                if count >= limit:
                    return "\n".join(output) + "\n[match limit reached]"
        return "\n".join(output) or "No matches found"
    raise ValueError("unknown file operation")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    files: ConfinedFiles | None = None
    try:
        if len(arguments) != 2:
            raise ValueError("expected worktree root")
        request = json.loads(sys.stdin.buffer.read(2 * MAX_FILE_BYTES + 1))
        files = ConfinedFiles(arguments[0])
        root_info = os.fstat(files.fd)
        if arguments[1] != f"{root_info.st_dev}:{root_info.st_ino}":
            raise ValueError("worktree root identity changed")
        result = operate(files, request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True))
        return 0
    except Exception:
        # No file contents, absolute paths, or input text in diagnostic output.
        print(json.dumps({"ok": False, "error": "PAO file operation denied or unavailable"}))
        return 1
    finally:
        if files is not None:
            files.close()


if __name__ == "__main__":
    raise SystemExit(main())
