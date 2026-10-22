#!/bin/sh
# Executado uma única vez pelo entrypoint oficial, logo após o initdb.
# O servidor temporário usado para carregar o Pagila já está de pé e não relê o arquivo,
# então a configuração de logs só vale a partir da inicialização definitiva.
set -eu
echo "include '/etc/postgresql/sacla.conf'" >> "$PGDATA/postgresql.conf"
