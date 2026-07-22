# 待验证与待办

这份文档给接手的人看，不需要任何上下文。

## 这是什么

Bob（macOS 翻译软件）的 ElevenLabs 语音合成插件。当前 v1.0.2，功能可用。

问题不在功能，在于**一批结论只有文档依据、没有真机验证**。这个项目已经因此摔过三次：

1. 照抄上游 2025 年的音色列表 → 默认音色对当前账号必然报 402
2. Bob 文档写 `$data` 有 `length` 属性，实测是 `undefined` → 据此写的空音频防护形同虚设
3. 把 HTTP 400 并进「订阅限制」分支 → 文本超长和音色 ID 写错都被误报成订阅问题

**所以本文档的规矩是：改任何一条之前，先按「怎么验」那栏跑出结果。文档说的不算数。**

## 现成的工具

```bash
make test                              # 31 项断言，用 macOS 自带 jsc（Bob 同款引擎），不联网
python3 scripts/verify_api.py          # 拿真实 Key 打 ElevenLabs，6 组探针，约 30~40 credits
python3 scripts/verify_api.py --dry-run
make install                           # 打包并让 Bob 安装
```

Bob 日志（插件每次请求都会写一行，含实际发出的 model/voice/format）：

```bash
grep '11labs-tts' ~/Library/Containers/com.hezongyidev.Bob/Data/Documents/MMKitLogs/MMLogs/Default/*.log | tail -20
```

插件加载时还会写一行 `runtime ...`，记录 `$data` 实际有哪些方法。

---

## P0 — 有文档依据、当前代码明确不符

### 1. 字符上限是按账号档位区分的，不是固定值

- **现状**：`src/config.js` 的 `MODELS` 表把上限写死成 v3=5000 / multilingual_v2=10000 / flash_v2_5=40000 / flash_v2=30000。
- **证据**：`GET /v1/models` 每个模型返回 `max_characters_request_free_user` 和 `max_characters_request_subscribed_user` 两个字段。文档表格里的单一数字对应哪一档没写明。
- **怎么验**：`curl -s -H "xi-api-key: $KEY" https://api.elevenlabs.io/v1/models | python3 -m json.tool | grep -A2 max_characters`
- **怎么改**：拿到真实值后更新 `MODELS` 表，并在注释里写明对应哪一档。`src/main.js` 里超限拦截用的就是这张表。

### 2. eleven_v3 不支持 speed / similarity_boost / use_speaker_boost

- **现状**：`src/info.json` 暴露 5 个音色参数，`src/main.js` 的 `buildVoiceSettings()` 不区分模型，一律下发。v3 下有 3 个是无效项。
- **怎么验**：`python3 scripts/verify_api.py --only settings`，看 `v3 + speed/style` 那条是 200 还是报错。
- **怎么改**：`buildVoiceSettings()` 加模型判断，v3 时只保留 `stability` 和 `style`。若实测证明只是被静默忽略，改成在 `info.json` 的对应 `desc` 里注明「v3 下无效」即可，不必动代码。
- **附带未定**：v3 的 `stability` 是否只接受 0.0 / 0.5 / 1.0 三个离散值。插件菜单恰好只给这三个，但这是巧合不是设计。实测 `v3 + stability=0.3` 即可确认。

### 3. pluginValidate 的端点选择存在系统性误报

- **现状**：`src/main.js` 的 `pluginValidate()` 打 `GET /v1/models`，401 一律报「API Key 无效」。
- **证据**：ElevenLabs 网页控制台新建 Key **默认是受限的**，用户要逐项勾选权限。所以「只勾了 Text to Speech」是官方默认路径的自然结果，不是边缘情况。这种 Key 打 `/v1/models` 会失败，但 TTS 完全可用 → 误报。
- **另一条证据**：不存在「免权限」的探测端点。官方自己的验证流程用 `/v1/user`，但也要求用户单独把 User 权限设为 Read。换端点解决不了问题。
- **三个可选方案**（`pluginValidate` 是 Bob 的**可选**接口，不实现只是设置界面不显示验证按钮）：
  - (a) 不实现，只在 `tts()` 里报错
  - (b) 保留探测，但把 `missing_permissions` / `insufficient_permissions` 判为**通过**并附提示，只有 `invalid_api_key` 才判失败
  - (c) 直接打 `POST /v1/text-to-speech/{voice}` 发 1 个字符 —— 唯一能保证「通过 = TTS 一定能用」的做法，代价是每次验证消耗 1~2 credits
- **建议**：(b)。已在 `toServiceError()` 里区分了这两类 status，`pluginValidate` 还没跟上。

### 4. troubleshootingLink 在 tts 错误里是否渲染，未证实

- **现状**：多处报错把自救 URL 只放在 `troubleshootingLink` 字段里。
- **风险**：如果 Bob 不在 tts 错误提示里渲染这个字段，用户就完全看不到那个地址。
- **怎么验**：故意触发一次 402（音色填 `9BWtsMINqrJLrRacOk9x`），看 Bob 弹出的提示里有没有可点链接。
- **怎么改**：确认不渲染的话，把关键 URL 明文写进 `message`。

---

## P1 — 只有真机能回答

全部由 `scripts/verify_api.py` 覆盖，跑一次约 30~40 credits。**要求记录 HTTP 状态码 + 响应体 JSON 全文**，不要只记「失败了」。

| # | 问题 | 为什么重要 | 命令 |
|---|---|---|---|
| 1 | multilingual_v2 传 `language_code` 是 422 还是被忽略 | `src/main.js` 为此做了模型门控。若只是被忽略，这段复杂度可以删掉 | `--only language` |
| 2 | 部分下发 voice_settings 时，未下发的字段是否真的沿用音色在网站上保存的设置 | `info.json` 里五处「跟随音色自带设置」文案全押在这个前提上。若实际是回落到全局默认值，这些文案全是错的 | `--only settings` |
| 3 | 免费档请求 `mp3_44100_192` 的确切 HTTP 码和 status | 决定这个菜单项要不要保留 | `--only formats` |
| 4 | `eleven_multilingual_v2` 是否可用 | 4 个模型里唯一没在真机跑过的 | `--only models` |
| 5 | 那 6 个错误 status 里，哪些真的会出现 | `toServiceError()` 的分派依赖它们 | `--only status` |

跑完把输出末尾的「实测到的 detail.status」一节贴进本文档存档。

---

## P2 — Bob 侧未定

这几条查不到官方说法，只能靠观察。不紧急，但踩到会很费时间。

- **`$data.length` 为什么是 undefined**：文档明确写了这个属性。是版本差异还是文档错误？插件已经绕开（改用 base64 字符串长度），所以只是知识缺口。加载时的 `runtime` 日志行会记录实际情况。
- **`supportLanguages()` 的调用时机**：每次合成都调，还是安装时调一次后缓存？当前返回静态并集，怎样都安全。但如果将来想按模型动态返回语言列表，这条必须先搞清楚。
- **Bob 保留旧配置值**：已实测确认——把某个 value 从 `info.json` 的 `menuValues` 里删掉后，Bob 仍会把用户此前保存的旧值发出去，界面却显示成菜单第一项。这个行为让排查极易走偏（本项目为此浪费了大量时间）。**有没有官方推荐的强制重置做法**（比如换 option identifier）未查实。目前的应对是让报错带上实际发出的 Voice ID。
- **超时无余量**：`pluginTimeoutInterval()` 返回 60，`$http.request` 的 timeout 也是 60。两者同时到点时谁先触发不确定——如果 Bob 先超时，插件自己的错误信息就显示不出来。把 `$http` 的 timeout 调到 50 更稳妥，但这只是推测，没验证过。

---

## 时间炸弹：2026-12-31

**官方原文**：「All our Default voices will expire on December 31, 2026, and they will no longer be accessible after this date.」

`src/info.json` 菜单里那 21 个音色**全部**在这一天失效，包括默认的 Bella。官方替换对照表只有 19 行，Bella 和 Adam 不在其中 —— 意味着它们连官方接班音色都没有。

**更麻烦的是没有现成替代方案**：官方指定的 19 个新音色（Darian / Talia / Elara …）全部属于 Voice Library，而「Voice Library voices are not available via the API to free tier users」。付费用户也得先手动 add 到 My Voices 才能用，且计费倍率更高。

所以到期后，**不存在任何一份能写死进 `info.json`、对免费用户开箱可用的音色列表**。这不是「到时候换一批 ID」能解决的。

### 唯一可能的出路，需要验证

免费档有 3 个 Voice Design 音色槽。用 Voice Design 生成的音色（`category=generated`）属于用户自己的账号，理论上不受音色库限制。

**但「免费档 Voice Design 生成的音色能否通过 API 合成」从未验证过。** 这条的价值最高——它决定 2027 年免费用户还有没有任何可用路径，也决定 README 里的自救指南能不能写出来。

验证方法：在 elevenlabs.io 用 Voice Design 生成并保存一个音色，拿到 voice_id，然后

```bash
python3 scripts/verify_api.py --only status --voice-id <新音色的ID>
```

### 建议的时间表

- **现在**：README 顶部写明这个截止日，别让用户以为是长期方案
- **11 月**：发一个版本，把菜单默认值切到 `__custom__`，21 项标题加「已失效」前缀，`desc` 顶部放自救步骤

---

## 已经验证过的，不要再查

省得重复劳动。以下都有官方原文或真机实证支撑：

- `language_code` 收**两字母** ISO 639-1。v3 文档里的 `afr`/`ara`/`hye` 三字母只是展示用，不是 API 值
- 挪威语用 `no` 不是 `nb`；菲律宾语用 `fil` 不是 `tl`；`zh-Hant` → `zh` 正确；`sr`/`sr-Cyrl`/`sr-Latn` 都映射到 `sr`
- ElevenLabs **没有粤语**，`yue` → `zh` 和 `wyw` → `zh` 是权宜之计
- 模型不支持某语言时传 `language_code` 会被**忽略**而非报错 → `supportLanguages()` 返回并集是安全的
- 部分下发 `voice_settings`（只传 stability 不传其余）在协议层面**合法**
- `speed` 合法区间 0.7~1.2，菜单给的 0.8~1.2 都在范围内
- `$data.isData()` 真实存在，是**类方法**（挂在 `$data` 上）
- `data.toUTF8()` 对非 UTF-8 数据返回 `undefined`，不是空串也不抛错
- `pluginTimeoutInterval()` 是正式 API，对 tts 插件生效，单位是秒
- `resp.error` **只在网络层错误时**设置，HTTP 4xx/5xx 不会置 error（必须自己查 `statusCode`）
- `detail.status` 已被官方标为 legacy，现行格式用 `detail.code` + `detail.type`。**两套并存且不互通**，都要读
- 402「Free users cannot use library voices」的真实 status 是 `payment_required`
- 权限不足在旧格式是 **401 + `missing_permissions`**，新格式是 **403 + `insufficient_permissions`**——都不是「Key 无效」
- 超限是 **400 + `max_character_limit_exceeded`**（新码 `text_too_long`）
- Aria / Rachel / Charlotte 是 **Legacy 音色**，会被自动路由到音色库音色，免费订阅必然 402。插件已在发请求前拦截
- 免费档的判据是**音色来源是不是音色库**，与「在不在你账号里」无关
