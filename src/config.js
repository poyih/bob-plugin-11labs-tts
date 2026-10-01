// 模型目录同时供运行时与同步工具使用，避免菜单和三张能力表分别维护。
var catalog = require("./model_catalog.js").MODEL_CATALOG;
var API_BASE = "https://api.elevenlabs.io/v1";
var FALLBACK_MODEL = { charLimit: 5000 };
var MODELS = {};
var MODEL_LANGUAGES = {};
var MODEL_SETTINGS = {};

Object.keys(catalog).forEach(function (id) {
    var item = catalog[id];
    var model = { charLimit: item.charLimit, transport: item.transport };
    if (item.englishOnly) { model.englishOnly = true; }
    if (item.localLimit) { model.localLimit = true; }
    MODELS[id] = model;
    // Multilingual v2 的自动识别策略不能被 API 返回的语言列表覆盖。
    MODEL_LANGUAGES[id] = item.sendLanguageCode ? item.languages : [];
    var settings = {};
    Object.keys(item.settings).forEach(function (field) { settings[field] = item.settings[field]; });
    Object.keys(item.settingsOverrides || {}).forEach(function (field) { settings[field] = item.settingsOverrides[field]; });
    MODEL_SETTINGS[id] = settings;
});

function modelAcceptsLanguage(modelId, code) {
    var languages = MODEL_LANGUAGES[modelId];
    return languages === null || Array.isArray(languages) && languages.indexOf(code) !== -1;
}

function modelAcceptsSetting(modelId, field) {
    var caps = MODEL_SETTINGS[modelId];
    return !caps || !(field in caps) ? true : caps[field];
}

// Bob 语言代码 -> ElevenLabs 的 ISO 639-1 代码。
// tier 表示「最低要哪一档模型才原生支持」：
//   v2    - multilingual_v2 / flash_v2_5 / v3 都支持
//   flash - flash_v2_5 及以上
//   v3    - v3 / v3 Conversational
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

function languageCodeForModel(modelId, bobLanguage) {
    var overrides = catalog[modelId] && catalog[modelId].languageOverrides || {};
    var code = overrides[bobLanguage] || langMap.get(bobLanguage);
    return code && modelAcceptsLanguage(modelId, code) ? code : null;
}

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
exports.languageCodeForModel = languageCodeForModel;
