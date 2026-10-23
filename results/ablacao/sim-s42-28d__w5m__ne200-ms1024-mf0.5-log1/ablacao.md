# Ablação de atributos na validação (configuração congelada `ne200-ms1024-mf0.5-log1`)

Exploratória (D52). 8 simulações (sim-s42-28d, sim-s7-28d, sim-s2026-28d, sim-s101-28d, sim-s202-28d, sim-s303-28d, sim-s404-28d, sim-s505-28d); em cada uma, média de 5 sementes de ajuste. Δ = variante − completo, em pontos percentuais (PR-AUC em unidades); intervalo t de 95% entre simulações. Só treino e validação; o teste não é usado.

## Isolation Forest sem cada grupo

Completo: F1 64.9, PR-AUC 0.592, cenários detectados 73.0.

| Grupo retirado | Atributos | Δ F1 | Δ PR-AUC | Δ cenários | Pior em (F1) |
|---|---|---:|---:|---:|---:|
| relativos ao histórico | `z_commands`, `z_rows_returned`, `hour_surprisal`, `new_tables_frac`, `new_ips_frac`, `new_query_shapes_frac` | -17.0 [-19.4; -14.7] | -0.201 [-0.213; -0.189] | -4.8 [-8.5; -1.2] | 8 de 8 |
| novidade | `new_tables_frac`, `new_ips_frac`, `new_query_shapes_frac` | -2.6 [-4.2; -1.0] | -0.029 [-0.044; -0.013] | -4.0 [-5.2; -2.9] | 8 de 8 |
| horário | `hour_sin`, `hour_cos`, `weekend`, `hour_surprisal` | -10.9 [-12.7; -9.1] | -0.129 [-0.140; -0.118] | -1.6 [-5.4; +2.3] | 8 de 8 |
| origem | `n_ips`, `new_ips_frac` | -0.9 [-2.6; +0.9] | -0.012 [-0.029; +0.006] | -2.2 [-4.8; +0.4] | 6 de 8 |
| sem auto_explain | `rows_returned_sum`, `rows_returned_max`, `rows_affected_sum`, `n_tables`, `z_rows_returned`, `new_tables_frac` | +0.1 [-1.3; +1.5] | +0.017 [+0.004; +0.031] | -9.6 [-12.7; -6.6] | 5 de 8 |

## Sem auto_explain: o sistema inteiro

Regras sem `volume`, `dml_volume`; IF sem o grupo `sem_auto_explain`; união das duas versões reduzidas.

| Detector | F1 completo | F1 sem auto_explain | Δ F1 | Cenários completo | Cenários sem auto_explain | Δ cenários |
|---|---:|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 64.9 | 65.0 | +0.1 [-1.3; +1.5] | 73.0 | 63.3 | -9.6 [-12.7; -6.6] |
| Regras | 83.2 | 77.1 | -6.1 [-7.9; -4.3] | 76.4 | 52.5 | -23.9 [-28.1; -19.6] |
| Isolation Forest ∪ regras | 75.2 | 73.0 | -2.1 [-4.1; -0.2] | 85.3 | 70.2 | -15.1 [-17.6; -12.6] |

### Cenários detectados por tipo, completo → sem auto_explain (%)

Todos os cenários de validação das simulações; média nas sementes de ajuste.

| Tipo | Cenários | Isolation Forest (ajustado) | Regras | Isolation Forest ∪ regras |
|---|---:|---:|---:|---:|
| dml_incomum | 120 | 46.7 → 32.5 | 60.0 → 0.0 | 69.5 → 32.5 |
| falha_auth | 120 | 77.3 → 74.7 | 97.5 → 97.5 | 98.3 → 98.2 |
| fora_horario | 120 | 95.0 → 95.7 | 90.8 → 90.8 | 98.5 → 99.5 |
| rajada | 120 | 53.5 → 55.8 | 37.5 → 26.7 | 56.7 → 55.8 |
| sem_permissao | 120 | 84.7 → 85.8 | 100.0 → 100.0 | 100.0 → 100.0 |
| volume_alto | 120 | 80.7 → 35.5 | 72.5 → 0.0 | 89.0 → 35.5 |
