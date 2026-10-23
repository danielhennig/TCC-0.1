# Validação confirmatória (configuração congelada `ne200-ms1024-mf0.5-log1`)

Pré-registro: `docs/decisoes.md`, D52. Simulações confirmatórias: `sim-s101-28d`, `sim-s202-28d`, `sim-s303-28d`, `sim-s404-28d`, `sim-s505-28d` (780 cenários de teste, 60 do tipo inédito). Valores em %; intervalos de 95% por bootstrap (2000 reamostragens, estratificadas por simulação).

## Hipóteses (agregado confirmatório)

| | Comparação | Medida | A | B | A − B [IC] | Discordantes (só A / só B) | p | p (Holm) | Resultado |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| H1 | Isolation Forest ∪ regras × Regras | cenários detectados | 84.4 | 73.5 | 10.9 [8.9; 13.0] | 87 / 0 | < 0.0001 | < 0.0001 | rejeita H0 |
| H2 | Isolation Forest ∪ regras × Regras | F1 | 79.0 | 79.1 | -0.1 [-2.3; 2.5] | — | 0.96 | 1 | não rejeita H0 |
| H3 | Isolation Forest (ajustado) × Regras | cenários detectados (tipo inédito) | 50.7 | 41.7 | 9.0 [-9.5; 27.0] | 20 / 15 | 0.5 | 1 | não rejeita H0 |

Testes bicaudais, α = 0.05, correção de Holm sobre as 3 hipóteses. Cenários: McNemar exato com a detecção em ao menos 50% das sementes do modelo. F1: permutação pareada por blocos usuário × dia (10000 permutações).

## Diferenças descritivas (agregado confirmatório, sem teste)

| Comparação | Medida | A | B | A − B [IC] |
|---|---|---:|---:|---:|
| Isolation Forest (ajustado) × Regras | F1 | 66.3 | 79.1 | -12.8 [-16.2; -9.3] |
| Isolation Forest ∪ regras × Regras | precisão | 78.8 | 94.7 | -15.9 [-18.1; -13.6] |
| Isolation Forest ∪ regras × Regras | recall | 79.3 | 67.9 | 11.4 [9.0; 14.4] |
| Isolation Forest (ajustado) × Regras | cenários detectados | 69.0 | 73.5 | -4.5 [-7.9; -1.3] |

## Desempenho por detector (agregado confirmatório)

| Detector | Precisão | Recall | F1 | Cenários detectados | Tipo inédito detectado |
|---|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 74.6 [71.4; 78.0] | 59.6 [56.2; 62.8] | 66.3 [63.4; 68.9] | 69.0 [65.9; 71.9] | 50.7 [39.3; 62.0] |
| Isolation Forest (sem rótulos) | 71.7 [66.8; 76.7] | 30.9 [28.0; 34.1] | 43.2 [39.9; 46.6] | 37.5 [34.6; 40.6] | 19.3 [12.3; 26.7] |
| Regras | 94.7 [93.0; 96.2] | 67.9 [63.9; 71.6] | 79.1 [76.3; 81.7] | 73.5 [70.4; 76.5] | 41.7 [30.0; 53.3] |
| Isolation Forest ∪ regras | 78.8 [75.9; 81.6] | 79.3 [76.5; 82.1] | 79.0 [76.9; 81.2] | 84.4 [81.9; 86.7] | 76.7 [68.0; 85.3] |

## Referência: simulações de desenvolvimento (agregado, já usadas antes)

| Detector | Precisão | Recall | F1 | Cenários detectados | Tipo inédito detectado |
|---|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 76.6 [72.0; 80.7] | 60.1 [55.7; 64.5] | 67.3 [63.4; 70.9] | 71.4 [67.8; 75.1] | 51.9 [37.8; 66.4] |
| Isolation Forest (sem rótulos) | 69.6 [62.8; 75.9] | 26.4 [22.4; 30.5] | 38.3 [33.6; 42.9] | 33.7 [29.7; 37.5] | 28.3 [16.1; 41.7] |
| Regras | 93.4 [91.1; 95.5] | 68.3 [62.9; 73.6] | 78.9 [75.0; 82.5] | 73.1 [69.2; 77.1] | 44.4 [30.6; 58.3] |
| Isolation Forest ∪ regras | 79.5 [75.8; 82.8] | 80.6 [76.6; 84.3] | 80.0 [76.9; 82.9] | 85.9 [82.9; 88.8] | 78.6 [66.4; 89.7] |

## Por simulação (F1 e cenários detectados, com IC)

| Simulação | Grupo | Detector | F1 | Cenários detectados |
|---|---|---|---:|---:|
| sim-s42-28d | desenvolvimento | Isolation Forest (ajustado) | 68.1 [61.5; 73.5] | 75.5 [69.2; 81.3] |
| sim-s42-28d | desenvolvimento | Isolation Forest (sem rótulos) | 39.3 [32.3; 46.2] | 36.3 [29.5; 43.0] |
| sim-s42-28d | desenvolvimento | Regras | 80.2 [72.0; 86.7] | 73.1 [66.0; 80.1] |
| sim-s42-28d | desenvolvimento | Isolation Forest ∪ regras | 79.7 [73.2; 85.0] | 86.2 [80.9; 91.2] |
| sim-s7-28d | desenvolvimento | Isolation Forest (ajustado) | 65.3 [59.2; 71.0] | 64.3 [57.3; 70.9] |
| sim-s7-28d | desenvolvimento | Isolation Forest (sem rótulos) | 39.7 [32.5; 47.6] | 34.9 [28.5; 41.6] |
| sim-s7-28d | desenvolvimento | Regras | 74.1 [66.4; 81.2] | 71.8 [64.7; 78.2] |
| sim-s7-28d | desenvolvimento | Isolation Forest ∪ regras | 77.8 [72.5; 82.5] | 81.4 [75.4; 87.1] |
| sim-s2026-28d | desenvolvimento | Isolation Forest (ajustado) | 68.1 [60.5; 74.9] | 74.3 [68.1; 80.3] |
| sim-s2026-28d | desenvolvimento | Isolation Forest (sem rótulos) | 35.7 [26.3; 44.0] | 29.9 [23.6; 36.4] |
| sim-s2026-28d | desenvolvimento | Regras | 82.1 [76.4; 87.0] | 74.4 [67.3; 81.4] |
| sim-s2026-28d | desenvolvimento | Isolation Forest ∪ regras | 82.4 [77.9; 86.5] | 90.0 [85.4; 94.2] |
| sim-s101-28d | confirmatória | Isolation Forest (ajustado) | 66.4 [60.7; 71.5] | 66.6 [59.9; 73.4] |
| sim-s101-28d | confirmatória | Isolation Forest (sem rótulos) | 43.7 [35.9; 50.8] | 38.7 [32.0; 45.5] |
| sim-s101-28d | confirmatória | Regras | 78.5 [72.0; 84.4] | 71.2 [64.1; 78.2] |
| sim-s101-28d | confirmatória | Isolation Forest ∪ regras | 79.8 [75.0; 84.0] | 83.1 [77.6; 88.3] |
| sim-s202-28d | confirmatória | Isolation Forest (ajustado) | 67.0 [59.8; 73.6] | 69.1 [62.2; 75.7] |
| sim-s202-28d | confirmatória | Isolation Forest (sem rótulos) | 47.5 [39.7; 54.4] | 39.6 [32.8; 46.8] |
| sim-s202-28d | confirmatória | Regras | 82.5 [77.9; 86.7] | 76.3 [69.2; 82.7] |
| sim-s202-28d | confirmatória | Isolation Forest ∪ regras | 79.5 [74.1; 84.3] | 84.7 [79.0; 89.8] |
| sim-s303-28d | confirmatória | Isolation Forest (ajustado) | 63.5 [55.5; 69.8] | 62.5 [55.3; 69.2] |
| sim-s303-28d | confirmatória | Isolation Forest (sem rótulos) | 41.8 [33.1; 50.0] | 33.8 [26.9; 40.7] |
| sim-s303-28d | confirmatória | Regras | 76.8 [69.6; 82.9] | 73.1 [66.0; 79.5] |
| sim-s303-28d | confirmatória | Isolation Forest ∪ regras | 80.1 [74.7; 84.4] | 85.1 [79.4; 89.9] |
| sim-s404-28d | confirmatória | Isolation Forest (ajustado) | 68.8 [62.5; 74.5] | 69.6 [62.8; 76.5] |
| sim-s404-28d | confirmatória | Isolation Forest (sem rótulos) | 44.0 [35.8; 51.9] | 37.0 [30.6; 44.0] |
| sim-s404-28d | confirmatória | Regras | 77.9 [71.6; 83.2] | 71.8 [64.7; 78.8] |
| sim-s404-28d | confirmatória | Isolation Forest ∪ regras | 78.7 [72.9; 83.4] | 81.1 [75.2; 86.9] |
| sim-s505-28d | confirmatória | Isolation Forest (ajustado) | 65.2 [59.1; 70.2] | 77.0 [71.2; 82.6] |
| sim-s505-28d | confirmatória | Isolation Forest (sem rótulos) | 38.7 [30.8; 45.9] | 38.5 [31.9; 45.8] |
| sim-s505-28d | confirmatória | Regras | 79.5 [72.8; 85.1] | 75.0 [67.9; 81.4] |
| sim-s505-28d | confirmatória | Isolation Forest ∪ regras | 77.4 [72.2; 81.5] | 88.0 [83.0; 92.5] |

## Conferência

- As simulações de desenvolvimento (sim-s2026-28d, sim-s42-28d, sim-s7-28d) reproduzem os números publicados (diferença máxima: 5.6e-17).
