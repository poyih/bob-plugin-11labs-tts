// MP3 结构校验，只依赖 Bob 已实测可用的 data.toBase64() 返回值。
// 按需读取 base64 中的字节，不生成整份解码数组；逐帧检查到末尾，避免只验首帧
// 而放过最后一帧截断。这不是音频解码器，不校验压缩数据、CRC 或实际听感。
// MPEG 帧头/长度：RFC 5219 §3、§7；ID3v2 结构：https://id3.org/Developer%20Information

var MPEG1_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320];
var MPEG2_BITRATES = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
var SAMPLE_RATES = [44100, 48000, 32000];

function sextet(text, index) {
    var code = text.charCodeAt(index);
    if (code >= 65 && code <= 90) { return code - 65; }
    if (code >= 97 && code <= 122) { return code - 71; }
    if (code >= 48 && code <= 57) { return code + 4; }
    return code === 43 ? 62 : code === 47 ? 63 : 0;
}

function base64Reader(text) {
    if (typeof text !== "string" || !text.length || text.length % 4 !== 0 ||
        /[^A-Za-z0-9+/=]/.test(text)) {
        return null;
    }
    var padding = text.charAt(text.length - 1) === "="
        ? (text.charAt(text.length - 2) === "=" ? 2 : 1) : 0;
    var firstPadding = text.indexOf("=");
    if (firstPadding !== -1 && firstPadding !== text.length - padding) {
        return null;
    }
    // 最后一组没有承载数据的低位必须为 0；拒绝非规范编码和多余 padding。
    if (padding === 2 && (sextet(text, text.length - 3) & 15) !== 0 ||
        padding === 1 && (sextet(text, text.length - 2) & 3) !== 0) {
        return null;
    }
    return {
        length: text.length / 4 * 3 - padding,
        byte: function (index) {
            var group = Math.floor(index / 3) * 4;
            var part = index % 3;
            if (part === 0) {
                return (sextet(text, group) << 2) | (sextet(text, group + 1) >> 4);
            }
            if (part === 1) {
                return ((sextet(text, group + 1) & 15) << 4) | (sextet(text, group + 2) >> 2);
            }
            return ((sextet(text, group + 2) & 3) << 6) | sextet(text, group + 3);
        }
    };
}

function matches(reader, offset, text) {
    if (offset + text.length > reader.length) { return false; }
    for (var i = 0; i < text.length; i++) {
        if (reader.byte(offset + i) !== text.charCodeAt(i)) { return false; }
    }
    return true;
}

function skipID3(reader) {
    if (!matches(reader, 0, "ID3")) { return 0; }
    if (reader.length < 10) { return -1; }
    var version = reader.byte(3);
    var flags = reader.byte(5);
    var reservedMask = version === 2 ? 63 : version === 3 ? 31 : 15;
    if ((version !== 2 && version !== 3 && version !== 4) ||
        reader.byte(4) === 255 || (flags & reservedMask) !== 0) {
        return -1;
    }
    var size = 0;
    for (var i = 6; i < 10; i++) {
        var value = reader.byte(i);
        if (value & 128) { return -1; }
        size = size * 128 + value;
    }
    var end = 10 + size;
    if (end > reader.length) { return -1; }
    // v2.4 的 tag size 不含 header/footer；footer 必须复制 header 的版本、标志和大小。
    if (version === 4 && (flags & 16) !== 0) {
        if (end + 10 > reader.length || !matches(reader, end, "3DI")) { return -1; }
        for (var field = 3; field < 10; field++) {
            if (reader.byte(end + field) !== reader.byte(field)) { return -1; }
        }
        end += 10;
    }
    return end;
}

function isMP3(encoded) {
    var reader = base64Reader(encoded);
    if (!reader) { return false; }
    var offset = skipID3(reader);
    if (offset < 0) { return false; }
    var frameCount = 0;
    var streamVersion = null;
    var streamRate = null;
    while (offset < reader.length) {
        // ID3v1 只能出现在完整音频帧之后，恰好占据末尾 128 bytes。
        // 不提前裁掉尾部，避免把音频帧内部碰巧出现的 TAG 当成标签。
        if (frameCount > 0 && reader.length - offset === 128 && matches(reader, offset, "TAG")) {
            return true;
        }
        if (reader.length - offset < 4) { return false; }
        var first = reader.byte(offset);
        var second = reader.byte(offset + 1);
        var third = reader.byte(offset + 2);
        var fourth = reader.byte(offset + 3);
        var version = (second >> 3) & 3;
        var layer = (second >> 1) & 3;
        var bitrateIndex = third >> 4;
        var rateIndex = (third >> 2) & 3;
        if (first !== 255 || (second & 224) !== 224 || version === 1 || layer !== 1 ||
            bitrateIndex === 0 || bitrateIndex === 15 || rateIndex === 3 || (fourth & 3) === 2) {
            // 菜单的固定码率格式无需支持无法直接求帧长的 free-format（bitrate index 0）。
            return false;
        }
        var rate = SAMPLE_RATES[rateIndex] / (version === 3 ? 1 : version === 2 ? 2 : 4);
        if (streamVersion !== null && (version !== streamVersion || rate !== streamRate)) {
            return false;
        }
        streamVersion = version;
        streamRate = rate;
        var bitrate = (version === 3 ? MPEG1_BITRATES : MPEG2_BITRATES)[bitrateIndex];
        var length = Math.floor((version === 3 ? 144 : 72) * bitrate * 1000 / rate) + ((third >> 1) & 1);
        if (length > reader.length - offset) { return false; }
        // 每轮至少前进一个完整帧；不扫描 payload 查找下一处同步字，垃圾无法被跳过。
        offset += length;
        frameCount += 1;
    }
    return frameCount > 0;
}

exports.isMP3 = isMP3;
