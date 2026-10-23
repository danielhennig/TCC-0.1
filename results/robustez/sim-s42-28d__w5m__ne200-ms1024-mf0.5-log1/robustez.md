# Robustez a outras simulações (configuração congelada `ne200-ms1024-mf0.5-log1`)

Média das sementes de avaliação em cada simulação; valores em %.

| Simulação | Contaminação do teste | Detector | Precisão | Recall | F1 | Cenários detectados |
|---|---:|---|---:|---:|---:|---:|
| sim-s42-28d | 7.23 | Isolation Forest (ajustado) | 76.5 | 61.7 | 68.1 | 75.5 |
| sim-s42-28d | 7.23 | Isolation Forest (sem rótulos) | 69.2 | 27.5 | 39.3 | 36.3 |
| sim-s42-28d | 7.23 | Regras | 96.7 | 68.5 | 80.2 | 73.1 |
| sim-s42-28d | 7.23 | Isolation Forest ∪ regras | 80.2 | 79.3 | 79.7 | 86.2 |
| sim-s7-28d | 6.77 | Isolation Forest (ajustado) | 79.3 | 55.7 | 65.3 | 64.3 |
| sim-s7-28d | 6.77 | Isolation Forest (sem rótulos) | 66.8 | 28.3 | 39.7 | 34.9 |
| sim-s7-28d | 6.77 | Regras | 89.9 | 63.1 | 74.1 | 71.8 |
| sim-s7-28d | 6.77 | Isolation Forest ∪ regras | 79.9 | 75.8 | 77.8 | 81.4 |
| sim-s2026-28d | 6.99 | Isolation Forest (ajustado) | 74.7 | 62.7 | 68.1 | 74.3 |
| sim-s2026-28d | 6.99 | Isolation Forest (sem rótulos) | 73.7 | 23.6 | 35.7 | 29.9 |
| sim-s2026-28d | 6.99 | Regras | 93.5 | 73.2 | 82.1 | 74.4 |
| sim-s2026-28d | 6.99 | Isolation Forest ∪ regras | 78.6 | 86.7 | 82.4 | 90.0 |

## Entre simulações (média ± desvio das médias por simulação)

| Detector | Precisão | Recall | F1 | Cenários detectados |
|---|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 76.8 ± 2.3 | 60.0 ± 3.8 | 67.2 ± 1.6 | 71.4 ± 6.2 |
| Isolation Forest (sem rótulos) | 69.9 ± 3.5 | 26.4 ± 2.5 | 38.2 ± 2.2 | 33.7 ± 3.4 |
| Regras | 93.3 ± 3.4 | 68.3 ± 5.1 | 78.8 ± 4.2 | 73.1 ± 1.3 |
| Isolation Forest ∪ regras | 79.5 ± 0.8 | 80.6 ± 5.6 | 80.0 ± 2.3 | 85.9 ± 4.3 |
