FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash build-essential ca-certificates curl git libcap-dev pkg-config \
    python3 python3-venv ripgrep tar unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
