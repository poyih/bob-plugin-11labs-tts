#!/usr/bin/env python3
"""打一个版本：写版本号 -> 打包 -> 算 sha256 -> 更新 appcast.json。

本地：
    python3 scripts/release.py --version 1.0.1 --notes "修复 xxx"

CI 里由 .github/workflows/release.yml 调用，版本号取自 git tag。
只依赖标准库，不需要 node/npm。
"""

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
INFO = ROOT / "src" / "info.json"
APPCAST = ROOT / "appcast.json"
DIST = ROOT / "dist"
NAME = "bob-plugin-11labs-tts"


def read_json(path):
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def write_json(path, data):
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=4, ensure_ascii=False)
        fp.write("\n")


def build_bundle(version):
    """把 src/ 的内容（不含 src 目录本身）压成 .bobplugin。"""
    DIST.mkdir(exist_ok=True)
    bundle = DIST / f"{NAME}-{version}.bobplugin"
    if bundle.exists():
        bundle.unlink()
    subprocess.run(
        ["zip", "-qr", str(bundle), ".", "-x", "*.DS_Store"],
        cwd=ROOT / "src",
        check=True,
    )
    return bundle


def sha256_of(path):
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="例如 1.0.1")
    parser.add_argument("--notes", default="", help="appcast 里展示的更新说明")
    parser.add_argument(
        "--repo",
        default="poyih/bob-plugin-11labs-tts",
        help="GitHub owner/repo，用于拼下载地址",
    )
    parser.add_argument(
        "--timestamp",
        type=int,
        default=None,
        help="毫秒时间戳，默认取当前时间",
    )
    args = parser.parse_args()

    version = args.version.lstrip("v")
    if not all(part.isdigit() for part in version.split(".")):
        sys.exit(f"版本号必须形如 1.0.1，收到 {version!r}")

    info = read_json(INFO)
    info["version"] = version
    write_json(INFO, info)

    bundle = build_bundle(version)
    checksum = sha256_of(bundle)

    entry = {
        "version": version,
        "desc": args.notes or f"版本 {version}",
        "sha256": checksum,
        "url": (
            f"https://github.com/{args.repo}/releases/download/"
            f"v{version}/{bundle.name}"
        ),
        "minBobVersion": info.get("minBobVersion", "1.8.0"),
        "timestamp": args.timestamp if args.timestamp is not None else int(time.time() * 1000),
    }

    appcast = read_json(APPCAST) if APPCAST.exists() else {}
    appcast["identifier"] = info["identifier"]
    versions = [v for v in appcast.get("versions", []) if v.get("version") != version]
    # appcast 要求新版本在前
    appcast["versions"] = [entry] + versions
    write_json(APPCAST, appcast)

    print(f"bundle : {bundle.relative_to(ROOT)}")
    print(f"sha256 : {checksum}")
    print(f"url    : {entry['url']}")


if __name__ == "__main__":
    main()
