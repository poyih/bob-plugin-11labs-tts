#!/usr/bin/env python3
"""sync_catalog.apply_overlay 的单测，纯本地、不联网。

apply_overlay 是展示层规则的唯一入口，过去靠人眼盯。这里把它锁死：

- 过滤 DEPRECATED_MODELS（turbo_v2_5 / turbo_v2）出菜单
- 模型按 MODEL_ORDER 排序，Flash v2 从菜单隐藏并在同步后继续过滤
- 退役音色追加「（2026-12-31 停用）」后缀，长期可用音色不加
- __custom__ 始终排到最末、且不加退役后缀
- 音色 menuValues 的既有顺序被保留（v1.0.3 起手工排过，不能再被 sort 冲掉）
- 只有顺序变化时也会报告已修改，确保主程序真正写盘
- 合法的 __custom__ 默认值不会被误重置
- MODEL_TITLES 与 MODEL_ORDER 覆盖同一批模型，且真实 src/info.json 已满足展示层规则
- 菜单里的每个模型在 config.js 的三张能力表里都登记了（新增模型时最容易漏）

直接 import scripts/sync_catalog.py，把它当库用。
"""

import copy
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "sync_catalog", ROOT / "scripts" / "sync_catalog.py"
)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

CUSTOM = sync.CUSTOM_VOICE
SUFFIX = sync.RETIRING_SUFFIX


def _info(model_values, voice_values, voice_default=None):
    """造一份最小 info.json，两个 option 各带 menuValues。"""
    model_mv = [{"title": f"model-{v}", "value": v} for v in model_values]
    voice_mv = [{"title": f"voice-{v}", "value": v} for v in voice_values]
    return {
        "options": [
            {"identifier": "model", "menuValues": model_mv,
             "defaultValue": model_values[0] if model_values else None},
            {"identifier": "voice", "menuValues": voice_mv,
             "defaultValue": voice_default or (voice_values[0] if voice_values else None)},
        ]
    }


def _voice_titles(values):
    return [e["title"] for e in values if e["value"] != CUSTOM]


def _voice_ids(values):
    return [e["value"] for e in values if e["value"] != CUSTOM]


FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# 1. 过滤废弃模型 ----------------------------------------------------------
def test_deprecated_models_filtered():
    info = _info(
        ["eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3", "eleven_turbo_v2"],
        ["hpp4J3VqNfWAUOO0d1Us", CUSTOM],
    )
    sync.apply_overlay(info)
    model_ids = [e["value"] for e in info["options"][0]["menuValues"]]
    check("eleven_turbo_v2_5" not in model_ids, "turbo_v2_5 被过滤")
    check("eleven_turbo_v2" not in model_ids, "turbo_v2 被过滤")
    check("eleven_flash_v2_5" in model_ids and "eleven_v3" in model_ids,
          "flash_v2_5 / v3 保留")


# 2. 模型按 MODEL_ORDER 排序 ----------------------------------------------
def test_model_order():
    info = _info(
        ["eleven_v3", "eleven_v4_turbo", "eleven_flash_v2", "eleven_v3_conversational",
         "eleven_flash_v2_5", "eleven_v4", "eleven_multilingual_v2"],
        ["hpp4J3VqNfWAUOO0d1Us", CUSTOM],
    )
    sync.apply_overlay(info)
    model_ids = [e["value"] for e in info["options"][0]["menuValues"]]
    check(model_ids == sync.MODEL_ORDER, "模型按 MODEL_ORDER 重排")


# 3. 退役音色加后缀，长期可用音色不加 ---------------------------------------
def test_retiring_suffix():
    # 取一个在 RETIRING_VOICES 里、一个不在（伪造成自建音色）。
    retiring = next(iter(sync.RETIRING_VOICES))
    custom_made = "zzUserDesignedVoice0001"  # 不在退役名单里
    info = _info(["eleven_flash_v2_5"], [retiring, custom_made, CUSTOM])
    sync.apply_overlay(info)
    vals = {e["value"]: e["title"] for e in info["options"][1]["menuValues"]}
    check(vals[retiring].endswith(SUFFIX),
          f"退役音色 {retiring} 追加后缀")
    check(SUFFIX not in vals[custom_made],
          f"自建音色 {custom_made} 不加后缀")


# 4. __custom__ 始终最末、且不加后缀 ---------------------------------------
def test_custom_last_and_unmarked():
    info = _info(["eleven_flash_v2_5"],
                 [CUSTOM, "hpp4J3VqNfWAUOO0d1Us", "pNInz6obpgDQGcFmaJgB"])
    sync.apply_overlay(info)
    voice_mv = info["options"][1]["menuValues"]
    check(voice_mv[-1]["value"] == CUSTOM,
          "__custom__ 排在最末（即便输入时在前）")
    check(SUFFIX not in voice_mv[-1]["title"],
          "__custom__ 不加退役后缀")


# 5. 音色既有顺序被保留（不被标题字母序冲掉）-------------------------------
def test_voice_order_preserved():
    # 故意给一个反 MODEL_ORDER/字母序的输入，apply_overlay 不应重排音色。
    ids = [
        "pqHfZKP75CvOlQylNhV4",  # Bill
        "hpp4J3VqNfWAUOO0d1Us",  # Bella
        "SAz9YHcvj6GT2YYXdXww",  # River
        "EXAVITQu4vr4xnSDxMaL",  # Sarah
    ]
    info = _info(["eleven_flash_v2_5"], ids + [CUSTOM])
    sync.apply_overlay(info)
    out_ids = [e["value"] for e in info["options"][1]["menuValues"] if e["value"] != CUSTOM]
    check(out_ids == ids, "音色 menuValues 既有顺序原样保留（不被 sort 冲掉）")


# 6. 重复 apply_overlay 幂等 -----------------------------------------------
def test_overlay_idempotent():
    info = _info(["eleven_flash_v2_5"],
                 ["hpp4J3VqNfWAUOO0d1Us", "zzUserDesignedVoice0001", CUSTOM])
    sync.apply_overlay(info)
    snap = copy.deepcopy(info)
    changed = sync.apply_overlay(info)
    check(info == snap, "重复套规则幂等（不叠加后缀、不改顺序）")
    check(changed == 0, "幂等调用返回 0（没有变化）")


# 7. 仅模型顺序变化也必须报告修改 -----------------------------------------
def test_order_only_reports_change():
    info = _info(
        ["eleven_v3", "eleven_flash_v2_5"],
        ["zzUserDesignedVoice0001", CUSTOM],
    )
    for entry in info["options"][0]["menuValues"]:
        entry["title"] = sync.MODEL_TITLES[entry["value"]]
    changed = sync.apply_overlay(info)
    check(changed == 1, "只有模型重排时 apply_overlay 也返回非零")


# 8. 仅把 __custom__ 移到末尾也必须报告修改 -------------------------------
def test_custom_move_only_reports_change():
    info = _info(
        ["eleven_flash_v2_5"],
        [CUSTOM, "zzUserDesignedVoice0001"],
    )
    info["options"][0]["menuValues"][0]["title"] = sync.MODEL_TITLES["eleven_flash_v2_5"]
    changed = sync.apply_overlay(info)
    check(changed == 1, "只有 __custom__ 位置变化时也返回非零")


# 9. 默认值修复保留合法 __custom__ -----------------------------------------
def test_custom_default_preserved():
    info = _info(
        ["eleven_flash_v2_5"],
        ["zzUserDesignedVoice0001", CUSTOM],
        voice_default=CUSTOM,
    )
    changed = sync.repair_defaults(info)
    check(changed == 0, "合法的 __custom__ 默认值不被重置")
    check(info["options"][1]["defaultValue"] == CUSTOM, "__custom__ 默认值保持不变")


# 10. 失效默认值修到第一个非自定义项 ---------------------------------------
def test_missing_default_repaired():
    info = _info(
        ["eleven_flash_v2_5"],
        [CUSTOM, "zzUserDesignedVoice0001"],
        voice_default="goneVoice",
    )
    changed = sync.repair_defaults(info)
    check(changed == 1, "失效默认值会被报告为修改")
    check(info["options"][1]["defaultValue"] == "zzUserDesignedVoice0001",
          "失效默认值优先修到第一个非自定义音色")


# 11. 标题表 / 顺序表 / 真实 info.json 三者一致 ---------------------------
def test_real_info_consistent():
    check(set(sync.MODEL_TITLES) == set(sync.MODEL_ORDER),
          "MODEL_TITLES 与 MODEL_ORDER 覆盖同一批模型")
    with sync.INFO.open(encoding="utf-8") as fp:
        info = json.load(fp)
    snap = copy.deepcopy(info)
    changed = sync.apply_overlay(info)
    check(changed == 0 and info == snap,
          "真实 src/info.json 已满足展示层规则（顺序、标题、__custom__ 位置）")
    model_ids = [e["value"] for e in sync.option_by_id(info, "model")["menuValues"]]
    check(model_ids == sync.MODEL_ORDER, "真实 info.json 的模型菜单与 MODEL_ORDER 完全一致")


# 12. 菜单里的模型在 config.js 三张能力表里都有登记 ------------------------
def test_config_tables_cover_menu():
    tables = runtime_tables(sync.CATALOG_PATH)
    for table in ("MODELS", "MODEL_LANGUAGES", "MODEL_SETTINGS"):
        missing = [mid for mid in sync.MODEL_ORDER if mid not in tables[table]]
        check(not missing,
              f"config.js {table} 登记了菜单里的全部模型"
              + (f"（缺 {', '.join(missing)}）" if missing else ""))


def runtime_tables(catalog_path):
    jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
    code = ('globalThis.exports={};load(' + json.dumps(str(catalog_path)) + ');var c=exports;'
            'globalThis.require=function(){return c;};globalThis.exports={};load("src/config.js");'
            'print(JSON.stringify({MODELS:exports.MODELS,MODEL_LANGUAGES:exports.MODEL_LANGUAGES,MODEL_SETTINGS:exports.MODEL_SETTINGS}));')
    return json.loads(subprocess.run([jsc, "-e", code], cwd=ROOT, check=True, capture_output=True, text=True).stdout)


def test_metadata_refresh_reaches_runtime_and_keeps_policies():
    original = sync.model_catalog.read()
    updated, unknown = sync.model_catalog.refresh(original, [
        {"model_id": "eleven_flash_v2_5", "maximum_text_length_per_request": 12000,
         "max_characters_request_free_user": 1, "max_characters_request_subscribed_user": 2,
         "languages": [{"language_id": "zh"}, {"language_id": "en"}], "can_use_style": False, "can_use_speaker_boost": True},
        {"model_id": "eleven_multilingual_v2", "languages": [{"language_id": "zh"}]},
        {"model_id": "eleven_v4_turbo", "maximum_text_length_per_request": 99999, "can_use_style": True, "can_use_speaker_boost": True},
        {"model_id": "eleven_future", "can_do_text_to_speech": True},
    ])
    check(unknown == ["eleven_future"] and "eleven_future" not in updated, "未知模型先报告待核验，不进入目录或菜单")
    check(original["eleven_flash_v2_5"]["charLimit"] == 40000, "元数据转换不修改输入目录")
    with tempfile.TemporaryDirectory() as temp:
        path = pathlib.Path(temp) / "models.js"
        path.write_text(sync.model_catalog.render(updated))
        tables = runtime_tables(path)
    check(tables["MODELS"]["eleven_flash_v2_5"]["charLimit"] == 12000,
          "运行时使用同步的新上限，不使用已弃用的 free/subscribed 字段")
    check(tables["MODEL_LANGUAGES"]["eleven_flash_v2_5"] == ["en", "zh"] and
          tables["MODEL_SETTINGS"]["eleven_flash_v2_5"]["use_speaker_boost"] is True,
          "同步的语言和参数能力实际进入运行时")
    check(tables["MODEL_LANGUAGES"]["eleven_multilingual_v2"] == [] and
          tables["MODEL_SETTINGS"]["eleven_v4_turbo"]["style"] is False and
          tables["MODELS"]["eleven_v4_turbo"]["charLimit"] == 10000,
          "API 元数据更新不覆盖自动识别、v4 参数及 Turbo 本地上限策略")


def test_models_sync_dry_run_and_atomic_failure():
    with tempfile.TemporaryDirectory() as temp:
        info_path = pathlib.Path(temp) / "info.json"
        catalog_path = pathlib.Path(temp) / "models.js"
        info_path.write_bytes(sync.INFO.read_bytes())
        catalog_path.write_bytes(sync.CATALOG_PATH.read_bytes())
        original_info, original_catalog = info_path.read_bytes(), catalog_path.read_bytes()
        upstream = [{"model_id": "eleven_v3", "maximum_text_length_per_request": 6000}]
        with patch.object(sync, "INFO", info_path), patch.object(sync, "CATALOG_PATH", catalog_path), \
             patch.object(sync, "api_get", return_value=upstream), patch.dict(sync.os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             contextlib.redirect_stdout(io.StringIO()):
            sync.main(["--models-only", "--dry-run"])
            check(info_path.read_bytes() == original_info and catalog_path.read_bytes() == original_catalog,
                  "dry-run 不写入菜单或模型能力目录")
            original_writer = sync.model_catalog.write_text_atomic
            def fail_info(path, text):
                if pathlib.Path(path) == info_path:
                    raise OSError("simulated info write failure")
                return original_writer(path, text)
            info = json.loads(info_path.read_text())
            info["summary"] = "changed"
            updated, _ = sync.model_catalog.refresh(sync.model_catalog.read(catalog_path), upstream)
            with patch.object(sync.model_catalog, "write_text_atomic", side_effect=fail_info):
                try:
                    sync.write_catalog_and_info(updated, info)
                    check(False, "第二个文件失败应报告错误")
                except OSError:
                    pass
            check(info_path.read_bytes() == original_info and catalog_path.read_bytes() == original_catalog,
                  "菜单写入失败会恢复能力目录，不留下两个版本")
            sync.main(["--models-only"])
        check(sync.model_catalog.read(catalog_path)["eleven_v3"]["charLimit"] == 6000,
              "实际同步持久化能力目录，即使模型菜单没有变化")
        ids = [e["value"] for e in sync.option_by_id(json.loads(info_path.read_text()), "model")["menuValues"]]
        check(ids == sync.MODEL_ORDER, "元数据响应缺少其他模型时保留已核验菜单")


def test_replace_voices_protects_presets_and_default():
    info = json.loads(sync.INFO.read_text())
    option = sync.option_by_id(info, "voice")
    protected = [{"title": title, "value": vid} for vid, title in sync.SUCCESSOR_VOICES]
    fresh = [{"title": "Sarah", "value": "EXAVITQu4vr4xnSDxMaL"}, {"title": "Own", "value": "ownVoice"}]
    option["menuValues"], _, stale = sync.merge(option, fresh, True, CUSTOM, protected)
    sync.apply_overlay(info)
    sync.repair_defaults(info)
    ids = [e["value"] for e in option["menuValues"]]
    check(set(sync.SUCCESSOR_TITLES).issubset(ids) and ids[-1] == CUSTOM, "整体替换后保留全部 19 个接班音色和自定义项")
    check(option["defaultValue"] == "WQP7cQUF5aAS6Axh5yaa" and not stale, "默认接班音色不被退役音色替换，预设不误报账户已删除")
    check("ownVoice" in ids and len(ids) == len(set(ids)), "账户音色可加入且菜单无重复")
    damaged = _info(["eleven_flash_v2_5"], ["EXAVITQu4vr4xnSDxMaL", CUSTOM])
    voice = sync.option_by_id(damaged, "voice")
    voice["menuValues"], _, _ = sync.merge(voice, fresh, True, CUSTOM, protected)
    sync.repair_defaults(damaged)
    check(voice["defaultValue"] in sync.SUCCESSOR_TITLES, "旧默认值属于退役音色时修到接班音色")


def test_invalid_metadata_does_not_rewrite_catalog():
    original = sync.model_catalog.read()
    for fields in ({"maximum_text_length_per_request": True}, {"can_use_style": "false"}, {"languages": [{}]}):
        try:
            sync.model_catalog.refresh(original, [{"model_id": "eleven_v3", **fields}])
            check(False, "非法 API 元数据应拒绝")
        except ValueError:
            pass
    check(original == sync.model_catalog.read(), "非法 API 元数据不会修改原目录")


def run():
    tests = [
        test_deprecated_models_filtered,
        test_model_order,
        test_retiring_suffix,
        test_custom_last_and_unmarked,
        test_voice_order_preserved,
        test_overlay_idempotent,
        test_order_only_reports_change,
        test_custom_move_only_reports_change,
        test_custom_default_preserved,
        test_missing_default_repaired,
        test_real_info_consistent,
        test_config_tables_cover_menu,
        test_metadata_refresh_reaches_runtime_and_keeps_policies,
        test_models_sync_dry_run_and_atomic_failure,
        test_replace_voices_protects_presets_and_default,
        test_invalid_metadata_does_not_rewrite_catalog,
    ]
    for t in tests:
        print(f"── {t.__name__}")
        t()
    print()
    if FAILS:
        print(f"FAIL ({len(FAILS)}):")
        for msg in FAILS:
            print("  - " + msg)
        return 1
    print(f"ALL PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
