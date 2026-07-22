// 本地测试：用 macOS 自带的 jsc（Bob 插件运行时用的同一个 JavaScriptCore）跑 src/main.js，
// $http / $data / $option 全部用桩替换，不会真的请求 ElevenLabs，也不消耗额度。
//
// 用法：make test
//
// jsc 路径：/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc

var failures = [];
var checks = 0;

function ok(condition, label) {
    checks += 1;
    if (!condition) {
        failures.push(label);
        print("FAIL  " + label);
    } else {
        print("ok    " + label);
    }
}

// ------------------------------------------------------------ 运行时桩

function utf8Bytes(str) {
    var bytes = [];
    for (var i = 0; i < str.length; i++) {
        var c = str.charCodeAt(i);
        if (c < 0x80) {
            bytes.push(c);
        } else if (c < 0x800) {
            bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
        } else {
            bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
        }
    }
    return bytes;
}

var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function base64(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i += 3) {
        var b0 = bytes[i];
        var b1 = bytes[i + 1];
        var b2 = bytes[i + 2];
        out += B64[b0 >> 2];
        out += B64[((b0 & 3) << 4) | ((b1 === undefined ? 0 : b1) >> 4)];
        out += b1 === undefined ? "=" : B64[((b1 & 15) << 2) | ((b2 === undefined ? 0 : b2) >> 6)];
        out += b2 === undefined ? "=" : B64[b2 & 63];
    }
    return out;
}

// 刻意不暴露 length：Bob 实际运行时 $data 就没有这个属性（文档写了但实测为
// undefined），桩要跟真实运行时一致，否则会放过依赖 length 的错误代码。
function makeData(bytes) {
    return {
        __data: true,
        toBase64: function () {
            return base64(bytes);
        },
        toUTF8: function () {
            var s = "";
            for (var i = 0; i < bytes.length; i++) {
                s += String.fromCharCode(bytes[i]);
            }
            return s;
        }
    };
}

globalThis.$data = {
    isData: function (v) {
        return !!(v && v.__data);
    },
    fromData: function (v) {
        return v;
    },
    fromUTF8: function (s) {
        return makeData(utf8Bytes(s));
    }
};

var logs = [];

globalThis.$log = {
    info: function (m) {
        logs.push(String(m));
    },
    error: function (m) {
        logs.push(String(m));
    }
};

function loggedLine(needle) {
    return logs.some(function (line) {
        return line.indexOf(needle) >= 0;
    });
}

globalThis.$option = {};

var lastRequest = null;
var nextResponse = null;

globalThis.$http = {
    request: function (req) {
        lastRequest = req;
        return Promise.resolve(nextResponse);
    }
};

function audioResponse(statusCode) {
    var data = makeData([0x49, 0x44, 0x33, 0x04, 0x00, 0x00, 0x11, 0x22]);
    return { response: { statusCode: statusCode, headers: { "content-type": "audio/mpeg" } }, data: data, rawData: data };
}

function jsonResponse(statusCode, obj) {
    return {
        response: { statusCode: statusCode, headers: { "content-type": "application/json" } },
        data: obj,
        rawData: makeData(utf8Bytes(JSON.stringify(obj)))
    };
}

// ------------------------------------------------------------ 加载插件

globalThis.exports = {};
load("src/config.js");
var configModule = globalThis.exports;

globalThis.require = function (path) {
    if (path === "./config.js" || path === "config.js") {
        return configModule;
    }
    throw new Error("未知模块: " + path);
};

globalThis.exports = {};
load("src/main.js");
var plugin = globalThis.exports;

// ------------------------------------------------------------ 用例

var BASE_OPTIONS = {
    apiKey: "sk_test",
    model: "eleven_multilingual_v2",
    voice: "hpp4J3VqNfWAUOO0d1Us",
    customVoiceId: "",
    outputFormat: "mp3_44100_128",
    stability: "",
    similarityBoost: "",
    style: "",
    speed: "",
    speakerBoost: ""
};

function withOptions(overrides) {
    var opts = {};
    Object.keys(BASE_OPTIONS).forEach(function (k) {
        opts[k] = BASE_OPTIONS[k];
    });
    Object.keys(overrides || {}).forEach(function (k) {
        opts[k] = overrides[k];
    });
    globalThis.$option = opts;
}

function speak(query) {
    return new Promise(function (resolve) {
        plugin.tts(query, resolve);
    });
}

var EN = { text: "hello world", lang: "en" };

(async function () {
    // 1. supportLanguages
    var langs = plugin.supportLanguages();
    ok(Array.isArray(langs) && langs.length > 30, "supportLanguages 返回语言数组");
    ok(langs.indexOf("zh-Hans") >= 0 && langs.indexOf("en") >= 0, "语言列表包含 zh-Hans 与 en");

    // 2. 缺 API Key
    withOptions({ apiKey: "" });
    var r = await speak(EN);
    ok(r.error && r.error.type === "secretKey", "缺 API Key 时报 secretKey");

    // 3. 选了「自定义 Voice ID」但没填
    withOptions({ voice: "__custom__" });
    r = await speak(EN);
    ok(r.error && r.error.type === "param", "自定义音色为空时报 param");

    // 4. 自定义 Voice ID 覆盖菜单选择
    withOptions({ voice: "hpp4J3VqNfWAUOO0d1Us", customVoiceId: "  myCloneVoice  " });
    nextResponse = audioResponse(200);
    logs = [];
    r = await speak(EN);
    ok(lastRequest.url.indexOf("/text-to-speech/myCloneVoice?") > 0, "自定义 Voice ID 优先且被 trim");
    ok(loggedLine("voice=myCloneVoice(custom)"), "日志记录实际使用的 voice 及其来源");
    ok(loggedLine("success status=200"), "成功时写一条 success 日志");

    // 4b. Legacy 音色提前拦截
    withOptions({ customVoiceId: "21m00Tcm4TlvDq8ikWAM" });
    r = await speak(EN);
    ok(r.error && r.error.type === "param" && r.error.message.indexOf("Legacy") > 0,
        "Legacy 音色在发请求前就被拦下");

    // 5. 超出模型字符上限
    var long = new Array(10050).join("x") + "yyyy";
    withOptions({ model: "eleven_multilingual_v2" });
    r = await speak({ text: long, lang: "en" });
    ok(r.error && r.error.type === "param" && r.error.message.indexOf("10000") > 0,
        "超过 multilingual_v2 的 10000 字上限时报 param");

    // 6. multilingual_v2 不下发 language_code
    withOptions({ model: "eleven_multilingual_v2" });
    nextResponse = audioResponse(200);
    await speak({ text: "你好", lang: "zh-Hans" });
    ok(lastRequest.body.language_code === undefined, "multilingual_v2 不带 language_code");

    // 7. flash v2.5 下发 language_code
    withOptions({ model: "eleven_flash_v2_5" });
    nextResponse = audioResponse(200);
    await speak({ text: "你好", lang: "zh-Hans" });
    ok(lastRequest.body.language_code === "zh", "flash v2.5 带上 language_code=zh");

    // 8. 正常返回音频
    withOptions({});
    nextResponse = audioResponse(200);
    r = await speak(EN);
    ok(r.result && r.result.type === "base64" && r.result.value.length > 0, "成功时返回 base64 音频");
    ok(lastRequest.url.indexOf("output_format=mp3_44100_128") > 0, "URL 带上 output_format");
    ok(lastRequest.header["xi-api-key"] === "sk_test", "带上 xi-api-key 请求头");

    // 9. 默认不覆盖音色自带设置
    ok(lastRequest.body.voice_settings === undefined, "未设置时不下发 voice_settings");

    // 10. 只下发被覆盖的项
    withOptions({ stability: "0.5", speed: "1.1", speakerBoost: "false" });
    nextResponse = audioResponse(200);
    await speak(EN);
    var vs = lastRequest.body.voice_settings;
    ok(vs && vs.stability === 0.5 && vs.speed === 1.1 && vs.use_speaker_boost === false,
        "voice_settings 按需下发并转成数字/布尔");
    ok(vs && vs.similarity_boost === undefined && vs.style === undefined,
        "未覆盖的 voice_settings 字段不下发");

    // 11. 401 invalid_api_key
    withOptions({});
    nextResponse = jsonResponse(401, { detail: { status: "invalid_api_key", message: "bad key" } });
    r = await speak(EN);
    ok(r.error && r.error.type === "secretKey", "401 invalid_api_key 映射为 secretKey");

    // 12. 额度用完
    nextResponse = jsonResponse(401, { detail: { status: "quota_exceeded", message: "out of credits" } });
    r = await speak(EN);
    ok(r.error && r.error.type === "api" && r.error.message.indexOf("额度") >= 0,
        "quota_exceeded 提示额度用完而不是密钥错误");

    // 13. 422 参数校验
    nextResponse = jsonResponse(422, { detail: [{ loc: ["body", "text"], msg: "field required" }] });
    r = await speak(EN);
    ok(r.error && r.error.type === "param" && r.error.message.indexOf("field required") > 0,
        "422 校验错误带出具体字段信息");

    // 14. 音色不存在
    nextResponse = jsonResponse(404, { detail: { status: "voice_not_found", message: "no such voice" } });
    r = await speak(EN);
    ok(r.error && r.error.type === "notFound", "voice_not_found 映射为 notFound");

    // 15. 402 免费订阅用不了音色库音色（Bob 日志里实际遇到的）
    nextResponse = jsonResponse(402, {
        detail: {
            status: "voice_not_allowed_for_free_users",
            message: "Free users cannot use library voices via the API."
        }
    });
    r = await speak(EN);
    ok(r.error && r.error.type === "api" && r.error.message.indexOf("音色库音色") > 0,
        "402 说清限制是音色库来源，而非「不在你账号里」");
    ok(r.error.message.indexOf("hpp4J3VqNfWAUOO0d1Us") > 0 && r.error.message.indexOf("音色菜单") > 0,
        "402 报错里带上实际发出的 Voice ID 及其来源");

    // 15b. 400 + max_character_limit_exceeded 不能说成订阅问题（曾经的 regression）
    nextResponse = jsonResponse(400, { detail: { status: "max_character_limit_exceeded", message: "too long" } });
    r = await speak(EN);
    ok(r.error && r.error.type === "param" && r.error.message.indexOf("字符上限") > 0,
        "400 超长文本报 param 而非订阅限制");

    // 15c. 新错误格式用 detail.code 判别（detail.status 已是 legacy）
    nextResponse = jsonResponse(403, { detail: { code: "insufficient_permissions", type: "authorization_error", message: "no tts scope" } });
    r = await speak(EN);
    ok(r.error && r.error.type === "secretKey" && r.error.message.indexOf("缺少权限") > 0,
        "新格式 detail.code 能被识别，且区分于「Key 无效」");

    // 16. 2xx 但返回 JSON —— 不查状态码就会把它当音频播放
    nextResponse = jsonResponse(200, { detail: { status: "something_odd", message: "not audio" } });
    r = await speak(EN);
    ok(r.error && !r.result, "2xx 但响应不是音频时报错而不是播放乱码");

    // 16. 网络错误
    nextResponse = { error: { message: "timed out" }, response: null };
    r = await speak(EN);
    ok(r.error && r.error.type === "network", "网络失败映射为 network");

    // 17. 空音频（$data 没有 length，只能靠 base64 是否为空判断）
    nextResponse = { response: { statusCode: 200 }, data: makeData([]), rawData: makeData([]) };
    r = await speak(EN);
    ok(r.error && r.error.type === "api", "空音频报 api 错误");

    // 18. 英语专用模型配上非英语
    withOptions({ model: "eleven_flash_v2" });
    nextResponse = audioResponse(200);
    logs = [];
    await speak({ text: "你好", lang: "zh-Hans" });
    ok(loggedLine("warn eleven_flash_v2 仅支持英语"), "英语专用模型遇到中文时写警告日志");

    // 18. pluginValidate
    nextResponse = jsonResponse(200, { models: [] });
    var v = await new Promise(function (resolve) {
        plugin.pluginValidate(resolve);
    });
    ok(v.result === true, "pluginValidate 在 200 时通过");

    nextResponse = jsonResponse(401, { detail: { status: "invalid_api_key", message: "bad" } });
    v = await new Promise(function (resolve) {
        plugin.pluginValidate(resolve);
    });
    ok(v.result === false && v.error.type === "secretKey", "pluginValidate 在 401 时报 secretKey");

    print("");
    if (failures.length === 0) {
        print("ALL PASS (" + checks + " checks)");
    } else {
        print("FAILED " + failures.length + "/" + checks);
    }
})();
