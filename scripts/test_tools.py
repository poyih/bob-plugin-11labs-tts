#!/usr/bin/env python3
"""发布与 API 核验工具的纯本地回归测试；不联网、不消耗额度。"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load_script("release")
resolve_voices = load_script("resolve_voices")
verify_api = load_script("verify_api")


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class ReleaseTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
