#!/usr/bin/env python3
"""把 ElevenLabs 账号里的模型 / 音色同步到 src/info.json 的下拉菜单。

上游插件停更后最先过时的就是这两个列表，所以这里做成随手可跑的：

    python3 scripts/sync_catalog.py --api-key sk_xxx            # 只补新增，保留已有中文标题
    python3 scripts/sync_catalog.py --api-key sk_xxx --replace  # 用 API 返回的英文标题整体重写
    python3 scripts/sync_catalog.py --api-key sk_xxx --dry-run  # 只看差异，不写文件

只依赖标准库。
"""

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
INFO = ROOT / "src" / "info.json"
API_BASE = "https://api.elevenlabs.io/v1"
CUSTOM_VOICE = "__custom__"


def api_get(path, api_key):
    req = urllib.request.Request(API_BASE + path, headers={"xi-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:500]
        sys.exit(f"请求 {path} 失败：HTTP {err.code} {body}")
    except urllib.error.URLError as err:
        sys.exit(f"请求 {path} 失败：{err.reason}")


def option_by_id(info, identifier):
    for option in info["options"]:
        if option["identifier"] == identifier:
            return option
    sys.exit(f"info.json 里找不到 identifier 为 {identifier} 的选项")


def model_entries(api_key):
    models = api_get("/models", api_key)
    entries = []
    for model in models:
        if not model.get("can_do_text_to_speech"):
            continue
        name = model.get("name") or model["model_id"]
        desc = (model.get("description") or "").strip()
        title = f"{name} — {desc}" if desc else name
        entries.append({"title": title[:160], "value": model["model_id"]})
    return entries


def voice_entries(api_key):
    """返回 (菜单条目, voice_id -> category)。

    category 很关键：免费订阅通过 API 只能用账号内的音色，用音色库音色会 402
    （Free users cannot use library voices via the API）。ElevenLabs 的 Default
    音色（Aria/Roger/Sarah 等）也属于音色库，且官方已宣布 2026-12-31 停用。
    """
    voices = api_get("/voices", api_key).get("voices", [])
    entries = []
    categories = {}
    for voice in voices:
        labels = voice.get("labels") or {}
        bits = [labels.get(k) for k in ("gender", "accent", "description")]
        suffix = " · ".join(b for b in bits if b)
        title = f"{voice['name']} — {suffix}" if suffix else voice["name"]
        entries.append({"title": title[:160], "value": voice["voice_id"]})
        categories[voice["voice_id"]] = voice.get("category") or "unknown"
    return entries, categories


def merge(option, fresh, replace, keep_tail_value=None):
    """返回 (新的 menuValues, 新增列表, 上游已不存在的列表)。"""
    existing = option.get("menuValues", [])
    tail = [e for e in existing if e["value"] == keep_tail_value]
    existing_body = [e for e in existing if e["value"] != keep_tail_value]

    known = {e["value"] for e in existing_body}
    upstream = {e["value"] for e in fresh}

    added = [e for e in fresh if e["value"] not in known]
    stale = [e for e in existing_body if e["value"] not in upstream]

    if replace:
        merged = fresh + tail
    else:
        merged = existing_body + added + tail

    return merged, added, stale


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--replace", action="store_true", help="整体重写而不是只补新增")
    parser.add_argument("--dry-run", action="store_true", help="只打印差异")
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--voices-only", action="store_true")
    args = parser.parse_args()

    with INFO.open(encoding="utf-8") as fp:
        info = json.load(fp)

    do_models = not args.voices_only
    do_voices = not args.models_only
    changed = False

    if do_models:
        option = option_by_id(info, "model")
        merged, added, stale = merge(option, model_entries(args.api_key), args.replace)
        for entry in added:
            print(f"+ 模型  {entry['value']}  {entry['title']}")
        for entry in stale:
            print(f"! 模型  {entry['value']}  账号里已看不到（可能已下线）")
        if merged != option.get("menuValues"):
            option["menuValues"] = merged
            changed = True

    if do_voices:
        option = option_by_id(info, "voice")
        fresh, categories = voice_entries(args.api_key)
        merged, added, stale = merge(
            option, fresh, args.replace, keep_tail_value=CUSTOM_VOICE
        )
        for entry in added:
            print(f"+ 音色  [{categories.get(entry['value'], '?'):<12}] {entry['value']}  {entry['title']}")
        for entry in stale:
            print(f"! 音色  {entry['value']}  账号里已看不到")

        seen = sorted({categories[e["value"]] for e in fresh})
        print(f"\n账号里的音色分类：{', '.join(seen) or '（空）'}")
        print("免费订阅通过 API 只能用账号内的音色；音色库音色会返回 402。")
        print("拿不准就用 premade 或 generated（Voice Design 生成）的那几个。")

        if merged != option.get("menuValues"):
            option["menuValues"] = merged
            changed = True

    if not changed:
        print("没有变化。")
        return

    if args.dry_run:
        print("\n--dry-run，未写入 src/info.json")
        return

    with INFO.open("w", encoding="utf-8") as fp:
        json.dump(info, fp, indent=4, ensure_ascii=False)
        fp.write("\n")
    print("\n已更新 src/info.json")


if __name__ == "__main__":
    main()
