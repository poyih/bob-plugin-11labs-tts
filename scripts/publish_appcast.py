#!/usr/bin/env python3
"""从已构建的版本记录发布 appcast；每次基于最新远程提交，在临时 worktree 中合并。"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from release import normalize_version, read_json, upsert_version, write_json_atomic


def validate_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("identifier"), str) or not payload["identifier"]:
        raise ValueError("发布记录缺少插件 identifier")
    entry = payload.get("entry")
    if not isinstance(entry, dict) or entry.get("version") != normalize_version(entry.get("version")):
        raise ValueError("发布记录必须包含规范版本号")
    if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
        raise ValueError("发布记录缺少合法 SHA256")
    if not isinstance(entry.get("url"), str):
        raise ValueError("下载地址必须是 HTTPS URL")
    url = urlparse(entry["url"])
    if url.scheme != "https" or not url.hostname:
        raise ValueError("下载地址必须是 HTTPS URL")
    if any(not isinstance(entry.get(key), str) or not entry[key] for key in ("desc", "minBobVersion")):
        raise ValueError("发布记录缺少更新说明或最低 Bob 版本")
    if type(entry.get("timestamp")) is not int or entry["timestamp"] < 0:
        raise ValueError("发布记录缺少毫秒时间戳")
    return payload


def run_git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True,
                            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git 命令失败")
    return result


def merge_appcast(appcast, payload):
    validate_payload(payload)
    if not isinstance(appcast, dict):
        raise ValueError("appcast 顶层必须是对象")
    if appcast.get("identifier") and appcast["identifier"] != payload["identifier"]:
        raise ValueError("远程 appcast 与本次发布的插件 identifier 不一致")
    result = dict(appcast)
    result["identifier"] = payload["identifier"]
    result["versions"] = upsert_version(appcast.get("versions", []), payload["entry"])
    return result


def publish(root, payload, branch="main", attempts=3):
    validate_payload(payload)
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("重试次数必须在 1~5 之间")
    root = Path(root).resolve()
    run_git(root, "check-ref-format", "refs/heads/" + branch)
    version = payload["entry"]["version"]
    for attempt in range(1, attempts + 1):
        run_git(root, "fetch", "--quiet", "origin", "refs/heads/" + branch)
        base = run_git(root, "rev-parse", "FETCH_HEAD").stdout.strip()
        with tempfile.TemporaryDirectory(prefix="bob-appcast-") as temp:
            worktree = Path(temp) / "publish"
            added = False
            try:
                # 共享仓库的认证配置，但不修改调用者的 checkout、暂存区或未提交文件。
                run_git(root, "worktree", "add", "--quiet", "--detach", str(worktree), base)
                added = True
                path = worktree / "appcast.json"
                original = read_json(path) if path.exists() else {}
                merged = merge_appcast(original, payload)
                if merged == original:
                    print(f"appcast v{version} 已存在，无需提交")
                    return base
                write_json_atomic(path, merged)
                run_git(worktree, "add", "--", "appcast.json")
                run_git(worktree, "commit", "--quiet", "-m", f"chore: publish appcast v{version}")
                commit = run_git(worktree, "rev-parse", "HEAD").stdout.strip()
                pushed = run_git(worktree, "push", "origin", "HEAD:refs/heads/" + branch, check=False)
                if pushed.returncode == 0:
                    print(f"appcast v{version} 已发布：{commit}")
                    return commit
                reason = pushed.stderr + pushed.stdout
                # 权限、网络或分支规则错误不能被当成并发更新不断重试。
                if not any(marker in reason for marker in ("non-fast-forward", "fetch first")):
                    raise RuntimeError(reason.strip())
                if attempt == attempts:
                    raise RuntimeError(f"默认分支持续更新，{attempts} 次推送均失败：{reason.strip()}")
                print(f"默认分支已更新，重新合并 appcast（{attempt + 1}/{attempts}）")
            finally:
                if added:
                    run_git(root, "worktree", "remove", "--force", str(worktree))
    raise RuntimeError("appcast 未发布")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry-file", required=True, help="release.py --entry-output 生成的固定版本记录")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        publish(args.repo_root, read_json(args.entry_file), args.branch, args.attempts)
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"发布 appcast 失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
