// ElevenLabs 服务的静态元信息。
//
// info.json 里的模型 / 音色菜单可以用 scripts/sync_catalog.py 从账号拉最新的，
// 但「模型能力」这类 API 不返回的信息在这里手工维护。

var API_BASE = "https://api.elevenlabs.io/v1";

// 单次请求字符上限与 language_code 支持情况。
// 出处：https://elevenlabs.io/docs/overview/models（Character limits 一节）
// language_code 官方说明：模型不支持时会被忽略，但 multilingual_v2 明确「不支持该参数」，
// 所以只对确认可用的模型下发。
var MODELS = {
    eleven_v3: { charLimit: 5000, languageCode: true },
    eleven_multilingual_v2: { charLimit: 10000, languageCode: false },
    eleven_flash_v2_5: { charLimit: 40000, languageCode: true },
    eleven_flash_v2: { charLimit: 30000, languageCode: true, englishOnly: true },
    // 已被 ElevenLabs 标记为 deprecated，保留只为兼容老配置
    eleven_turbo_v2_5: { charLimit: 40000, languageCode: true },
    eleven_turbo_v2: { charLimit: 30000, languageCode: true, englishOnly: true }
};

// 菜单里没有的模型（比如用户手填了新模型 ID）走这套保守默认值
var FALLBACK_MODEL = { charLimit: 5000, languageCode: false };

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

var langMap = new Map(LANGUAGES.map(function (item) {
    return [item[0], item[1]];
}));

exports.API_BASE = API_BASE;
exports.MODELS = MODELS;
exports.FALLBACK_MODEL = FALLBACK_MODEL;
exports.LANGUAGES = LANGUAGES;
exports.langMap = langMap;
