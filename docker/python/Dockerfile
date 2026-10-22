# syntax=docker/dockerfile:1
# Ambiente Python do SACLA e da bancada experimental (simlab), com versões fixadas.
FROM python:3.13.17-slim-bookworm@sha256:e31e5f8101202ad5f862e07eeb3d79e3c1f611b45291573e9b0a5bda65857e7e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# O código é montado em /app em tempo de execução (bind mount), não copiado para a imagem.
