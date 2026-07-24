#!/usr/bin/env python3
"""核对 2026-12-31 之后的接班音色：官方 19 个 voice_id + 本账号可用性。

背景：2026-12-31 之后 src/info.json 里那 21 个 Default 音色全部失效。官方帮助
文章给了一张 19 行替换表；表格**正文**只有名字，但**每个名字本身是超链接**，
链接最终指向 elevenlabs.io/app/voice-library?search=<voice_id> —— ID 就在那里。
19 个 ID 已据此提取并固化在下面的 REPLACEMENTS（提取方法见其注释）。

新音色**不在** /v1/voices 里（免密钥与带密钥都只返回那 21 个 premade），
所以本脚本做三件事：

  1. 列出官方权威 ID（离线，无需网络）
  2. 用音色库搜索给每个 ID 补元数据：category / free_users_allowed / 计费倍率
  3. **交叉校验**：按名字搜到的结果与官方 ID 是否一致，不一致就告警

第 3 步不是多余的。实测 Kellan：音色库搜索只返回一个候选
ogqEVaDb8zHocDItWo7S（high_quality、free=True，信号看起来完全干净），
而官方链接给的是 cymHWdiF8WjUCg6vvFxx。**按名字搜会静悄悄挑错、毫无警示**，
这正是网上流传那批错 ID 的成因。其余 18 个两法一致。

用法：
    python3 scripts/resolve_voices.py              # 核对并打印（只读，不耗额度）
    python3 scripts/resolve_voices.py --offline    # 只打印官方表，完全不联网、不要 Key
    python3 scripts/resolve_voices.py --probe      # 额外对每个 ID 实打 2 字符确认真能合成
    python3 scripts/resolve_voices.py --json       # 输出可并进 info.json 的 menuValues 片段

核对只读、不消耗额度；--probe 每个音色约 1~2 credits（19 个约 30 credits）。
只依赖标准库。全程不打印 API Key。
"""

import argparse
import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.elevenlabs.io/v1"
PROBE_TEXT = "hi"

# 官方替换表（help.elevenlabs.io「What are Default voices?」）。
#
# voice_id 的权威出处：**该表每个新音色名本身是超链接**，指向
# r.contact.elevenlabs.io 的跟踪页；跟踪页正文的 meta-refresh 目标是
# https://elevenlabs.io/app/voice-library?search=<voice_id>。
# 注意跟踪页返回 HTTP 405 且不发 Location 头，`curl -L` 跟不到底，必须读正文。
# 19 个 ID 于 2026-07-23 由此逐个提取（表格文字取自 docs 镜像
# /docs/help-center/product/voice-customization/my-voices/what-are-default-voices.md，
# 因为 help 站点直连被 Zendesk 403）。
#
# 注意 Bella 与 Adam **不在表内**：官方没给这两个安排接班音色。
#
# ⚠️ 为什么必须用官方链接的 ID，而不是「按名字去音色库搜」：
# 实测 Kellan 一项，音色库搜索只返回一个候选 ogqEVaDb8zHocDItWo7S
# （"Kellan - Resonant, Smooth and Confident"，high_quality、free=True——信号
# 看起来完全干净），但官方链接给的是 cymHWdiF8WjUCg6vvFxx。**按名字搜会静悄悄
# 挑错，且毫无警示。** 这正是网上流传那批错 ID 的成因。其余 18 个两法一致。
#
# 另有两处官方表文字与音色库现名不符，ID 以官方链接为准：
#   Eddie  官方表 "Helpful and Comforting" → 库中现名 "Natural and Helpful"
#   Finley 官方表 "Articulate Anchor"      → 库中现名多一个空格
REPLACEMENTS = [
    ("Roger", "Darian - Warm Grounded Storyteller", "gOupLcAkjEnguROwi4oS"),
    ("Sarah", "Talia - Warm Soft Guide", "OZ0L6eISlOejga3XjDFt"),
    ("Laura", "Elara - Crisp Pro Narrator", "WQP7cQUF5aAS6Axh5yaa"),
    ("Charlie", "Baxter - Dry Calm Aussie", "jSuBIjxMKhqIfb0wCK1F"),
    ("George", "Eldrin - Crisp British Baritone", "6WwXjDDEMyNmFG95zycZ"),
    ("Callum", "Kellan - Casual Friendly Speaker", "cymHWdiF8WjUCg6vvFxx"),
    ("River", "Elowen - Upbeat Modern Narrator", "dvbL7qkNGZY1IqPGZAjM"),
    ("Harry", "Kaelen - Amateur Warrior", "10NkTYmU7tSz3Kkl3Lex"),
    ("Liam", "Lawrence - Bright and Informative", "ktkP7Nsj67dw2zcplQYt"),
    ("Alice", "Alicia - Polished Global Anchor", "BFd5oBc2DDna33pSi4Gf"),
    ("Matilda", "Maisie - Friendly Casual Neighbor", "QtY3JBOUKEB5xzrRfOKc"),
    ("Will", "Warren - Effortless and Cool", "7QN34D2r3hCNwbOYIeK0"),
    ("Jessica", "Jade - Upbeat and Natural", "g7LVvkPWALzPxOQbF6OE"),
    ("Eric", "Eddie - Helpful and Comforting", "l7kNoIfnJKPg7779LI2t"),
    ("Chris", "Caleb - Trusted Guide", "AaOhDHYJ1XLZk74lXhdE"),
    ("Brian", "Sawyer - Midnight Storyteller", "8dEUmyPMdDdK91vboYih"),
    ("Daniel", "Finley - Articulate Anchor", "fnYMz3F5gMEDGMWcH1ex"),
    ("Lily", "Florence - Atmospheric Storyteller", "22N9cF8z0o7y23njdyaY"),
    ("Bill", "Wyatt - Seasoned Mentor", "FrS6cKLB1wg4WYgPa9GW"),
]


def request(method, path, api_key, body=None, timeout=30):
    """返回 (http_status, parsed_json_or_None, byte_count)。"""
    url = API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"xi-api-key": api_key}
    if data:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload), len(payload)
            except (ValueError, UnicodeDecodeError):
                return resp.status, None, len(payload)      # 音频
    except urllib.error.HTTPError as err:
        payload = err.read()
        try:
            return err.code, json.loads(payload), len(payload)
        except (ValueError, UnicodeDecodeError):
            return err.code, {"_raw": payload[:300].decode("utf-8", "replace")}, len(payload)
    except urllib.error.URLError as err:
        return 0, {"_network": str(err.reason)}, 0
    except (socket.timeout, TimeoutError, OSError) as err:
        return 0, {"_timeout": str(err)}, 0


def api_error(detail):
    """把错误体压成一行，方便排查。两套命名空间（code/status）都带上。"""
    if not isinstance(detail, dict):
        return str(detail)[:200]
    inner = detail.get("detail")
    if isinstance(inner, dict):
        bits = [inner.get("code"), inner.get("status"), inner.get("message")]
        return " / ".join(str(b) for b in bits if b)[:200]
    return json.dumps(detail)[:200]


def own_voices(api_key):
    """账号自己的音色（/v1/voices）。新音色通常不在这里，但先查一遍，
    因为若用户已把某个接班音色 add 到 My Voices，这里能直接拿到 ID。
    返回 (音色列表, 请求是否成功)。"""
    status, data, _ = request("GET", "/voices", api_key)
    if status != 200 or not isinstance(data, dict):
        print(f"  ! 读取 /v1/voices 失败（HTTP {status}）：{api_error(data)}", file=sys.stderr)
        return [], False
    return data.get("voices") or [], True


def search_library(api_key, term, page_size=30):
    """在音色库里按名字搜。返回 (候选列表, 错误串或 None)。"""
    query = urllib.parse.urlencode({"search": term, "page_size": page_size})
    status, data, _ = request("GET", f"/shared-voices?{query}", api_key)
    if status != 200 or not isinstance(data, dict):
        return [], f"HTTP {status} {api_error(data)}"
    return (data.get("voices") or []), None


def first_token(display_name):
    """"Darian - Warm Grounded Storyteller" -> "Darian"。"""
    return display_name.split(" - ")[0].strip()


def describe(voice):
    """把一个候选压成一行人读信息。字段按存在与否取，避免 schema 变动炸掉。"""
    bits = []
    for key, label in (
        ("category", "cat"),
        ("free_users_allowed", "free"),
        ("is_added_by_user", "已添加"),
    ):
        if key in voice:
            bits.append(f"{label}={voice[key]}")
    # 计费倍率字段各版本命名不一，能拿到就显示 —— 倍率高的音色成本会翻倍
    for key in ("rate", "price_multiplier", "credit_multiplier"):
        if voice.get(key) not in (None, "", 1):
            bits.append(f"{key}={voice[key]}")
    return "  ".join(bits)


def crosscheck_one(api_key, new_name, official_id, owned):
    """给官方 ID 补元数据，并与「按名字搜」的结果交叉校验。

    返回 {meta, verdict, note, candidates}：
      verdict 'own'      —— 该 ID 已在你账号里（一定可用）
              'match'    —— 按名字搜能搜到同一个 ID，两法一致
              'diverge'  —— 搜到同名的**别的** ID，官方 ID 不在搜索结果里（Kellan 即此）
              'unlisted' —— 名字搜不到任何候选
              'error'    —— 搜索失败
    """
    token = first_token(new_name)

    owned_hit = next((v for v in owned if v.get("voice_id") == official_id), None)
    if owned_hit:
        return {"meta": owned_hit, "verdict": "own", "note": "已在账号内",
                "candidates": []}

    found, err = search_library(api_key, token)
    if err:
        return {"meta": None, "verdict": "error", "note": err, "candidates": []}

    same_name = [v for v in found if first_token(v.get("name", "")) == token]
    official = next((v for v in same_name if v.get("voice_id") == official_id), None)

    if official:
        others = [v for v in same_name if v.get("voice_id") != official_id]
        note = f"搜索一致（另有 {len(others)} 个同名干扰项）" if others else "搜索一致"
        return {"meta": official, "verdict": "match", "note": note,
                "candidates": others}

    if not same_name:
        return {"meta": None, "verdict": "unlisted",
                "note": "音色库按名字搜不到（官方 ID 仍以表为准）", "candidates": []}

    return {"meta": None, "verdict": "diverge",
            "note": f"⚠ 按名字搜到 {len(same_name)} 个同名音色，但**都不是**官方 ID —— "
                    f"只按名字搜就会挑错",
            "candidates": same_name}


def probe(api_key, voice_id):
    """实打 2 字符，确认这个 ID 在本账号下真能合成。返回 (ok, 说明)。"""
    status, detail, size = request(
        "POST", f"/text-to-speech/{voice_id}?output_format=mp3_22050_32", api_key,
        {"text": PROBE_TEXT, "model_id": "eleven_flash_v2_5"},
    )
    if 200 <= status < 300 and detail is None and size > 0:
        return True, f"200，{size} bytes 音频"
    if 200 <= status < 300:
        return False, f"HTTP {status} 返回 JSON 或空响应，并非音频：{api_error(detail)}"
    return False, f"HTTP {status} {api_error(detail)}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true",
                        help="对每个 ID 实打 2 字符确认可用（约 1~2 credits/个）")
    parser.add_argument("--json", action="store_true",
                        help="额外输出可并进 info.json 的 menuValues 片段")
    parser.add_argument("--offline", action="store_true",
                        help="只打印官方表，不联网、不需要 Key")
    args = parser.parse_args(argv)

    if args.offline:
        print(f"官方接班音色表（{len(REPLACEMENTS)} 个，出处见脚本 REPLACEMENTS 注释）")
        print("=" * 74)
        for old, new, vid in REPLACEMENTS:
            print(f"  {old:<9} → {vid}  {new}")
        print("=" * 74)
        print("Bella 与 Adam 不在官方替换表内，没有接班音色。")
        if args.json:
            print("\ninfo.json menuValues 片段（标题请自行改成中文）：")
            print(json.dumps([{"title": new, "value": vid}
                              for _, new, vid in REPLACEMENTS],
                             indent=4, ensure_ascii=False))
        return 0

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        try:
            api_key = getpass.getpass("ElevenLabs API Key（输入不回显）: ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\n已取消")
    api_key = (api_key or "").strip()
    if not api_key:
        print("没有拿到 API Key", file=sys.stderr)
        return 1

    print("读取账号音色 …")
    owned, owned_ok = own_voices(api_key)
    print(f"  账号内 {len(owned)} 个\n")

    print(f"核对官方替换表的 {len(REPLACEMENTS)} 个接班音色（只读，不耗额度）")
    print("=" * 78)

    diverged = []
    operational_failures = 0

    for old, new, vid in REPLACEMENTS:
        r = crosscheck_one(api_key, new, vid, owned)
        mark = {"own": "✓", "match": "✓", "unlisted": "·",
                "diverge": "⚠", "error": "✗"}[r["verdict"]]
        meta = describe(r["meta"]) if r["meta"] else ""
        print(f"{mark} {old:<9} → {first_token(new):<9} {vid}  {meta}")
        print(f"{'':32}{r['note']}")

        if r["verdict"] == "diverge":
            diverged.append((old, new, vid))
            for v in r["candidates"]:
                print(f"{'':32}  搜到 {v['voice_id']}  {v.get('name','?')}  {describe(v)}")
        elif r["verdict"] == "error":
            operational_failures += 1

    print("=" * 78)
    if diverged:
        names = "、".join(first_token(n) for _, n, _ in diverged)
        print(f"官方 ID {len(REPLACEMENTS)} 个；与按名字搜的结果分歧 {len(diverged)} 个"
              f"（{names}）—— 只按名字搜会挑错，务必用官方 ID")
    else:
        print(f"官方 ID {len(REPLACEMENTS)} 个；与按名字搜的结果无分歧")
    print("Bella 与 Adam 不在官方替换表内，没有接班音色。")

    if args.probe:
        print(f"\n实打确认（每个约 1~2 credits，共 {len(REPLACEMENTS)} 个）")
        print("-" * 78)
        usable = 0
        for old, new, vid in REPLACEMENTS:
            ok, note = probe(api_key, vid)
            print(f"  {'✓' if ok else '✗'} {first_token(new):<9} {vid}  {note}")
            usable += 1 if ok else 0
        print(f"  本账号下可用 {usable} / {len(REPLACEMENTS)}")
        operational_failures += len(REPLACEMENTS) - usable

    if args.json:
        print("\ninfo.json menuValues 片段（标题请自行改成中文）：")
        print(json.dumps([{"title": new, "value": vid}
                          for _, new, vid in REPLACEMENTS],
                         indent=4, ensure_ascii=False))
    if not owned_ok:
        operational_failures += 1
    if operational_failures:
        print(f"\n核验未完整完成：{operational_failures} 个请求或探测失败。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
