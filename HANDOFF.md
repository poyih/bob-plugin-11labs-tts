# 待验证与待办

这份文档给接手的人看，不需要任何上下文。

## 这是什么

Bob（macOS 翻译软件）的 ElevenLabs 语音合成插件。当前 v1.0.7，功能可用。

问题不在功能，在于**一批结论只有文档依据、没有真机验证**。这个项目已经因此摔过三次：

1. 沿用了一份 2025 年的音色列表 → 默认音色对当前账号必然报 402
2. Bob 文档写 `$data` 有 `length` 属性，实测是 `undefined` → 据此写的空音频防护形同虚设
3. 把 HTTP 400 并进「订阅限制」分支 → 文本超长和音色 ID 写错都被误报成订阅问题

**所以本文档的规矩是：改任何一条之前，先按「怎么验」那栏跑出结果。文档说的不算数。**

## 现成的工具

```bash
make test                              # 插件 67 项检查 + sync/tools 各 10 组测试，全程离线
python3 scripts/verify_api.py          # 拿真实 Key 打 ElevenLabs，6 组探针，约 30~40 credits
python3 scripts/verify_api.py --dry-run
python3 scripts/resolve_voices.py --offline   # 官方 19 个接班音色 ID 对照表（不联网、不要 Key）
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
- **结论**：style / speaker_boost 在 v3（以及 flash_v2_5 / flash_v2 / turbo）上被**静默忽略**，仅 multilingual_v2 真正生效。`info.json` 的两条说明已注明范围，`buildVoiceSettings()` 也会按模型能力过滤，不再把已知无效字段发给 API。
- **附带未定已解**：v3 的 stability 接受任意 [0,1] 值，无离散限制。

### 3. pluginValidate 的端点选择存在系统性误报 — ✅ 已改

- **实测**：本 Key 下 `/models`、`/voices`、`/user`、`/user/subscription` 全 200（payg 全权限），无法直接复现「受限 Key 打 /models 失败」。但缺权限的错误形态已在既有真机记录里：旧 401 `missing_permissions` / 新 403 `insufficient_permissions`。
- **已改**：不再用需要模型读取权限的 `GET /models`。验证会向当前音色发一个**单字符真实 TTS 请求**，只有收到音频才通过，因此能无歧义地检查 `text_to_speech` scope、当前音色、模型和格式；每次验证消耗 1 个字符。`missing_permissions` / `insufficient_permissions` 明确失败，成功响应也不再夹带 `error` 字段。
- **仍建议直验**：用一把只勾 `text_to_speech` 的受限 Key 跑 Bob 的“验证”，确认线上错误形态仍与现有测试桩一致。

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
not_logged_in                HTTP 401  ← 无密钥打 /v1/shared-voices?search=
                                         （code=unauthorized、type=authentication_error）
```

旧猜的 6 个本次是否出现：`voice_not_found` ✓、`invalid_api_key` ✓；`voice_does_not_exist` 未出现（实际是 `voice_not_found`）；`quota_exceeded`/`detected_unusual_activity`/`missing_permissions` 本次未触发（payg 全权限、额度未尽），前两者保留分派、后者已按 P0#3 处理。

本次共消耗约 6000 字符额度（payg，含一次 10001 字超限探针的竞态消耗 ~2748）。

---

## P2 — Bob 侧未定

这几条查不到官方说法，只能靠观察。不紧急，但踩到会很费时间。

- **`$data.length` 为什么是 undefined**：**已实测确认**（2026-07-23 加载时 `runtime` 日志行）：`$data.isData=function`、`sample.length=undefined`、`sample.toBase64=function`、`sample.toUTF8=function`、`Date.now=function`。文档写了 `length` 但实际没有；`Date.now` 可用（`main.js` 里的计时没问题）。插件已用 base64 字符串长度绕开，仅作知识缺口存档。
- **`supportLanguages()` 的调用时机**：每次合成都调，还是安装时调一次后缓存？当前返回静态并集，怎样都安全。但如果将来想按模型动态返回语言列表，这条必须先搞清楚。
- **Bob 保留旧配置值**：已实测确认——把某个 value 从 `info.json` 的 `menuValues` 里删掉后，Bob 仍会把用户此前保存的旧值发出去，界面却显示成菜单第一项。这个行为让排查极易走偏（本项目为此浪费了大量时间）。**有没有官方推荐的强制重置做法**（比如换 option identifier）未查实。目前的应对是让报错带上实际发出的 Voice ID。
- **超时余量 — 已修**：`pluginTimeoutInterval()` 仍为 60 秒，单次 `$http.request` 改为 50 秒，保证插件能先把网络错误交回 Bob。
- **Legacy 误拦截 — 已修**：Aria/Rachel/Charlotte 不再被客户端预拦截，只写警告并交给 API 按当前账户判定；付费/payg 可继续使用，免费档若被拒则展示 API 的实际错误。

---

## 时间炸弹：2026-12-31

> **v1.0.6 已落地**：`src/info.json` 音色菜单已换成下方 19 个官方接班音色，默认值
> `WQP7cQUF5aAS6Axh5yaa`（Elara）。21 个退役音色移出菜单，但 `config.js` 的
> `RETIRING_VOICES` 保留它们 → 老用户因 Bob「保留旧配置」仍会发老音色时，`main.js`
> 截止日前写日志提示到期与接班音色；从 2027-01-01 UTC 起明确拦截旧 ID，避免继续发出注定失败的请求。
> `sync_catalog.py` 默认**不再同步音色**（`/v1/voices` 只返回退役的 21 个，同步会把
> 菜单打回原形），需要时用 `--sync-voices`。
> **payg 实测 19/19 可用**（2026-07-23，`resolve_voices.py --probe`）：19 个 ID 逐个
> 实打 2 字符全部 HTTP 200，返回 1794~2944 bytes 合法 mp3。**该结论的边界要说清**：
> 它证明这 19 个 ID 是活的、且本账号能合成，**不证明它们是「正确的接班音色」**——
> 拿一个搜错的同名音色去打同样会 200。ID 正确性的依据始终是官方表自身的超链接。
> **仍未实证**：对**免费档**能否合成。API 字段 `free_users_allowed=True`，但无免费档
> Key 未实测。若该字段不准，免费用户会立刻全坏 —— 退路是自定义 Voice ID。


**官方原文**（elevenlabs.io/docs/overview/capabilities/voices）：
- 「All our Default voices will expire on December 31, 2026, and they will no longer be accessible after this date.」
- 「Our Default voices are being replaced with new voices that you will be able to use in perpetuity.」
- 「Voice Library voices are not available via the API to free tier users.」

**历史状态（v1.0.5 及更早）**：`src/info.json` 菜单里的 21 个音色全部是 `category=premade`（= Default 音色），全部在这一天失效，包括当时默认的 Bella。该结论已用 payg 账号 live `/v1/voices` 逐条核对；v1.0.6 起当前菜单已替换为上面的 19 个接班音色。

**到期政策不区分档位（2026-07-23 三路独立核验确认）**：官方原文是「**All** our Default voices will expire」，**没有付费豁免**。付费(payg)账号和免费账号一样，21 个 Default 音色在 2026-12-31 后预期停止可用。「Voice Library voices are not available via the API to free tier users」是**另一回事**（音色库音色对免费档的 API 限制），不是 Default 到期政策。

**到期后旧 voice_id 会怎样——官方只说「no longer accessible」，未说明失败模式**。「自动路由到替换音色」是官方为 **Legacy 音色**（已被 fully deprecated、从所有产品移除的旧类别）描述的行为，**不是** Default 到期的官方行为，不要外推。Default 到期后旧 ID 预期直接停止工作（具体是 404/400 还是静默失败，官方未说）。

**「accounts created before March 2026」是官方说法（已核验）**：出自 help.elevenlabs.io Help Center 的「What are Default voices?」/「How do I access ElevenLabs Default voices?」两篇文章（direct fetch 被 Zendesk 403，逐字引文取自官方域名搜索 snippet）。含义是：**2026 年 3 月之后注册的账号现在就可能根本拿不到 Default 音色**——这是与 2026-12-31 到期**并列的另一条限制**。本 payg 账号早于该日期（21 个全在），不受影响。

**接班音色：官方有名字表，没有 ID 表（2026-07-23 读一手原文确认）**

官方帮助文章「What are Default voices?」确有一张 **19 行替换表**，但**只有名字、没有 voice_id**：Roger→Darian - Warm Grounded Storyteller、Sarah→Talia - Warm Soft Guide、Chris→Caleb - Trusted Guide……（全表见 `scripts/resolve_voices.py` 顶部的 `REPLACEMENTS`，逐字抄录）。**Bella 与 Adam 不在表内 —— 官方没给这两个安排接班音色。**

出处说明：help.elevenlabs.io 直连被 Zendesk **403**，逐字原文取自 docs 镜像 `/docs/help-center/product/voice-customization/my-voices/what-are-default-voices.md`（**这比之前的「搜索 snippet」出处更硬**，第 138 段那两句引文同样已由该镜像逐字复核）。另注：该文**没有**「in perpetuity」那句，那句在 `capabilities/voices` 页。

**⚠️ 网上流传着几份新音色 ID 表，一律不要采信，以下方官方链接提取的为准。** 那些表大多是「拿官方公告里的名字去 API 搜、挑一个同名的」得来的 —— 音色库里同名音色极多（Jade 有 10 个、Eddie 有 5 个），搜错是常态。同一批传播里还夹带着「接班音色是音色库音色、免费档 API 用不了」之类的说法，同样没有依据。

本项目也一度采信过这类二手说法，根因是一轮 verify 工作流只跑完取证、证伪阶段被中断 —— **未经证伪的取证输出不能当结论**。

**ID 已拿到，出处是官方表自己的超链接（2026-07-23）。** 表格正文只有名字，但**每个新音色名本身是超链接**，指向 `r.contact.elevenlabs.io` 跟踪页；该页返回 **HTTP 405 且不发 Location 头**（`curl -L` 跟不到底，必须读正文），正文的 meta-refresh 目标就是 `https://elevenlabs.io/app/voice-library?search=<voice_id>`。19 个全部提取成功，已固化在 `scripts/resolve_voices.py` 的 `REPLACEMENTS`：

| 旧 | 新 | voice_id |
|---|---|---|
| Roger | Darian | `gOupLcAkjEnguROwi4oS` |
| Sarah | Talia | `OZ0L6eISlOejga3XjDFt` |
| Laura | Elara | `WQP7cQUF5aAS6Axh5yaa` |
| Charlie | Baxter | `jSuBIjxMKhqIfb0wCK1F` |
| George | Eldrin | `6WwXjDDEMyNmFG95zycZ` |
| Callum | Kellan | `cymHWdiF8WjUCg6vvFxx` |
| River | Elowen | `dvbL7qkNGZY1IqPGZAjM` |
| Harry | Kaelen | `10NkTYmU7tSz3Kkl3Lex` |
| Liam | Lawrence | `ktkP7Nsj67dw2zcplQYt` |
| Alice | Alicia | `BFd5oBc2DDna33pSi4Gf` |
| Matilda | Maisie | `QtY3JBOUKEB5xzrRfOKc` |
| Will | Warren | `7QN34D2r3hCNwbOYIeK0` |
| Jessica | Jade | `g7LVvkPWALzPxOQbF6OE` |
| Eric | Eddie | `l7kNoIfnJKPg7779LI2t` |
| Chris | Caleb | `AaOhDHYJ1XLZk74lXhdE` |
| Brian | Sawyer | `8dEUmyPMdDdK91vboYih` |
| Daniel | Finley | `fnYMz3F5gMEDGMWcH1ex` |
| Lily | Florence | `22N9cF8z0o7y23njdyaY` |
| Bill | Wyatt | `FrS6cKLB1wg4WYgPa9GW` |

离线查看：`python3 scripts/resolve_voices.py --offline`（不联网、不需要 Key）。

**⚠️ 千万别改用「按名字去音色库搜」来取这些 ID。** 实测 payg 账号搜 `Kellan`：只返回一个候选 `ogqEVaDb8zHocDItWo7S`（"Kellan - Resonant, Smooth and Confident"，`cat=high_quality`、`free=True` —— 信号看起来完全干净、毫无歧义），**但它不是官方那个**。按名字搜会静悄悄挑错且不给任何警示。其余 18 个两法一致。`resolve_voices.py` 默认会做这个交叉校验并对分歧告警。

**教训：二手说法一律不算数，只有官方链接 + API 实测算数。** 流传的表偶尔碰巧对，但你无从分辨对错——本项目实测的 Kellan 分歧就是证据。

另有两处官方表文字与音色库现名不符，**ID 以官方链接为准**：Eddie（表写 "Helpful and Comforting"，库中现名 "Natural and Helpful"）、Finley（库中名多一个空格）。

**接班音色对免费档大概率可用（未实测）**：payg 账号搜到的官方对应音色，元数据均为 `cat=high_quality`、`free_users_allowed=True`、无计费倍率。这与文档那句「Voice Library voices are not available via the API to free tier users」冲突 —— 合理解释是音色库逐个音色带 `free_users_allowed` 标志，文档那句是过度简化。**但这是 API 字段，不是免费档真实合成过**，无免费档 Key 未能实证。另注意同名冒牌里有 `rate=2.0` 的（Darian-Velvety、Jade-Calm、Jade-Millennial、Wyatt 裸名），选错**双倍计费**。

**payg 实测 19/19 全部 200**（2026-07-23，`--probe`，返回 1794~2944 bytes 合法 mp3），含库搜会搞错的 Kellan `cymHWdiF8WjUCg6vvFxx`。换个账号复验：`python3 scripts/resolve_voices.py --probe`（约 1~2 credits/个）。

**付费 vs 免费的迁移难度不同（关键结论）**：
- 付费档（含 payg）：到期后迁移**容易**——付费档可通过 API 使用 Voice Library 音色（实测 Aria 200）和接班的新 Default 音色，换一批 voice ID 填进「自定义 Voice ID」或更新菜单即可继续用。
- 免费档：到期后迁移**难**——音色库音色对免费档 API 禁用，没有现成、开箱可用的替换列表。免费档可能的出路见下文「唯一可能的出路」。

所以：对**付费用户**，2026-12-31 不是末日，到期前换音色 ID 即可；对**免费用户**，到期后**不存在任何一份能写死进 `info.json`、开箱可用的音色列表**——除非接班的新 Default 音色对免费档 API 开放（未证实），或 Voice Design 音色能走 API（见下文，未验证）。

### 唯一可能的出路，需要验证

免费档有 3 个 Voice Design 音色槽。用 Voice Design 生成的音色（`category=generated`）属于用户自己的账号，理论上不受音色库限制。

**但「免费档 Voice Design 生成的音色能否通过 API 合成」从未验证过。** 这条的价值最高——它决定 2027 年免费用户还有没有任何可用路径，也决定 README 里的自救指南能不能写出来。

验证方法：在 elevenlabs.io 用 Voice Design 生成并保存一个音色，拿到 voice_id，然后

```bash
python3 scripts/verify_api.py --only status --voice-id <新音色的ID>
```

### 建议的时间表

- **现在**：README 顶部写明这个截止日，别让用户以为是长期方案
- **11 月**：用免费档 Key 复验当前 19 个接班音色及 Voice Design 音色；按实测结果更新自救说明。不要在没有证据时把默认值强行切到 `__custom__`

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
- Aria / Rachel / Charlotte 是 **Legacy 音色**，会被自动路由到音色库音色；payg 实测可用，免费档可能 402。插件不预拦截，由 API 按账户判定
- 免费档的判据是**音色来源是不是音色库**，与「在不在你账号里」无关
- `info.json` 模型菜单 4 项（flash_v2_5 / multilingual_v2 / v3 / flash_v2）= payg 账号 live `/v1/models` 里**非弃用 TTS 模型的全集**，一一对应；弃用的 turbo_v2_5 / turbo_v2 正确不在菜单，但留在 `config.js` 的 `MODELS` 里给老配置兜底正确上限（否则掉进 `FALLBACK_MODEL` 的 5000）。菜单语言数标注（32 / 29 / 70+ / 仅英语）与官方 overview 一致。overview 提到的 multilingual_v1 在 live API 已不返回，插件正确未收录。**结论：模型菜单无需改**
- **历史记录**：v1.0.3 的 21 个音色是当时 payg 账号 live `/v1/voices` 的 premade 全集，并按女声/男声/中性与口音人工排序；当时默认值为 Bella。该菜单已在 v1.0.6 被 19 个官方接班音色替换，不能再当作当前状态
