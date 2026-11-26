PYTHON ?= ./venv/bin/python
PROTO_DIR = proto
PY_GEN_DIR = src/tts_service/transport/grpc/gen
GO_OUT_DIR ?= ./gen/go

.PHONY: proto-python proto-go test run run-http

proto-python:
	mkdir -p $(PY_GEN_DIR)/tts/v1
	$(PYTHON) -m grpc_tools.protoc \
		-I $(PROTO_DIR) \
		--python_out=$(PY_GEN_DIR) \
		--pyi_out=$(PY_GEN_DIR) \
		--grpc_python_out=$(PY_GEN_DIR) \
		$(PROTO_DIR)/tts/v1/tts.proto
	touch $(PY_GEN_DIR)/__init__.py $(PY_GEN_DIR)/tts/__init__.py $(PY_GEN_DIR)/tts/v1/__init__.py
	$(PYTHON) -c 'from pathlib import Path; p = Path("$(PY_GEN_DIR)/tts/v1/tts_pb2_grpc.py"); p.write_text(p.read_text().replace("from tts.v1 import tts_pb2 as", "from . import tts_pb2 as"))'

# Generate Go stubs for go-dubber (override GO_OUT_DIR=/path/to/go-dubber/gen)
proto-go:
	mkdir -p $(GO_OUT_DIR)
	protoc \
		-I $(PROTO_DIR) \
		--go_out=$(GO_OUT_DIR) --go_opt=paths=source_relative \
		--go-grpc_out=$(GO_OUT_DIR) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/tts/v1/tts.proto

test:
	PYTHONPATH=src $(PYTHON) -m pytest -v

run:
	PYTHONPATH=src $(PYTHON) -m tts_service

run-http:
	PYTHONPATH=src $(PYTHON) -m uvicorn app:app --reload --port 8000
