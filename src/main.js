var config = require("./config.js");

var LOG_TAG = "[11labs-tts]";

// 写进 Bob 日志，方便出问题时确认实际发出去的是什么
function logInfo(message) {
    if (typeof $log !== "undefined" && $log && typeof $log.info === "function") {
        $log.info(LOG_TAG + " " + message);
    }
}

// 插件加载时探一次运行时能力。Bob 文档写了 $data 有 length 属性，实际是
// undefined —— 曾据此写的空音频防护形同虚设。文档不可全信，实测留档。
(function probeRuntime() {
    try {
        var sample = $data.fromUTF8("ab");
        logInfo(
            "runtime" +
            " $data.isData=" + typeof ($data && $data.isData) +
            " sample.length=" + typeof sample.length +
            " sample.toBase64=" + typeof sample.toBase64 +
            " sample.toUTF8=" + typeof sample.toUTF8 +
            " base64(ab)=" + sample.toBase64() +
            " Date.now=" + typeof Date.now
        );
    } catch (err) {
        logInfo("runtime 探测失败: " + (err && err.message));
    }
})();

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
    var result = { code: "", status: "", kind: "", requestId: "", message: "" };
    if (!obj) {
        result.message = bodyToText(body).slice(0, 500);
        return result;
    }

    var detail = obj.detail;
    if (typeof detail === "string") {
        result.message = detail;
    } else if (Array.isArray(detail)) {
        result.kind = "validation_error";
        result.message = detail
            .map(function (item) {
                return item && (item.msg || item.message);
            })
            .filter(Boolean)
            .join("；");
    } else if (detail && typeof detail === "object") {
        // ElevenLabs 新格式用 detail.code + detail.type，旧格式用 detail.status，两套并存且不互通。
        // 关键：同一个错误的 code 与 status 可能不同且各自都不可替代——
        //   192kbps 越档：code=subscription_required、status=output_format_not_allowed
        //   不支持的语言：code=invalid_parameters、status=unsupported_language
        // 旧实现用 `code || status` 取一个，会把具体 status 折叠成通用 code，丢掉判别信息。
        // 所以三个字段都单独留下，分派时按 code 或 status 任一命中。
        result.code = detail.code || "";
        result.status = detail.status || "";
        result.kind = detail.type || "";
        result.requestId = detail.request_id || "";
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

    // 命中 code 或 status 任一即算（两套命名空间并存，详见 parseApiError）
    function has(names) {
        return names.indexOf(parsed.code) !== -1 || names.indexOf(parsed.status) !== -1;
    }

    // 402：免费订阅用音色库音色。实测真实字符串为 payment_required；voice_access_denied
    // 与 voice_not_allowed_for_free_users 留作同义兜底（旧版测试见过后者）。
    if (has(["payment_required", "voice_access_denied", "voice_not_allowed_for_free_users"])) {
        return {
            type: "api",
            message:
                "当前订阅无法使用该音色" + detail +
                "。音色库音色对免费订阅的 API 不开放，请换成菜单里的音色，或升级订阅",
            troubleshootingLink: "https://elevenlabs.io/app/voice-lab"
        };
    }
    // 400：文本超过该模型单次上限，与订阅无关。code=text_too_long / status=max_character_limit_exceeded
    if (has(["max_character_limit_exceeded", "text_too_long"])) {
        return { type: "param", message: "文本超过该模型单次字符上限" + detail + "，请分段朗读" };
    }
    // 音色不存在
    if (has(["voice_not_found", "voice_does_not_exist"])) {
        return { type: "notFound", message: "音色不存在，请检查 Voice ID" + detail };
    }
    // 模型不存在（实测 400 + status=model_not_found，旧格式只带 status 不带 code）
    if (has(["model_not_found"])) {
        return { type: "param", message: "模型不存在，请在插件设置里检查模型选择" + detail };
    }
    // 音频格式需要更高订阅档：192kbps 需 Creator+。实测 403，code=subscription_required、
    // status=output_format_not_allowed。曾经落到下面 401/403 兜底被误报成「Key 无效」，必须前置拦截。
    if (has(["output_format_not_allowed", "subscription_required"])) {
        return {
            type: "param",
            message: "该音频格式需要 Creator 及以上订阅" + detail + "，请在设置里换一个格式",
            troubleshootingLink: "https://elevenlabs.io/app/subscription"
        };
    }
    // 音频格式不被支持（自定义格式写错等）。实测 403 + code=status=invalid_output_format
    if (has(["invalid_output_format"])) {
        return { type: "param", message: "音频格式不被支持" + detail };
    }
    // 当前模型不支持该 language_code。插件已按模型语言集门控，正常不会触发，留作兜底。
    // 实测 400，code=invalid_parameters、status=unsupported_language（code 是通用值，靠 status 分派）。
    if (has(["unsupported_language"])) {
        return { type: "param", message: "当前模型不支持该语言的 language_code" + detail };
    }
    // voice_settings 越界（菜单已限定合法区间，兜底）。实测 speed=2.0 → 400 + invalid_voice_settings
    if (has(["invalid_voice_settings"])) {
        return { type: "param", message: "音色参数越界" + detail };
    }
    // API Key 无效
    if (has(["invalid_api_key"])) {
        return {
            type: "secretKey",
            message: "API Key 无效" + detail,
            troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
        };
    }
    // 与 invalid_api_key 是两回事：Key 有效但没勾对权限，换 Key 没用。
    // 旧格式 401 + missing_permissions，新格式 403 + insufficient_permissions。
    if (has(["missing_permissions", "insufficient_permissions"])) {
        return {
            type: "secretKey",
            message:
                "API Key 缺少权限" + detail +
                "。ElevenLabs 新建 Key 默认是受限的，请到 elevenlabs.io/app/settings/api-keys 勾选 Text to Speech",
            troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
        };
    }
    // 额度用完（insufficient_credits 新码 / quota_exceeded 旧码）
    if (has(["insufficient_credits", "quota_exceeded"])) {
        return {
            type: "api",
            message: "ElevenLabs 字符额度已用完" + detail,
            troubleshootingLink: "https://elevenlabs.io/app/subscription"
        };
    }
    if (has(["too_many_concurrent_requests", "concurrent_limit_exceeded"])) {
        return { type: "api", message: "并发请求超出订阅上限" + detail + "，请稍后重试" };
    }
    if (has(["system_busy"])) {
        return { type: "api", message: "ElevenLabs 服务端繁忙" + detail + "，稍后重试即可" };
    }
    if (has(["detected_unusual_activity"])) {
        return { type: "api", message: "账号被判定为异常活动，免费额度已被暂停" + detail };
    }

    // 状态码兜底：上面的 code/status 命中失败时才走这里。401/403 落到这通常是
    // Key/权限问题（输出格式、缺权限这类已知 403 已被前置拦截）。
    if (statusCode === 401 || statusCode === 403) {
        return {
            type: "secretKey",
            message: "API Key 无效或缺少权限" + detail,
            troubleshootingLink: "https://elevenlabs.io/app/settings/api-keys"
        };
    }
    // 400 不能一概而论：voice_not_found、max_character_limit_exceeded、unsupported_language
    // 都走 400，含义各异，必须靠上面的 code/status 分派，落到这里只给中性文案。
    if (statusCode === 400) {
        return { type: "param", message: "请求被拒绝" + detail };
    }
    if (statusCode === 402) {
        // 判据是音色的来源：音色库（Voice Library）音色对免费订阅的 API 不开放。
        return {
            type: "api",
            message:
                "当前订阅无法使用该音色" + detail +
                "。音色库音色对免费订阅的 API 不开放，请换成菜单里的音色，或升级订阅",
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

// troubleshootingLink 经真机确认在 Bob 的 tts 报错弹窗里只渲染成纯文本（URL 能看见但不可点）。
// 为防止任何情况下用户看不到自救地址，把 troubleshootingLink 的 URL 明文追加进 message；
// message 里已经含该地址（如缺权限那条）则不重复。
function ensureLinkVisible(err) {
    if (!err || !err.troubleshootingLink || !err.message) {
        return err;
    }
    var link = err.troubleshootingLink;
    var probe = link.replace(/^https?:\/\//, "");
    if (err.message.indexOf(link) !== -1 || err.message.indexOf(probe) !== -1) {
        return err;
    }
    err.message = err.message + "（详见 " + link + "）";
    return err;
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

// 只把用户显式覆盖过的项发给 API，其余留空则沿用音色在 ElevenLabs 上保存的设置。
// 再按模型能力门控：style / use_speaker_boost 只有 multilingual_v2 支持，其余模型
// （含 v3）会忽略，这里直接不发（详见 config.js MODEL_SETTINGS）。
function buildVoiceSettings(modelId) {
    var settings = {};
    var dropped = [];

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
        if (isNaN(value)) {
            return;
        }
        if (!config.modelAcceptsSetting(modelId, pair[1])) {
            dropped.push(pair[1]);
            return;
        }
        settings[pair[1]] = value;
    });

    var boost = trimmed($option.speakerBoost);
    if (boost === "true" || boost === "false") {
        if (config.modelAcceptsSetting(modelId, "use_speaker_boost")) {
            settings.use_speaker_boost = boost === "true";
        } else {
            dropped.push("use_speaker_boost");
        }
    }

    if (dropped.length) {
        logInfo("voice_settings 丢弃（" + modelId + " 不支持）：" + dropped.join(","));
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

            // 受限 Key 打 /models 会因缺读权限而 401/403，但这不代表 TTS 不能用——
            // 新建 Key 默认只勾 Text to Speech 是官方默认路径，正是 HANDOFF 里 P0#3 的误报来源。
            // 不存在「免权限」的探测端点，所以把缺权限判为通过（TTS 通常仍可用），只有
            // invalid_api_key 才判失败。注：Bob 在 result:true 时一般不展示 error 字段，
            // 提示另写一行日志留档。
            var parsed = parseApiError(resp.data);
            var permDenied =
                ["missing_permissions", "insufficient_permissions"].indexOf(parsed.code) !== -1 ||
                ["missing_permissions", "insufficient_permissions"].indexOf(parsed.status) !== -1;
            if (permDenied) {
                logInfo("validate 受限通过：Key 缺读取权限（不影响朗读），HTTP " + statusCode);
                completion({
                    result: true,
                    error: {
                        type: "secretKey",
                        message: "API Key 可用，但缺少读取权限（不影响朗读）"
                    }
                });
                return;
            }

            var failure = toServiceError(statusCode, resp.data);
            ensureLinkVisible(failure);
            completion({ result: false, error: failure });
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

            // Legacy 音色会被 ElevenLabs 路由到音色库音色，免费订阅必然 402。
            // 提前拦截，省得用户对着一句「当前订阅无法使用该音色」去猜。
            var legacyName = config.LEGACY_VOICES[voiceId];
            if (legacyName) {
                throw {
                    type: "param",
                    message:
                        legacyName + "（" + voiceId + "）已被 ElevenLabs 列为 Legacy 音色，" +
                        "调用时会被路由到音色库音色，免费订阅无法使用。请换一个音色"
                };
            }

            // 老音色仍能用到 2026-12-31，不拦截，但要留痕：Bob 会保留用户此前保存
            // 的选项值，菜单换成接班音色后界面显示新的、实际发的仍是老的。
            var retiring = config.RETIRING_VOICES[voiceId];
            if (retiring) {
                logInfo(
                    "warn 音色 " + retiring.name + "（" + voiceId + "）将于 2026-12-31 失效" +
                    (retiring.successor
                        ? "，官方接班音色为 " + retiring.successor + "，请在设置里改选"
                        : "，官方未指定接班音色，请在设置里另选一个")
                );
            }

            var text = typeof query.text === "string" ? query.text : "";
            if (!trimmed(text)) {
                throw { type: "param", message: "没有可合成的文本" };
            }

            var modelId = trimmed($option.model) || "eleven_flash_v2_5";
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

            // 只下发该模型确实支持的 language_code：模型对支持列表外的 code 直接回
            // 400 unsupported_language（实测，非文档所说的「忽略」），所以按模型语言集门控；
            // 不支持的就不下发，让模型自行识别（实测 flash_v2 + 中文不带 code 仍能合成）。
            var languageCode = config.langMap.get(query.lang);
            if (languageCode && config.modelAcceptsLanguage(modelId, languageCode)) {
                body.language_code = languageCode;
            }

            var voiceSettings = buildVoiceSettings(modelId);
            if (voiceSettings) {
                body.voice_settings = voiceSettings;
            }

            // 英语专用模型读别的语言会出怪音。不拦截（用户可能是故意的），
            // 但要在日志里留痕 —— Bob 会保留旧配置，这种错配很容易是残留造成的。
            if (info.englishOnly && query.lang !== "en") {
                logInfo("warn " + modelId + " 仅支持英语，当前语言 " + query.lang + "，发音可能异常");
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

            // 必须查状态码：非 2xx 时响应体是 JSON 错误，直接 base64 会被当成音频播放，
            // 表现为「点了没声音也没报错」。
            var statusCode = resp.response ? resp.response.statusCode : 0;
            if (statusCode < 200 || statusCode >= 300) {
                var failure = toServiceError(statusCode, resp.data);
                if (failure.type === "notFound" || statusCode === 402 || statusCode === 404) {
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
            var audio = raw && isBinary(raw) ? raw : $data.fromData(raw);
            // 不要用 audio.length 判空：Bob 实际运行时 $data 不暴露该属性（恒为
            // undefined），拿它比较等于没有防护。base64 字符串长度才是可靠的。
            var encoded = audio ? audio.toBase64() : "";
            if (!encoded) {
                throw { type: "api", message: "ElevenLabs 返回了空音频" };
            }

            logInfo(
                "success status=" + statusCode + " base64_chars=" + encoded.length +
                " ms=" + (Date.now() - startedAt)
            );

            completion({
                result: {
                    type: "base64",
                    value: encoded,
                    raw: {
                        model_id: modelId,
                        voice_id: voiceId,
                        output_format: outputFormat,
                        base64_chars: encoded.length
                    }
                }
            });
        } catch (err) {
            ensureLinkVisible(err);
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
