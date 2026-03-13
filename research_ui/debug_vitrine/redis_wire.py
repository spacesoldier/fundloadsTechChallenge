from __future__ import annotations

import socket

from .helpers import redis_str


def encode_command(parts: list[object]) -> bytes:
    chunks = [f"*{len(parts)}\r\n".encode("utf-8")]
    for part in parts:
        raw = redis_str(part).encode("utf-8")
        chunks.append(f"${len(raw)}\r\n".encode("utf-8"))
        chunks.append(raw + b"\r\n")
    return b"".join(chunks)


def read_reply(conn: socket.socket) -> object:
    token = recv_exact(conn, 1)
    if not token:
        return None
    marker = token.decode("ascii", errors="ignore")
    if marker == "+":
        return readline(conn)
    if marker == "-":
        message = readline(conn)
        raise RuntimeError(f"redis error: {message}")
    if marker == ":":
        raw = readline(conn)
        try:
            return int(raw)
        except Exception:
            return 0
    if marker == "$":
        raw = readline(conn)
        try:
            length = int(raw)
        except Exception:
            return None
        if length < 0:
            return None
        data = recv_exact(conn, length)
        _ = recv_exact(conn, 2)
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if marker == "*":
        raw = readline(conn)
        try:
            count = int(raw)
        except Exception:
            return []
        if count < 0:
            return []
        return [read_reply(conn) for _ in range(count)]
    return None


def readline(conn: socket.socket) -> str:
    data = bytearray()
    while True:
        chunk = recv_exact(conn, 1)
        if not chunk:
            break
        data.extend(chunk)
        if data.endswith(b"\r\n"):
            break
    if data.endswith(b"\r\n"):
        data = data[:-2]
    return data.decode("utf-8", errors="ignore")


def recv_exact(conn: socket.socket, size: int) -> bytes:
    if size <= 0:
        return b""
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)
