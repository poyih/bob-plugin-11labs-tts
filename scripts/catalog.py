"""读取 / 更新与 Bob 共用的模型目录，不执行 JavaScript。"""

import copy
import json
import os
from pathlib import Path
import re
import tempfile

CATALOG = Path(__file__).resolve().parent.parent / "src" / "model_catalog.js"
HEADER = "// 模型菜单与运行时能力的共同目录。scripts/catalog.py 按 JSON 读取此模块。\n// 来源：v1.0.11 已维护的 API 元数据及官方接口核对；策略字段由项目维护。\n"


def validate(catalog):
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("模型目录必须是非空对象")
    for model_id, item in catalog.items():
        if not re.fullmatch(r"eleven_[a-z0-9_]+", model_id) or not isinstance(item, dict):
            raise ValueError(f"模型目录含非法模型 {model_id!r}")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ValueError(f"{model_id} 缺少菜单标题")
        for field in ("order", "charLimit"):
            value = item.get(field)
            if type(value) is not int or value < (1 if field == "charLimit" else 0):
                raise ValueError(f"{model_id}.{field} 必须是合法整数")
        if item.get("transport") not in ("http", "dialogueWebSocket"):
            raise ValueError(f"{model_id} 尚未登记可用的传输协议")
        for field in ("hidden", "deprecated", "sendLanguageCode"):
            if type(item.get(field)) is not bool:
                raise ValueError(f"{model_id}.{field} 必须是布尔值")
        for field in ("localLimit", "englishOnly"):
            if field in item and type(item[field]) is not bool:
                raise ValueError(f"{model_id}.{field} 必须是布尔值")
        langs = item.get("languages")
        if langs is not None and (not isinstance(langs, list) or any(not isinstance(v, str) or not v for v in langs)):
            raise ValueError(f"{model_id}.languages 必须是语言数组或 null")
        if "settings" not in item:
            raise ValueError(f"{model_id} 缺少已核验的参数能力表")
        for field in ("settings", "settingsOverrides"):
            values = item.get(field, {})
            if not isinstance(values, dict) or any(type(v) is not bool for v in values.values()):
                raise ValueError(f"{model_id}.{field} 必须是布尔能力表")
        overrides = item.get("languageOverrides", {})
        if not isinstance(overrides, dict) or any(not isinstance(v, str) or not v for v in overrides.values()):
            raise ValueError(f"{model_id}.languageOverrides 必须是语言代码映射")
    return catalog


def read(path=CATALOG):
    source = Path(path).read_text(encoding="utf-8")
    match = re.search(r"var MODEL_CATALOG = (\{.*\n\});\s*exports\.MODEL_CATALOG = MODEL_CATALOG;\s*$", source, re.S)
    if not match:
        raise ValueError("模型目录必须使用约定的 JSON 模块格式")
    return validate(json.loads(match.group(1)))


def render(catalog):
    validate(catalog)
    return HEADER + "var MODEL_CATALOG = " + json.dumps(catalog, ensure_ascii=False, indent=4) + ";\n\nexports.MODEL_CATALOG = MODEL_CATALOG;\n"


def visible_entries(catalog):
    return [{"title": item["title"], "value": model_id}
            for model_id, item in sorted(catalog.items(), key=lambda pair: pair[1]["order"])
            if not item["hidden"] and not item["deprecated"]]


def refresh(catalog, models):
    """只同步已登记接口的模型；未知模型列为待核验，不自动投入菜单。"""
    if not isinstance(models, list):
        raise ValueError("/models 响应必须是数组")
    result, unknown, seen = copy.deepcopy(catalog), [], set()
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("model_id"), str):
            raise ValueError("/models 含有缺少 model_id 的记录")
        model_id = model["model_id"]
        if model_id in seen:
            raise ValueError(f"/models 重复返回 {model_id}")
        seen.add(model_id)
        if model_id not in result:
            if model.get("can_do_text_to_speech") is True:
                unknown.append(model_id)
            continue
        item = result[model_id]
        # 新字段是实际限制；旧 free/subscribed 字段已被官方标为不再强制执行。
        limit = model.get("maximum_text_length_per_request")
        if limit is not None:
            if type(limit) is not int or limit <= 0:
                raise ValueError(f"{model_id} 返回非法字符上限")
            if not item.get("localLimit"):
                item["charLimit"] = limit
        if model.get("languages") is not None:
            languages = model["languages"]
            if not isinstance(languages, list) or any(
                not isinstance(v, dict) or not isinstance(v.get("language_id"), str) or not v["language_id"] for v in languages
            ):
                raise ValueError(f"{model_id} 返回非法语言列表")
            item["languages"] = sorted({v["language_id"] for v in languages})
        for remote, local in (("can_use_style", "style"), ("can_use_speaker_boost", "use_speaker_boost")):
            if remote in model and model[remote] is not None:
                if type(model[remote]) is not bool:
                    raise ValueError(f"{model_id}.{remote} 必须是布尔值")
                item["settings"][local] = model[remote]
    validate(result)
    return result, unknown


def write_text_atomic(path, text):
    path = Path(path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as fp:
            temp_name = fp.name
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(temp_name, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temp_name, path)
    except BaseException:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
