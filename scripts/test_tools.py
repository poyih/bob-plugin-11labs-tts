#!/usr/bin/env python3
"""发布与 API 核验工具的纯本地回归测试；不联网、不消耗额度。"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import api_client

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load_script("release")
publish_appcast = load_script("publish_appcast")
resolve_voices = load_script("resolve_voices")
verify_api = load_script("verify_api")


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class ReleaseTests(unittest.TestCase):
    def test_release_freezes_entry_for_later_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "src").mkdir()
            write_json(root / "src" / "info.json", {"version": "1.0.12", "identifier": "com.example.plugin"})
            entry_path = root / "dist" / "entry.json"
            with contextlib.redirect_stdout(io.StringIO()):
                bundle, entry = release.release("1.0.12", "fixes", "owner/repo", 123,
                                               root, root, entry_output=entry_path)
            payload = release.read_json(entry_path)
            self.assertEqual(payload, {"identifier": "com.example.plugin", "entry": entry})
            self.assertEqual(entry["sha256"], release.sha256_of(bundle))

    def test_version_is_strict_semver(self):
        self.assertEqual(release.normalize_version("v1.2.3"), "1.2.3")
        for value in ("1.2", "1.2.3.4", "1.2.beta", "v1.2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.normalize_version(value)

    def test_upsert_replaces_and_semantically_sorts(self):
        versions = [
            {"version": "1.0.9", "desc": "old"},
            {"version": "1.0.10", "desc": "newest"},
            {"version": "1.0.8", "desc": "older"},
        ]
        result = release.upsert_version(
            versions,
            {"version": "1.0.9", "desc": "replacement"},
        )
        self.assertEqual([item["version"] for item in result], ["1.0.10", "1.0.9", "1.0.8"])
        self.assertEqual(result[1]["desc"], "replacement")
        self.assertEqual(sum(item["version"] == "1.0.9" for item in result), 1)

    def test_bundle_is_deterministic_and_excludes_ds_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / "src"
            source.mkdir()
            (source / "main.js").write_text("exports.x = 1;\n", encoding="utf-8")
            (source / "info.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")
            (source / ".DS_Store").write_bytes(b"noise")

            bundle = release.build_bundle("1.2.3", root)
            first = release.sha256_of(bundle)
            os.utime(source / "main.js", (2_000_000_000, 2_000_000_000))
            second = release.sha256_of(release.build_bundle("1.2.3", root))

            self.assertEqual(first, second)
            with release.zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), ["info.json", "main.js"])

    def test_release_rejects_unprepared_source_without_writing(self):
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as meta_temp:
            source_root = pathlib.Path(source_temp)
            metadata_root = pathlib.Path(meta_temp)
            (source_root / "src").mkdir()
            write_json(
                source_root / "src" / "info.json",
                {
                    "version": "1.0.7",
                    "identifier": "com.example.plugin",
                    "minBobVersion": "1.8.0",
                },
            )
            original = {
                "identifier": "com.example.plugin",
                "versions": [{"version": "1.0.7"}],
            }
            write_json(metadata_root / "appcast.json", original)

            with self.assertRaises(ValueError):
                release.release(
                    "1.0.8",
                    "",
                    "owner/repo",
                    1,
                    metadata_root,
                    source_root,
                )
            self.assertEqual(release.read_json(metadata_root / "appcast.json"), original)
            self.assertFalse((source_root / "dist").exists())

    def test_release_updates_separate_metadata_checkout(self):
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as meta_temp:
            source_root = pathlib.Path(source_temp)
            metadata_root = pathlib.Path(meta_temp)
            (source_root / "src").mkdir()
            write_json(
                source_root / "src" / "info.json",
                {
                    "version": "1.0.9",
                    "identifier": "com.example.plugin",
                    "minBobVersion": "1.8.0",
                },
            )
            (source_root / "src" / "main.js").write_text("exports.x = 1;\n", encoding="utf-8")
            write_json(
                metadata_root / "appcast.json",
                {
                    "identifier": "com.example.plugin",
                    "versions": [
                        {"version": "1.0.10"},
                        {"version": "1.0.9", "desc": "stale"},
                    ],
                },
            )

            with contextlib.redirect_stdout(io.StringIO()):
                bundle, entry = release.release(
                    "1.0.9",
                    "fixed",
                    "owner/repo",
                    123,
                    metadata_root,
                    source_root,
                )

            appcast = release.read_json(metadata_root / "appcast.json")
            self.assertTrue(bundle.exists())
            self.assertEqual(entry["timestamp"], 123)
            self.assertEqual([item["version"] for item in appcast["versions"]], ["1.0.10", "1.0.9"])
            self.assertEqual(appcast["versions"][1]["desc"], "fixed")


class VerifyApiTests(unittest.TestCase):
    def test_model_probes_follow_catalog_and_skip_websocket(self):
        catalog = verify_api.model_catalog.read()
        catalog["eleven_registered_future"] = {**catalog["eleven_v3"], "title": "Future", "order": 99}
        called = []
        def tts(key, voice, **kwargs):
            called.append(kwargs["model_id"])
            return 200, None, "417 bytes"
        metadata = [{"model_id": "eleven_v3_conversational", "languages": [], "maximum_text_length_per_request": 5000}]
        with patch.object(verify_api.model_catalog, "read", return_value=catalog), \
             patch.object(verify_api, "tts", side_effect=tts), patch.object(verify_api, "request", return_value=(200, metadata, 100)):
            results = list(verify_api.probes_models("test-key", "voice"))
        self.assertIn("eleven_v4", called)
        self.assertIn("eleven_registered_future", called)
        self.assertNotIn("eleven_v4_turbo", called)
        self.assertNotIn("eleven_flash_v2", called)
        self.assertIn("max_chars=5000", results[-1].note)

    def test_result_exposes_both_error_namespaces(self):
        result = verify_api.Result(
            "status",
            "sample",
            403,
            {
                "detail": {
                    "code": "subscription_required",
                    "status": "output_format_not_allowed",
                    "type": "authorization_error",
                    "request_id": "req_123",
                    "message": "upgrade",
                }
            },
        )
        self.assertEqual(result.code_string, "subscription_required")
        self.assertEqual(result.status_string, "output_format_not_allowed")
        self.assertEqual(result.type_string, "authorization_error")
        self.assertEqual(result.request_id, "req_123")

    def test_tts_rejects_successful_json_as_audio(self):
        original = verify_api.request
        try:
            verify_api.request = lambda *args, **kwargs: (200, {"detail": "not audio"}, 20)
            status, detail, note = verify_api.tts("key", "voice")
        finally:
            verify_api.request = original
        result = verify_api.Result("models", "sample", status, detail, note)
        self.assertFalse(result.ok)
        self.assertTrue(result.operational_failure)

    def test_scope_only_does_not_prefetch_a_voice(self):
        original_group = verify_api.GROUPS["scope"]
        original_key = os.environ.get("ELEVENLABS_API_KEY")

        def fake_scope(api_key, voice):
            self.assertEqual(voice, "")
            yield verify_api.Result("scope", "fake", 200, {"ok": True}, "ok")

        try:
            verify_api.GROUPS["scope"] = ("fake scope", fake_scope)
            os.environ["ELEVENLABS_API_KEY"] = "test-key"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = verify_api.main(["--only", "scope"])
            self.assertEqual(code, 0)
        finally:
            verify_api.GROUPS["scope"] = original_group
            if original_key is None:
                os.environ.pop("ELEVENLABS_API_KEY", None)
            else:
                os.environ["ELEVENLABS_API_KEY"] = original_key

    def test_network_failure_returns_nonzero(self):
        original_group = verify_api.GROUPS["scope"]
        original_key = os.environ.get("ELEVENLABS_API_KEY")

        def failed_scope(api_key, voice):
            yield verify_api.Result("scope", "timeout", 0, {"_timeout": "timed out"})

        try:
            verify_api.GROUPS["scope"] = ("failed scope", failed_scope)
            os.environ["ELEVENLABS_API_KEY"] = "test-key"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = verify_api.main(["--only", "scope"])
            self.assertEqual(code, 1)
        finally:
            verify_api.GROUPS["scope"] = original_group
            if original_key is None:
                os.environ.pop("ELEVENLABS_API_KEY", None)
            else:
                os.environ["ELEVENLABS_API_KEY"] = original_key


class ResolveVoiceTests(unittest.TestCase):
    def test_probe_requires_nonempty_binary_audio(self):
        original = resolve_voices.request
        try:
            resolve_voices.request = lambda *args, **kwargs: (200, {"detail": "not audio"}, 12)
            self.assertFalse(resolve_voices.probe("key", "voice")[0])
            resolve_voices.request = lambda *args, **kwargs: (200, None, 12)
            self.assertTrue(resolve_voices.probe("key", "voice")[0])
            resolve_voices.request = lambda *args, **kwargs: (200, None, 0)
            self.assertFalse(resolve_voices.probe("key", "voice")[0])
        finally:
            resolve_voices.request = original


class HttpClientTests(unittest.TestCase):
    # MPEG-1 Layer III, 128 kbps / 44.1 kHz：417-byte 首帧。
    MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 413

    def response(self, payload, mime="audio/mpeg"):
        class Response:
            status = 200
            headers = {"Content-Type": mime}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return payload
        return Response()

    def test_html_is_not_audio_in_both_probe_tools(self):
        for mime in ("text/html", "audio/mpeg", "application/octet-stream", ""):
            with self.subTest(mime=mime), patch.object(
                api_client.urllib.request, "urlopen", return_value=self.response(b"<html>gateway</html>", mime)
            ):
                status, detail, note = verify_api.tts("test-key", "voice")
                self.assertFalse(verify_api.Result("models", "voice", status, detail, note).ok)
                self.assertTrue(verify_api.Result("models", "voice", status, detail, note).operational_failure)
                self.assertFalse(resolve_voices.probe("test-key", "voice")[0])

    def test_mp3_with_or_without_id3_is_accepted(self):
        for payload in (self.MP3, b"ID3\x04\x00\x00\x00\x00\x00\x00" + self.MP3):
            with self.subTest(payload=payload[:10]), patch.object(
                api_client.urllib.request, "urlopen", return_value=self.response(payload)
            ):
                self.assertTrue(resolve_voices.probe("test-key", "voice")[0])
                status, detail, note = verify_api.tts("test-key", "voice")
                self.assertTrue(verify_api.Result("models", "voice", status, detail, note).ok)

    def test_empty_truncated_and_non_mp3_binary_are_rejected(self):
        for payload in (b"", self.MP3[:4], b"ID3\x04\x00\x00\x00\x00\x00\x00", b"\xff\x00garbage"):
            with self.subTest(payload=payload), patch.object(
                api_client.urllib.request, "urlopen", return_value=self.response(payload)
            ):
                self.assertFalse(resolve_voices.probe("test-key", "voice")[0])

    def test_non_audio_mime_is_rejected_even_with_mp3_bytes(self):
        with patch.object(api_client.urllib.request, "urlopen", return_value=self.response(self.MP3, "application/json")):
            self.assertFalse(resolve_voices.probe("test-key", "voice")[0])

    def test_json_endpoints_reject_gateway_html(self):
        with patch.object(api_client.urllib.request, "urlopen", return_value=self.response(b"<html>gateway</html>", "text/html")):
            status, detail, _ = verify_api.request("GET", "/models", "test-key")
            result = verify_api.Result("scope", "models", status, detail)
            self.assertFalse(result.ok)
            self.assertTrue(result.operational_failure)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(resolve_voices.own_voices("test-key")[1])

    def test_server_failures_are_operational_not_expected_probe_errors(self):
        self.assertTrue(verify_api.Result("models", "server", 503, {"detail": "busy"}).operational_failure)
        self.assertFalse(verify_api.Result("status", "bad model", 400, {"detail": "model_not_found"}).operational_failure)


class AppcastPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.remote, self.author, self.metadata = [self.root / name for name in ("origin.git", "author", "metadata")]
        self.git(self.root, "init", "--bare", str(self.remote))
        self.git(self.root, "init", "-b", "main", str(self.author))
        self.identity(self.author)
        self.identifier = "com.example.plugin"
        self.old = self.entry("1.0.11")
        write_json(self.author / "appcast.json", {"identifier": self.identifier, "versions": [self.old], "channel": "stable"})
        (self.author / "README.md").write_text("old source\n")
        self.git(self.author, "add", ".")
        self.git(self.author, "commit", "-m", "initial")
        self.git(self.author, "remote", "add", "origin", str(self.remote))
        self.git(self.author, "push", "origin", "main")
        self.git(self.root, "clone", "--branch", "main", str(self.remote), str(self.metadata))
        self.identity(self.metadata)
        self.payload = {"identifier": self.identifier, "entry": self.entry("1.0.12")}

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def identity(self, root):
        self.git(root, "config", "user.name", "Offline Tests")
        self.git(root, "config", "user.email", "tests@example.invalid")
        self.git(root, "config", "commit.gpgsign", "false")
        self.git(root, "config", "core.hooksPath", str(self.root / "no-hooks"))

    def entry(self, version):
        return {"version": version, "desc": "fixes", "sha256": "a" * 64,
                "url": "https://example.com/" + version + ".bobplugin", "minBobVersion": "1.8.0", "timestamp": 123}

    def remote_appcast(self):
        return json.loads(self.git(self.remote, "show", "main:appcast.json"))

    def test_race_preserves_new_versions_source_and_callers_draft(self):
        # 模拟 release.py 的过期草稿及用户暂存的文件，发布器不得清理或提交它们。
        (self.metadata / "appcast.json").write_text('{"versions": []}\n')
        (self.metadata / "README.md").write_text("local draft\n")
        self.git(self.metadata, "add", "README.md")
        before_status = self.git(self.metadata, "status", "--porcelain")
        original_git = publish_appcast.run_git
        pushes = []
        def racing(root, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                if len(pushes) == 1:
                    latest = self.remote_appcast()
                    latest["versions"].insert(0, self.entry("1.0.13"))
                    write_json(self.author / "appcast.json", latest)
                    (self.author / "README.md").write_text("new remote source\n")
                    self.git(self.author, "add", ".")
                    self.git(self.author, "commit", "-m", "concurrent change")
                    self.git(self.author, "push", "origin", "main")
            return original_git(root, *args, **kwargs)
        with patch.object(publish_appcast, "run_git", side_effect=racing), contextlib.redirect_stdout(io.StringIO()):
            publish_appcast.publish(self.metadata, self.payload)
        appcast = self.remote_appcast()
        self.assertEqual([e["version"] for e in appcast["versions"]], ["1.0.13", "1.0.12", "1.0.11"])
        self.assertEqual(appcast["versions"][1], self.payload["entry"])
        self.assertEqual(appcast["channel"], "stable")
        self.assertEqual(self.git(self.remote, "show", "main:README.md"), "new remote source")
        self.assertEqual(self.git(self.metadata, "status", "--porcelain"), before_status)
        self.assertEqual((self.metadata / "README.md").read_text(), "local draft\n")
        self.assertEqual(len(pushes), 2)
        self.assertEqual(self.git(self.metadata, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_already_published_entry_is_idempotent(self):
        before = self.git(self.remote, "rev-parse", "main")
        with contextlib.redirect_stdout(io.StringIO()):
            result = publish_appcast.publish(self.metadata, {"identifier": self.identifier, "entry": self.old})
        self.assertEqual(result, before)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

    def test_permission_failure_is_not_retried(self):
        original_git = publish_appcast.run_git
        pushes = []
        def denied(root, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, "", "remote: protected branch rule rejects this push")
            return original_git(root, *args, **kwargs)
        with patch.object(publish_appcast, "run_git", side_effect=denied), self.assertRaises(RuntimeError):
            publish_appcast.publish(self.metadata, self.payload)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(self.remote_appcast()["versions"], [self.old])
        self.assertEqual(self.git(self.metadata, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_continuous_races_are_bounded(self):
        original_git = publish_appcast.run_git
        pushes = []
        def racing(root, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                (self.author / "README.md").write_text(f"concurrent change {len(pushes)}\n")
                self.git(self.author, "add", "README.md")
                self.git(self.author, "commit", "-m", "concurrent change")
                self.git(self.author, "push", "origin", "main")
            return original_git(root, *args, **kwargs)
        with patch.object(publish_appcast, "run_git", side_effect=racing), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            publish_appcast.publish(self.metadata, self.payload, attempts=3)
        self.assertEqual(len(pushes), 3)
        self.assertEqual(self.remote_appcast()["versions"], [self.old])
        self.assertEqual(self.git(self.metadata, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_identifier_mismatch_never_publishes(self):
        before = self.git(self.remote, "rev-parse", "main")
        self.payload["identifier"] = "com.other.plugin"
        with self.assertRaises(ValueError):
            publish_appcast.publish(self.metadata, self.payload)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

if __name__ == "__main__":
    unittest.main(verbosity=2)
