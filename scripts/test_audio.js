// 独立离线 MP3 结构测试：在仓库根目录用 macOS JavaScriptCore 执行。
// /System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc scripts/test_audio.js
// 音频帧使用固定的标准头/帧长夹具，不联网、不依赖 Bob、不调用合成 API。
globalThis.exports = {};
load("src/audio.js");
var audio = globalThis.exports;
var failures = [];
var checks = 0;
var alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function encode(bytes) {
    var result = "";
    for (var i = 0; i < bytes.length; i += 3) {
        var first = bytes[i];
        var second = bytes[i + 1];
        var third = bytes[i + 2];
        result += alphabet[first >> 2];
        result += alphabet[((first & 3) << 4) | ((second || 0) >> 4)];
        result += second === undefined ? "=" : alphabet[((second & 15) << 2) | ((third || 0) >> 6)];
        result += third === undefined ? "=" : alphabet[third & 63];
    }
    return result;
}

function zeros(length) {
    var bytes = [];
    for (var i = 0; i < length; i++) { bytes.push(0); }
    return bytes;
}

function ascii(text) {
    var bytes = [];
    for (var i = 0; i < text.length; i++) { bytes.push(text.charCodeAt(i)); }
    return bytes;
}

function frame(header, length) {
    return header.concat(zeros(length - header.length));
}

function check(condition, label) {
    checks += 1;
    print((condition ? "ok    " : "FAIL  ") + label);
    if (!condition) { failures.push(label); }
}

function accepts(bytes, label) { check(audio.isMP3(encode(bytes)), label); }
function rejects(bytes, label) { check(!audio.isMP3(encode(bytes)), label); }

// 固定帧长来自 MPEG Layer III 帧长表；VBR 只改变 bitrate/padding，不改变版本或采样率。
var mp3_128 = frame([255, 251, 144, 0], 417);
var mp3_128_padded = frame([255, 251, 146, 0], 418);
var mp3_64 = frame([255, 251, 80, 0], 208);
var mp3_192 = frame([255, 251, 176, 0], 626);
var mp3_22050_32 = frame([255, 243, 64, 0], 104);
var mp3_11025_32 = frame([255, 227, 64, 0], 208);

accepts(mp3_128, "MP3 44100/128 单个完整帧，无 ID3");
accepts(mp3_128.concat(mp3_128_padded, mp3_128), "MP3 44100/128 多帧，允许 padding 变化");
accepts(mp3_64.concat(mp3_64), "MP3 44100/64 菜单格式");
accepts(mp3_192.concat(mp3_192), "MP3 44100/192 菜单格式");
accepts(mp3_22050_32.concat(mp3_22050_32), "MPEG2 MP3 22050/32 菜单格式");
accepts(mp3_11025_32.concat(mp3_11025_32), "MPEG2.5 Layer III");
accepts(mp3_128.concat(mp3_64, mp3_192, mp3_128_padded), "VBR 多帧，码率和 padding 可变");
accepts(frame([255, 250, 144, 192], 417), "允许 CRC 和单声道");
accepts(frame([255, 251, 148, 0], 384), "MPEG1 48000Hz");
accepts(frame([255, 251, 152, 0], 576), "MPEG1 32000Hz");
accepts(frame([255, 243, 68, 0], 96), "MPEG2 24000Hz");
accepts(frame([255, 243, 72, 0], 144), "MPEG2 16000Hz");
accepts(frame([255, 227, 68, 0], 192), "MPEG2.5 12000Hz");
accepts(frame([255, 227, 72, 0], 288), "MPEG2.5 8000Hz");
var xing = mp3_128.slice();
ascii("Xing").forEach(function (value, index) { xing[36 + index] = value; });
accepts(xing.concat(mp3_64), "Xing/VBR 元数据在音频帧内可保留");

function synchsafe(size) {
    return [(size >> 21) & 127, (size >> 14) & 127, (size >> 7) & 127, size & 127];
}

function tag(version, payload, flags) {
    var header = ascii("ID3").concat([version, 0, flags || 0], synchsafe(payload.length));
    var result = header.concat(payload);
    if (version === 4 && (flags & 16)) {
        result = result.concat(ascii("3DI"), header.slice(3));
    }
    return result;
}

var v22 = tag(2, ascii("TT2").concat([0, 0, 2, 0, 65]));
var title = ascii("TIT2").concat([0, 0, 0, 2, 0, 0, 0, 65]);
var v23 = tag(3, title);
var v24 = tag(4, title);
var v24Footer = tag(4, title, 16);
accepts(v22.concat(mp3_128), "ID3v2.2 标签");
accepts(v23.concat(mp3_128), "ID3v2.3 标签");
accepts(v24.concat(mp3_128), "ID3v2.4 标签");
accepts(v24Footer.concat(mp3_128), "ID3v2.4 footer 不计入 tag size");
accepts(tag(3, title.concat(zeros(128)), 128).concat(mp3_128), "synchsafe 大小跨越 127，允许 unsynchronisation 标志");
var id3v1 = ascii("TAG").concat(zeros(125));
accepts(mp3_128.concat(id3v1), "末尾完整 ID3v1");
accepts(v23.concat(mp3_128, mp3_64, id3v1), "ID3v2 + VBR 音频 + ID3v1");
var payloadTAG = mp3_128.slice();
payloadTAG[289] = 84;
payloadTAG[290] = 65;
payloadTAG[291] = 71;
accepts(payloadTAG, "帧内末尾 128 bytes 的 TAG 不被误裁剪");

rejects([], "空数据");
rejects([255, 255, 255], "原问题：三个 FF bytes 不能当 MP3");
rejects(ascii('{"detail":{"message":"gateway"}}'), "JSON 不是音频");
rejects(ascii("<html>gateway</html>"), "HTML 不是音频");
rejects([255, 216, 255, 224].concat(zeros(1000)), "其他二进制格式不是 MP3");
rejects(v22, "ID3v2.2-only");
rejects(v23, "ID3v2.3-only");
rejects(v24Footer, "ID3v2.4 + footer-only");
rejects(id3v1, "ID3v1-only");
rejects(ascii("ID3\u0004\u0000"), "ID3 头截断");
var invalidTag = v23.slice();
invalidTag[3] = 5;
rejects(invalidTag.concat(mp3_128), "未知 ID3 主版本");
invalidTag = v23.slice();
invalidTag[4] = 255;
rejects(invalidTag.concat(mp3_128), "非法 ID3 revision");
invalidTag = v23.slice();
invalidTag[5] = 1;
rejects(invalidTag.concat(mp3_128), "ID3 保留标志位");
invalidTag = v23.slice();
invalidTag[6] = 128;
rejects(invalidTag.concat(mp3_128), "ID3 size 不是 synchsafe");
invalidTag = v23.slice();
invalidTag[9] = 127;
rejects(invalidTag.concat(mp3_128), "ID3 大小不能吞掉部分音频后继续同步");
rejects(ascii("ID3").concat([4, 0, 0, 127, 127, 127, 127], mp3_128), "ID3 声明大小超过文件长度");
rejects(v24Footer.slice(0, -10).concat(mp3_128), "声明的 ID3v2.4 footer 缺失");
invalidTag = v24Footer.slice();
invalidTag[invalidTag.length - 10] = 0;
rejects(invalidTag.concat(mp3_128), "ID3 footer 标记损坏");
invalidTag = v24Footer.slice();
invalidTag[invalidTag.length - 1] ^= 1;
rejects(invalidTag.concat(mp3_128), "ID3 footer 大小必须与 header 一致");

rejects(mp3_128.slice(0, 3), "首帧头截断");
rejects(mp3_128.slice(0, -1), "首帧 payload 截断");
rejects(v23.concat(mp3_128.slice(0, -1)), "标签后的首帧截断");
rejects(mp3_128.concat(mp3_64.slice(0, 3)), "尾帧头截断");
rejects(mp3_128.concat(mp3_64.slice(0, -1)), "完整首帧不能掩盖尾帧截断");
rejects(mp3_128.concat(mp3_64.slice(0, -1), id3v1), "ID3v1 不能掩盖尾帧截断");
rejects(mp3_128.concat(id3v1.slice(0, -1)), "ID3v1 尾部截断");
rejects(mp3_128.concat(id3v1, [0]), "ID3v1 后不允许多余垃圾");
rejects(mp3_128.concat([0], mp3_128), "帧之间插入垃圾不能重新同步跳过");
rejects(mp3_128.concat([0]), "音频尾部垃圾");
rejects([0].concat(mp3_128), "音频前部垃圾");
var badHeaderCases = [
    [255, 235, 144, 0], // MPEG version reserved
    [255, 253, 144, 0], // Layer II
    [255, 249, 144, 0], // layer reserved
    [255, 251, 0, 0],   // free-format
    [255, 251, 240, 0], // bitrate reserved
    [255, 251, 156, 0], // sample rate reserved
    [255, 251, 144, 2]  // emphasis reserved
];
badHeaderCases.forEach(function (header, index) {
    rejects(frame(header, 417), "非法 MPEG 格式头 " + (index + 1));
});
var brokenSecond = mp3_128.concat(mp3_128);
brokenSecond[417] = 0;
rejects(brokenSecond, "后续帧同步字损坏");
rejects(mp3_128.concat(mp3_22050_32), "同一流内 MPEG 版本/采样率变化");
rejects(mp3_128.concat(frame([255, 251, 148, 0], 384)), "同一流内采样率变化");

[
    null, undefined, 123, {}, "", " ", "////\n", "AA=", "AAAA=", "A===", "=AAA", "AA=A", "AA**",
    "AB==", "AAB="
].forEach(function (value, index) {
    check(!audio.isMP3(value), "无效或非规范 base64 " + (index + 1));
});
var encoded64 = encode(mp3_64);
check(!audio.isMP3(encoded64.slice(0, -3) + "B=="), "音频 base64 padding 的闲置位必须为 0");
check(!audio.isMP3(encoded64.slice(0, 100) + "*" + encoded64.slice(101)), "未读取的 payload 也检查 base64 字符");

// 长数据不使用带递归分组的整串正则，也不复制解码数组；覆盖数 MB 的典型长朗读响应。
var longAudio = encode(mp3_128);
for (var doubling = 0; doubling < 13; doubling++) { longAudio += longAudio; }
check(audio.isMP3(longAudio), "8192 完整帧的大响应");
check(!audio.isMP3(longAudio.slice(0, -4)), "大响应尾帧截断");

print(failures.length ? "FAILED " + failures.length + "/" + checks : "ALL PASS (" + checks + " checks)");
if (failures.length) { quit(1); }
