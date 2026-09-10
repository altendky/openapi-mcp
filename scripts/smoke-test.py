#!/usr/bin/env python3
"""Exercise a real stdio MCP process against a local HTTP API."""

import argparse
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BINARY = bytes([0, 255, 10, 13, 128])


class Api(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        self.requests.append((self.command, self.path, dict(self.headers), body))
        path = urlsplit(self.path).path
        status, content_type, response = 200, "application/json", body or b"{}"
        if path.endswith("/content"):
            content_type, response = "application/octet-stream", BINARY
        elif path.endswith("/busy"):
            status, response = 503, b'{"code":"capacity"}'
        elif path.endswith("/assets"):
            status, response = 201, b'{"uploaded":true}'
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        if status == 503:
            self.send_header("Retry-After", "7")
        self.end_headers()
        self.wfile.write(response)

    do_PATCH = handle_request
    do_POST = handle_request
    do_GET = handle_request


class Mcp:
    def __init__(self, command, cwd, env):
        self.stderr = tempfile.TemporaryFile(mode="w+t")
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr, text=True)
        self.responses = queue.Queue()
        self.next_id = 0

        def receive():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)

        self.reader = threading.Thread(target=receive, daemon=True)
        self.reader.start()

    def send(self, message):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params or {}})
        while True:
            line = self.responses.get(timeout=15)
            if line is None:
                self.stderr.seek(0)
                raise AssertionError("MCP process exited: " + self.stderr.read())
            message = json.loads(line)
            if message.get("id") == self.next_id:
                return message

    def initialize(self):
        response = self.request("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "openapi-mcp-smoke", "version": "1.0"},
        })
        assert response["result"]["serverInfo"]["name"] == "openapi-mcp", response
        self.send({"method": "notifications/initialized"})

    def call(self, name, arguments):
        response = self.request("tools/call", {"name": "media_" + name, "arguments": arguments})
        assert "error" not in response, response
        return response["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
            assert self.process.returncode == 0, self.process.returncode
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
            self.reader.join(timeout=2)
            self.stderr.close()


def data(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def run(command):
    command = [str(Path(arg).resolve()) if Path(arg).is_file() else arg for arg in command]
    api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
    worker = threading.Thread(target=api.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="openapi-mcp-smoke-") as temp:
            directory = Path(temp)
            source = ROOT / "crates/openapi-mcp-io/tests/fixtures/media-catalog.json"
            (directory / "spec.json").write_text(source.read_text())
            (directory / "upload.bin").write_bytes(BINARY)
            config = {
                "spec": "spec.json", "base_url": f"http://127.0.0.1:{api.server_port}/api/v2/",
                "headers": {"X-Configured": "from-config", "X-Workspace": "configured-workspace"},
                "bearer_token_env": "OPENAPI_MCP_SMOKE_TOKEN", "tool_prefix": "media",
            }
            path = directory / "config.json"
            path.write_text(json.dumps(config))
            env = {**os.environ, "OPENAPI_MCP_SMOKE_TOKEN": "test-token", "NO_PROXY": "127.0.0.1"}
            env.pop("OPENAPI_MCP_BEARER_TOKEN", None)
            client = Mcp([*command, "--config", str(path), "--header", "X-Configured=from-cli"], temp, env)
            try:
                client.initialize()
                listed = client.request("tools/list")["result"]["tools"]
                assert [tool["name"] for tool in listed] == ["media_search", "media_explain", "media_call", "media_schema"]
                assert "test-token" not in json.dumps(listed) and "configured-workspace" not in json.dumps(listed)
                assert len(data(client.call("search", {"query": "updateItem"}))) == 1
                assert data(client.call("explain", {"endpoint": "updateItem"}))["method"] == "PATCH"
                assert data(client.call("schema", {"schema": "ItemPatch"}))["required"] == ["title"]
                assert Api.requests == [], Api.requests
                result = client.call("call", {
                    "endpoint": "updateItem", "path_params": {"collection_id": "a/b", "item_id": "c d"},
                    "query_params": {"note": "one & two"},
                    "header_params": {"Authorization": "untrusted"},
                    "body": {"title": "Updated"},
                })
                assert data(result) == {"title": "Updated"}
                method, url, headers, body = Api.requests[-1]
                headers = {key.lower(): value for key, value in headers.items()}
                assert method == "PATCH" and urlsplit(url).path == "/api/v2/collections/a%2Fb/items/c%20d", url
                assert parse_qs(urlsplit(url).query) == {"note": ["one & two"]}
                assert headers["authorization"] == "Bearer test-token", headers
                assert headers["x-configured"] == "from-cli", headers
                assert headers["x-workspace"] == "configured-workspace", headers
                assert json.loads(body) == {"title": "Updated"}
                upload = {
                    "endpoint": "uploadAsset", "path_params": {"collection_id": "a"},
                    "body": {"label": "Sample"},
                    "file_refs": [{"path": str(directory / "upload.bin"), "field": "file", "encoding": "raw_bytes"}],
                }
                denied = client.call("call", upload)
                assert denied["isError"] and "disabled" in denied["content"][0]["text"], denied
                assert len(Api.requests) == 1
                binary = data(client.call("call", {"endpoint": "downloadAsset", "path_params": {"asset_id": "a"}}))
                assert binary["byteLength"] == len(BINARY), binary
                busy = client.call("call", {"endpoint": "checkCapacity"})
                assert busy["isError"] and "service_unavailable" in busy["content"][0]["text"], busy
                assert "error" in client.request("tools/call", {"name": "missing", "arguments": {}})
            finally:
                client.close()
            client = Mcp([*command, "--config", str(path), "--allow-file-reads", "--bearer-token", "override"], temp, env)
            try:
                client.initialize()
                assert data(client.call("call", upload)) == {"uploaded": True}
                _, _, headers, body = Api.requests[-1]
                headers = {key.lower(): value for key, value in headers.items()}
                assert headers["authorization"] == "Bearer override", headers
                assert BINARY in body and b'name="file"' in body and b"Sample" in body, body
            finally:
                client.close()
            # The command-line base URL also supplies a server URL for specs that omit servers.
            document = json.loads(source.read_text())
            del document["servers"]
            (directory / "spec.json").write_text(json.dumps(document))
            config.pop("base_url")
            path.write_text(json.dumps(config))
            client = Mcp([*command, "--config", str(path), "--base-url", f"http://127.0.0.1:{api.server_port}/api/v2"], temp, env)
            try:
                client.initialize()
                assert data(client.call("schema", {"schema": "ItemPatch"}))["name"] == "ItemPatch"
            finally:
                client.close()
    finally:
        api.shutdown()
        api.server_close()
        worker.join(timeout=2)
    print("stdio smoke test passed: discovery, JSON HTTP, auth, binary, errors, and file uploads")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a binary or launcher command after --")
    run(command)
