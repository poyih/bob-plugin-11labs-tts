#!/usr/bin/env python3
"""把官方「Default 音色替换表」的 19 个新音色名解析成真实 voice_id。

背景：2026-12-31 之后 src/info.json 里那 21 个 Default 音色全部失效。官方帮助
文章给了一张 19 行替换表，但**只有名字、没有 ID** —— 新音色不在 /v1/voices 里
（免密钥和带密钥都只返回那 21 个 premade），只能通过需要鉴权的音色库搜索查到。

为什么不能直接抄网上流传的那份 ID 表：那份表的作者本人已经撤回。
LiveKit 社区帖 community.livekit.io/t/new-default-elevenlabs-voices/459 第 8 楼
（Mike Saunders, 2026-04-22）原文：

    "The announcement email only had names so I guess I pulled in the wrong
     codes from the API."

官方公告只给了名字，他按名字去 API 搜，搜到了**同名的错误音色**（于是同帖里
另一位说这些是 Pro Cloned 音色 —— 他查的正是那批错 ID）。

所以本脚本的核心设计是：**绝不自动挑选**。同名候选一律全部列出、标明歧义，
由人来定。名字撞车正是上面那次出错的根因，自动挑等于重蹈覆辙。

用法：
    python3 scripts/resolve_voices.py              # 解析并打印（只读，不耗额度）
    python3 scripts/resolve_voices.py --probe      # 额外对每个 ID 实打 2 字符确认真能合成
    python3 scripts/resolve_voices.py --json       # 输出可并进 info.json 的 menuValues 片段

解析只读、不消耗额度；--probe 每个音色约 1~2 credits（19 个约 30 credits）。
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

# 官方替换表（help.elevenlabs.io「What are Default voices?」，2026-07-23 逐字抄录
# 自 docs 镜像 /docs/help-center/product/voice-customization/my-voices/
# what-are-default-voices.md —— help 站点直连被 Zendesk 403）。
# 19 行。注意 Bella 与 Adam **不在表内**：官方没有给这两个安排接班音色。
REPLACEMENTS = [
    ("Roger", "Darian - Warm Grounded Storyteller"),
    ("Sarah", "Talia - Warm Soft Guide"),
    ("Laura", "Elara - Crisp Pro Narrator"),
    ("Charlie", "Baxter - Dry Calm Aussie"),
    ("George", "Eldrin - Crisp British Baritone"),
    ("Callum", "Kellan - Casual Friendly Speaker"),
    ("River", "Elowen - Upbeat Modern Narrator"),
    ("Harry", "Kaelen - Amateur Warrior"),
    ("Liam", "Lawrence - Bright and Informative"),
    ("Alice", "Alicia - Polished Global Anchor"),
    ("Matilda", "Maisie - Friendly Casual Neighbor"),
    ("Will", "Warren - Effortless and Cool"),
    ("Jessica", "Jade - Upbeat and Natural"),
    ("Eric", "Eddie - Helpful and Comforting"),
    ("Chris", "Caleb - Trusted Guide"),
    ("Brian", "Sawyer - Midnight Storyteller"),
    ("Daniel", "Finley - Articulate Anchor"),
    ("Lily", "Florence - Atmospheric Storyteller"),
    ("Bill", "Wyatt - Seasoned Mentor"),
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
    因为若用户已把某个接班音色 add 到 My Voices，这里能直接拿到 ID。"""
    status, data, _ = request("GET", "/voices", api_key)
    if status != 200 or not isinstance(data, dict):
        print(f"  ! 读取 /v1/voices 失败（HTTP {status}）：{api_error(data)}", file=sys.stderr)
        return []
    return data.get("voices") or []


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


def resolve_one(api_key, new_name, owned_by_name):
    """解析一个新音色名。返回 dict：
       {name, token, matches: [voice...], source: 'own'|'library'|None, error}

    绝不自动挑选：exact 命中也把其余同名候选一并留在 matches 里，交给人看。
    """
    token = first_token(new_name)

    # 1) 先看账号自己有没有（免搜索，且这类 ID 一定可用）
    owned = [v for v in owned_by_name if first_token(v.get("name", "")) == token]
    if owned:
        return {"name": new_name, "token": token, "matches": owned,
                "source": "own", "error": None}

    # 2) 再搜音色库
    found, err = search_library(api_key, token)
    if err:
        return {"name": new_name, "token": token, "matches": [],
                "source": None, "error": err}

    matches = [v for v in found if first_token(v.get("name", "")) == token]
    # 完整显示名精确命中的排前面，但**不丢弃**其余同名候选
    matches.sort(key=lambda v: (v.get("name", "") != new_name,
                                v.get("name", "")))
    return {"name": new_name, "token": token, "matches": matches,
            "source": "library" if matches else None, "error": None}


def probe(api_key, voice_id):
    """实打 2 字符，确认这个 ID 在本账号下真能合成。返回 (ok, 说明)。"""
    status, detail, size = request(
        "POST", f"/text-to-speech/{voice_id}?output_format=mp3_22050_32", api_key,
        {"text": PROBE_TEXT, "model_id": "eleven_flash_v2_5"},
    )
    if 200 <= status < 300:
        return True, f"200，{size} bytes 音频"
    return False, f"HTTP {status} {api_error(detail)}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=None,
                        help="留空则读 ELEVENLABS_API_KEY，再留空则交互式输入（不回显）")
    parser.add_argument("--probe", action="store_true",
                        help="对每个解析到的 ID 实打 2 字符确认可用（约 1~2 credits/个）")
    parser.add_argument("--json", action="store_true",
                        help="额外输出可并进 info.json 的 menuValues 片段")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        try:
            api_key = getpass.getpass("ElevenLabs API Key（输入不回显）: ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\n已取消")
    api_key = (api_key or "").strip()
    if not api_key:
        sys.exit("没有拿到 API Key")

    print("读取账号音色 …")
    owned = own_voices(api_key)
    print(f"  账号内 {len(owned)} 个\n")

    print(f"解析官方替换表的 {len(REPLACEMENTS)} 个新音色（只读，不耗额度）")
    print("=" * 78)

    resolved, ambiguous, missing = [], [], []

    for old, new in REPLACEMENTS:
        r = resolve_one(api_key, new, owned)
        head = f"{old:<9} → {r['token']:<9}"

        if r["error"]:
            print(f"{head}  ✗ 搜索失败：{r['error']}")
            missing.append((old, new, r["error"]))
            continue
        if not r["matches"]:
            print(f"{head}  ✗ 音色库里没搜到")
            missing.append((old, new, "not found"))
            continue

        exact = [v for v in r["matches"] if v.get("name") == new]
        src = "账号内" if r["source"] == "own" else "音色库"

        if len(r["matches"]) == 1 and exact:
            v = r["matches"][0]
            print(f"{head}  ✓ {v['voice_id']}  [{src}]  {describe(v)}")
            resolved.append((old, new, v))
        else:
            # 同名多个 —— 正是当年出错的地方，全部摊开，不替你选
            flag = "⚠ 同名多个，需人工确认" if not exact else "⚠ 有同名干扰项"
            print(f"{head}  {flag}（{len(r['matches'])} 个候选，[{src}]）")
            for v in r["matches"]:
                mark = "←官方全名精确匹配" if v.get("name") == new else ""
                print(f"{'':13}   {v['voice_id']}  {v.get('name','?')}  {describe(v)} {mark}")
            if exact:
                resolved.append((old, new, exact[0]))
            ambiguous.append((old, new, r["matches"]))

    print("=" * 78)
    print(f"解析到 {len(resolved)} / {len(REPLACEMENTS)}；"
          f"同名歧义 {len(ambiguous)}；未找到 {len(missing)}")
    print("注意：Bella 与 Adam 不在官方替换表内，没有接班音色。")

    if args.probe and resolved:
        print(f"\n实打确认（每个约 1~2 credits，共 {len(resolved)} 个）")
        print("-" * 78)
        usable = 0
        for old, new, v in resolved:
            ok, note = probe(api_key, v["voice_id"])
            print(f"  {'✓' if ok else '✗'} {first_token(new):<9} {v['voice_id']}  {note}")
            usable += 1 if ok else 0
        print(f"  本账号下可用 {usable} / {len(resolved)}")

    if args.json and resolved:
        print("\ninfo.json menuValues 片段（标题请自行改成中文）：")
        print(json.dumps(
            [{"title": v.get("name", new), "value": v["voice_id"]}
             for _, new, v in resolved],
            indent=4, ensure_ascii=False))


if __name__ == "__main__":
    main()
