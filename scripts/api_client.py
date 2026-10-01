"""核验工具共用的 HTTP 客户端；只有非空、完整首帧的 MP3 才返回 detail=None。"""

import json
import socket
import urllib.error
import urllib.request


def is_mp3(payload):
    """跳过 ID3v2 标签后检查 MPEG Layer III 首帧，不以 JSON 解析失败判音频。

    MPEG 的四字节头和 11-bit syncword 见 RFC 5219 §3 / §7。
    此处检查结构，不代替解码器或实际听音。
    """
    offset = 0
    if payload.startswith(b"ID3"):
        if len(payload) < 10 or payload[3] not in (2, 3, 4) or any(b & 0x80 for b in payload[6:10]):
            return False
        size = 0
        for value in payload[6:10]:
            size = (size << 7) | value
        offset = 10 + size
        if payload[3] == 4 and payload[5] & 0x10:
            offset += 10  # ID3v2.4 footer
    if len(payload) < offset + 4:
        return False
    first, second, third, _ = payload[offset:offset + 4]
    version, layer = (second >> 3) & 3, (second >> 1) & 3
    rate_index, bitrate_index = (third >> 2) & 3, third >> 4
    if first != 0xFF or second & 0xE0 != 0xE0 or version == 1 or layer != 1:
        return False
    if rate_index == 3 or bitrate_index in (0, 15):
        return False
    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    if version != 3:
        bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    rate = (44100, 48000, 32000)[rate_index] // (1 if version == 3 else 2 if version == 2 else 4)
    frame_size = (144 if version == 3 else 72) * bitrates[bitrate_index] * 1000 // rate + ((third >> 1) & 1)
    return len(payload) >= offset + frame_size


def operational_failure(status, detail):
    return status == 0 or status >= 500 or (
        isinstance(detail, dict) and any(key in detail for key in ("_non_audio", "_invalid_json"))
    )


def request(api_base, method, path, api_key, body=None, timeout=60, expect_audio=False):
    """返回 (status, JSON/error marker/None, size)，不打印或保存 Key。"""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"xi-api-key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api_base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            mime = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if expect_audio:
                allowed = not mime or mime.startswith("audio/") or mime == "application/octet-stream"
                if allowed and is_mp3(payload):
                    return response.status, None, len(payload)
                detail = {"detail": {"code": "invalid_audio_response", "message":
                    f"响应不是完整 MP3（Content-Type: {mime or '缺失'}，{len(payload)} bytes）"}}
                return response.status, {"_non_audio": detail}, len(payload)
            try:
                return response.status, json.loads(payload), len(payload)
            except (ValueError, UnicodeDecodeError):
                return response.status, {"_invalid_json": {"detail":
                    "响应不是 JSON（Content-Type: " + (mime or "缺失") + "）"}}, len(payload)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            detail = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            detail = {"_raw": payload[:300].decode("utf-8", "replace")}
        return error.code, detail, len(payload)
    except urllib.error.URLError as error:
        return 0, {"_network": str(error.reason)}, 0
    except (socket.timeout, TimeoutError, OSError) as error:
        return 0, {"_timeout": str(error)}, 0
