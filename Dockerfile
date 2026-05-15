# ── Base image ────────────────────────────────────────────────────────────────
# python:3.11-slim is a minimal Debian image with Python pre-installed.
# "slim" omits dev headers, test files, and docs — ~50MB vs ~900MB for full.
FROM python:3.11-slim

WORKDIR /app

# ── System dependencies ───────────────────────────────────────────────────────
# git is needed by some HuggingFace Hub utilities during model download.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────────
# Install CPU-only PyTorch first. The default pip install pulls the CUDA build
# (~2GB). We're serving on CPU, so the CPU wheel (~200MB) is correct.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy requirements before source so Docker can cache this layer.
# If only source code changes, this layer is reused and deps aren't reinstalled.
COPY requirements-serving.txt .
RUN pip install --no-cache-dir -r requirements-serving.txt

# ── Application source ─────────────────────────────────────────────────────────
COPY src/ ./src/

# ── Environment ───────────────────────────────────────────────────────────────
# Default model — override by setting HF_MODEL_ID as a Space secret.
ENV HF_MODEL_ID=pranavsagar10/content-classifier-distilbert

# HF Spaces requires the app to listen on port 7860.
EXPOSE 7860

# ── Startup ───────────────────────────────────────────────────────────────────
# --host 0.0.0.0 makes uvicorn accept connections from outside the container.
# Without it, uvicorn binds to 127.0.0.1 (localhost only) and the Space won't respond.
CMD ["uvicorn", "src.serving.app:app", "--host", "0.0.0.0", "--port", "7860"]
