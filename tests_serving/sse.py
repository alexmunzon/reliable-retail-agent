"""Incremental SSE parser used by tests: survives arbitrary chunk and UTF-8 boundaries."""
from __future__ import annotations

import codecs


class SSEParser:
    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._fields: dict[str, list[str]] = {}
        self.comments = 0

    def feed(self, chunk: bytes) -> list[dict]:
        self._buffer += self._decoder.decode(chunk)
        events = []
        while True:
            cut = min((i for i in (self._buffer.find("\r\n"), self._buffer.find("\n"), self._buffer.find("\r")) if i >= 0),
                      default=-1)
            if cut < 0:
                return events
            width = 2 if self._buffer.startswith("\r\n", cut) else 1
            if self._buffer[cut] == "\r" and cut + 1 == len(self._buffer):
                return events  # A lone CR may be the first half of CRLF; wait for more input.
            line, self._buffer = self._buffer[:cut], self._buffer[cut + width:]
            if not line:
                if "data" in self._fields or "id" in self._fields:
                    events.append({"id": self._fields.get("id", [None])[-1],
                                   "event": self._fields.get("event", ["message"])[-1],
                                   "data": "\n".join(self._fields.get("data", []))})
                self._fields = {}
            elif line.startswith(":"):
                self.comments += 1
            else:
                name, _, value = line.partition(":")
                self._fields.setdefault(name, []).append(value[1:] if value.startswith(" ") else value)
