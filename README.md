# SACLA — Sistema de Análise Comportamental de Logs de Acesso

TCC em Ciência da Computação: **detecção de acessos anômalos em logs do PostgreSQL utilizando
aprendizado de máquina não supervisionado** (Isolation Forest).

```
simulação de usuários → PostgreSQL 18 + Pagila → log JSON (jsonlog + auto_explain)
    → SACLA: leitura → eventos → janelas por usuário → Isolation Forest → relatório explicado
```

O SACLA lê só o log nativo do PostgreSQL. Ele aponta janelas de 5 minutos em que um usuário se
comporta de forma atípica em relação ao próprio histórico e explica por quê. É um sistema que
**auxilia** a identificação: um alerta indica comportamento atípico, não um ataque.

As cinco fases estão concluídas:
1. ambiente e logs;
2. usuários, simuladores e ground truth;
3. leitura e atributos;
4. modelo, avaliação e baseline;
5. relatórios, figuras e documentação.

A metodologia e os resultados estão em [docs/metodologia.md](docs/metodologia.md); as
decisões técnicas, com fontes e evidências, em [docs/decisoes.md](docs/decisoes.md).

## Resultados (teste, 4ª semana simulada)

| Detector | Precisão | Recall | F1 | Cenários detectados |
|---|---:|---:|---:|---:|
| Isolation Forest (ajustado na validação) | 74,5 | 55,8 | 63,8 | 69,8 |
| Isolation Forest (sem rótulos) | 64,7 | 24,7 | 35,8 | 33,8 |
| Regras de especialista | 96,7 | 68,5 | 80,2 | 73,1 |
| Isolation Forest ∪ regras | 80,0 | 78,5 | 79,2 | 84,7 |

Valores em %, média de 10 sementes do modelo. Nas três simulações (sementes 42, 7 e 2026), o
F1 do Isolation Forest foi 62,4 ± 1,2. Interpretação, limitações e ameaças à validade: seções
9 e 11 da metodologia.

## Pré-requisitos

- Docker Desktop (ou Docker Engine) com Compose v2, em execução.
- Python 3.11+ no computador, só para rodar `tasks.py`; todo o resto roda em containers.

## Reproduzir o experimento

```bash
python tasks.py setup        # constrói as imagens e recria o banco do zero (Pagila + papéis)
python tasks.py smoke        # gera eventos conhecidos e confere as linhas reais do jsonlog
python tasks.py bench        # mede o custo do registro de comandos e do auto_explain
python tasks.py simulate     # 28 dias simulados, semente 42 (~2,5 min)
python tasks.py features     # janelas de 5 min com atributos e rótulos (--window N)
python tasks.py evaluate     # ajuste na validação, teste único, regras, sensibilidade (~5 min)
python tasks.py robustness --exp-id sim-s42-28d__w5m__ne500-ms1024-mf0.5-log1
python tasks.py report       # relatório de anomalias do teste e figuras
python tasks.py test         # testes (pytest)
python tasks.py lint         # ruff + mypy
```

Outros comandos: `python tasks.py --help`. Com `make` disponível, `make setup` etc. também
funcionam. As senhas são do laboratório local e ficam em `.env`, criado a partir de
`.env.example`.

## Usar o SACLA num log real

O SACLA precisa do PostgreSQL com a configuração de log de
[docker/postgres/sacla.conf](docker/postgres/sacla.conf):
- `jsonlog`;
- uma linha por comando;
- `auto_explain` com `log_analyze`.

Ajuste o detector com um período considerado normal e analise outro período:

```bash
python tasks.py fit   --logs /logs --until 2026-03-16T03:00:00Z --model data/sacla/detector.joblib
python tasks.py score --logs /logs --since 2026-03-23T03:00:00Z --model data/sacla/detector.joblib \
                      --out data/sacla/relatorio
```

- `/logs` é o volume de logs do banco, visto de dentro do container. Também serve uma pasta do
  projeto, como `data/runs/sim-s42-28d/retimed`.
- Datas em ISO 8601 com fuso; o intervalo é `[--since, --until)`.
- O limiar é definido sem rótulos: por padrão, 1,44% das janelas do histórico ficam acima dele
  (`detector.train_alert_rate` em `config/sacla.yaml`, ou `--alert-rate` no `fit`).

O relatório (`relatorio.html`, `alertas.csv`, `episodios.csv`, `usuarios.csv`) lista os
episódios do mais atípico ao menos atípico. Cada alerta mostra os atributos mais raros para
aquele usuário, por exemplo:

> linhas retornadas (soma): 112.507 (acima do máximo do histórico; mediana 61, máximo 10.072)

## Saídas

| Pasta | Conteúdo | Versionada |
|---|---|---|
| `data/runs/<run_id>/` | agenda, manifesto (ground truth), log bruto e remapeado de cada simulação | não (reproduzível) |
| `data/features/<run_id>/` | conjunto rotulado: uma linha por usuário × janela | não |
| `data/models/<exp_id>/` | modelo avaliado (semente 0) | não |
| `results/ambiente/` | verificações do jsonlog e custo do `auto_explain` (Fase 1) | sim |
| `results/simulacao/<run_id>/` | relatórios e manifesto de cada simulação | sim |
| `results/dataset/<run_id>/` | resumo do conjunto rotulado e conferência com o executor | sim |
| `results/experimentos/<exp_id>/` | métricas, tabelas, scores, `resumo.md`, `relatorio/` e `figuras/` | sim |
| `results/robustez/<exp_id>/` | o mesmo experimento em outras simulações | sim |

As figuras (PNG 300 dpi e PDF) e as legendas sugeridas (`figuras/legendas.md`) estão prontas
para a monografia.

## Estrutura

```
config/            configuração em YAML (simulação, anomalias, SACLA, modelo, regras)
docker/            imagens do PostgreSQL (Pagila + sacla.conf) e do Python
src/sacla/         o SACLA: reader (1), events/sqlclass/features (2), model (3),
                   explain/report (4), pipeline e cli (uso real)
src/simlab/        bancada experimental: provisionamento, simulação, remapeamento, verificações
src/evaluation/    rótulos, conjunto de dados, ajuste, métricas, regras, robustez, relatório
                   do experimento e figuras
tests/             testes pytest e linhas reais de log (fixtures)
docs/              metodologia.md e decisoes.md
```

O pacote `sacla` não importa `simlab` nem `evaluation`; um teste garante isso. Os rótulos
existem só no manifesto e na avaliação.

## Limitações principais

- Tráfego sintético, regular e desenhado pelo mesmo autor das anomalias e das regras.
- Horários simulados e execução sequencial, sem concorrência real.
- `auto_explain` com análise: ~10% de latência e ~10 KB de log por comando.
- Os atributos de novidade (tabelas, IPs e formatos de consulta inéditos) valem 0 em todo o
  treino e não influenciam o Isolation Forest (D41). Eles aparecem só nas explicações.
- O score do Isolation Forest satura fora da faixa do treino: um valor 100× acima do máximo
  pontua como um pouco acima.
- Sem atualização do histórico: o crescimento das tabelas faz a rotina batch legítima parecer
  atípica no teste (deriva de dados).
