from __future__ import annotations

import http.server
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import local_fix_agent as lfa


class ProbeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        if self.path == "/json":
            payload = json.dumps(
                {
                    "items": [{"id": 1, "name": "demo"}],
                    "meta": {"next": None, "total": 1},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-RateLimit-Remaining", "7")
            self.send_header("Retry-After", "3")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        if self.path == "/redirect-json":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
            return
        if self.path == "/master.m3u8":
            payload = (
                "#EXTM3U\n"
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",DEFAULT=YES,URI="audio.m3u8"\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=640000,AUDIO="aac"\n'
                "variant.m3u8\n"
                '#EXT-X-STREAM-INF:BANDWIDTH=1280000,AUDIO="aac"\n'
                "https://example.com/high.m3u8\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        if self.path in {"/variant.m3u8", "/audio.m3u8"}:
            payload = (
                "#EXTM3U\n"
                "#EXT-X-TARGETDURATION:6\n"
                "#EXT-X-MEDIA-SEQUENCE:1\n"
                "#EXTINF:6.0,\n"
                "seg1.ts\n"
                "#EXTINF:6.0,\n"
                "seg2.ts\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        if self.path == "/media.m3u8":
            payload = (
                "#EXTM3U\n"
                "#EXT-X-TARGETDURATION:8\n"
                "#EXT-X-MEDIA-SEQUENCE:42\n"
                '#EXT-X-KEY:METHOD=AES-128,URI="key.key"\n'
                "#EXTINF:8.0,\n"
                "segment1.ts\n"
                "#EXTINF:8.0,\n"
                "segment2.ts\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-mpegURL")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        if self.path in {"/seg1.ts", "/seg2.ts", "/segment1.ts", "/segment2.ts", "/key.key"}:
            payload = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        if self.path == "/slow":
            time.sleep(0.2)
            payload = b"too slow"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


@contextmanager
def probe_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_probe_api_json_summary_extracts_shape_and_rate_limit_headers() -> None:
    with probe_server() as base_url:
        result = lfa.probe_endpoint(f"{base_url}/json", probe_type="json_summary")

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["body_is_json"] is True
    assert result["probe_confidence"] == "high"
    assert result["json_top_level_keys"] == ["items", "meta"]
    assert result["json_shape"] == {"items": [{"id": "int", "name": "str"}], "meta": {"next": "NoneType", "total": "int"}}
    assert result["rate_limit_headers"] == {"X-RateLimit-Remaining": "7", "Retry-After": "3"}


def test_probe_api_redirect_records_redirect_chain() -> None:
    with probe_server() as base_url:
        result = lfa.probe_endpoint(f"{base_url}/redirect-json", probe_type="json_summary")

    assert result["ok"] is True
    assert result["redirected"] is True
    assert result["status_code"] == 200
    assert result["redirect_chain"][0]["status_code"] == 302
    assert result["final_url"].endswith("/json")


def test_probe_m3u8_master_playlist_detects_variants_and_groups() -> None:
    with probe_server() as base_url:
        result = lfa.probe_endpoint(f"{base_url}/master.m3u8", probe_type="m3u8_summary", follow_up_limit=1)

    assert result["ok"] is True
    assert result["valid_playlist"] is True
    assert result["playlist_type"] == "master"
    assert result["variant_count"] == 2
    assert result["audio_group_references"] == ["aac"]
    assert result["uri_reference_mode"] == "mixed"
    assert len(result["sample_uri_probe_results"]) == 1
    assert result["sample_uri_probe_results"][0]["status_code"] == 200


def test_probe_m3u8_media_playlist_detects_segments_and_key_tags() -> None:
    with probe_server() as base_url:
        result = lfa.probe_endpoint(f"{base_url}/media.m3u8", probe_type="m3u8_summary", follow_up_limit=2)

    assert result["ok"] is True
    assert result["valid_playlist"] is True
    assert result["playlist_type"] == "media"
    assert result["target_duration"] == 8
    assert result["media_sequence"] == 42
    assert result["segment_count"] == 2
    assert result["key_tags_present"] is True
    assert result["uri_reference_mode"] == "relative"
    assert [item["status_code"] for item in result["sample_uri_probe_results"]] == [200, 200]


def test_build_probe_proxy_map_uses_explicit_and_inherited_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lfa, "CURRENT_SUBPROCESS_ENV", {"HTTP_PROXY": "http://proxy.internal:8080", "ALL_PROXY": "socks5://proxy.internal:1080"})

    proxy_map = lfa.build_probe_proxy_map(https_proxy="http://secure-proxy.internal:8443")

    assert proxy_map == {
        "http": "http://proxy.internal:8080",
        "https": "http://secure-proxy.internal:8443",
        "all": "socks5://proxy.internal:1080",
    }
    assert lfa.probe_uses_proxy("https://example.com/data.json", proxy_map) is True


def test_probe_endpoint_redacts_secrets_in_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        lfa,
        "bounded_http_fetch",
        lambda *args, **kwargs: {
            "ok": True,
            "status_code": 200,
            "content_type": "application/json",
            "headers": {
                "Authorization": "Bearer raw-secret",
                "Set-Cookie": "sessionid=123",
                "X-Api-Key": "abc123",
            },
            "body_text": '{"ok": true}',
            "body_bytes": b'{"ok": true}',
            "truncated": False,
            "redirect_chain": [],
            "redirected": False,
            "final_url": "https://user:pass@example.com/data?token=abc",
            "proxy_used": False,
            "error": "",
            "timed_out": False,
        },
    )

    result = lfa.probe_endpoint(
        "https://user:pass@example.com/data?token=abc",
        probe_type="headers_summary",
        bearer_token="raw-secret",
        cookies="sessionid=123",
        custom_headers={"X-Api-Key": "abc123"},
    )

    assert result["redactions_applied"] is True
    assert "<REDACTED_USER>" in result["endpoint"]
    assert result["response_headers"]["Authorization"] == "Bearer <REDACTED>"
    assert result["response_headers"]["Set-Cookie"] == "<REDACTED_COOKIE>"
    assert result["response_headers"]["X-Api-Key"] == "<REDACTED>"

    lfa.print_probe_result(result)
    output = capsys.readouterr().out
    assert "raw-secret" not in output
    assert "sessionid=123" not in output
    assert "abc123" not in output


def test_probe_endpoint_timeout_failure_returns_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "bounded_http_fetch",
        lambda *args, **kwargs: {
            "ok": False,
            "status_code": 0,
            "content_type": "",
            "headers": {},
            "body_text": "",
            "body_bytes": b"",
            "truncated": False,
            "redirect_chain": [],
            "redirected": False,
            "final_url": args[0],
            "proxy_used": False,
            "error": "timed out",
            "timed_out": True,
        },
    )

    result = lfa.probe_endpoint("https://example.com/slow", probe_type="json_summary")

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["confidence"] == "low"
    assert result["summary"] == "timed out"


def test_build_script_validation_plan_adds_probe_recommendations_for_m3u8_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "stream_probe.py"
    script.write_text(
        "import requests\n"
        "PLAYLIST = 'https://media.example.com/master.m3u8'\n"
        "def main():\n"
        "    print(PLAYLIST)\n"
    )

    plan = lfa.build_script_validation_plan(repo, script)

    assert plan["probe_recommendations"] == [
        {
            "endpoint": "https://media.example.com/master.m3u8",
            "probe_type": "m3u8_summary",
            "reason": "script references an HLS playlist and live playlist structure may affect parsing or validation",
        }
    ]


def test_build_script_validation_plan_extracts_probe_header_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "api_probe.py"
    script.write_text(
        "import requests\n"
        "API_URL = 'https://api.example.com/data'\n"
        "HEADERS = {\n"
        "    'Authorization': 'Bearer demo-token',\n"
        "    'X-Api-Key': 'demo-key',\n"
        "    'Accept': 'application/json',\n"
        "}\n"
    )

    plan = lfa.build_script_validation_plan(repo, script)

    assert plan["suggested_probe_headers"] == {
        "Authorization": "Bearer demo-token",
        "X-Api-Key": "demo-key",
        "Accept": "application/json",
    }


def test_build_script_validation_plan_does_not_recommend_probe_for_local_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "local_tool.py"
    script.write_text(
        "import argparse\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.parse_args()\n"
    )

    plan = lfa.build_script_validation_plan(repo, script)

    assert plan["network_dependency"] == {"detected": False, "confidence": "low", "reason": ""}
    assert plan["probe_recommendations"] == []


def test_maybe_enrich_validation_plan_with_probes_downgrades_risky_runtime_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {
        "active": True,
        "primary_command": "python network_cli.py",
        "chosen_stack": [
            {"kind": "syntax", "command": "python -m py_compile network_cli.py"},
            {"kind": "cli_run", "command": "python network_cli.py"},
        ],
        "candidates": [
            {"kind": "syntax", "command": "python -m py_compile network_cli.py", "confidence": 1.0},
            {"kind": "cli_run", "command": "python network_cli.py", "confidence": 0.4},
            {"kind": "cli_help", "command": "python network_cli.py --help", "confidence": 0.6},
            {"kind": "import", "command": "python -c 'import network_cli'", "confidence": 0.5},
        ],
        "confidence_level": "medium",
        "limited_validation": False,
        "only_syntax_import_validation": False,
        "limited_reason": "",
        "network_dependency": {"detected": True, "confidence": "high", "reason": "script uses an HTTP client and embeds a concrete live endpoint"},
        "probe_recommendations": [
            {
                "endpoint": "https://api.example.com/data",
                "probe_type": "json_summary",
                "reason": "script references a live HTTP endpoint and response shape may affect parsing or auth handling",
            }
        ],
        "auto_probe_evaluated": False,
        "auto_probe_used": False,
        "probe_findings": [],
        "probe_reasoning": "",
    }
    monkeypatch.setattr(
        lfa,
        "probe_endpoint",
        lambda endpoint, **kwargs: {
            "ok": True,
            "probe_type": "json_summary",
            "endpoint": endpoint,
            "status_code": 401,
            "summary": "status=401; content_type=application/json; authentication appears required or the bearer token was rejected",
        },
    )

    enriched = lfa.maybe_enrich_validation_plan_with_probes(plan)

    assert enriched["auto_probe_used"] is True
    assert enriched["primary_command"] == "python -c 'import network_cli'"
    assert enriched["chosen_stack"] == [
        {"kind": "syntax", "command": "python -m py_compile network_cli.py"},
        {"kind": "import", "command": "python -c 'import network_cli'", "confidence": 0.5},
    ]
    assert enriched["limited_validation"] is True
    assert enriched["confidence_level"] == "low"
    assert "downgraded to a safer non-network path" in enriched["limited_reason"]
    assert "choose a safer validation path" in enriched["probe_reasoning"]


def test_maybe_enrich_validation_plan_interactive_includes_detected_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {
        "active": True,
        "primary_command": "python network_cli.py",
        "chosen_stack": [
            {"kind": "syntax", "command": "python -m py_compile network_cli.py"},
            {"kind": "cli_run", "command": "python network_cli.py"},
        ],
        "candidates": [
            {"kind": "syntax", "command": "python -m py_compile network_cli.py", "confidence": 1.0},
            {"kind": "cli_run", "command": "python network_cli.py", "confidence": 0.7},
        ],
        "confidence_level": "medium",
        "limited_validation": False,
        "only_syntax_import_validation": False,
        "limited_reason": "",
        "network_dependency": {"detected": True, "confidence": "high", "reason": "script uses an HTTP client and embeds a concrete live endpoint"},
        "probe_recommendations": [
            {
                "endpoint": "https://api.example.com/data",
                "probe_type": "json_summary",
                "reason": "script references a live HTTP endpoint and response shape may affect parsing or auth handling",
            }
        ],
        "suggested_probe_headers": {"Authorization": "Bearer demo-token", "Accept": "application/json"},
        "auto_probe_evaluated": False,
        "auto_probe_used": False,
        "probe_findings": [],
        "probe_reasoning": "",
    }

    class DummyStdin:
        def isatty(self) -> bool:
            return True

    prompt_answers = iter([True, False, True])
    captured: dict[str, object] = {}

    monkeypatch.setattr(lfa.sys, "stdin", DummyStdin())
    monkeypatch.setattr(lfa, "prompt_yes_no", lambda *args, **kwargs: next(prompt_answers))
    monkeypatch.setattr("builtins.input", lambda prompt="": "api")

    def fake_probe(endpoint: str, **kwargs) -> dict:
        captured["endpoint"] = endpoint
        captured["kwargs"] = kwargs
        return {
            "ok": True,
            "probe_type": kwargs.get("probe_type", "json_summary"),
            "endpoint": endpoint,
            "status_code": 200,
            "summary": "status=200; content_type=application/json; json body detected",
        }

    monkeypatch.setattr(lfa, "probe_endpoint", fake_probe)

    enriched = lfa.maybe_enrich_validation_plan_with_probes(plan)

    assert enriched["auto_probe_used"] is True
    assert captured["endpoint"] == "https://api.example.com/data"
    assert captured["kwargs"] == {
        "probe_type": "json_summary",
        "custom_headers": {"Authorization": "Bearer demo-token", "Accept": "application/json"},
        "http_proxy": "",
        "https_proxy": "",
    }


def test_repair_prompt_includes_live_probe_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lfa,
        "CURRENT_VALIDATION_PLAN",
        {
            "active": True,
            "primary_command": "python network_cli.py --help",
            "chosen_stack": [
                {"kind": "syntax", "command": "python -m py_compile network_cli.py"},
                {"kind": "cli_help", "command": "python network_cli.py --help"},
            ],
            "confidence_level": "low",
            "auto_probe_used": True,
            "probe_findings": [
                {
                    "endpoint": "https://api.example.com/data",
                    "probe_type": "json_summary",
                    "summary": "status=401; content_type=application/json",
                }
            ],
            "probe_reasoning": "Used live probe evidence from https://api.example.com/data to choose a safer validation path: status=401",
        },
    )

    prompt = lfa.build_user_prompt(
        Path("/tmp/repo"),
        "feature",
        "python network_cli.py",
        "minimal_patch",
        lfa.FAILURE_RUNTIME_ERROR,
    )

    assert "Live probe evidence:" in prompt
    assert "probe_type: json_summary" in prompt
    assert "status=401" in prompt


def test_print_probe_result_uses_probe_confidence_label(capsys: pytest.CaptureFixture[str]) -> None:
    lfa.print_probe_result(
        {
            "probe_type": "json_summary",
            "endpoint": "https://api.example.com/data",
            "method": "GET",
            "status_code": 200,
            "content_type": "application/json",
            "redirected": False,
            "proxy_used": False,
            "proxy_likely_worked": False,
            "redactions_applied": False,
            "probe_confidence": "high",
            "summary": "status=200; content_type=application/json; json body detected",
        }
    )

    output = capsys.readouterr().out
    assert "probe_confidence: high" in output
