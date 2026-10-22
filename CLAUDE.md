# SACLA — instruções para o Claude Code

TCC: detecção de acessos anômalos em logs do PostgreSQL (jsonlog) com Isolation Forest.
Fluxo: simuladores (`src/simlab`) → PostgreSQL 18 + Pagila → jsonlog → SACLA (`src/sacla`)
→ relatório. Avaliação em `src/evaluation` (a partir da Fase 4).

## Escopo fechado — não adicionar
API REST, correlação com outros logs, pgAudit, WAL, Kafka/Spark, big data, Grafana,
comparação entre vários algoritmos de ML, tempo real, bloqueio automático, interface web.
O SACLA *auxilia* a identificação; nunca classifica algo como ataque.

## Regras do projeto
- Trabalho por fases (1 ambiente, 2 simuladores, 3 parser/agregação, 4 modelo/avaliação,
  5 relatórios/docs), uma branch `fase-N-…` por fase; parar ao fim de cada fase para revisão.
- Nada de afirmar que funciona sem executar: rodar os comandos e mostrar a saída real.
- Verificar na documentação oficial antes de depender de um comportamento do PostgreSQL;
  registrar a decisão e a evidência em `docs/decisoes.md`.
- `sacla` não importa `simlab` nem `evaluation` (garantido por `tests/test_architecture.py`).
- Rótulos de avaliação vêm só do manifesto de ground truth, nunca do conteúdo do log.
- Estatísticas por usuário só do período de treino; ajuste só na validação; teste usado uma vez.
- Configuração em YAML (`config/`), sem valores mágicos no código; seeds fixas; versões fixadas.
- Código: identificadores em inglês; comentários, docstrings, mensagens, docs e relatórios em
  pt-BR. Tipado (mypy; strict em `sacla`), formatado com ruff (linha de 100).

## Comandos (Windows sem make: usar `python tasks.py`)
`setup` (recria tudo do zero) · `reset` · `smoke` · `bench` · `simulate [--days N --seed S]`
(recria banco e logs e roda a simulação) · `retime` · `test` · `lint` · `fmt` · `lock` · `psql`
· `shell`. Tudo roda nos containers do `docker-compose.yml`; o Docker Desktop precisa estar
ligado. Não rode outro comando de container enquanto um `simulate` estiver recriando o banco.

## Simulação (Fase 2)
Saídas em `data/runs/<run_id>/`; o SACLA lê só `retimed/`. Rótulos ficam só em
`manifest.csv` (nunca em `events.parquet`). Configuração: `config/profiles.yaml`
(comportamentos, ruído), `config/queries.yaml` (SQL), `config/anomalies.yaml`.

## Ambiente
Host Windows 11 + Docker Desktop. Logs ficam no volume `pglogs` (montado em `/logs` no
container `sacla`). O container `sacla` está em três redes (lan/app/externa) para variar o IP
de origem.
