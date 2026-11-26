FROM python:3.12-slim

# 1. System dependencies (ffmpeg is required by ffsubsync)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Install Python dependencies before copying application code
# Pass --build-arg INSTALL_CUDA=1 for GPU (PyTorch CUDA) support; default is CPU/ONNX (torch-free).
ARG INSTALL_CUDA=0
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt && \
    if [ "$INSTALL_CUDA" = "1" ]; then pip install "vieneu[cuda]"; fi

# 3. Copy application source & install package in no-deps mode
COPY pyproject.toml app.py ./
COPY proto ./proto
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1 \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50051 \
    HF_HOME=/root/.cache/huggingface

EXPOSE 8000 50051

CMD ["python", "-m", "tts_service"]
