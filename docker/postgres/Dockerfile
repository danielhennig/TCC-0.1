# syntax=docker/dockerfile:1
# PostgreSQL 18.6 + pgvector 0.8.7 (exigido pelo schema atual do Pagila), fixado por digest.
FROM pgvector/pgvector:0.8.7-pg18-bookworm@sha256:2358fcba361ed2233a5ed81b5fe4ca779ccb304120ce531a3bf51c0ed7e2bc11

# Pagila fixado em um commit (o repositório não publica tags) e conferido por SHA-256.
ARG PAGILA_COMMIT=ce6e114236bb828e5ec0dc835f65f1bf1b9179f5
ADD --chmod=644 --checksum=sha256:071ee940a73c8f4fad2997185788065291607e2c55559e3747959e8275391536 \
    https://raw.githubusercontent.com/devrimgunduz/pagila/${PAGILA_COMMIT}/pagila-schema.sql \
    /docker-entrypoint-initdb.d/01-pagila-schema.sql
ADD --chmod=644 --checksum=sha256:a88efa94c7ae8bc9cf55def4efc9f164d064d5b9cd93f11719ba3b5ace1602f7 \
    https://raw.githubusercontent.com/devrimgunduz/pagila/${PAGILA_COMMIT}/pagila-data.sql \
    /docker-entrypoint-initdb.d/02-pagila-data.sql

# Configuração de logs do SACLA, incluída no postgresql.conf pelo script 00 do initdb.
# O servidor temporário do initdb não relê o arquivo, então a carga do Pagila não é logada.
COPY --chmod=644 sacla.conf /etc/postgresql/sacla.conf
COPY --chmod=755 initdb/00-include-sacla-conf.sh /docker-entrypoint-initdb.d/00-include-sacla-conf.sh

# Diretório dos logs jsonlog (montado como volume nomeado `pglogs`, que herda dono e modo).
RUN mkdir -p /var/log/postgresql/sacla \
    && chown -R postgres:postgres /var/log/postgresql \
    && chmod 755 /var/log/postgresql/sacla
