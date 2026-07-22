#!/usr/bin/env python3
"""sync_catalog.apply_overlay 的单测，纯本地、不联网。

apply_overlay 是展示层规则的唯一入口，过去靠人眼盯。这里把它锁死：

- 过滤 DEPRECATED_MODELS（turbo_v2_5 / turbo_v2）出菜单
- 模型按 MODEL_ORDER 排序
- 退役音色追加「（2026-12-31 停用）」后缀，长期可用音色不加
- __custom__ 始终排到最末、且不加退役后缀
- 音色 menuValues 的既有顺序被保留（v1.0.3 起手工排过，不能再被 sort 冲掉）

直接 import scripts/sync_catalog.py，把它当库用。
"""

import copy
import importlib.util
import pathlib

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
        ["eleven_v3", "eleven_flash_v2", "eleven_flash_v2_5", "eleven_multilingual_v2"],
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
    sync.apply_overlay(info)
    check(info == snap, "重复套规则幂等（不叠加后缀、不改顺序）")


def run():
    tests = [
        test_deprecated_models_filtered,
        test_model_order,
        test_retiring_suffix,
        test_custom_last_and_unmarked,
        test_voice_order_preserved,
        test_overlay_idempotent,
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