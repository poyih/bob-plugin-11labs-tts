# 待验证与待办

这份文档给接手的人看，不需要任何上下文。

## 这是什么

Bob（macOS 翻译软件）的 ElevenLabs 语音合成插件。当前 v1.0.3，功能可用。

问题不在功能，在于**一批结论只有文档依据、没有真机验证**。这个项目已经因此摔过三次：

1. 沿用了一份 2025 年的音色列表 → 默认音色对当前账号必然报 402
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

> 2026-07-23 真机全部验完。下面每条都标了实测结论与是否改了代码。验证用账号是 **payg（按量付费）** 档，不是免费档——所以「免费档必 402」「192kbps 越档」这类结论里，免费档行为靠既有真机记录 + 本次 payg 实测交叉确认；payg 能用音色库音色（Aria 实测 200），无法复现免费档 402。

### 1. 字符上限是按账号档位区分的，不是固定值 — ✅ 无需改

- **实测**：`GET /v1/models` 每个模型的 `max_characters_request_free_user` 与 `max_characters_request_subscribed_user` **完全相等**：v3=5000、multilingual_v2=10000、flash_v2_5=40000、flash_v2=30000。
- **结论**：不存在「数字属于哪一档」的歧义，`config.js` 的 `MODELS.charLimit` 本来就对，不动。
- **附带**：multilingual_v2 上限用 12000 字实打，0.4s 内回 400 + `max_character_limit_exceeded`（`code=text_too_long`/`status=max_character_limit_exceeded`，两套都有）。10000 这一档坐实。

### 2. eleven_v3 不支持 speed / similarity_boost / use_speaker_boost — ✅ 改 info.json 文案

- **实测**：`v3 + speed/style` → 200（接受不报错）；`v3 + stability=0.3` → 200（**非**离散值，菜单只给 0/0.5/1 是巧合）。`/v1/models` 元数据 `can_use_style=false`、`can_use_speaker_boost=false`。
- **结论**：style / speaker_boost 在 v3（以及 flash_v2_5 / flash_v2 / turbo）上被**静默忽略**，仅 multilingual_v2 真正生效。按 HANDOFF 规矩「静默忽略就只改文案」，`info.json` 的 `style` / `speakerBoost` 两条 `desc` 已注明「仅在 Multilingual v2 上生效」，`buildVoiceSettings()` 不动代码。
- **附带未定已解**：v3 的 stability 接受任意 [0,1] 值，无离散限制。

### 3. pluginValidate 的端点选择存在系统性误报 — ✅ 已改（方案 b）

- **实测**：本 Key 下 `/models`、`/voices`、`/user`、`/user/subscription` 全 200（payg 全权限），无法直接复现「受限 Key 打 /models 失败」。但缺权限的错误形态已在既有真机记录里：旧 401 `missing_permissions` / 新 403 `insufficient_permissions`。
- **已改**：`pluginValidate()` 命中 `missing_permissions` / `insufficient_permissions` 时判**通过**并写一行日志提示「Key 缺读取权限、不影响朗读」；只有 `invalid_api_key` 才判失败。该改法对「/models 返回别的错」不劣于现状（仍按失败处理），只在确属缺权限时改善，安全。
- **未直验**：受限 Key 打 /models 是否**恰好**返回 `missing_permissions`/`insufficient_permissions` 未直接复现（无受限 Key）。若实测不符，退化为现状（报失败），无回归。

### 4. troubleshootingLink 在 tts 错误里是否渲染 — ✅ 已验已改

- **实测**：真机触发 402，Bob 弹窗里 `troubleshootingLink` **渲染成纯文本**（URL 看得见但不可点）。
- **已改**：`main.js` 加 `ensureLinkVisible()`，把 `troubleshootingLink` 的 URL 明文追加进 `message`（已含则不重复），`tts()` 与 `pluginValidate()` 的报错都套用。无论 Bob 渲染与否，自救地址都在正文里。

### 5.（新发现）403 输出格式错误被误报成「Key 无效」— ✅ 已改

- **实测**：192kbps 在非 Creator 档回 **403**，`code=subscription_required`、`status=output_format_not_allowed`；非法格式回 403 `invalid_output_format`。旧 `toServiceError()` 把 403 一律落到「API Key 无效或缺少权限」→ 误报。
- **已改**：`toServiceError()` 前置拦截这两类 → `param`，提示「该格式需 Creator 及以上订阅」。顺带加了 `model_not_found` / `unsupported_language` / `invalid_voice_settings` 的具体分派。

### 6.（新发现）`parseApiError` 把 code 与 status 折叠，丢判别信息 — ✅ 已改

- **实测**：同一错误 `code` 与 `status` 可能不同且不可互替（192kbps：`subscription_required` vs `output_format_not_allowed`；语言：`invalid_parameters` vs `unsupported_language`）。旧实现 `code || status` 取一个，会吃掉具体 status。
- **已改**：`parseApiError()` 三字段（`code`/`status`/`kind`）分开留；`toServiceError()` 用 `has()` 按 code 或 status 任一命中。

---

## P1 — 只有真机能回答

> 2026-07-23 用 payg Key 跑完 `scripts/verify_api.py` 全量 + 若干定向 curl。结论如下。

| # | 问题 | 实测结论 | 处置 |
|---|---|---|---|
| 1 | multilingual_v2 传 `language_code` 是 422 还是被忽略 | **200，不报错**（multilingual_v2 + zh 实测通过）。是「接受」，应用还是忽略 2 字符文本看不出来。它是自动语言识别模型，下发收益未证实 | 门控**保留**但细化：见下文「语言门控」 |
| 2 | 部分下发 voice_settings，未下发字段是否沿用音色保存设置 | **本次无法判定**：2 字符 hi 看不出音频差异，协议层只确认「部分下发合法（200）」。未下发字段回落到「音色保存设置」还是「全局默认」仍无定论 | `info.json`「跟随音色自带设置」文案**暂不改**（无实测依据；改成「全局默认」同样未验） |
| 3 | 192kbps 的确切 HTTP/status | **403 `output_format_not_allowed`**（`code=subscription_required`），「only available on the Creator tier and above」。payg 也被拒 | 菜单项保留（Creator+ 用户可用），`toServiceError` 已正确分派（见 P0#5） |
| 4 | `eleven_multilingual_v2` 是否可用 | **可用**，实测 200 | 无需改 |
| 5 | 6 个猜的 status 哪些真出现 | 见下方存档表。`voice_not_found`/`invalid_api_key` 出现；`voice_does_not_exist` 未出现（实际是 `voice_not_found`）；另冒出 `model_not_found`/`unsupported_language`/`output_format_not_allowed`/`invalid_output_format`/`invalid_voice_settings` 等 | `toServiceError` 已据实测重写 |

### 语言门控（P1#1 的真正结论）

- 原以为「模型不支持 language_code 就忽略」，**实测证伪**：不支持时回 **400 `unsupported_language`**。
- 所以模型门控**不能删**，反而要细化到「按模型 × 语言」：`config.js` 新增 `MODEL_LANGUAGES`（取自 `/v1/models` 每模型 `languages` 字段，逐模型实打复核），`main.js` 用 `config.modelAcceptsLanguage(modelId, code)` 判断，**只在模型支持时下发**，否则留空让模型自行识别。
- v3 = 全支持（74 种）；flash_v2_5 / turbo_v2_5 = 32 种；flash_v2 / turbo_v2 = 仅 `en`；multilingual_v2 = **一律不下发**（自动识别模型，保留历史保守行为）；未知模型 = 一律不下发（最保守）。
- 实测覆盖：flash_v2_5 + zh → 200（下发）；flash_v2_5 + af → 不下发 → 200（避免 400）；flash_v2 + 中文不带 code → 200（怪音）；v3 + af → 200（下发）。

### 实测到的 detail.status 存档（2026-07-23，payg）

```
invalid_api_key              HTTP 401  ← 无效 API Key
invalid_output_format        HTTP 403  ← 非法 output_format
invalid_voice_settings       HTTP 400  ← 越界 speed=2.0
max_character_limit_exceeded HTTP 400  ← multilingual_v2 + 12000 字（code=text_too_long）
model_not_found              HTTP 400  ← 不存在的 model_id（旧格式只带 status）
output_format_not_allowed    HTTP 403  ← 192kbps 非 Creator（code=subscription_required）
unsupported_language         HTTP 400  ← flash_v2_5 + af 等（code=invalid_parameters）
voice_not_found              HTTP 404  ← 不存在的 voice_id
```

旧猜的 6 个本次是否出现：`voice_not_found` ✓、`invalid_api_key` ✓；`voice_does_not_exist` 未出现（实际是 `voice_not_found`）；`quota_exceeded`/`detected_unusual_activity`/`missing_permissions` 本次未触发（payg 全权限、额度未尽），前两者保留分派、后者已按 P0#3 处理。

本次共消耗约 6000 字符额度（payg，含一次 10001 字超限探针的竞态消耗 ~2748）。

---

## P2 — Bob 侧未定

这几条查不到官方说法，只能靠观察。不紧急，但踩到会很费时间。

- **`$data.length` 为什么是 undefined**：**已实测确认**（2026-07-23 加载时 `runtime` 日志行）：`$data.isData=function`、`sample.length=undefined`、`sample.toBase64=function`、`sample.toUTF8=function`、`Date.now=function`。文档写了 `length` 但实际没有；`Date.now` 可用（`main.js` 里的计时没问题）。插件已用 base64 字符串长度绕开，仅作知识缺口存档。
- **`supportLanguages()` 的调用时机**：每次合成都调，还是安装时调一次后缓存？当前返回静态并集，怎样都安全。但如果将来想按模型动态返回语言列表，这条必须先搞清楚。
- **Bob 保留旧配置值**：已实测确认——把某个 value 从 `info.json` 的 `menuValues` 里删掉后，Bob 仍会把用户此前保存的旧值发出去，界面却显示成菜单第一项。这个行为让排查极易走偏（本项目为此浪费了大量时间）。**有没有官方推荐的强制重置做法**（比如换 option identifier）未查实。目前的应对是让报错带上实际发出的 Voice ID。
- **超时无余量**：`pluginTimeoutInterval()` 返回 60，`$http.request` 的 timeout 也是 60。两者同时到点时谁先触发不确定——如果 Bob 先超时，插件自己的错误信息就显示不出来。把 `$http` 的 timeout 调到 50 更稳妥，但这只是推测，没验证过。
- **Legacy 音色拦截是按免费档设计的，付费档会被误伤**（本次 payg 实测新发现）：payg 档实测 Aria `9BWtsMINqrJLrRacOk9x` 合成 **200**——付费档用得了音色库音色。但 `main.js` 的 Legacy 拦截对 Aria/Rachel/Charlotte **不分档一律 pre-block**，付费用户若在「自定义 Voice ID」填这三个会被拦下（其实能合成）。未改：拦截保护的是免费档多数用户（避免开箱 402），误伤付费档三个 ID 属边缘情况；且 `toServiceError` 现在已能把 402 解释清楚，真要去掉拦截也安全。要不要去掉交给你定。

---

## 时间炸弹：2026-12-31

**官方原文**（elevenlabs.io/docs/overview/capabilities/voices）：
- 「All our Default voices will expire on December 31, 2026, and they will no longer be accessible after this date.」
- 「Our Default voices are being replaced with new voices that you will be able to use in perpetuity.」
- 「Voice Library voices are not available via the API to free tier users.」

`src/info.json` 菜单里那 21 个音色**全部**是 `category=premade`（= Default 音色），**全部**在这一天失效，包括默认的 Bella。这一条已用 payg 账号 live `/v1/voices` 逐条核对：21 个音色的 ID + 名称与当前 API 完全一致，类别全是 premade。

**接班音色情况未经官方核验**（2026-07-23 复核纠正）：此前文档写「官方替换对照表只有 19 行，Bella 和 Adam 连接班音色都没有」「官方指定的 19 个新音色 Darian/Talia/Elara…」——这些**都无法从官方来源证实**：
- 官方 docs 只说「会被可永久使用的新音色取代」，**没有公开的替换 ID 表**。
- 官方帮助站两篇相关文章（help.elevenlabs.io）被 Zendesk 反爬 403，取不到正文。
- 搜到的唯一替换映射来自第三方杂志（elevenlabsmagazine.com），不可信；且它反而把 Adam 列为接班音色，与旧断言矛盾。

所以「Bella/Adam 有没有接班音色」「接班音色叫什么、是不是音色库音色」**一律按未定处理**。这不影响插件该怎么做——无论接班表什么样，「到期前标停用、引导用户改用自定义 Voice ID」都是稳妥的。

**已知确定的限制**：Voice Library（音色库）音色对免费档 API 不可用（官方原文如上）。付费档实测可用（payg + Aria 音色库音色 = 200）。Default/premade 音色比音色库音色更可及，payg 实测 21 个全在账号内、可用。

所以到期后，**不存在任何一份能写死进 `info.json`、对免费用户开箱可用的音色列表**——这不是「到时候换一批 ID」能解决的，除非接班的新 Default 音色对免费档 API 开放（未证实）。

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
- ⚠️ 已**证伪**（原写「模型不支持某语言时传 language_code 会被忽略而非报错」）：实测模型对支持列表外的 `language_code` 直接回 **400 `unsupported_language`**，不是忽略。flash_v2（仅英语）+ zh 必 400，flash_v2_5 + af 也必 400。`supportLanguages()` 返回并集只保证 Bob 不崩，合成仍会失败。**已修**：`config.js` 按模型语言集 `MODEL_LANGUAGES` 门控，只下发模型确实支持的 `language_code`，其余留空让模型自行识别（实测 flash_v2 + 中文不带 code 仍能合成出「怪音」）
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
- `info.json` 模型菜单 4 项（flash_v2_5 / multilingual_v2 / v3 / flash_v2）= payg 账号 live `/v1/models` 里**非弃用 TTS 模型的全集**，一一对应；弃用的 turbo_v2_5 / turbo_v2 正确不在菜单，但留在 `config.js` 的 `MODELS` 里给老配置兜底正确上限（否则掉进 `FALLBACK_MODEL` 的 5000）。菜单语言数标注（32 / 29 / 70+ / 仅英语）与官方 overview 一致。overview 提到的 multilingual_v1 在 live API 已不返回，插件正确未收录。**结论：模型菜单无需改**
- `info.json` 21 个音色 = payg 账号 live `/v1/voices` 的 premade 全集，ID 与名称和当前 API 完全一致（如 `EXAVITQu4vr4xnSDxMaL`=Sarah、`hpp4J3VqNfWAUOO0d1Us`=Bella——是 API 现行名，不是早年 shuffle 前的旧名）。全部 premade = Default 音色 → 全部 2026-12-31 到期，菜单已逐项标注。**结论：音色菜单内容无需改，v1.0.3 只做了排序**
- v1.0.3 音色菜单排序规则：女声在前、男声其后、中性（River）最后；同性别内美式在前、英式其次、澳式最后；`__custom__` 仍在末尾。`defaultValue` 仍为 Bella（`hpp4J3VqNfWAUOO0d1Us`），不随排序改变。排序不删除任何 value，不触发 Bob「保留旧值」的坑
