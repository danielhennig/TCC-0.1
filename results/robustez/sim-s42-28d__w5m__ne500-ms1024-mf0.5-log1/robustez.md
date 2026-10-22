# Robustez a outras simulações (configuração congelada `ne500-ms1024-mf0.5-log1`)

Média das sementes de avaliação em cada simulação; valores em %.

| Simulação | Contaminação do teste | Detector | Precisão | Recall | F1 | Cenários detectados |
|---|---:|---|---:|---:|---:|---:|
| sim-s42-28d | 7.23 | Isolation Forest (ajustado) | 74.5 | 55.8 | 63.8 | 69.8 |
| sim-s42-28d | 7.23 | Isolation Forest (sem rótulos) | 64.7 | 24.7 | 35.8 | 33.8 |
| sim-s42-28d | 7.23 | Regras | 96.7 | 68.5 | 80.2 | 73.1 |
| sim-s42-28d | 7.23 | Isolation Forest ∪ regras | 80.0 | 78.5 | 79.2 | 84.7 |
| sim-s7-28d | 6.77 | Isolation Forest (ajustado) | 73.1 | 54.1 | 62.1 | 63.5 |
| sim-s7-28d | 6.77 | Isolation Forest (sem rótulos) | 63.1 | 27.6 | 38.3 | 34.4 |
| sim-s7-28d | 6.77 | Regras | 89.9 | 63.1 | 74.1 | 71.8 |
| sim-s7-28d | 6.77 | Isolation Forest ∪ regras | 75.6 | 76.2 | 75.9 | 81.6 |
| sim-s2026-28d | 6.99 | Isolation Forest (ajustado) | 71.8 | 53.7 | 61.4 | 67.3 |
| sim-s2026-28d | 6.99 | Isolation Forest (sem rótulos) | 72.3 | 21.8 | 33.5 | 27.9 |
| sim-s2026-28d | 6.99 | Regras | 93.5 | 73.2 | 82.1 | 74.4 |
| sim-s2026-28d | 6.99 | Isolation Forest ∪ regras | 78.4 | 85.9 | 82.0 | 88.5 |

## Entre simulações (média ± desvio das médias por simulação)

| Detector | Precisão | Recall | F1 | Cenários detectados |
|---|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 73.1 ± 1.4 | 54.5 ± 1.1 | 62.4 ± 1.2 | 66.9 ± 3.2 |
| Isolation Forest (sem rótulos) | 66.7 ± 4.9 | 24.7 ± 2.9 | 35.9 ± 2.4 | 32.1 ± 3.6 |
| Regras | 93.3 ± 3.4 | 68.3 ± 5.1 | 78.8 ± 4.2 | 73.1 ± 1.3 |
| Isolation Forest ∪ regras | 78.0 ± 2.2 | 80.2 ± 5.0 | 79.0 ± 3.0 | 85.0 ± 3.5 |
