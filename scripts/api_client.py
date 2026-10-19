"""核验工具共用的 HTTP 客户端；只有通过与插件相同逐帧校验的 MP3 才返回 detail=None。"""

import json
import socket
import urllib.error
import urllib.request

MPEG1_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
MPEG2_BITRATES = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
SAMPLE_RATES = (44100, 48000, 32000)
# ID3v2.2 / 2.3 / 2.4 未定义的标志位必须为 0。
ID3_RESERVED_FLAGS = {2: 0x3F, 3: 0x1F, 4: 0x0F}


def skip_id3(payload):
    """返回 ID3v2 标签之后的偏移；没有标签为 0，标签损坏为 -1。"""
    if not payload.startswith(b"ID3"):
        return 0
    if len(payload) < 10:
        return -1
    version, flags = payload[3], payload[5]
    reserved = ID3_RESERVED_FLAGS.get(version)
    if reserved is None or payload[4] == 0xFF or flags & reserved or any(b & 0x80 for b in payload[6:10]):
        return -1
    size = 0
    for value in payload[6:10]:
        size = (size << 7) | value
    end = 10 + size
    if end > len(payload):
        return -1
    # v2.4 的 tag size 不含 header/footer；footer 必须复制 header 的版本、标志和大小。
    if version == 4 and flags & 0x10:
        if payload[end:end + 3] != b"3DI" or payload[end + 3:end + 10] != payload[3:10]:
            return -1
        end += 10
    return end


def is_mp3(payload):
    """与 src/audio.js 的 isMP3 一致：跳过 ID3v2 后逐帧检查到末尾。

    只验首帧会放过尾部截断或垃圾数据，真实 Key 的核验就无法代表插件是否接受这段音频。
    MPEG 帧头/长度见 RFC 5219 §3 / §7。此处检查结构，不代替解码器或实际听音。
    """
    offset = skip_id3(payload)
    if offset < 0:
        return False
    frames, stream = 0, None
    while offset < len(payload):
        # ID3v1 只能出现在完整音频帧之后，恰好占据末尾 128 bytes。
        if frames and len(payload) - offset == 128 and payload[offset:offset + 3] == b"TAG":
            return True
        if len(payload) - offset < 4:
            return False
        first, second, third, fourth = payload[offset:offset + 4]
        version, layer = (second >> 3) & 3, (second >> 1) & 3
        bitrate_index, rate_index = third >> 4, (third >> 2) & 3
        if (first != 0xFF or second & 0xE0 != 0xE0 or version == 1 or layer != 1
                or bitrate_index in (0, 15) or rate_index == 3 or fourth & 3 == 2):
            return False
        rate = SAMPLE_RATES[rate_index] // (1 if version == 3 else 2 if version == 2 else 4)
        if stream is not None and stream != (version, rate):
            return False
        stream = (version, rate)
        bitrate = (MPEG1_BITRATES if version == 3 else MPEG2_BITRATES)[bitrate_index]
        length = (144 if version == 3 else 72) * bitrate * 1000 // rate + ((third >> 1) & 1)
        if length > len(payload) - offset:
            return False
        offset += length
        frames += 1
    return frames > 0


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
