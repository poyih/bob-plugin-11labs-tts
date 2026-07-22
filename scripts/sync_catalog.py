#!/usr/bin/env python3
"""把 ElevenLabs 账号里的模型 / 音色同步到 src/info.json 的下拉菜单。

上游插件停更后最先过时的就是这两个列表，所以这里做成随手可跑的：

    python3 scripts/sync_catalog.py --api-key sk_xxx            # 只补新增，保留已有中文标题
    python3 scripts/sync_catalog.py --api-key sk_xxx --replace  # 用 API 返回的英文标题整体重写
    python3 scripts/sync_catalog.py --api-key sk_xxx --dry-run  # 只看差异，不写文件

只依赖标准库。
"""

import argparse
import getpass
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
INFO = ROOT / "src" / "info.json"
API_BASE = "https://api.elevenlabs.io/v1"
CUSTOM_VOICE = "__custom__"

# ---------------------------------------------------------------- 展示层规则
#
# API 只给「有什么」，给不了「该不该显示、怎么显示」。这些规则在每次同步后
# 套一遍，否则 --replace 会把已废弃的模型带回菜单、把标注冲掉。

# ElevenLabs 已标记 deprecated（/v1/models 仍会返回），不进菜单
DEPRECATED_MODELS = {"eleven_turbo_v2_5", "eleven_turbo_v2"}

# API 的模型描述是长英文，菜单里读不动，用中文短标题覆盖
MODEL_TITLES = {
    "eleven_flash_v2_5": "Flash v2.5 — 最快、最便宜（32 语言，推荐划词朗读）",
    "eleven_multilingual_v2": "Multilingual v2 — 音质最稳、情感自然（29 语言）",
    "eleven_v3": "v3 — 表现力最强、支持 70+ 语言（较慢、较贵）",
    "eleven_flash_v2": "Flash v2 — 仅英语，超低延迟",
}
MODEL_ORDER = ["eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2"]

# 官方公告的 Default 音色退役名单，2026-12-31 停用
# https://elevenlabs.io/docs/help-center/product/voice-customization/my-voices/what-are-default-voices
RETIRING_VOICES = {
    "CwhRBWXzGAHq8TQ4Fs17",  # Roger
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "FGY2WhTYpPnrIDTdsKH5",  # Laura
    "IKne3meq5aSn9XLyUdCD",  # Charlie
    "JBFqnCBsd6RMkjVDRZzb",  # George
    "N2lVS1w4EtoT3dr4eOWO",  # Callum
    "SAz9YHcvj6GT2YYXdXww",  # River
    "SOYHLrjzK2X1ezoPC6cr",  # Harry
    "TX3LPaxmHKxFdv7VOQHJ",  # Liam
    "Xb7hH8MSUJpSbSDYk0k2",  # Alice
    "XrExE9yKIg1WjnnlVkGX",  # Matilda
    "bIHbv24MWmeRgasZH58o",  # Will
    "cgSgspJ2msm6clMCkdW9",  # Jessica
    "cjVigY5qzO86Huf0OWal",  # Eric
    "iP95p4xoKVk53GoZ742B",  # Chris
    "nPczCjzI2devNBz1zQrb",  # Brian
    "onwK4e9ZLuTAKqWW03F9",  # Daniel
    "pFZP5JQG7iQjIQuC4Bku",  # Lily
    "pqHfZKP75CvOlQylNhV4",  # Bill
}
RETIRING_SUFFIX = "（2026-12-31 停用）"


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


def apply_overlay(info):
    """把展示层规则套到菜单上。纯本地操作，不需要 API。

    返回被改动的条目数，便于打印。
    """
    touched = 0

    model_option = option_by_id(info, "model")
    kept = []
    for entry in model_option.get("menuValues", []):
        if entry["value"] in DEPRECATED_MODELS:
            print(f"- 模型  {entry['value']}  已废弃，移出菜单")
            touched += 1
            continue
        title = MODEL_TITLES.get(entry["value"])
        if title and entry["title"] != title:
            entry["title"] = title
            touched += 1
        kept.append(entry)
    kept.sort(key=lambda e: (MODEL_ORDER.index(e["value"]) if e["value"] in MODEL_ORDER else 99))
    model_option["menuValues"] = kept

    voice_option = option_by_id(info, "voice")
    body, tail = [], []
    for entry in voice_option.get("menuValues", []):
        if entry["value"] == CUSTOM_VOICE:
            tail.append(entry)
            continue
        retiring = entry["value"] in RETIRING_VOICES
        title = entry["title"].replace(RETIRING_SUFFIX, "")
        if retiring:
            title += RETIRING_SUFFIX
        if title != entry["title"]:
            entry["title"] = title
            touched += 1
        body.append((retiring, entry))
    # 长期可用的排前面，省得默认选到年底就失效的
    body.sort(key=lambda pair: (pair[0], pair[1]["title"]))
    voice_option["menuValues"] = [e for _, e in body] + tail

    alive = sum(1 for r, _ in body if not r)
    print(f"\n展示层：模型 {len(kept)} 个，音色 {len(body)} 个"
          f"（{alive} 个长期可用，{len(body) - alive} 个 2026-12-31 停用）")
    return touched


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
    parser.add_argument(
        "--api-key",
        default=None,
        help="留空则读环境变量 ELEVENLABS_API_KEY，再留空则交互式输入（不回显、不进 shell 历史）",
    )
    parser.add_argument("--replace", action="store_true", help="整体重写而不是只补新增")
    parser.add_argument("--dry-run", action="store_true", help="只打印差异")
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--voices-only", action="store_true")
    parser.add_argument(
        "--overlay-only",
        action="store_true",
        help="只重新套用展示层规则（废弃模型过滤、中文标题、退役标注），不联网",
    )
    args = parser.parse_args()

    if not args.overlay_only:
        api_key = args.api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            try:
                api_key = getpass.getpass("ElevenLabs API Key（输入不回显）: ")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\n已取消")
        args.api_key = (api_key or "").strip()
        if not args.api_key:
            sys.exit("没有拿到 API Key")

    with INFO.open(encoding="utf-8") as fp:
        info = json.load(fp)

    do_models = not args.voices_only and not args.overlay_only
    do_voices = not args.models_only and not args.overlay_only
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

    # API 只负责「有什么」，展示规则每次都要重新套一遍
    if apply_overlay(info):
        changed = True

    # --replace 会整体换掉菜单，默认值有可能被换没了。Bob 对不在菜单里的值不会
    # 报错，只会照旧发出去，界面却显示成第一项 —— 这正是之前 402 排查被误导的
    # 原因，所以这里必须兜住。
    for identifier in ("model", "voice"):
        option = option_by_id(info, identifier)
        values = [e["value"] for e in option.get("menuValues", []) if e["value"] != CUSTOM_VOICE]
        if values and option.get("defaultValue") not in values:
            print(f"\n! {identifier} 的默认值 {option.get('defaultValue')} 已不在菜单里，改为 {values[0]}")
            option["defaultValue"] = values[0]
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
