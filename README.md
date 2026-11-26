# VieNeu-TTS Distributed Service (`vieneu-tts-service`)

A dual-protocol (**HTTP/REST** + **gRPC**) Python service hosting the **[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS)** neural voice synthesis engine (Vietnamese & English, 48 kHz v3 Turbo) and asynchronous subtitle timing synchronization (`ffsubsync`).

This service is a wire-compatible drop-in replacement for `tts-service`: both HTTP (`:8000`) and gRPC (`:50051`) run in a single process on a shared `asyncio` event loop so the loaded ONNX/PyTorch model is never duplicated in memory.

---

## Architecture

```text
vieneu-tts-service/
├── proto/tts/v1/tts.proto          # Shared Protobuf contract (Python server & Go client)
├── src/tts_service/
│   ├── config.py                   # Typed env configuration (pydantic-settings)
│   ├── logging_config.py           # Structured logging
│   ├── domain/                     # Transport-agnostic models & exceptions
│   ├── services/
│   │   ├── tts.py                  # VieNeu-TTS engine wrapper, speed/resample, WAV/PCM encoding
│   │   └── subsync.py              # Non-blocking async ffsubsync runner with timeout
│   ├── transport/
│   │   ├── http/                   # FastAPI app, routes, Bearer auth, health probes
│   │   └── grpc/                   # grpc.aio server, Bearer interceptor, health & reflection
│   └── main.py                     # Unified HTTP (:8000) + gRPC (:50051) entrypoint
├── tests/                          # Unit & integration tests (HTTP + gRPC)
├── app.py                          # Backwards-compatible ASGI shim (`uvicorn app:app`)
├── openapi.yaml                    # OpenAPI 3.0.3 specification
└── Makefile                        # Proto codegen (Python & Go), test, run targets
```

---

## Features

- **Dual-Protocol Serving**: HTTP/REST (`:8000`) and gRPC (`:50051`) share the same loaded VieNeu-TTS model in RAM/VRAM.
- **VieNeu-TTS v3 Turbo (48 kHz)**: Runs torch-free on CPU via ONNX Runtime (`fp32` or `int8`) or automatically switches to PyTorch CUDA graphs on GPU. Also supports `VIENEU_MODE=v3nano` for low-power CPUs.
- **Native Streaming**: `SynthesizeStream` streams real-time `pcm_s16le` audio chunks directly from `Vieneu.infer_stream(...)` for both Vietnamese and English.
- **Configurable Sample Rate & Speed**: Native 48 kHz output by default; set `SAMPLE_RATE=24000` (or any rate in `[8000, 96000]`) for automatic `soxr` resampling. Non-1.0 `speed` values are time-stretched cleanly via `librosa`.
- **Non-Blocking Subtitle Sync**: `ffsubsync` executes via `asyncio.create_subprocess_exec` with configurable timeout and cleanup.
- **Timing-Safe Authentication**: Constant-time `Bearer <API_TOKEN>` validation across both HTTP and gRPC.
- **Health & Introspection**: HTTP `/health`, `/healthz`, `/readyz` + standard `grpc.health.v1.Health` and gRPC Server Reflection.

---

## HTTP/REST API Specification

Base URL: `http://localhost:8000`  
Full OpenAPI 3.0.3 schema: [openapi.yaml](openapi.yaml)

### Authentication
All `/v1/*` endpoints require:
```http
Authorization: Bearer <API_TOKEN>
```
Health endpoints (`/health`, `/healthz`, `/readyz`) are unauthenticated.

### 1. `POST /v1/audio/speech`
Synthesizes a dialogue line into a 16-bit mono PCM WAV stream (48 kHz default).

**Request Body (`application/json`):**
```json
{
  "text": "Xin chào mọi người",
  "lang": "vi",
  "role": "A",
  "speed": 1.0
}
```

| Field   | Type   | Constraints         | Default | Description                                        |
|---------|--------|---------------------|---------|----------------------------------------------------|
| `text`  | string | non-empty           | —       | Text content to synthesize                         |
| `lang`  | string | `"vi"` \| `"en"`    | `"vi"`  | Target language                                    |
| `role`  | string | `"A"` \| `"B"`      | `"A"`   | Voice preset (`A` = primary, `B` = secondary)      |
| `speed` | float  | `0.25 <= x <= 4.0`  | `1.0`   | Reading speed multiplier                           |

**Response:**
- `200 OK` (`Content-Type: audio/wav`) with binary WAV body.
- Response headers: `X-Audio-Sample-Rate: 48000`, `X-Audio-Duration-Seconds: <float>`.
- Error codes: `400` (invalid input), `403` (unauthorized), `503` (engine loading), `500` (synthesis failure).

### 2. `POST /v1/utils/subsync`
Synchronizes a target `.srt` file against a reference `.srt` or `.aac` file using `ffsubsync`.

**Request (`multipart/form-data`):**
| Field            | Type   | Required | Description                                |
|------------------|--------|----------|--------------------------------------------|
| `target_sub`     | file   | Yes      | Target `.srt` subtitle file                |
| `reference_file` | file   | Yes      | Reference `.srt` or `.aac` file            |
| `ref_type`       | string | Yes      | `"sub"` or `"audio"`                       |

**Response:**
- `200 OK` (`Content-Type: text/plain`) with synchronized `.srt` bytes.
- Error codes: `400` (empty upload / invalid `ref_type`), `403` (unauthorized), `500` (`ffsubsync` failure/timeout).

### 3. Health & Readiness Endpoints
- `GET /health` / `GET /healthz` → `200 {"status": "ok"}` (liveness)
- `GET /readyz` → `200 {"status": "ready", "engines_loaded": true}` or `503 {"status": "not_ready", "engines_loaded": false}`

---

## gRPC API Specification

Address: `localhost:50051` (HTTP/2 plaintext; terminate TLS at reverse proxy/mesh if exposed externally)  
Contract: [proto/tts/v1/tts.proto](proto/tts/v1/tts.proto)  
Service: `tts.v1.TTSService`  
Default max message size: `100 MB` (configurable via `GRPC_MAX_MESSAGE_MB`).

### Authentication
Pass the Bearer token in gRPC metadata:
```text
authorization: Bearer <API_TOKEN>
```
Standard health (`grpc.health.v1.Health`) and reflection (`grpc.reflection.v1alpha.ServerReflection`) RPCs bypass authentication.

### RPC Methods

| RPC | Type | Request | Response | Description |
|-----|------|---------|----------|-------------|
| `Synthesize` | Unary | `SynthesizeRequest` | `SynthesizeResponse` | Returns complete PCM_16 WAV bytes (`audio_wav`), `sample_rate`, and `duration_seconds`. |
| `SynthesizeStream` | Server-streaming | `SynthesizeRequest` | `stream AudioChunk` | Streams raw `pcm_s16le` mono chunks (`pcm_data`, `sample_rate`, `is_final`). |
| `SyncSubtitle` | Unary | `SyncSubtitleRequest` | `SyncSubtitleResponse` | Synchronizes `target_srt` against `reference_data` in a single request (up to `GRPC_MAX_MESSAGE_MB`). |
| `SyncSubtitleStream` | Client-streaming | `stream SyncSubtitleChunk` | `SyncSubtitleResponse` | Streams `metadata` (`ref_type`) first, followed by `target_srt_chunk` and `reference_chunk` frames for large files. |

### Quick Test with `grpcurl`
```bash
# List services via reflection
grpcurl -plaintext localhost:50051 list

# Health check
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check

# Synthesize speech
grpcurl -plaintext \
  -H "authorization: Bearer my_super_secret_token_123" \
  -d '{"text":"Xin chào từ VieNeu-TTS","lang":"LANGUAGE_VI","role":"VOICE_ROLE_A","speed":1.0}' \
  localhost:50051 tts.v1.TTSService/Synthesize
```

---

## Local Development

```bash
# Create virtual environment and install dependencies (CPU / ONNX, torch-free)
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# Optional GPU (CUDA) support:
# ./venv/bin/pip install "vieneu[cuda]"

# Run both HTTP (:8000) and gRPC (:50051)
API_TOKEN=my_super_secret_token_123 make run
# or
./start.sh

# Run HTTP-only with auto-reload (backwards-compatible)
make run-http

# Regenerate Python gRPC stubs after editing proto/tts/v1/tts.proto
make proto-python

# Run test suite
make test
```

---

## Docker Deployment

```bash
# CPU / ONNX build (default, torch-free)
docker build -t vieneu-tts-service:cpu .

# GPU / PyTorch CUDA build
docker build --build-arg INSTALL_CUDA=1 -t vieneu-tts-service:gpu .

# Run (mount HuggingFace cache to avoid re-downloading models)
docker run -d \
  --name vieneu-tts-service \
  --gpus all \
  -p 8000:8000 \
  -p 50051:50051 \
  -v hf_cache:/root/.cache/huggingface \
  -e API_TOKEN=my_super_secret_token_123 \
  --restart unless-stopped \
  vieneu-tts-service:gpu
```

---

## Environment Variables

| Variable                  | Default                     | Description                                                                 |
|---------------------------|-----------------------------|-----------------------------------------------------------------------------|
| `API_TOKEN`               | `my_super_secret_token_123` | Bearer token for HTTP and gRPC auth (override in prod)                      |
| `HTTP_HOST`               | `0.0.0.0`                   | HTTP bind host                                                              |
| `HTTP_PORT`               | `8000`                      | HTTP bind port                                                              |
| `GRPC_HOST`               | `0.0.0.0`                   | gRPC bind host                                                              |
| `GRPC_PORT`               | `50051`                     | gRPC bind port                                                              |
| `ENABLE_HTTP`             | `true`                      | Enable FastAPI HTTP server                                                  |
| `ENABLE_GRPC`             | `true`                      | Enable gRPC server                                                          |
| `GRPC_REFLECTION`         | `true`                      | Enable gRPC Server Reflection                                               |
| `GRPC_MAX_MESSAGE_MB`     | `100`                       | Max gRPC send/receive message size in MB                                    |
| `DEVICE`                  | `auto`                      | `auto`, `cuda`, or `cpu`                                                    |
| `VIENEU_MODE`             | `v3turbo`                   | VieNeu-TTS mode: `v3turbo` (48 kHz) or `v3nano` (24 kHz, lightweight CPU)   |
| `VIENEU_BACKEND`          | `auto`                      | `auto` (ONNX on CPU, PyTorch on GPU), `onnx`, or `pytorch`                  |
| `VIENEU_PRECISION`        | `fp32`                      | CPU/ONNX backbone precision: `fp32` (default) or `int8` (requires VNNI)     |
| `VIENEU_BACKBONE_REPO`    | *(unset)*                   | Optional HuggingFace repo override for VieNeu-TTS weights                   |
| `VIENEU_ONNX_DIR`         | *(unset)*                   | Optional local directory containing exported ONNX graphs                    |
| `VIENEU_THREADS`          | `0`                         | ONNX/CPU intra-op threads (`0` = engine default)                            |
| `VIENEU_MAX_STREAMS`      | `16`                        | Max concurrent GPU streams for `infer_stream`                               |
| `VIENEU_WATERMARK`        | `false`                     | Enable audio watermarking (requires `pip install "vieneu[watermark]"`)      |
| `VIENEU_WARMUP`           | `true`                      | Warm up engine graphs on startup                                            |
| `SAMPLE_RATE`             | `48000`                     | Output audio sample rate in Hz (resampled via `soxr` if different from engine) |
| `PEAK_TARGET`             | `0.95`                      | Peak normalization target `(0.0, 1.0]`                                      |
| `MAX_CONCURRENT_TTS`      | `2`                         | Max concurrent TTS synthesis tasks                                          |
| `MAX_CONCURRENT_SUBSYNC`  | `2`                         | Max concurrent `ffsubsync` processes                                        |
| `SUBSYNC_TIMEOUT_SECONDS` | `300.0`                     | Timeout per `ffsubsync` invocation in seconds                               |
| `VI_VOICE_A`              | `Hải Đăng`                  | VieNeu-TTS preset voice for Vietnamese role `A`                             |
| `VI_VOICE_B`              | `Mai Anh`                   | VieNeu-TTS preset voice for Vietnamese role `B`                             |
| `EN_VOICE_A`              | `Hải Đăng`                  | VieNeu-TTS preset voice for English role `A`                                |
| `EN_VOICE_B`              | `Mai Anh`                   | VieNeu-TTS preset voice for English role `B`                                |
| `LOG_LEVEL`               | `INFO`                      | `DEBUG`, `INFO`, `WARNING`, or `ERROR`                                      |

---

## License

[MIT](LICENSE)
