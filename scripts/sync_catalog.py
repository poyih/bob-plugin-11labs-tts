#!/usr/bin/env python3
"""把 ElevenLabs 账号里的模型 / 音色同步到 src/info.json 的下拉菜单。

这两个列表最容易过时，所以做成随手可跑的：

    python3 scripts/sync_catalog.py             # 同步已登记模型的能力，保留中文标题
    python3 scripts/sync_catalog.py --sync-voices --replace   # 替换账户音色，保留内置接班音色
    python3 scripts/sync_catalog.py --dry-run   # 只看差异，不写文件

API Key 从 ELEVENLABS_API_KEY 读取；未设置时安全地交互输入。只依赖标准库。
"""

import argparse
import copy
import getpass
import json
import os
import pathlib
import sys

import api_client
import catalog as model_catalog

ROOT = pathlib.Path(__file__).resolve().parent.parent
INFO = ROOT / "src" / "info.json"
API_BASE = "https://api.elevenlabs.io/v1"
CUSTOM_VOICE = "__custom__"

# ---------------------------------------------------------------- 展示层规则
#
# API 只给「有什么」，给不了「该不该显示、怎么显示」。这些规则在每次同步后
# 套一遍，否则 --replace 会把已废弃的模型带回菜单、把标注冲掉。

CATALOG_PATH = model_catalog.CATALOG
_INITIAL_CATALOG = model_catalog.read(CATALOG_PATH)
DEPRECATED_MODELS = {mid for mid, item in _INITIAL_CATALOG.items() if item["deprecated"]}
HIDDEN_MODELS = {mid for mid, item in _INITIAL_CATALOG.items() if item["hidden"]}
MODEL_TITLES = {item["value"]: item["title"] for item in model_catalog.visible_entries(_INITIAL_CATALOG)}
MODEL_ORDER = list(MODEL_TITLES)

# 官方原文：「All our Default voices will expire on December 31, 2026」——
# 这 21 个 Default(premade) 音色全部到期。v1.0.6 起菜单已换成官方指定的接班音色
# （见下方 SUCCESSOR_VOICES），这张表只在老音色被同步回菜单时用于打「停用」标注。
# https://elevenlabs.io/docs/overview/capabilities/voices
RETIRING_VOICES = {
    "hpp4J3VqNfWAUOO0d1Us",  # Bella
    "pNInz6obpgDQGcFmaJgB",  # Adam
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

# 官方接班音色（2026-12-31 后仍可用），按官方替换表顺序。
# /v1/voices 不一定返回内置接班音色。账户列表替换时保护这些条目；
# 默认仍不读取账户音色，需显式 --sync-voices。
SUCCESSOR_VOICES = [
    ("gOupLcAkjEnguROwi4oS", "Darian — 温暖沉稳的讲述者"),
    ("OZ0L6eISlOejga3XjDFt", "Talia — 温柔轻缓的引导者"),
    ("WQP7cQUF5aAS6Axh5yaa", "Elara — 清脆利落的专业旁白"),
    ("jSuBIjxMKhqIfb0wCK1F", "Baxter — 澳式、冷静克制"),
    ("6WwXjDDEMyNmFG95zycZ", "Eldrin — 清晰的英式男中音"),
    ("cymHWdiF8WjUCg6vvFxx", "Kellan — 随和亲切的日常口吻"),
    ("dvbL7qkNGZY1IqPGZAjM", "Elowen — 明快现代的旁白"),
    ("10NkTYmU7tSz3Kkl3Lex", "Kaelen — 青涩的战士感"),
    ("ktkP7Nsj67dw2zcplQYt", "Lawrence — 明亮、条理清晰"),
    ("BFd5oBc2DDna33pSi4Gf", "Alicia — 干练的国际主播腔"),
    ("QtY3JBOUKEB5xzrRfOKc", "Maisie — 亲切随和、邻家感"),
    ("7QN34D2r3hCNwbOYIeK0", "Warren — 松弛而酷"),
    ("g7LVvkPWALzPxOQbF6OE", "Jade — 明快自然"),
    ("l7kNoIfnJKPg7779LI2t", "Eddie — 热心、令人安心"),
    ("AaOhDHYJ1XLZk74lXhdE", "Caleb — 值得信赖的引路人"),
    ("8dEUmyPMdDdK91vboYih", "Sawyer — 深夜讲故事的嗓音"),
    ("fnYMz3F5gMEDGMWcH1ex", "Finley — 咬字清晰的主播腔"),
    ("22N9cF8z0o7y23njdyaY", "Florence — 富有氛围感的讲述者"),
    ("FrS6cKLB1wg4WYgPa9GW", "Wyatt — 老练的导师感"),
]
SUCCESSOR_TITLES = dict(SUCCESSOR_VOICES)

# 音色中文标题。接班音色见上；下面这批是 2026-12-31 退役的老音色，留着是为了
# 万一有人把它们同步回菜单时标题不至于变回英文。
VOICE_TITLES = {
    "pNInz6obpgDQGcFmaJgB": "Adam — 男声 · 美式 · 强势坚定",
    "hpp4J3VqNfWAUOO0d1Us": "Bella — 女声 · 美式 · 专业、明亮温暖",
    "Xb7hH8MSUJpSbSDYk0k2": "Alice — 女声 · 英式 · 清晰、像老师",
    "pqHfZKP75CvOlQylNhV4": "Bill — 男声 · 美式 · 睿智成熟、均衡",
    "nPczCjzI2devNBz1zQrb": "Brian — 男声 · 美式 · 低沉浑厚、令人安定",
    "N2lVS1w4EtoT3dr4eOWO": "Callum — 男声 · 美式 · 沙哑、狡黠",
    "IKne3meq5aSn9XLyUdCD": "Charlie — 男声 · 澳式 · 低沉自信、有活力",
    "iP95p4xoKVk53GoZ742B": "Chris — 男声 · 美式 · 有魅力、接地气",
    "onwK4e9ZLuTAKqWW03F9": "Daniel — 男声 · 英式 · 沉稳播音腔",
    "cjVigY5qzO86Huf0OWal": "Eric — 男声 · 美式 · 流畅、可信赖",
    "JBFqnCBsd6RMkjVDRZzb": "George — 男声 · 英式 · 温暖、擅长讲故事",
    "SOYHLrjzK2X1ezoPC6cr": "Harry — 男声 · 美式 · 凶悍战士感",
    "cgSgspJ2msm6clMCkdW9": "Jessica — 女声 · 美式 · 俏皮明亮、温暖",
    "FGY2WhTYpPnrIDTdsKH5": "Laura — 女声 · 美式 · 热情、古灵精怪",
    "TX3LPaxmHKxFdv7VOQHJ": "Liam — 男声 · 美式 · 有活力、自媒体口吻",
    "pFZP5JQG7iQjIQuC4Bku": "Lily — 女声 · 英式 · 丝绒质感、演员腔",
    "XrExE9yKIg1WjnnlVkGX": "Matilda — 女声 · 美式 · 博学、专业",
    "SAz9YHcvj6GT2YYXdXww": "River — 中性声 · 美式 · 放松、中立、偏说明性",
    "CwhRBWXzGAHq8TQ4Fs17": "Roger — 男声 · 美式 · 松弛随性、声音浑厚",
    "EXAVITQu4vr4xnSDxMaL": "Sarah — 女声 · 美式 · 成熟沉稳、令人安心",
    "bIHbv24MWmeRgasZH58o": "Will — 男声 · 美式 · 松弛乐观",
}


def api_get(path, api_key):
    status, data, _ = api_client.request(API_BASE, "GET", path, api_key, timeout=30)
    if not 200 <= status < 300 or api_client.operational_failure(status, data):
        sys.exit(f"请求 {path} 失败：HTTP {status} {json.dumps(data, ensure_ascii=False)[:500]}")
    return data


def option_by_id(info, identifier):
    for option in info["options"]:
        if option["identifier"] == identifier:
            return option
    sys.exit(f"info.json 里找不到 identifier 为 {identifier} 的选项")


def model_entries(api_key, catalog=None):
    catalog = model_catalog.read(CATALOG_PATH) if catalog is None else catalog
    updated, unknown = model_catalog.refresh(catalog, api_get("/models", api_key))
    for mid in unknown:
        print(f"? 模型  {mid}  尚未核验接口，未加入菜单；请先在 model_catalog.js 登记")
    catalog.clear()
    catalog.update(updated)
    return model_catalog.visible_entries(catalog)


def voice_entries(api_key):
    """返回 (菜单条目, voice_id -> category)。

    category 很关键：免费订阅通过 API 用音色库(Voice Library)音色会 402
    （Voice Library voices are not available via the API to free tier users）。
    当前内置菜单是 19 个接班音色；账户接口不一定会返回它们。
    Default/premade 音色已宣布 2026-12-31 停用，不能用它们替换内置预设。
    """
    data = api_get("/voices", api_key)
    if not isinstance(data, dict) or not isinstance(data.get("voices"), list):
        raise ValueError("/voices 响应必须包含 voices 数组")
    voices = data["voices"]
    entries = []
    categories = {}
    for voice in voices:
        if not isinstance(voice, dict) or any(not isinstance(voice.get(k), str) or not voice[k] for k in ("voice_id", "name")):
            raise ValueError("/voices 含有缺少合法 ID / 名称的记录")
        labels = voice.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("音色 labels 必须是对象")
        bits = [labels.get(k) for k in ("gender", "accent", "description")]
        if any(value is not None and not isinstance(value, str) for value in bits):
            raise ValueError("音色描述标签必须是字符串")
        suffix = " · ".join(b for b in bits if b)
        title = f"{voice['name']} — {suffix}" if suffix else voice["name"]
        entries.append({"title": title[:160], "value": voice["voice_id"]})
        categories[voice["voice_id"]] = voice.get("category") or "unknown"
    return entries, categories


def apply_overlay(info, catalog=None):
    """把展示层规则套到菜单上。纯本地操作，不需要 API。

    有任何内容或顺序变化时返回 1，否则返回 0。
    """
    before = copy.deepcopy(info)
    catalog = _INITIAL_CATALOG if catalog is None else catalog
    visible = model_catalog.visible_entries(catalog)
    titles = {entry["value"]: entry["title"] for entry in visible}
    order = list(titles)

    model_option = option_by_id(info, "model")
    kept = []
    for entry in model_option.get("menuValues", []):
        spec = catalog.get(entry["value"], {})
        if spec.get("deprecated"):
            print(f"- 模型  {entry['value']}  已废弃，移出菜单")
            continue
        if spec.get("hidden"):
            print(f"- 模型  {entry['value']}  从菜单隐藏，旧配置仍兼容")
            continue
        if entry["value"] not in titles:
            print(f"? 模型  {entry['value']}  未登记接口能力，移出菜单")
            continue
        title = titles.get(entry["value"])
        if title and entry["title"] != title:
            entry["title"] = title
        kept.append(entry)
    kept.sort(key=lambda e: order.index(e["value"]))
    model_option["menuValues"] = kept

    voice_option = option_by_id(info, "voice")
    body, tail = [], []
    retiring_count = 0
    for entry in voice_option.get("menuValues", []):
        if entry["value"] == CUSTOM_VOICE:
            tail.append(entry)
            continue
        vid = entry["value"]
        retiring = vid in RETIRING_VOICES
        if retiring:
            retiring_count += 1
        # 接班音色用固定标题；老音色用 VOICE_TITLES；都没有（自建音色）保留 API 原文
        title = (SUCCESSOR_TITLES.get(vid)
                 or VOICE_TITLES.get(vid)
                 or entry["title"].replace(RETIRING_SUFFIX, ""))
        if retiring and not title.endswith(RETIRING_SUFFIX):
            title += RETIRING_SUFFIX
        if title != entry["title"]:
            entry["title"] = title
        body.append(entry)
    # 保留 menuValues 既有顺序（v1.0.3 起按女前男后、美式前英式后手工排过）。
    # 不再按退役/标题重排：21 个 Default 音色全部在退役名单里，旧 sort 会退化成
    # 按标题字母序，把手工排序冲掉。__custom__ 始终排到最末。
    voice_option["menuValues"] = body + tail

    successors = sum(1 for e in body if e["value"] in SUCCESSOR_TITLES)
    print(f"\n展示层：模型 {len(kept)} 个，音色 {len(body)} 个"
          f"（接班音色 {successors} 个，2026-12-31 退役 {retiring_count} 个）")
    return int(info != before)


def merge(option, fresh, replace, keep_tail_value=None, protected_entries=None):
    """替换仅影响账户列表；内置接班音色及自定义项始终保留。"""
    existing = option.get("menuValues", [])
    def unique(entries):
        seen, result = set(), []
        for entry in entries:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) for k in ("value", "title")):
                raise ValueError("菜单条目必须包含字符串 value / title")
            if entry["value"] not in seen:
                result.append(dict(entry))
                seen.add(entry["value"])
        return result
    protected_entries = unique(protected_entries or [])
    protected_ids = {e["value"] for e in protected_entries}
    tail = unique([e for e in existing if e["value"] == keep_tail_value])
    existing_body = unique([e for e in existing if e["value"] != keep_tail_value])
    fresh_body = unique([e for e in fresh if e["value"] != keep_tail_value])
    known = {e["value"] for e in existing_body}
    upstream = {e["value"] for e in fresh_body}
    protected = unique([e for e in existing_body if e["value"] in protected_ids] + protected_entries)
    if replace:
        merged = protected + [e for e in fresh_body if e["value"] not in protected_ids]
    else:
        merged = unique(existing_body + protected + fresh_body)
    added = [e for e in merged if e["value"] not in known]
    stale = [e for e in existing_body if e["value"] not in upstream and e["value"] not in protected_ids]
    return merged + tail, added, stale


def repair_defaults(info):
    """确保 model/voice 默认值仍存在；合法的 __custom__ 也必须保留。"""
    changed = 0
    for identifier in ("model", "voice"):
        option = option_by_id(info, identifier)
        values = [entry["value"] for entry in option.get("menuValues", [])]
        current = option.get("defaultValue")
        if not values:
            raise ValueError(f"{identifier} 菜单为空，不能生成可用默认值")
        if current in values and not (identifier == "voice" and current in RETIRING_VOICES):
            continue
        safe = [v for v in values if v != CUSTOM_VOICE and not (identifier == "voice" and v in RETIRING_VOICES)]
        replacement = safe[0] if safe else CUSTOM_VOICE if CUSTOM_VOICE in values else None
        if replacement is None:
            raise ValueError("音色菜单只有退役条目，请恢复内置接班音色")
        reason = "属于退役音色" if identifier == "voice" and current in RETIRING_VOICES else "已不在菜单里"
        print(f"\n! {identifier} 的默认值 {current} {reason}，改为 {replacement}")
        option["defaultValue"] = replacement
        changed += 1
    return changed


def write_json_atomic(path, value):
    model_catalog.write_text_atomic(path, json.dumps(value, indent=4, ensure_ascii=False) + "\n")


def write_catalog_and_info(catalog, info):
    outputs = {pathlib.Path(CATALOG_PATH): model_catalog.render(catalog),
               pathlib.Path(INFO): json.dumps(info, indent=4, ensure_ascii=False) + "\n"}
    originals = {path: path.read_text(encoding="utf-8") for path in outputs}
    applied = []
    try:
        for path, text in outputs.items():
            if text != originals[path]:
                model_catalog.write_text_atomic(path, text)
                applied.append(path)
    except BaseException:
        for path in reversed(applied):
            model_catalog.write_text_atomic(path, originals[path])
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", action="store_true", help="替换账户列表，保留已核验模型及内置音色")
    parser.add_argument("--dry-run", action="store_true", help="只打印差异")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--models-only", action="store_true")
    scope.add_argument("--voices-only", action="store_true")
    parser.add_argument("--sync-voices", action="store_true",
                        help="显式同步音色（默认跳过，见 do_voices 处注释）")
    parser.add_argument(
        "--overlay-only",
        action="store_true",
        help="只重新套用展示层规则（废弃模型过滤、中文标题、退役标注），不联网",
    )
    args = parser.parse_args(argv)
    if args.models_only and args.sync_voices:
        parser.error("--models-only 与 --sync-voices 不能同时使用")
    catalog = model_catalog.read(CATALOG_PATH)
    before_catalog = copy.deepcopy(catalog)

    if not args.overlay_only:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            try:
                api_key = getpass.getpass("ElevenLabs API Key（输入不回显）: ")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\n已取消")
        api_key = (api_key or "").strip()
        if not api_key:
            sys.exit("没有拿到 API Key")

    with INFO.open(encoding="utf-8") as fp:
        info = json.load(fp)

    do_models = not args.voices_only
    # 音色需要额外读取权限，仍由 --sync-voices 显式开启；替换时保护内置接班音色。
    do_voices = (args.voices_only or args.sync_voices) and not args.overlay_only
    if not do_voices and not args.models_only and not args.overlay_only:
        print("跳过账户音色同步；确需同步请加 --sync-voices（内置接班音色会保留）\n")
    changed = False

    if do_models:
        option = option_by_id(info, "model")
        fresh = model_catalog.visible_entries(catalog) if args.overlay_only else model_entries(api_key, catalog)
        merged, added, stale = merge(option, fresh, args.replace)
        for entry in added:
            print(f"+ 模型  {entry['value']}  {entry['title']}")
        for entry in stale:
            print(f"! 模型  {entry['value']}  账号里已看不到（可能已下线）")
        if merged != option.get("menuValues"):
            option["menuValues"] = merged
            changed = True

    if do_voices:
        option = option_by_id(info, "voice")
        fresh, categories = voice_entries(api_key)
        merged, added, stale = merge(
            option, fresh, args.replace, keep_tail_value=CUSTOM_VOICE,
            protected_entries=[{"title": title, "value": vid} for vid, title in SUCCESSOR_VOICES]
        )
        for entry in added:
            category = categories.get(entry['value'], 'builtin' if entry['value'] in SUCCESSOR_TITLES else '?')
            print(f"+ 音色  [{category:<12}] {entry['value']}  {entry['title']}")
        for entry in stale:
            print(f"! 音色  {entry['value']}  账号里已看不到")

        seen = sorted({categories[e["value"]] for e in fresh})
        print(f"\n账号里的音色分类：{', '.join(seen) or '（空）'}")
        print("内置接班音色和账户音色已合并；可用性以当前 Key、订阅及 Bob 验证结果为准。")

        if merged != option.get("menuValues"):
            option["menuValues"] = merged
            changed = True

    # API 只负责「有什么」，展示规则每次都要重新套一遍
    if apply_overlay(info, catalog):
        changed = True

    # --replace 会整体换掉菜单，默认值有可能被换没了。Bob 对不在菜单里的值不会
    # 报错，只会照旧发出去，界面却显示成第一项，因此必须兜住。__custom__ 是
    # 合法菜单值，不能把用户主动选择的自定义音色重置掉。
    if repair_defaults(info):
        changed = True

    changed = changed or catalog != before_catalog
    if not changed:
        print("没有变化。")
        return

    if args.dry_run:
        print("\n--dry-run，未写入模型目录或 src/info.json")
        return

    write_catalog_and_info(catalog, info)
    print("\n已更新模型目录与 src/info.json")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        sys.exit(f"同步失败：{error}")
