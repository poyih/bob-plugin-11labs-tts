#!/usr/bin/env python3
"""拿真实 API 把插件里的假设一条条打出原形。

插件里有一批假设只来自文档，从没被真机验证过：错误 detail.code/status 的具体字符串、
各模型是否可用、output_format 的订阅门槛、voice_settings 能否部分下发、
language_code 在 multilingual_v2 上到底是被忽略还是报错。文档答不了这些，
只有真打一遍才知道。

    python3 scripts/verify_api.py                 # 提示输入 Key，跑全部探针
    python3 scripts/verify_api.py --only status   # 只跑某一组
    python3 scripts/verify_api.py --dry-run       # 只列出会发什么请求，不联网

成功的合成用 2 字符文本，约 1~2 credits 一次；失败的请求不计费。
全量跑一次大约消耗 30~40 credits（免费档每月 10000）。

只依赖标准库。全程不打印 API Key。
"""

import argparse
import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.elevenlabs.io/v1"

# 2 个字符，把每次成功合成的成本压到最低
TEXT = "hi"

# 音色库音色：免费档用它必然 402，正是用来逼出那个 status 字符串的。
# 注意 payg/付费档实测 Aria 是 200（付费档能用音色库音色），所以这条探针只在
# 免费档 Key 下能复现 402；付费档跑时它返回 200，402 的 payment_required 字符串
# 靠既有免费档真机记录支撑（见 HANDOFF「已经验证过」一节）。
LIBRARY_VOICE = "9BWtsMINqrJLrRacOk9x"  # Aria
DEFAULT_PROBE_VOICE = "WQP7cQUF5aAS6Axh5yaa"  # 插件当前默认 Elara


class Result:
    def __init__(self, group, name, status, detail, note=""):
        self.group = group
        self.name = name
        self.status = status          # HTTP 状态码，0 表示网络层失败
        self.detail = detail          # 解析出的 JSON（失败时）或 None
        self.note = note              # 成功时的补充信息

    @property
    def ok(self):
        return 200 <= self.status < 300 and not (
            isinstance(self.detail, dict) and "_non_audio" in self.detail
        )

    @property
    def operational_failure(self):
        return self.status == 0 or (
            isinstance(self.detail, dict) and "_non_audio" in self.detail
        )

    @property
    def error_detail(self):
        d = self.detail
        if isinstance(d, dict) and "_non_audio" in d:
            return d.get("_non_audio")
        return d

    @property
    def code_string(self):
        """新格式的 detail.code。"""
        d = self.error_detail
        if isinstance(d, dict):
            inner = d.get("detail")
            if isinstance(inner, dict):
                return inner.get("code") or ""
        return ""

    @property
    def status_string(self):
        """旧格式/兼容字段 detail.status。"""
        d = self.error_detail
        if isinstance(d, dict):
            inner = d.get("detail")
            if isinstance(inner, dict):
                return inner.get("status") or ""
            if isinstance(inner, list) and inner:
                return "(422 校验数组)"
        return ""

    @property
    def type_string(self):
        d = self.error_detail
        if isinstance(d, dict):
            inner = d.get("detail")
            if isinstance(inner, dict):
                return inner.get("type") or ""
        return ""

    @property
    def request_id(self):
        d = self.error_detail
        if isinstance(d, dict):
            inner = d.get("detail")
            if isinstance(inner, dict):
                return inner.get("request_id") or ""
        return ""

    @property
    def message(self):
        d = self.error_detail
        if isinstance(d, dict):
            inner = d.get("detail")
            if isinstance(inner, dict):
                return inner.get("message") or ""
            if isinstance(inner, str):
                return inner
            if isinstance(inner, list) and inner:
                return "; ".join(
                    str(i.get("msg") or i.get("message") or i) for i in inner
                )
            if "_network" in d:
                return d["_network"]
            if "_timeout" in d:
                return d["_timeout"]
        return ""


def request(method, path, api_key, body=None, timeout=60):
    """返回 (http_status, parsed_json_or_None, raw_byte_count)。"""
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
                return resp.status, None, len(payload)   # 音频
    except urllib.error.HTTPError as err:
        payload = err.read()
        try:
            return err.code, json.loads(payload), len(payload)
        except (ValueError, UnicodeDecodeError):
            return err.code, {"_raw": payload[:300].decode("utf-8", "replace")}, len(payload)
    except urllib.error.URLError as err:
        return 0, {"_network": str(err.reason)}, 0
    except (socket.timeout, TimeoutError, OSError) as err:
        # socket.timeout 不是 URLError 的子类，会直接穿透 —— 曾让整个脚本挂在
        # 超长文本探针上。统一兜底，避免一次超时毁掉整轮结果。
        return 0, {"_timeout": str(err)}, 0


def classify_audio(status, detail, size, note=""):
    """把 2xx JSON/空体标成操作失败，避免探针把它计作音频成功。"""
    if 200 <= status < 300:
        if detail is None and size > 0:
            note = f"{size} bytes 音频" + (f"，{note}" if note else "")
        else:
            detail = {"_non_audio": detail}
            note = f"{size} bytes，但响应为 JSON 或空内容，并非音频"
    return status, detail, note


def tts(api_key, voice, note="", **overrides):
    """发一次合成请求。overrides 直接并进 body，query 参数用 _query。"""
    query = overrides.pop("_query", "")
    body = {"text": TEXT, "model_id": "eleven_flash_v2_5"}
    body.update(overrides)
    path = f"/text-to-speech/{voice}{query}"
    return classify_audio(*request("POST", path, api_key, body), note=note)


# ---------------------------------------------------------------- 探针定义

def probes_scope(api_key, voice):
    """哪些端点在这把 Key 下可用 —— 决定 pluginValidate 该打哪个。"""
    for path in ("/models", "/voices", "/user", "/user/subscription"):
        status, detail, size = request("GET", path, api_key)
        yield Result("scope", f"GET {path}", status, detail,
                     f"{size} bytes" if 200 <= status < 300 else "")


def probes_status(api_key, voice):
    """把各类失败的 detail.code/status 原文逼出来。这些请求全部失败，不计费。"""
    # 密钥无效
    status, detail, size = request("GET", "/voices", "sk_definitely_not_a_real_key")
    yield Result("status", "无效 API Key", status, detail)

    # 音色不存在
    s, d, n = tts(api_key, "voice_id_that_does_not_exist_0000")
    yield Result("status", "音色 ID 不存在", s, d, n)

    # 音色库音色（免费档必 402）—— 插件真机遇到的那个
    s, d, n = tts(api_key, LIBRARY_VOICE)
    yield Result("status", f"音色库音色 {LIBRARY_VOICE}", s, d, n)

    # 模型不存在
    s, d, n = tts(api_key, voice, model_id="eleven_model_that_does_not_exist")
    yield Result("status", "model_id 不存在", s, d, n)

    # 超出字符上限（用 multilingual_v2 的 10000 上限，构造 12000 字符）。
    # 实测 10001（恰好超 1）有时会与长度校验竞态、先合成一段再被掐，白耗 credits；
    # 12000 远超上限，校验在前置阶段即判 400，不计费也不超时。
    over = "a" * 12000
    status, detail, size = request(
        "POST", f"/text-to-speech/{voice}", api_key,
        {"text": over, "model_id": "eleven_multilingual_v2"},
    )
    status, detail, note = classify_audio(
        status,
        detail,
        size,
        "说明上限不是 10000",
    )
    yield Result("status", "超出 multilingual_v2 字符上限（12000 字）",
                 status, detail, note)

    # 非法 output_format
    s, d, n = tts(api_key, voice, _query="?output_format=mp3_99999_999")
    yield Result("status", "非法 output_format", s, d, n)


def probes_models(api_key, voice):
    """4 个模型逐个实打，含从未在真机跑过的 multilingual_v2。"""
    for model in ("eleven_flash_v2_5", "eleven_flash_v2",
                  "eleven_multilingual_v2", "eleven_v3"):
        s, d, n = tts(api_key, voice, model_id=model)
        yield Result("models", model, s, d, n)


def probes_formats(api_key, voice):
    """插件菜单里的 4 种格式，含疑似需要 Creator 档的 192kbps。"""
    for fmt in ("mp3_44100_128", "mp3_44100_64", "mp3_22050_32", "mp3_44100_192"):
        s, d, n = tts(api_key, voice, _query=f"?output_format={fmt}")
        yield Result("formats", fmt, s, d, n)


def probes_settings(api_key, voice):
    """voice_settings 能否部分下发 —— 插件的『按需覆盖』策略成不成立全看这个。"""
    cases = [
        ("只传 stability", {"stability": 0.5}),
        ("只传 speed", {"speed": 1.1}),
        ("五项全传", {
            "stability": 0.5, "similarity_boost": 0.75, "style": 0.3,
            "speed": 1.1, "use_speaker_boost": False,
        }),
        ("越界 speed=2.0", {"speed": 2.0}),
        ("越界 stability=1.5", {"stability": 1.5}),
    ]
    for name, settings in cases:
        s, d, n = tts(api_key, voice, voice_settings=settings)
        yield Result("settings", name, s, d, n)

    # v3 是否只认离散 stability
    for value in (0.0, 0.3, 0.5, 1.0):
        s, d, n = tts(api_key, voice, model_id="eleven_v3",
                      voice_settings={"stability": value})
        yield Result("settings", f"v3 + stability={value}", s, d, n)

    # v3 是否接受 speed / style
    s, d, n = tts(api_key, voice, model_id="eleven_v3",
                  voice_settings={"speed": 1.1, "style": 0.3})
    yield Result("settings", "v3 + speed/style", s, d, n)


def probes_language(api_key, voice):
    """language_code 的真实行为：被忽略还是报错。"""
    cases = [
        ("flash_v2_5 + zh", "eleven_flash_v2_5", "zh"),
        ("flash_v2_5 + yue（粤语，疑似不支持）", "eleven_flash_v2_5", "yue"),
        ("flash_v2_5 + nb（挪威语，插件映射成 no）", "eleven_flash_v2_5", "nb"),
        ("flash_v2_5 + no", "eleven_flash_v2_5", "no"),
        ("flash_v2_5 + fil", "eleven_flash_v2_5", "fil"),
        ("flash_v2_5 + tl", "eleven_flash_v2_5", "tl"),
        ("flash_v2_5 + 乱码 zzz", "eleven_flash_v2_5", "zzz"),
        ("multilingual_v2 + zh（文档称不支持）", "eleven_multilingual_v2", "zh"),
        ("v3 + zh", "eleven_v3", "zh"),
        ("flash_v2 仅英语 + zh", "eleven_flash_v2", "zh"),
    ]
    for name, model, code in cases:
        s, d, n = tts(api_key, voice, model_id=model, language_code=code)
        yield Result("language", name, s, d, n)


GROUPS = {
    "scope": ("端点权限", probes_scope),
    "status": ("错误 code/status 原文", probes_status),
    "models": ("模型可用性", probes_models),
    "formats": ("音频格式", probes_formats),
    "settings": ("voice_settings", probes_settings),
    "language": ("language_code", probes_language),
}


# ---------------------------------------------------------------- 输出

def render(result):
    if result.ok:
        return f"  ✓ {result.name:<44} {result.note}"
    head = f"  ✗ {result.name:<44} HTTP {result.status}"
    bits = []
    if result.code_string:
        bits.append(f"code={result.code_string}")
    if result.status_string:
        bits.append(f"status={result.status_string}")
    if result.type_string:
        bits.append(f"type={result.type_string}")
    if result.request_id:
        bits.append(f"request_id={result.request_id}")
    if result.message:
        bits.append(result.message[:150])
    if result.note:
        bits.append(result.note[:150])
    return head + ("  " + " | ".join(bits) if bits else "")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice-id", default=None,
                        help="一个你账号里确实可用的 Voice ID；留空则自动挑第一个")
    parser.add_argument("--only", action="append", choices=sorted(GROUPS),
                        help="只跑指定分组，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只列出分组，不联网")
    args = parser.parse_args(argv)

    groups = args.only or list(GROUPS)

    if args.dry_run:
        print("将要执行的分组：")
        for key in groups:
            print(f"  {key:<10} {GROUPS[key][0]}")
        print("\n成功的合成每次约 1~2 credits，失败的不计费。")
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

    needs_voice = any(group != "scope" for group in groups)
    voice = (args.voice_id or "").strip()
    if needs_voice and not voice:
        status, data, _ = request("GET", "/voices", api_key)
        voices = (data.get("voices") or []) if isinstance(data, dict) else []
        if status == 200 and voices:
            voice = voices[0]["voice_id"]
            print(f"自动选用音色：{voices[0].get('name')} ({voice})\n")
        else:
            voice = DEFAULT_PROBE_VOICE
            print(
                f"! 拿不到音色列表（HTTP {status}），改用插件默认 Voice ID {voice}；"
                "也可用 --voice-id 明确指定\n",
                file=sys.stderr,
            )

    results = []
    for key in groups:
        title, probe = GROUPS[key]
        print(f"── {title} " + "─" * max(0, 56 - len(title)))
        for result in probe(api_key, voice):
            print(render(result))
            results.append(result)
        print()

    # 两套错误命名空间并存，分别汇总，不能再只看旧 detail.status。
    observed_statuses = {}
    observed_codes = {}
    for r in results:
        if r.status_string and not r.status_string.startswith("("):
            observed_statuses.setdefault(r.status_string, []).append((r.status, r.name))
        if r.code_string:
            observed_codes.setdefault(r.code_string, []).append((r.status, r.name))

    print("═" * 64)
    print("实测到的 detail.code（新格式）\n")
    if observed_codes:
        for name in sorted(observed_codes):
            first = observed_codes[name][0]
            print(f"  {name:<38} HTTP {first[0]}  ← {first[1]}")
    else:
        print("  （没有捕获到任何 detail.code）")

    print("\n实测到的 detail.status（旧格式/兼容字段）\n")
    if observed_statuses:
        for name in sorted(observed_statuses):
            first = observed_statuses[name][0]
            print(f"  {name:<38} HTTP {first[0]}  ← {first[1]}")
    else:
        print("  （没有捕获到任何 detail.status）")

    guessed = [
        "quota_exceeded", "detected_unusual_activity", "voice_not_found",
        "voice_does_not_exist", "invalid_api_key", "missing_permissions",
    ]
    print("\n插件里猜的 6 个，本次是否出现：")
    for name in guessed:
        appeared = name in observed_codes or name in observed_statuses
        print(f"  {'✓ 出现' if appeared else '· 未出现'}  {name}")
    print("\n（未出现 ≠ 不存在：本次没有触发额度用尽等场景。但出现的一定是真的。）")

    failed = [r for r in results if not r.ok]
    print(f"\n合计 {len(results)} 条探针，成功 {len(results) - len(failed)}，失败 {len(failed)}。")
    operational = [r for r in results if r.operational_failure]
    if operational:
        print(
            f"其中 {len(operational)} 条为网络/超时或伪音频响应，核验未完整完成。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
