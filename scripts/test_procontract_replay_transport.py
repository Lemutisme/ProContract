"""The replay transport against a scripted server on this host. No model and no provider."""

import gzip
import hashlib
import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import procontract_replay_transport as transport

RESPONSE = {"id": "resp-1", "object": "response", "status": "completed", "model": "model-x",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}}


def event(kind, **body):
    return ("event: %s\ndata: %s\n\n" % (kind, json.dumps(dict(body, type=kind)))).encode()


CREATED = event("response.created", response={"id": "resp-1", "model": "model-x", "status": "in_progress"})
COMPLETED = event("response.completed", response=RESPONSE)
FAILED = event("response.failed", response=dict(RESPONSE, status="failed", usage=None, error={"code": "server_error"}))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, content_type, body, **headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Connection", "close")
        self.send_header("X-Request-ID", "wire-%d" % len(self.server.seen))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        if "Transfer_Encoding" not in headers and "Content_Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def chunk(self, data):
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.flush()

    def drop(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        mode = self.server.mode
        if mode == "sse":
            self.reply(200, "text/event-stream", CREATED + COMPLETED + b"data: [DONE]\n\n")
        elif mode == "json":
            self.reply(200, "application/json", json.dumps(RESPONSE).encode())
        elif mode == "failed":
            self.reply(200, "text/event-stream", CREATED + FAILED)
        elif mode == "no_terminal":
            self.reply(200, "text/event-stream", CREATED)
        elif mode == "gzip":
            self.reply(200, "text/event-stream", gzip.compress(CREATED + COMPLETED), Content_Encoding="gzip")
        elif mode == "error":
            self.reply(429, "application/json", json.dumps({"error": {"type": "rate_limit_error", "message": "slow down"}}).encode(),
                       Retry_After="7")
        elif mode == "redirect":
            self.reply(302, "text/plain", b"", Location="/elsewhere")
        elif mode == "disconnect":
            self.drop()
        elif mode == "short":
            self.reply(200, "text/event-stream", CREATED, Content_Length=str(len(CREATED) + 400))
            self.drop()
        elif mode == "partial_terminal":
            body = CREATED + COMPLETED.rstrip(b"\n")
            self.reply(200, "text/event-stream", body, Content_Length=str(len(body) + 400))
            self.drop()
        elif mode in ("chunk_break", "terminal_then_break", "chunked", "stall"):
            self.reply(200, "text/event-stream", b"", Transfer_Encoding="chunked")
            self.chunk(CREATED)
            if mode == "stall":
                self.server.release.wait(30)
            if mode in ("terminal_then_break", "chunked"):
                self.chunk(COMPLETED)
            if mode == "chunked":
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                self.drop()


class TransportTest(unittest.TestCase):
    BODY = b'{ "model":"model-x",  "input":[{"role":"user","content":"\\u4f60\\u597d"}], "stream":true,\n "snapshot":{"remaining":1.0,"limit":null} }\n'

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.seen, self.server.mode, self.server.release = [], "sse", threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(self.stop)
        self.root, self.count = Path(directory.name), 0
        self.endpoint = "http://127.0.0.1:%d/v1/responses" % self.server.server_port

    def stop(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def send(self, mode="sse", body=None, **more):
        self.server.mode = mode
        self.count += 1
        body = self.BODY if body is None else body
        arguments = {"headers": {"Content-Type": "application/json", "Authorization": "Bearer s3cr3t-credential"},
                     "execution_id": "execution-new", "allocation_id": "allocation-new", "deadline": time.time() + 20,
                     "output_dir": self.root / ("send-%d" % self.count)}
        arguments.update(more)
        sha = arguments.pop("sha", hashlib.sha256(body).hexdigest())
        endpoint = arguments.pop("endpoint", self.endpoint)
        return transport.send(body, sha, endpoint, **arguments), arguments["output_dir"]

    def test_the_kept_bytes_are_sent_once_as_they_are(self):
        result, directory = self.send()
        self.assertEqual(len(self.server.seen), 1)
        got = self.server.seen[0]
        self.assertEqual(got["body"], self.BODY)                       # spacing, order, escapes and the last newline
        self.assertEqual(got["path"], "/v1/responses")
        self.assertEqual(sorted(got["headers"]), ["accept-encoding", "authorization", "content-length", "content-type", "host"])
        self.assertEqual((got["headers"]["accept-encoding"], got["headers"]["content-length"]), ("identity", str(len(self.BODY))))
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["remote"], {"terminal_observed": True, "terminal": "response.completed", "state": "response.completed"})
        self.assertEqual((result["said"]["response_id"], result["said"]["model"], result["said"]["usage"]),
                         ("resp-1", "model-x", RESPONSE["usage"]))
        self.assertTrue(result["said"]["done_marker"])
        self.assertEqual(result["http"]["status"], 200)
        self.assertEqual(result["response"]["request_id"], "wire-1")
        raw = (directory / "response.raw").read_bytes()
        self.assertEqual(raw, CREATED + COMPLETED + b"data: [DONE]\n\n")
        self.assertEqual((result["response"]["bytes"], result["response"]["sha256"], result["response"]["complete"]),
                         (len(raw), hashlib.sha256(raw).hexdigest(), True))
        self.assertEqual(result["local"], {"socket_closed": True, "watchdog_stopped": True, "deadline_fired": False, "requests_sent": 1})
        self.assertEqual((result["execution_id"], result["allocation_id"]), ("execution-new", "allocation-new"))
        self.assertEqual(json.loads((directory / "result.json").read_text()), result)
        started = json.loads((directory / "started.json").read_text())
        self.assertEqual(started["request"], result["request"])
        self.assertEqual(result["request"]["sha256"], hashlib.sha256(self.BODY).hexdigest())
        self.assertNotIn("x-execution-id", got["headers"])              # an identity goes on the wire only if the caller sends it
        again, _ = self.send(headers={"Content-Type": "application/json", "X-Execution-ID": "execution-new", "X-Allocation-ID": "a"})
        self.assertEqual(self.server.seen[1]["headers"]["x-execution-id"], "execution-new")
        self.assertEqual(again["request"]["headers"]["x-execution-id"], "execution-new")

    def test_a_credential_is_neither_written_nor_returned(self):
        result, directory = self.send(headers={"Content-Type": "application/json", "Authorization": "Bearer s3cr3t-credential",
                                               "X-Org-Key": "org-key-12345"},
                                      endpoint=self.endpoint + "?api-version=2026-01&key=query-secret-999")
        self.assertEqual(self.server.seen[0]["path"], "/v1/responses?api-version=2026-01&key=query-secret-999")
        self.assertEqual(self.server.seen[0]["headers"]["authorization"], "Bearer s3cr3t-credential")
        self.assertEqual(result["request"]["headers"]["authorization"], None)          # the name, not the value
        self.assertEqual(result["request"]["headers"]["x-org-key"], None)
        self.assertEqual(result["request"]["headers"]["content-type"], "application/json")
        self.assertEqual(result["request"]["endpoint"]["query_names"], ["api-version", "key"])
        everything = json.dumps(result).encode() + b"".join(path.read_bytes() for path in directory.iterdir())
        for secret in (b"s3cr3t-credential", b"org-key-12345", b"query-secret-999"):
            self.assertNotIn(secret, everything)

    def test_what_is_refused_sends_nothing_and_writes_nothing(self):
        good = {"Content-Type": "application/json"}
        refusals = [
            (dict(sha="0" * 64), "not the bytes its SHA-256 names"),
            (dict(sha="abc"), "named by its SHA-256"),
            (dict(body="text"), "the bytes that were kept"),
            (dict(headers={}), "states the content type"),
            (dict(headers=dict(good, **{"Transfer-Encoding": "chunked"})), "sent whole"),
            (dict(headers=dict(good, **{"Content-Length": "3"})), "stated content length"),
            (dict(headers=dict(good, **{"content-type": "text/plain"})), "given once"),
            (dict(headers=dict(good, **{"X-A": "line\nbreak"})), "on one line"),
            (dict(headers=dict(good, **{"X-A": "你"})), "Latin-1"),
            (dict(headers=[("Content-Type", "application/json")]), "names and values"),
            (dict(execution_id=""), "execution_id"),
            (dict(allocation_id=None), "allocation_id"),
            (dict(deadline=time.time() - 1), "deadline has passed"),
            (dict(deadline=True), "positive number"),
            (dict(idle_timeout=0), "positive number"),
            (dict(connect_timeout=None), "connecting has a time limit"),
            (dict(endpoint="http://example.com/v1/responses"), "only to this host"),
            (dict(endpoint="ftp://127.0.0.1/x"), "http or https URL"),
            (dict(endpoint="https://user:pw@127.0.0.1/x"), "without credentials"),
        ]
        for change, message in refusals:
            self.count += 1
            directory = self.root / ("refused-%d" % self.count)
            body = change.pop("body", self.BODY)
            sha = change.pop("sha", hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else "0" * 64)
            arguments = {"headers": dict(good), "execution_id": "e", "allocation_id": "a", "deadline": None, "output_dir": directory}
            endpoint = change.pop("endpoint", self.endpoint)
            arguments.update(change)
            with self.assertRaisesRegex(ValueError, message, msg=message):
                transport.send(body, sha, endpoint, **arguments)
            self.assertFalse(directory.exists(), message)
        self.assertEqual(self.server.seen, [])

    def test_a_directory_holds_one_send(self):
        result, directory = self.send()
        with self.assertRaisesRegex(ValueError, "already holds a send"):
            self.send(output_dir=directory)
        self.assertEqual(len(self.server.seen), 1)
        self.assertEqual(json.loads((directory / "result.json").read_text()), result)

    def test_an_error_status_is_kept_and_not_retried(self):
        result, directory = self.send("error")
        self.assertEqual(len(self.server.seen), 1)
        self.assertEqual((result["outcome"], result["http"]["status"], result["http"]["headers"]["retry-after"]), ("http_error", 429, "7"))
        self.assertEqual(result["said"]["error"], {"type": "rate_limit_error", "message": "slow down"})
        self.assertEqual(result["said"]["usage"], None)                                    # unknown, not zero
        self.assertEqual(result["remote"], {"terminal_observed": True, "terminal": "http_429", "state": "http_429"})
        self.assertIn(b"rate_limit_error", (directory / "response.raw").read_bytes())

    def test_a_redirect_is_not_followed(self):
        result, _ = self.send("redirect")
        self.assertEqual(len(self.server.seen), 1)
        self.assertEqual((result["outcome"], result["http"]["status"], result["remote"]["terminal"]),
                         ("redirect_not_followed", 302, "http_302"))

    def test_a_terminal_other_than_completed_is_the_providers_failure(self):
        result, _ = self.send("failed")
        self.assertEqual((result["outcome"], result["remote"]["terminal"]), ("provider_failed", "response.failed"))
        self.assertEqual((result["said"]["status"], result["said"]["error"]), ("failed", {"code": "server_error"}))
        result, _ = self.send("json")
        self.assertEqual((result["outcome"], result["said"]["usage"], result["said"]["events"]), ("completed", RESPONSE["usage"], None))
        result, _ = self.send("chunked")
        self.assertEqual((result["outcome"], result["response"]["complete"], result["said"]["events"]), ("completed", True, 2))

    def test_a_reply_that_does_not_say_how_it_ended_leaves_the_remote_state_unknown(self):
        result, _ = self.send("no_terminal")
        self.assertEqual((result["outcome"], result["response"]["complete"]), ("no_terminal_event", True))
        self.assertEqual(result["remote"], {"terminal_observed": False, "terminal": None, "state": "unknown"})
        self.assertEqual(result["said"]["response_id"], "resp-1")                         # what it did say is kept
        result, directory = self.send("gzip")
        self.assertEqual((result["outcome"], result["remote"]["state"]), ("no_terminal_event", "unknown"))
        self.assertIn("content-encoded", result["said"]["unread"])
        self.assertEqual(gzip.decompress((directory / "response.raw").read_bytes()), CREATED + COMPLETED)

    def test_a_broken_exchange_is_closed_here_and_unknown_there(self):
        for mode, got_status, kept in (("disconnect", False, b""), ("short", True, CREATED),
                                      ("chunk_break", True, CREATED),
                                      ("partial_terminal", True, CREATED + COMPLETED.rstrip(b"\n"))):
            before = len(self.server.seen)
            result, directory = self.send(mode)
            self.assertEqual(len(self.server.seen), before + 1, mode)                     # one request, no second try
            self.assertEqual(result["outcome"], "interrupted", mode)
            self.assertEqual(result["http"] is not None, got_status, mode)
            self.assertEqual(result["response"]["complete"], False, mode)
            self.assertEqual((directory / "response.raw").read_bytes(), kept, mode)       # what arrived is kept
            self.assertEqual(result["remote"], {"terminal_observed": False, "terminal": None, "state": "unknown"}, mode)
            self.assertEqual((result["local"]["socket_closed"], result["local"]["watchdog_stopped"], result["local"]["requests_sent"]),
                             (True, True, 1), mode)
        # A terminal event that arrived stays said, even though the connection then broke.
        result, _ = self.send("terminal_then_break")
        self.assertEqual((result["outcome"], result["response"]["complete"]), ("interrupted", False))
        self.assertEqual(result["remote"], {"terminal_observed": True, "terminal": "response.completed", "state": "response.completed"})

    def test_the_deadline_shuts_the_socket_and_claims_nothing_about_the_provider(self):
        began = time.time()
        result, directory = self.send("stall", deadline=time.time() + 0.6)
        self.assertLess(time.time() - began, 10)
        self.assertEqual(result["outcome"], "deadline")
        self.assertEqual(result["remote"], {"terminal_observed": False, "terminal": None, "state": "unknown"})
        self.assertEqual(result["local"], {"socket_closed": True, "watchdog_stopped": True, "deadline_fired": True, "requests_sent": 1})
        self.assertEqual((directory / "response.raw").read_bytes(), CREATED)
        self.assertEqual(len(self.server.seen), 1)
        self.server.release.set()
        # A silence longer than the idle limit ends the same way, by its own name.
        self.server.release = threading.Event()
        result, _ = self.send("stall", deadline=None, idle_timeout=0.5)
        self.assertEqual((result["outcome"], result["failure"]["class"], result["remote"]["state"]), ("interrupted", "TimeoutError", "unknown"))
        self.assertEqual(result["local"]["deadline_fired"], False)

    def test_no_connection_means_nothing_left_this_host(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        result, directory = self.send(endpoint="http://127.0.0.1:%d/v1/responses" % port)
        self.assertEqual((result["outcome"], result["stage_reached"], result["http"]), ("not_sent", "connect", None))
        self.assertEqual(result["remote"], {"terminal_observed": False, "terminal": None, "state": "not_reached"})
        self.assertEqual((result["local"]["requests_sent"], result["response"]["bytes"]), (0, 0))
        self.assertEqual(result["failure"]["class"], "ConnectionRefusedError")
        self.assertEqual(self.server.seen, [])

    def test_response_file_is_closed_before_recording_local_closure(self):
        replies, at_write = [], []
        original_get = transport.http.client.HTTPConnection.getresponse
        original_write = transport._write
        def get(connection):
            response = original_get(connection)
            replies.append(response)
            return response
        def write(path, value):
            if path.name == "result.json":
                at_write.append(all(reply.isclosed() for reply in replies))
            return original_write(path, value)
        with patch.object(transport.http.client.HTTPConnection, "getresponse", get), \
             patch.object(transport, "_write", write):
            result, _ = self.send("stall", deadline=None, idle_timeout=0.05)
        self.assertEqual(len(replies), 1)
        self.assertEqual(at_write, [True])
        self.assertTrue(result["local"]["socket_closed"])
        self.assertEqual(result["remote"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
