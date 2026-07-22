var config = require("./config.js");

var LOG_TAG = "[11labs-tts]";

// 写进 Bob 日志，方便出问题时确认实际发出去的是什么
function logInfo(message) {
    if (typeof $log !== "undefined" && $log && typeof $log.info === "function") {
        $log.info(LOG_TAG + " " + message);
    }
}

// ---------------------------------------------------------------- 工具函数

function trimmed(value) {
    return typeof value === "string" ? value.trim() : "";
}

function isBinary(value) {
    return typeof $data.isData === "function" && $data.isData(value);
}

// 把响应体尽量变成可读文本。Bob 会把 JSON 自动解析成对象，
// 解析不了的（比如音频）留在 $data 里。
function bodyToText(body) {
    if (body === undefined || body === null) {
        return "";
    }
    if (typeof body === "string") {
        return body;
    }
    if (isBinary(body)) {
        return body.toUTF8() || "";
    }
    try {
        return JSON.stringify(body);
    } catch (err) {
        return String(body);
    }
}

function bodyToObject(body) {
    if (body && typeof body === "object" && !isBinary(body)) {
        return body;
    }
    var text = bodyToText(body);
    if (!text) {
        return null;
    }
    try {
        return JSON.parse(text);
    } catch (err) {
        return null;
    }
}

// ElevenLabs 的错误格式有两种：
//   { "detail": { "status": "quota_exceeded", "message": "..." } }
//   { "detail": [ { "loc": [...], "msg": "..." } ] }      // 422 参数校验
function parseApiError(body) {
    var obj = bodyToObject(body);
    var result = { status: "", message: "" };
    if (!obj) {
        result.message = bodyToText(body).slice(0, 500);
        return result;
    }

    var detail = obj.detail;
    if (typeof detail === "string") {
        result.message = detail;
    } else if (Array.isArray(detail)) {
        result.message = detail
            .map(function (item) {
                return item && (item.msg || item.message);
            })
            .filter(Boolean)
            .join("；");
    } else if (detail && typeof detail === "object") {
        result.status = detail.status || "";
        result.message = detail.message || "";
    }

    if (!result.message) {
        result.message = obj.message || JSON.stringify(obj).slice(0, 500);
    }
    return result;
}

// 把 HTTP 状态码 + ElevenLabs 的 status 字段翻译成 Bob 的 service error
function toServiceError(statusCode, body) {
    var parsed = parseApiError(body);
    var detail = parsed.message ? "：" + parsed.message : "";

    switch (parsed.status) {
        case "quota_exceeded":
            return {
                type: "api",
                message: "ElevenLabs 字符额度已用完" + detail,
                troubleshootingLink: "https://elevenlabs.io/app/subscription"
            };
        case "detected_unusual_activity":
            return {
                type: "api",
                message: "账号被判定为异常活动，免费额度已被暂停" + detail
            };
        case "voice_not_found":
        case "voice_does_not_exist":
            return {
                type: "notFound",
                message: "音色不存在，请检查 Voice ID" + detail
            };
        case "invalid_api_key":
        case "missing_permissions":
            return {
                type: "secretKey",
                message: "API Key 无效或缺少权限" + detail,
                troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
            };
        default:
            break;
    }

    if (statusCode === 401 || statusCode === 403) {
        return {
            type: "secretKey",
            message: "API Key 无效或缺少权限" + detail,
            troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
        };
    }
    if (statusCode === 402) {
        // 免费账号调 API 时用不了音色库音色；ElevenLabs 的默认音色（Aria/Roger/Sarah 等）
        // 也属于音色库，且官方已宣布 2026-12-31 全部停用。
        return {
            type: "api",
            message:
                "当前订阅无法使用该音色" + detail +
                "。请在「自定义 Voice ID」里换成你账号内的音色（Voice Design 生成的即可），或升级订阅",
            troubleshootingLink: "https://elevenlabs.io/app/voice-lab"
        };
    }
    if (statusCode === 404) {
        return { type: "notFound", message: "接口或音色不存在" + detail };
    }
    if (statusCode === 422) {
        return { type: "param", message: "请求参数被拒绝" + detail };
    }
    if (statusCode === 429) {
        return { type: "api", message: "请求过于频繁，请稍后再试" + detail };
    }
    return {
        type: "api",
        message: "ElevenLabs 返回错误（HTTP " + statusCode + "）" + detail
    };
}

function modelInfo(modelId) {
    return config.MODELS[modelId] || config.FALLBACK_MODEL;
}

// 菜单里代表「用下面填的 Voice ID」的哨兵值
var CUSTOM_VOICE = "__custom__";

// 选中的音色：自定义 Voice ID 优先，方便用克隆音色 / 菜单里还没有的新音色
function resolveVoice() {
    var custom = trimmed($option.customVoiceId);
    if (custom) {
        return { id: custom, source: "custom" };
    }
    var selected = trimmed($option.voice);
    return {
        id: selected === CUSTOM_VOICE ? "" : selected,
        source: "menu"
    };
}

// 只把用户显式覆盖过的项发给 API，其余留空则沿用音色在 ElevenLabs 上保存的设置
function buildVoiceSettings() {
    var settings = {};

    var numeric = [
        ["stability", "stability"],
        ["similarityBoost", "similarity_boost"],
        ["style", "style"],
        ["speed", "speed"]
    ];
    numeric.forEach(function (pair) {
        var raw = trimmed($option[pair[0]]);
        if (raw === "") {
            return;
        }
        var value = Number(raw);
        if (!isNaN(value)) {
            settings[pair[1]] = value;
        }
    });

    var boost = trimmed($option.speakerBoost);
    if (boost === "true" || boost === "false") {
        settings.use_speaker_boost = boost === "true";
    }

    return Object.keys(settings).length > 0 ? settings : null;
}

// ---------------------------------------------------------------- 插件接口

function supportLanguages() {
    // 返回并集：某个模型不原生支持时最多是口音不准，不该直接判定为「不支持」。
    return config.LANGUAGES.map(function (item) {
        return item[0];
    });
}

function pluginTimeoutInterval() {
    // v3 合成较慢，给足时间
    return 60;
}

function pluginValidate(completion) {
    (async () => {
        var apiKey = trimmed($option.apiKey);
        if (!apiKey) {
            completion({
                result: false,
                error: {
                    type: "secretKey",
                    message: "请先填写 ElevenLabs API Key",
                    troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
                }
            });
            return;
        }

        try {
            var resp = await $http.request({
                method: "GET",
                url: config.API_BASE + "/models",
                header: { "xi-api-key": apiKey },
                timeout: 15
            });

            if (resp.error) {
                completion({
                    result: false,
                    error: {
                        type: "network",
                        message: "无法连接 ElevenLabs：" + (resp.error.message || "未知网络错误"),
                        addition: resp.error
                    }
                });
                return;
            }

            var statusCode = resp.response ? resp.response.statusCode : 0;
            if (statusCode === 200) {
                completion({ result: true });
                return;
            }

            completion({ result: false, error: toServiceError(statusCode, resp.data) });
        } catch (err) {
            completion({
                result: false,
                error: {
                    type: "network",
                    message: "验证失败：" + (err.message || "未知错误"),
                    addition: String(err)
                }
            });
        }
    })();
}

function tts(query, completion) {
    (async () => {
        try {
            var apiKey = trimmed($option.apiKey);
            if (!apiKey) {
                throw {
                    type: "secretKey",
                    message: "请先在插件设置里填写 ElevenLabs API Key",
                    troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
                };
            }

            var voice = resolveVoice();
            var voiceId = voice.id;
            if (!voiceId) {
                throw {
                    type: "param",
                    message: "请在插件设置里选择音色，或填写自定义 Voice ID"
                };
            }

            var text = typeof query.text === "string" ? query.text : "";
            if (!trimmed(text)) {
                throw { type: "param", message: "没有可合成的文本" };
            }

            var modelId = trimmed($option.model) || "eleven_multilingual_v2";
            var info = modelInfo(modelId);
            if (text.length > info.charLimit) {
                throw {
                    type: "param",
                    message:
                        "文本长度 " + text.length + " 超过 " + modelId +
                        " 的单次上限 " + info.charLimit + " 字符，请分段朗读"
                };
            }

            var body = { text: text, model_id: modelId };

            // multilingual_v2 明确不支持 language_code，其余模型不支持时会被忽略
            var languageCode = config.langMap.get(query.lang);
            if (info.languageCode && languageCode) {
                body.language_code = languageCode;
            }

            var voiceSettings = buildVoiceSettings();
            if (voiceSettings) {
                body.voice_settings = voiceSettings;
            }

            var outputFormat = trimmed($option.outputFormat) || "mp3_44100_128";
            var url = config.API_BASE + "/text-to-speech/" +
                encodeURIComponent(voiceId) + "?output_format=" + outputFormat;

            logInfo(
                "start chars=" + text.length +
                " lang=" + query.lang +
                " model=" + modelId +
                " voice=" + voiceId + "(" + voice.source + ")" +
                " format=" + outputFormat +
                " language_code=" + (body.language_code || "-") +
                " voice_settings=" + (voiceSettings ? JSON.stringify(voiceSettings) : "-")
            );
            var startedAt = Date.now();

            var resp = await $http.request({
                method: "POST",
                url: url,
                header: {
                    "xi-api-key": apiKey,
                    "Content-Type": "application/json"
                },
                body: body,
                timeout: 60
            });

            if (resp.error) {
                throw {
                    type: "network",
                    message: "请求 ElevenLabs 失败：" + (resp.error.message || "未知网络错误"),
                    addition: resp.error
                };
            }

            // 上游插件漏了这一步：非 2xx 时响应体是 JSON 错误，直接 base64 会当成音频播放，
            // 表现为「点了没声音也没报错」。
            var statusCode = resp.response ? resp.response.statusCode : 0;
            if (statusCode < 200 || statusCode >= 300) {
                var failure = toServiceError(statusCode, resp.data);
                if (statusCode === 402 || statusCode === 404) {
                    // Bob 会保留已保存的选项值，菜单里删掉的旧值依然会被发出去，
                    // 界面上却显示成菜单第一项。把真实 ID 带进报错，避免被界面误导。
                    failure.message += "（实际发出的 Voice ID：" + voiceId +
                        "，来源：" + (voice.source === "custom" ? "自定义输入框" : "音色菜单") + "）";
                }
                logInfo(
                    "failed status=" + statusCode + " voice=" + voiceId +
                    " model=" + modelId + " type=" + failure.type
                );
                throw failure;
            }

            // 2xx 但返回的是 JSON（Bob 能解析成对象）说明不是音频
            if (resp.data && typeof resp.data === "object" && !isBinary(resp.data)) {
                logInfo("failed status=" + statusCode + " 响应不是音频");
                throw toServiceError(statusCode, resp.data);
            }

            var raw = resp.rawData || resp.data;
            var audio = isBinary(raw) ? raw : $data.fromData(raw);
            if (!audio || audio.length === 0) {
                throw { type: "api", message: "ElevenLabs 返回了空音频" };
            }

            logInfo(
                "success status=" + statusCode + " bytes=" + audio.length +
                " ms=" + (Date.now() - startedAt)
            );

            completion({
                result: {
                    type: "base64",
                    value: audio.toBase64(),
                    raw: {
                        model_id: modelId,
                        voice_id: voiceId,
                        output_format: outputFormat,
                        bytes: audio.length
                    }
                }
            });
        } catch (err) {
            logInfo("error type=" + (err.type || "unknown") + " message=" + (err.message || ""));
            completion({
                error: {
                    type: err.type || "unknown",
                    message: err.message || "语音合成失败",
                    troubleshootingLink: err.troubleshootingLink,
                    addition: err.addition
                }
            });
        }
    })();
}

exports.supportLanguages = supportLanguages;
exports.pluginTimeoutInterval = pluginTimeoutInterval;
exports.pluginValidate = pluginValidate;
exports.tts = tts;
