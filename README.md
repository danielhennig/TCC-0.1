# SACLA — Sistema de Análise Comportamental de Logs de Acesso

TCC em Ciência da Computação: **detecção de acessos anômalos em logs do PostgreSQL utilizando
aprendizado de máquina não supervisionado** (Isolation Forest).

```
scripts de simulação → PostgreSQL 18 + Pagila → logs JSON (jsonlog) → SACLA → relatório de anomalias
```

> Estado atual: **Fase 1 concluída** (ambiente, base Pagila e configuração de logs).
> As fases 2 a 5 (simuladores, parser e agregação, modelo e avaliação, relatórios) virão a seguir.
> Instalação completa, resultados e limitações serão documentados aqui na Fase 5.

## Pré-requisitos

- Docker Desktop (ou Docker Engine) com Compose v2, em execução.
- Python 3.11+ no computador, só para rodar `tasks.py`; todo o resto roda em containers.

## Início rápido

```bash
python tasks.py setup   # constrói as imagens e recria o banco do zero (Pagila + papéis + usuários)
python tasks.py smoke   # gera eventos conhecidos e mostra/confere as linhas reais do jsonlog
python tasks.py bench   # mede o overhead do registro de comandos e do auto_explain
python tasks.py test    # testes (pytest)
python tasks.py lint    # ruff + mypy
```

Outros comandos: `python tasks.py --help`. Com `make` disponível, `make setup` etc. também
funcionam.

As senhas são do laboratório local e ficam em `.env`, criado a partir de `.env.example`.

## Estrutura

```
config/            configuração do experimento (YAML)
docker/            imagens do PostgreSQL (com Pagila e sacla.conf) e do Python
src/sacla/         o sistema SACLA (Fases 3–5)
src/simlab/        bancada experimental: provisionamento, simulação, verificações
tests/             testes pytest e linhas reais de log (fixtures)
results/           resultados versionados (ex.: results/ambiente/)
docs/decisoes.md   decisões técnicas, com fontes e evidências
```

## Decisões técnicas

Ver [docs/decisoes.md](docs/decisoes.md). Em resumo:

- jsonlog com `log_min_duration_statement=0` e `log_statement='none'`: uma linha por comando;
- `auto_explain` (JSON, `log_timing=off`) para obter as linhas retornadas e as tabelas de cada
  comando, com overhead medido;
- horários simulados por remapeamento exato dos timestamps (Fase 2);
- três redes Docker para variar a origem (IP) das conexões.
