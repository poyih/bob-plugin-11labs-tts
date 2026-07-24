// ElevenLabs 服务的静态元信息。
//
// info.json 里的模型 / 音色菜单可以用 scripts/sync_catalog.py 从账号拉最新的，
// 但「模型能力」这类 API 不返回的信息在这里手工维护。

var API_BASE = "https://api.elevenlabs.io/v1";

// 单次请求字符上限。
// 出处：GET /v1/models 每个模型的 max_characters_request_free_user /
// max_characters_request_subscribed_user（2026-07-23 真机拉取）。
// 关键：两个字段对每个模型都相等——同一个上限不区分免费/订阅档，所以这张表不存在
// 「数字属于哪一档」的歧义，直接照搬即可。multilingual_v2=10000 这一档还用 12000 字
// 实打过，确认在 0.4s 内回 400 + max_character_limit_exceeded。
var MODELS = {
    eleven_v3: { charLimit: 5000 },
    eleven_multilingual_v2: { charLimit: 10000 },
    eleven_flash_v2_5: { charLimit: 40000 },
    eleven_flash_v2: { charLimit: 30000, englishOnly: true },
    // 已被 ElevenLabs 标记为 deprecated，保留只为兼容老配置
    eleven_turbo_v2_5: { charLimit: 40000 },
    eleven_turbo_v2: { charLimit: 30000, englishOnly: true }
};

// 菜单里没有的模型（比如用户手填了新模型 ID）走这套保守默认值
var FALLBACK_MODEL = { charLimit: 5000 };

// 每个模型「原生支持、可安全下发 language_code」的 ISO 639-1 集合。
// 出处：GET /v1/models 每个模型的 languages 字段（2026-07-23 真机拉取，逐模型实打复核）。
//
// 为什么要按模型按语言门控：实测 ElevenLabs 对「不在该模型支持列表里」的 language_code
// 直接回 400 unsupported_language，而**不是**文档/旧结论所说的「被忽略」。曾经据此以为
// 「supportLanguages 返回并集也安全」，其实不然——flash_v2（仅英语）+ zh 必 400，
// flash_v2_5 + af（Afrikaans）也必 400。所以只能下发模型确实支持的语言，其余留空让模型
// 自己识别（实测 flash_v2 + 中文不带 language_code 仍能合成出「怪音」，不会报错）。
//
// null   = 插件用到的语言它全支持（v3 实测 74 种全覆盖，含 af/hy/ceb 等）。
// []     = 一律不下发 language_code。multilingual_v2 是自动语言识别模型，官方称不读
//          language_code，下发收益未证实且可能强制语种、误读跨语言文本，保留历史保守行为。
var MODEL_LANGUAGES = {
    eleven_v3: null,
    eleven_multilingual_v2: [],
    eleven_flash_v2_5: [
        "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil", "fr", "hi",
        "hr", "hu", "id", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro",
        "ru", "sk", "sv", "ta", "tr", "uk", "vi", "zh"
    ],
    eleven_flash_v2: ["en"],
    // turbo 已 deprecated，语言集与同名 flash 一致
    eleven_turbo_v2_5: [
        "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil", "fr", "hi",
        "hr", "hu", "id", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro",
        "ru", "sk", "sv", "ta", "tr", "uk", "vi", "zh"
    ],
    eleven_turbo_v2: ["en"]
};

// 某模型是否应下发该 language_code。未知模型保守不下发（让模型自行识别，绝不触发 400）。
function modelAcceptsLanguage(modelId, code) {
    var langs = MODEL_LANGUAGES[modelId];
    if (langs === undefined) {
        return false;
    }
    if (langs === null) {
        return true;
    }
    return langs.indexOf(code) !== -1;
}

// 各模型对 voice_settings 字段的支持能力。
// 出处：GET /v1/models 每个模型的 can_use_style / can_use_speaker_boost 布尔标志
// （2026-07-23 真机拉取）。实测这两项仅 multilingual_v2 为 true，flash_v2_5 / flash_v2 /
// v3 均为 false——传了会被服务端忽略。这里做运行时门控，让请求体与模型能力一致、日志更
// 干净，也对「个别模型可能改为 400 而非忽略」留一层保险；即便某标志日后变化，门控最坏
// 是漏发一个本可生效的字段（音质微损），不会造成报错。
//
// 注意：/v1/models 只暴露 can_use_style 和 can_use_speaker_boost 两个字段，没有
// speed / similarity_boost / stability 的 per-model 标志，所以这三项一律下发、不门控。
var MODEL_SETTINGS = {
    eleven_multilingual_v2: { style: true, use_speaker_boost: true },
    eleven_flash_v2_5: { style: false, use_speaker_boost: false },
    eleven_flash_v2: { style: false, use_speaker_boost: false },
    eleven_v3: { style: false, use_speaker_boost: false },
    // turbo 已 deprecated，能力与同名 flash 一致
    eleven_turbo_v2_5: { style: false, use_speaker_boost: false },
    eleven_turbo_v2: { style: false, use_speaker_boost: false }
};

// 某模型是否接受该 voice_settings 字段。只有 style / use_speaker_boost 受门控；
// 其余字段（stability / speed / similarity_boost）无 /v1/models 能力标志，一律放行。
// 未知模型或未知字段一律放行，保留用户意图。
function modelAcceptsSetting(modelId, field) {
    var caps = MODEL_SETTINGS[modelId];
    if (!caps || !(field in caps)) {
        return true;
    }
    return caps[field];
}

// Bob 语言代码 -> ElevenLabs 的 ISO 639-1 代码。
// tier 表示「最低要哪一档模型才原生支持」：
//   v2    - multilingual_v2 / flash_v2_5 / v3 都支持
//   flash - flash_v2_5 及以上
//   v3    - 仅 v3
// tier 目前只用于文档说明，运行时不会因此拒绝合成（模型不支持时只是口音不准，不该报错）。
var LANGUAGES = [
    ["zh-Hans", "zh", "v2"],
    ["zh-Hant", "zh", "v2"],
    ["yue", "zh", "v2"],
    ["wyw", "zh", "v2"],
    ["en", "en", "v2"],
    ["ja", "ja", "v2"],
    ["ko", "ko", "v2"],
    ["de", "de", "v2"],
    ["hi", "hi", "v2"],
    ["fr", "fr", "v2"],
    ["pt", "pt", "v2"],
    ["pt-pt", "pt", "v2"],
    ["pt-br", "pt", "v2"],
    ["it", "it", "v2"],
    ["es", "es", "v2"],
    ["id", "id", "v2"],
    ["nl", "nl", "v2"],
    ["tr", "tr", "v2"],
    ["fil", "fil", "v2"],
    ["tl", "fil", "v2"],
    ["pl", "pl", "v2"],
    ["sv", "sv", "v2"],
    ["bg", "bg", "v2"],
    ["ro", "ro", "v2"],
    ["ar", "ar", "v2"],
    ["cs", "cs", "v2"],
    ["el", "el", "v2"],
    ["fi", "fi", "v2"],
    ["hr", "hr", "v2"],
    ["ms", "ms", "v2"],
    ["sk", "sk", "v2"],
    ["da", "da", "v2"],
    ["ta", "ta", "v2"],
    ["uk", "uk", "v2"],
    ["ru", "ru", "v2"],
    ["hu", "hu", "flash"],
    ["no", "no", "flash"],
    ["nb", "no", "flash"],
    ["vi", "vi", "flash"],
    ["af", "af", "v3"],
    ["hy", "hy", "v3"],
    ["as", "as", "v3"],
    ["az", "az", "v3"],
    ["be", "be", "v3"],
    ["bn", "bn", "v3"],
    ["bs", "bs", "v3"],
    ["ca", "ca", "v3"],
    ["ceb", "ceb", "v3"],
    ["ny", "ny", "v3"],
    ["et", "et", "v3"],
    ["gl", "gl", "v3"],
    ["ka", "ka", "v3"],
    ["gu", "gu", "v3"],
    ["ha", "ha", "v3"],
    ["he", "he", "v3"],
    ["is", "is", "v3"],
    ["ga", "ga", "v3"],
    ["jv", "jv", "v3"],
    ["jw", "jv", "v3"],
    ["kn", "kn", "v3"],
    ["kk", "kk", "v3"],
    ["ky", "ky", "v3"],
    ["lv", "lv", "v3"],
    ["ln", "ln", "v3"],
    ["lt", "lt", "v3"],
    ["lb", "lb", "v3"],
    ["mk", "mk", "v3"],
    ["ml", "ml", "v3"],
    ["mr", "mr", "v3"],
    ["ne", "ne", "v3"],
    ["ps", "ps", "v3"],
    ["fa", "fa", "v3"],
    ["pa", "pa", "v3"],
    ["sr", "sr", "v3"],
    ["sr-Cyrl", "sr", "v3"],
    ["sr-Latn", "sr", "v3"],
    ["sd", "sd", "v3"],
    ["sl", "sl", "v3"],
    ["so", "so", "v3"],
    ["sw", "sw", "v3"],
    ["te", "te", "v3"],
    ["th", "th", "v3"],
    ["ur", "ur", "v3"],
    ["cy", "cy", "v3"]
];

// 已被 ElevenLabs 完全弃用的 Legacy 音色。官方原文：「Legacy voice IDs will
// automatically route to their replacement voice IDs」——而替代目标是音色库音色，
// 免费订阅通过 API 用不了，于是表现为 402「Free users cannot use library voices」。
// 本插件早期版本的默认音色正是其中之一，开箱即坏。
var LEGACY_VOICES = {
    "9BWtsMINqrJLrRacOk9x": "Aria",
    "21m00Tcm4TlvDq8ikWAM": "Rachel",
    "XB0fDUnXU5powFXDhCwa": "Charlotte"
};

// 2026-12-31 退役的 21 个 Default 音色 → 官方指定的接班音色。
// v1.0.6 起菜单已换成接班音色，这张表用于兼容旧配置：Bob 会保留用户此前保存的
// 选项值，即使该值已从 menuValues 移除也照旧发出（界面却显示成菜单第一项）。
// 所以老用户升级后仍会发老音色。截止日前写日志提醒，截止后由 main.js 明确拦截。
// successor 为 null 表示官方没给接班音色（Bella、Adam）。
var RETIRING_VOICES = {
    CwhRBWXzGAHq8TQ4Fs17: { name: "Roger", successor: "Darian" },
    EXAVITQu4vr4xnSDxMaL: { name: "Sarah", successor: "Talia" },
    FGY2WhTYpPnrIDTdsKH5: { name: "Laura", successor: "Elara" },
    IKne3meq5aSn9XLyUdCD: { name: "Charlie", successor: "Baxter" },
    JBFqnCBsd6RMkjVDRZzb: { name: "George", successor: "Eldrin" },
    N2lVS1w4EtoT3dr4eOWO: { name: "Callum", successor: "Kellan" },
    SAz9YHcvj6GT2YYXdXww: { name: "River", successor: "Elowen" },
    SOYHLrjzK2X1ezoPC6cr: { name: "Harry", successor: "Kaelen" },
    TX3LPaxmHKxFdv7VOQHJ: { name: "Liam", successor: "Lawrence" },
    Xb7hH8MSUJpSbSDYk0k2: { name: "Alice", successor: "Alicia" },
    XrExE9yKIg1WjnnlVkGX: { name: "Matilda", successor: "Maisie" },
    bIHbv24MWmeRgasZH58o: { name: "Will", successor: "Warren" },
    cgSgspJ2msm6clMCkdW9: { name: "Jessica", successor: "Jade" },
    cjVigY5qzO86Huf0OWal: { name: "Eric", successor: "Eddie" },
    iP95p4xoKVk53GoZ742B: { name: "Chris", successor: "Caleb" },
    nPczCjzI2devNBz1zQrb: { name: "Brian", successor: "Sawyer" },
    onwK4e9ZLuTAKqWW03F9: { name: "Daniel", successor: "Finley" },
    pFZP5JQG7iQjIQuC4Bku: { name: "Lily", successor: "Florence" },
    pqHfZKP75CvOlQylNhV4: { name: "Bill", successor: "Wyatt" },
    // 官方替换表里没有这两个，到期后无指定接班音色
    hpp4J3VqNfWAUOO0d1Us: { name: "Bella", successor: null },
    pNInz6obpgDQGcFmaJgB: { name: "Adam", successor: null }
};

var langMap = new Map(LANGUAGES.map(function (item) {
    return [item[0], item[1]];
}));

exports.API_BASE = API_BASE;
exports.MODELS = MODELS;
exports.FALLBACK_MODEL = FALLBACK_MODEL;
exports.LANGUAGES = LANGUAGES;
exports.LEGACY_VOICES = LEGACY_VOICES;
exports.RETIRING_VOICES = RETIRING_VOICES;
exports.langMap = langMap;
exports.MODEL_LANGUAGES = MODEL_LANGUAGES;
exports.modelAcceptsLanguage = modelAcceptsLanguage;
exports.MODEL_SETTINGS = MODEL_SETTINGS;
exports.modelAcceptsSetting = modelAcceptsSetting;
