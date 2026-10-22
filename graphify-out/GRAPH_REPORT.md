# Graph Report - TCC-0.1  (2026-10-21)

## Corpus Check
- 22 files · ~10,494 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 10 file(s) not represented in the graph (top: (none) 5, .example 1, .conf 1)

## Summary
- 233 nodes · 584 edges · 14 communities (8 shown, 6 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e534a39d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ConfigError
- tasks.py
- smoke.py
- bench.py
- provision.py
- test_logtail.py
- Decisões técnicas do SACLA
- profiles.py
- pathlib
- 00-include-sacla-conf.sh
- sacla

## God Nodes (most connected - your core abstractions)
1. `ConfigError` - 26 edges
2. `run_smoke()` - 19 edges
3. `LabConfig` - 17 edges
4. `main()` - 16 edges
5. `run()` - 15 edges
6. `run_bench()` - 14 edges
7. `provision()` - 14 edges
8. `SmokeContext` - 14 edges
9. `admin_connection()` - 13 edges
10. `connect()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `1. Banco de dados e base de exemplo` --references--> `setup()`  [INFERRED]
  docs/decisoes.md → tasks.py
- `Comandos (Windows sem make: usar `python tasks.py`)` --references--> `bench()`  [INFERRED]
  CLAUDE.md → tasks.py
- `Comandos (Windows sem make: usar `python tasks.py`)` --references--> `fmt()`  [INFERRED]
  CLAUDE.md → tasks.py
- `Comandos (Windows sem make: usar `python tasks.py`)` --references--> `lint()`  [INFERRED]
  CLAUDE.md → tasks.py
- `Comandos (Windows sem make: usar `python tasks.py`)` --references--> `lock()`  [INFERRED]
  CLAUDE.md → tasks.py

## Import Cycles
- None detected.

## Communities (14 total, 6 thin omitted)

### Community 0 - "ConfigError"
Cohesion: 0.11
Nodes (13): ConfigError, load_yaml(), setup_logging(), main(), _section(), admin_connection(), connect(), _env() (+5 more)

### Community 1 - "tasks.py"
Cohesion: 0.09
Nodes (20): Ambiente, Comandos (Windows sem make: usar `python tasks.py`), Escopo fechado — não adicionar, Regras do projeto, SACLA — instruções para o Claude Code, bench(), down(), ensure_env() (+12 more)

### Community 2 - "smoke.py"
Cohesion: 0.15
Nodes (29): _assign_records(), Check, _duration_lines(), _evaluate(), _first_line(), _is_duration(), _modify_table_rows(), _no_action() (+21 more)

### Community 3 - "bench.py"
Cohesion: 0.13
Nodes (12): _apply(), BenchCondition, BenchSettings, ConditionStats, _execute_block(), _make_statements(), _markdown_table(), run_bench() (+4 more)

### Community 4 - "provision.py"
Cohesion: 0.22
Nodes (13): alter_system_overrides(), check_pagila_counts(), ensure_group_role(), ensure_login_user(), ensure_payment_partitions(), _next_month(), _parse_year_month(), provision() (+5 more)

### Community 5 - "test_logtail.py"
Cohesion: 0.16
Nodes (11): LogSnapshot, parse_auto_explain(), read_new_records(), _plan_lines(), _root_rows_of_step(), _smoke_records(), _smoke_steps(), test_parse_auto_explain_ignores_other_messages() (+3 more)

### Community 6 - "Decisões técnicas do SACLA"
Cohesion: 0.14
Nodes (12): 1. Banco de dados e base de exemplo, 2. Configuração dos logs (`docker/postgres/sacla.conf`), 3. Linhas retornadas e afetadas (item 2 da proposta), 4. Horário dos acessos (item 3 da proposta), 5. Ambiente, Decisões técnicas do SACLA, Fatos do formato observados no ambiente (importantes para o parser — Fase 3), Decisões técnicas (+4 more)

### Community 7 - "profiles.py"
Cohesion: 0.24
Nodes (7): Grant, load_profiles(), _parse_objects(), _parse_role(), Profiles, Role, User

## Knowledge Gaps
- **12 isolated node(s):** `Escopo fechado — não adicionar`, `Regras do projeto`, `Ambiente`, `Pré-requisitos`, `Início rápido` (+7 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 72 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ConfigError` connect `ConfigError` to `smoke.py`, `bench.py`, `profiles.py`?**
  _High betweenness centrality (0.100) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `ConfigError` (e.g. with `BenchSettings` and `main()`) actually correct?**
  _`ConfigError` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Escopo fechado — não adicionar`, `Regras do projeto`, `Ambiente` to the rest of the system?**
  _12 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ConfigError` be split into smaller, more focused modules?**
  _Cohesion score 0.11201079622132254 - nodes in this community are weakly interconnected._
- **Why does `setup()` connect `tasks.py` to `Decisões técnicas do SACLA`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `run_smoke()` (e.g. with `LabConfig` and `LogSnapshot`) actually correct?**
  _`run_smoke()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `tasks.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08961593172119488 - nodes in this community are weakly interconnected._