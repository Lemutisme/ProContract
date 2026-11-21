"""One isolated code-mode session exposing only a frozen public evidence reader.

The host supplies pinned snapshot/binary paths and new execution identities. Model
code never gets filesystem/network tools or a way to choose another snapshot.
This is a tool surface, not yet the selector's model loop or a fork receipt.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
import select
import struct
import subprocess
import time
import uuid

from procontract_decision_replay import strict_json
from procontract_frozen_evidence import FrozenEvidence, QueryError
import procontract_operations as ops

TOOL = {"name": "contract_read_evidence",
        "tool_name": {"name": "contract_read_evidence", "namespace": None},
        "description": "Read only this invocation's frozen public evidence.", "kind": "function",
        "input_schema": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["index", "frontier", "sources", "read"]},
            "handle": {"type": "string"}, "source_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0}},
            "required": ["op"], "additionalProperties": False}, "output_schema": None}


class FrozenToolSession:
    def __init__(self, snapshot, snapshot_sha256, host_program, host_sha256, *,
                 execution, allocation, output_dir, deadline=None, max_reads=None,
                 max_heap_bytes=128 << 20, max_trace_bytes=16 << 20):
        if not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',x) for x in (execution, allocation)):
            raise ValueError("new execution/allocation must be named by the host")
        if max_reads is not None and (type(max_reads) is not int or max_reads < 0):
            raise ValueError("invalid new read allowance")
        if type(max_heap_bytes) is not int or max_heap_bytes <= 0:
            raise ValueError("invalid code-mode heap allowance")
        if type(max_trace_bytes) is not int or max_trace_bytes < 8192:
            raise ValueError("tool trace allowance must be at least 8 KiB")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)
                                     or deadline <= time.time()):
            raise ValueError("new allocation deadline is invalid or exhausted")
        with Path(host_program).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != host_sha256:
                raise ValueError("code-mode host differs from the registered binary")
        self.view = FrozenEvidence.load(snapshot, snapshot_sha256)
        self.execution, self.allocation = execution, allocation
        self.deadline, self.max_reads, self.reads = deadline, max_reads, 0
        self.max_trace_bytes, self.trace_bytes = max_trace_bytes, 0
        self.session = "frozen-" + uuid.uuid4().hex
        self.directory = Path(output_dir)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.trace = (self.directory / "tools.jsonl").open("x")
        self.stderr = (self.directory / "host.stderr").open("xb")
        self.next_id, self.pending, self.cells = 0, {}, set()
        self.notifications = []
        self.process = None
        self.closed = False
        self._record({"type": "open", "snapshot_sha256": snapshot_sha256,
                      "host_sha256": host_sha256, "deadline": deadline, "max_reads": max_reads,
                      "max_heap_bytes": max_heap_bytes, "max_trace_bytes": max_trace_bytes})
        try:
            # Inherit the operation's process group so owner cancellation also
            # reaches this child. No interpreter/shell executes the JS source.
            self.process = subprocess.Popen([str(host_program), "--listen", "stdio"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, bufsize=0,
                env={k:os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ})
            os.set_blocking(self.process.stdin.fileno(), False)
            self._send({"type": "connection/hello", "supportedVersions": [1],
                "requiredCapabilities": ["session-cell-execution-resource-limits"],
                "optionalCapabilities": ["bounded-output-v1"]})
            hello = self._receive()
            if hello.get("type") != "connection/ready" or hello.get("selectedVersion") != 1:
                raise ValueError("code-mode host did not accept the required protocol/limits")
            self._record({"type": "negotiated", "hello": hello})
            self.server_truncation = "bounded-output-v1" in hello.get("capabilities", [])
            request = {"method": "session/open", "sessionId": self.session,
                       "cellExecutionLimits": {"maxYieldTimeMs": 1000, "maxHeapSizeBytes": max_heap_bytes}}
            if self.server_truncation:
                request["outputTruncation"] = "server"
            self._operation(request)
        except BaseException:
            self.close()
            raise

    def _record(self, value):
        line = ops.encoded({"execution": self.execution, "allocation": self.allocation,
                           "at": time.time(), **value}) + "\n"
        if value.get("type") != "closed" and self.trace_bytes + len(line.encode()) > self.max_trace_bytes - 1024:
            raise ValueError("tool trace allocation exhausted")
        self.trace.write(line)
        self.trace_bytes += len(line.encode())
        self.trace.flush()

    def _remaining(self):
        if self.deadline is None:
            return None
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("new tool-session allocation expired")
        return remaining

    def _send(self, value):
        raw = ops.encoded(value).encode()
        if len(raw) > 64 << 20:
            raise ValueError("code-mode frame exceeds limit")
        data = memoryview(struct.pack("<I", len(raw)) + raw)
        while data:
            _, ready, _ = select.select([], [self.process.stdin], [], self._remaining())
            if not ready:
                raise TimeoutError("code-mode send exceeded allocation")
            try:
                written = os.write(self.process.stdin.fileno(), data)
            except BlockingIOError:
                continue
            data = data[written:]

    def _read_exact(self, count):
        parts = bytearray()
        while len(parts) < count:
            ready, _, _ = select.select([self.process.stdout], [], [], self._remaining())
            if not ready:
                raise TimeoutError("code-mode receive exceeded allocation")
            chunk = os.read(self.process.stdout.fileno(), count - len(parts))
            if not chunk:
                raise RuntimeError("code-mode host closed before its response")
            parts.extend(chunk)
        return bytes(parts)

    def _receive(self):
        count = struct.unpack("<I", self._read_exact(4))[0]
        if count > 64 << 20:
            raise ValueError("code-mode response exceeds frame limit")
        return strict_json(self._read_exact(count))

    def _delegate(self, message):
        if message.get("sessionId") != self.session:
            raise ValueError("callback belongs to another session")
        request = message["request"]
        self._record({"type": "delegate_request", "message": message})
        try:
            if request["type"] == "tool/invoke":
                invocation = request["invocation"]
                name = invocation["tool_name"]
                if (name != TOOL["tool_name"] or invocation["tool_kind"] != "function"):
                    raise QueryError("only contract_read_evidence is available")
                if self.max_reads is not None and self.reads >= self.max_reads:
                    raise QueryError("new execution's evidence-read allowance exhausted")
                self.reads += 1
                query = invocation["input"]
                try:
                    value = self.view.read(query)
                except QueryError as error:
                    self._record({"type":"read", "invocation":invocation, "error":str(error)})
                    raise
                self._record({"type":"read", "invocation":invocation, "result":value})
                value = {"type": "tool/result", "result": value}
            elif request["type"] == "notification/send":
                # Data for this model exchange, never a message to another person/service.
                self._record({"type": "notification", "request": request})
                self.notifications.append(request)
                value = {"type": "notification/delivered"}
            else:
                raise QueryError("unsupported delegate capability")
            response = {"status": "ok", "value": value}
        except QueryError as error:
            response = {"status": "error", "message": str(error)}
        self._record({"type": "delegate_response", "id": message["id"], "result": response})
        self._send({"type": "delegate/response", "id": message["id"], "result": response})

    def _wait(self, kind, identity):
        key = (kind, identity)
        while key not in self.pending:
            message = self._receive()
            typ = message.get("type")
            if typ == "delegate/request":
                self._delegate(message)
            elif typ == "cell/closed":
                self._record({"type": "cell_closed", "message": message})
            elif typ == "delegate/cancel":
                self._record({"type": "delegate_cancelled", "message": message})
            elif typ in ("operation/response", "execute/initialResponse"):
                self.pending[(typ, message["id"])] = message["result"]
            else:
                raise ValueError("unexpected code-mode message")
        result = self.pending.pop(key)
        if result["status"] != "ok":
            raise RuntimeError(result.get("message", "code-mode operation failed"))
        return result["value"]

    def _operation(self, request, initial=False):
        self.next_id += 1
        identity = self.next_id
        self._send({"type": "operation/request", "id": identity, "request": request})
        result = self._wait("operation/response", identity)
        if initial:
            self.cells.add(result["cellId"])
            result = self._wait("execute/initialResponse", identity)
        return result

    def execute(self, source, call_id, *, yield_time_ms=10000, max_output_tokens=10000):
        if self.closed:
            raise ValueError("tool session is closed")
        if not isinstance(source, str) or len(source.encode()) > 1 << 20:
            raise ValueError("exec source must be text within 1 MiB")
        self._record({"type": "execute", "call_id": call_id, "source": source})
        result = self._operation({"method": "session/execute", "sessionId": self.session,
            "request": {"tool_call_id": call_id, "enabled_tools": [TOOL], "source": source,
                        "yield_time_ms": yield_time_ms, "max_output_tokens": max_output_tokens}}, initial=True)
        self._record({"type": "execute_result", "call_id": call_id, "result": result})
        return result

    def wait(self, cell_id, *, terminate=False, yield_time_ms=10000, max_output_tokens=10000):
        if self.closed or cell_id not in self.cells:
            raise ValueError("cell does not belong to this active execution")
        if terminate:
            request = {"method": "session/terminate", "sessionId": self.session,
                       "cellId": cell_id}
        else:
            request = {"method": "session/wait", "sessionId": self.session,
                "request": {"cell_id": cell_id, "yield_time_ms": yield_time_ms}}
        if self.server_truncation:
            request["maxOutputTokens"] = max_output_tokens
        self._record({"type": "wait", "request": request,
                      "max_output_tokens":max_output_tokens,"output_budget_owner":"server" if self.server_truncation else "client"})
        result = self._operation(request)["outcome"]
        self._record({"type": "wait_result", "cell_id": cell_id, "result": result})
        return result

    def take_notifications(self):
        found,self.notifications=self.notifications,[]
        return found

    def close(self):
        if self.closed:
            return
        graceful = False
        # Cleanup has its own bounded wait; it never extends the execution grant.
        self.deadline = min(self.deadline, time.time()+5) if self.deadline is not None else time.time()+5
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self._operation({"method": "session/shutdown", "sessionId": self.session})
                    graceful = True
            except Exception:
                pass
            finally:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
                self.process.stdout.close()
        self._record({"type": "closed", "graceful": graceful, "local_closed": True,
                      "reads": self.reads, "pid": self.process.pid if self.process else None})
        os.fsync(self.trace.fileno())
        self.trace.close(); self.stderr.close(); self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
