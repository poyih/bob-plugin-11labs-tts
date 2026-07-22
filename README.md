# ElevenLabs 语音合成 · Bob 插件

给 [Bob](https://bobtranslate.com) 用的 ElevenLabs TTS 插件，划词之后直接用 AI 语音朗读。

> ⚠️ 菜单里的 21 个音色将于 **2026-12-31** 全部失效，届时无现成替代方案。详见 [HANDOFF.md](HANDOFF.md)。

重写自 [cdpath/bob-plugin-elevenlabs](https://github.com/cdpath/bob-plugin-elevenlabs)（MIT，2025-03 后停更）。

## 相比上游改了什么

| | 上游 | 本插件 |
|---|---|---|
| HTTP 错误处理 | **不检查状态码**，401/422 的 JSON 错误体会被当成音频播放（表现为「点了没反应」） | 状态码 + ElevenLabs 的 `detail.status` 一起映射成 Bob 的错误类型，额度用完、Key 无效、音色不存在分别提示 |
| 模型 | 停在 2025-03，含已废弃的 `turbo_v2` / `turbo_v2_5` 和 v1 老模型 | v3 / Multilingual v2 / Flash v2.5 / Flash v2，默认 Flash v2.5（延迟约 75ms，适合即时朗读） |
| 音色 | 21 个写死的列表，过期就没法用新音色 | 21 个官方音色 + **自定义 Voice ID 输入框**，克隆音色和新音色都能用，列表过期也不影响 |
| 音色参数 | 硬编码 `stability 0.5 / similarity_boost 0.75`，**覆盖**你在官网给音色存的设置 | 默认不下发，沿用音色自带设置；需要时再逐项覆盖，另可调语速、风格、Speaker Boost |
| 音频格式 | 固定 128kbps | 可选 32 / 64 / 128 / 192 kbps |
| 语言 | 17 种 | 84 个 Bob 语言代码，并按模型能力决定是否下发 `language_code`（Multilingual v2 不支持该参数） |
| 长文本 | 直接发出去等 API 报错 | 按模型的字符上限提前拦截并说明 |
| 测试 | 无 | `make test`，用 macOS 自带的 jsc（Bob 同款 JavaScriptCore 引擎）跑 23 项断言，不联网、不消耗额度 |
| 发布 | Node 脚本 | 纯标准库 Python + Makefile，打 tag 自动发版 |

## 安装

从 [Releases](https://github.com/poyih/bob-plugin-11labs-tts/releases) 下载 `.bobplugin` 双击安装，或者本地构建：

```bash
make install
```

装好后在 Bob 的「服务」里添加「ElevenLabs 语音合成」，填 API Key 即可。API Key 在 [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) 创建，需要勾选 `text_to_speech` 权限。

## 设置项

| 选项 | 说明 |
|---|---|
| API Key | 密文输入，只发给 `api.elevenlabs.io` |
| 模型 | 默认 Flash v2.5。要更好的情感表现换 Multilingual v2 或 v3（更慢更贵） |
| 音色 | 21 个官方音色；选「▸ 使用下方填写的自定义 Voice ID」可用自己的音色 |
| 自定义 Voice ID | 填了就优先生效。在 elevenlabs.io 音色详情页复制 Voice ID |
| 音频格式 | 朗读场景 32~64kbps 通常够用，还能明显缩短等待 |
| 稳定性 / 相似度 / 风格 / 语速 / Speaker Boost | 默认「跟随音色自带设置」，即完全不覆盖官网上的配置 |

## 常见报错

| 提示 | 原因 |
|---|---|
| 当前订阅无法使用该音色（HTTP 402） | 免费订阅不能通过 API 使用音色库音色，而 ElevenLabs 的 Default 音色（Aria/Roger/Sarah 等）正属于音色库。改用「自定义 Voice ID」填你账号内的音色 |
| API Key 无效或缺少权限 | Key 填错，或者创建时没勾 `text_to_speech` |
| ElevenLabs 字符额度已用完 | 当月免费/订阅额度耗尽，去 [订阅页](https://elevenlabs.io/app/subscription) 看用量 |
| 音色不存在 | 自定义 Voice ID 写错，或那个音色不在当前账号下 |
| 文本长度超过上限 | v3 5,000 / Multilingual v2 10,000 / Flash v2 30,000 / Flash v2.5 40,000 字符 |
| 请求过于频繁 | 触发并发限制，稍等重试 |

## 开发

```bash
make test     # 语法检查 + jsc 单测
make pack     # 打包到 dist/
make install  # 打包并交给 Bob 安装
```

模型和音色列表会随 ElevenLabs 更新而过期，随手同步一下即可（会提示输入 API Key，不回显、不进 shell 历史）：

```bash
make sync
```

默认只补新增、保留已有标题；`make sync REPLACE=1` 用 API 返回的内容整体重写。

同步之后会自动套一遍**展示层规则**（定义在 `scripts/sync_catalog.py` 顶部）：

- 过滤 ElevenLabs 已标记 deprecated 的模型 —— `/v1/models` 仍会返回它们，不过滤就会被带回菜单
- 用中文短标题覆盖 API 的长英文描述
- 给退役名单上的音色加「2026-12-31 停用」标注，并把长期可用的排到前面
- 校验 `defaultValue` 还在菜单里，不在就改成第一项

只想重新套规则而不联网：

```bash
python3 scripts/sync_catalog.py --overlay-only
```

### 核实假设

文档会骗人（Bob 文档写了 `$data` 有 `length`，实测是 `undefined`，据此写的空音频防护形同虚设）。两处探针用来把假设打回原形：

**运行时探针** —— 插件每次加载时自动写一行到 Bob 日志，记录 `$data` 实际有哪些方法：

```bash
grep '11labs-tts.*runtime' ~/Library/Containers/com.hezongyidev.Bob/Data/Documents/MMKitLogs/MMLogs/Default/*.log | tail -1
```

**API 探针** —— 拿真实 Key 打一遍 ElevenLabs，逼出错误 `detail.status` 的真实字符串、各模型可用性、格式订阅门槛、`voice_settings` 能否部分下发、`language_code` 到底被忽略还是报错：

```bash
python3 scripts/verify_api.py            # 全量，约 30~40 credits
python3 scripts/verify_api.py --only status --only models
python3 scripts/verify_api.py --dry-run  # 只看会发什么，不联网
```

失败的请求不计费，成功的用 2 字符文本把成本压到最低。

发版：

```bash
git tag v1.0.1 && git push origin v1.0.1
```

GitHub Actions 会跑测试、把版本号写回 `src/info.json`、打包、算 sha256、追加 `appcast.json` 记录并创建 Release。Bob 靠 `appcast.json` 检查更新。

> 如果你的 GitHub 用户名不是 `poyih`，需要改三处：`src/info.json` 的 `homepage` / `appcast`、`scripts/release.py` 的 `--repo` 默认值。

## 结构

```
src/
  info.json   插件元信息与设置项
  main.js     tts / pluginValidate / supportLanguages
  config.js   API 地址、各模型字符上限与 language_code 支持、Bob↔ISO 语言映射
  icon.png    插件图标
scripts/
  test_plugin.js    jsc 测试（桩掉 $http / $data / $option）
  release.py        写版本号 → 打包 → sha256 → 更新 appcast
  sync_catalog.py   从 ElevenLabs 同步模型/音色到 info.json
```

## License

MIT。上游 [cdpath/bob-plugin-elevenlabs](https://github.com/cdpath/bob-plugin-elevenlabs) 同为 MIT。
