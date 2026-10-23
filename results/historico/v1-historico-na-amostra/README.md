# v1 — primeira avaliação (atributos relativos do treino calculados na amostra)

Resultados da primeira avaliação completa, guardados para comparação na monografia. Foram
gerados com `features.training_history: in_sample`:
- as janelas de treino foram comparadas com o histórico do usuário que **inclui** elas mesmas;
- por isso, as três frações de novidade (tabelas, IPs e formatos de consulta inéditos) valeram
  0 em todo o treino;
- o Isolation Forest nunca as usou (docs/decisoes.md, D41).

A versão corrigida (v2, D45) está em `results/experimentos/` e `results/robustez/`. A
comparação entre as duas versões está na D46.

| Pasta | Conteúdo |
|---|---|
| `experimentos/<exp_id>/` | métricas, tabelas, scores, relatório do teste e figuras da v1 |
| `robustez/<exp_id>/` | a mesma configuração em outras simulações (sementes 7 e 2026) |

Para reproduzir a v1:
1. Use o commit `ae14148`.
2. Ou, no código atual, defina `features.training_history: in_sample` e volte `detector` em
   `config/sacla.yaml` para os valores daquele commit.
3. Rode `features`, `evaluate`, `robustness` e `report`.
