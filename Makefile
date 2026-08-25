.PHONY: help setup test lint dev checkup evals schema

help:
	@echo "setup    安装依赖并生成 config.yaml/.env"
	@echo "test     跑单测（离线，不调模型）"
	@echo "lint     ruff 检查"
	@echo "dev      本地起网关 http://localhost:8001"
	@echo "checkup  模型体检 —— 立项第 0 周先跑这个"
	@echo "evals    跑回归评测集"
	@echo "schema   刷新 schema 缓存"

setup:
	cd backend && pip install -e '.[dev]'
	@test -f config.yaml || (cp config.example.yaml config.yaml && echo "已生成 config.yaml，请填写数据源")
	@test -f .env || (cp .env.example .env && echo "已生成 .env，请填写 DASHSCOPE_API_KEY")

test:
	cd backend && python -m pytest tests/ -q

lint:
	cd backend && ruff check src tests
	ruff check scripts

dev:
	cd backend && AGENTBASE_CONFIG=../config.yaml python -m uvicorn \
		agentbase.gateway.app:create_app --factory --reload --port 8001

checkup:
	python scripts/model_checkup.py

evals:
	python scripts/run_evals.py --cases evals/cases.yaml

schema:
	python -c "from pathlib import Path; import sys; sys.path.insert(0,'backend/src'); \
		from agentbase.runtime import Runtime; rt=Runtime(); print(rt.refresh_schema())"
