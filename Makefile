JSC     := /System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc
NAME    := bob-plugin-11labs-tts
VERSION := $(shell python3 -c 'import json; print(json.load(open("src/info.json"))["version"])')
BUNDLE  := dist/$(NAME)-$(VERSION).bobplugin
PYFILES := $(sort $(wildcard scripts/*.py))

.DEFAULT_GOAL := help

help: ## 显示可用命令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

lint: ## 检查全部 JS/Python 语法和 JSON 格式
	@$(JSC) -e 'var fs=["src/main.js","src/config.js","scripts/test_plugin.js"]; for (var i=0;i<fs.length;i++){ checkSyntax(fs[i]); print("syntax ok  "+fs[i]); }'
	@PYTHONDONTWRITEBYTECODE=1 python3 -c 'from pathlib import Path; fs=[Path(f) for f in "$(PYFILES)".split()]; [compile(p.read_text(encoding="utf-8"), str(p), "exec") for p in fs]; [print("syntax ok  "+str(p)) for p in fs]'
	@python3 -c 'import json; [print("json ok    "+f) for f in ["src/info.json","appcast.json"] if json.load(open(f)) is not None]'

test: lint test-sync test-tools ## 跑全部离线单测（不联网、不消耗额度）
	@out=$$($(JSC) scripts/test_plugin.js) ; \
	 echo "$$out" ; \
	 echo "$$out" | grep -q '^ALL PASS' || { echo "测试未通过"; exit 1; }

test-sync: ## 跑 sync_catalog 展示层规则单测（不联网）
	@python3 scripts/test_sync.py

test-tools: ## 跑发布和 API 核验工具单测（不联网）
	@PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_tools.py

pack: test ## 打包成 .bobplugin
	@rm -rf dist && mkdir -p dist
	@cd src && zip -qr "../$(BUNDLE)" . -x '*.DS_Store'
	@echo "$(BUNDLE)"
	@shasum -a 256 "$(BUNDLE)"

install: pack ## 打包并让 Bob 安装
	@open "$(BUNDLE)"

sync: ## 从 ElevenLabs 同步模型/音色到 info.json（会提示输入 Key；REPLACE=1 整体重写）
	@python3 scripts/sync_catalog.py $(if $(REPLACE),--replace) $(SYNC_ARGS)

clean: ## 清理构建产物
	@rm -rf dist

.PHONY: help lint test test-sync test-tools pack install sync clean
