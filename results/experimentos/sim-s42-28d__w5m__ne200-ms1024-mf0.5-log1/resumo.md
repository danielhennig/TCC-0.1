# Experimento sim-s42-28d__w5m__ne200-ms1024-mf0.5-log1

Configuração escolhida na validação: `{'n_estimators': 200, 'max_samples': 1024, 'max_features': 0.5, 'log1p': True}`.

## Teste (média ± desvio nas sementes; valores em %)

| Detector | Precisão | Recall | F1 | FP | Cenários detectados | ROC-AUC | PR-AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 76.5 ± 3.7 | 61.7 ± 4.3 | 68.1 ± 2.0 | 65 | 75.5 ± 4.7 | 0.953 ± 0.003 | 0.700 ± 0.017 |
| Isolation Forest (sem rótulos) | 69.2 ± 2.3 | 27.5 ± 2.1 | 39.3 ± 2.3 | 41 | 36.3 ± 3.6 | 0.918 ± 0.006 | 0.507 ± 0.021 |
| Regras | 96.7 | 68.5 | 80.2 | 8 | 73.1 | — | — |
| Isolation Forest ∪ regras | 80.2 ± 3.8 | 79.3 ± 1.1 | 79.7 ± 1.3 | 67 | 86.2 ± 1.3 | — | — |

## Recall por janela, por tipo × intensidade (%)

| Tipo | Intensidade | Janelas | IF | IF sem rótulos | Regras | IF ∪ regras |
|---|---|---:|---:|---:|---:|---:|
| dml_incomum | baixa | 8 | 61.3 ± 4.0 | 7.5 ± 10.5 | 37.5 | 62.5 |
| dml_incomum | media | 8 | 36.2 ± 4.0 | 7.5 ± 6.5 | 75.0 | 75.0 |
| dml_incomum | alta | 8 | 92.5 ± 6.5 | 22.5 ± 18.4 | 100.0 | 100.0 |
| falha_auth | baixa | 11 | 87.3 ± 8.8 | 0.0 | 100.0 | 100.0 |
| falha_auth | media | 14 | 71.4 ± 6.7 | 0.0 | 92.9 | 92.9 |
| falha_auth | alta | 19 | 84.7 ± 6.8 | 1.1 ± 3.3 | 100.0 | 100.0 |
| fora_horario | baixa | 24 | 50.4 ± 6.3 | 8.8 ± 5.0 | 83.3 | 87.1 ± 1.3 |
| fora_horario | media | 37 | 68.4 ± 6.9 | 43.0 ± 5.0 | 56.8 | 82.2 ± 3.2 |
| fora_horario | alta | 46 | 72.2 ± 6.5 | 56.3 ± 3.3 | 100.0 | 100.0 |
| leitura_atipica | baixa | 8 | 18.8 ± 6.6 | 1.2 ± 4.0 | 12.5 | 25.0 |
| leitura_atipica | media | 16 | 28.7 ± 5.3 | 16.2 ± 7.3 | 18.8 | 43.1 ± 2.0 |
| leitura_atipica | alta | 25 | 36.4 ± 6.9 | 13.2 ± 10.7 | 44.0 | 66.8 ± 1.9 |
| rajada | baixa | 14 | 2.9 ± 3.7 | 5.7 ± 3.0 | 0.0 | 2.9 ± 3.7 |
| rajada | media | 17 | 47.1 ± 11.8 | 41.8 ± 6.5 | 17.6 | 55.3 ± 9.7 |
| rajada | alta | 18 | 56.7 ± 2.3 | 57.8 ± 2.9 | 44.4 | 62.2 ± 2.3 |
| sem_permissao | baixa | 9 | 47.8 ± 14.9 | 1.1 ± 3.5 | 100.0 | 100.0 |
| sem_permissao | media | 14 | 84.3 ± 4.5 | 12.1 ± 9.6 | 100.0 | 100.0 |
| sem_permissao | alta | 17 | 97.6 ± 4.1 | 23.5 ± 15.4 | 100.0 | 100.0 |
| volume_alto | baixa | 8 | 55.0 ± 18.8 | 13.8 ± 10.9 | 50.0 | 71.2 ± 8.4 |
| volume_alto | media | 8 | 93.8 ± 13.5 | 78.8 ± 13.2 | 75.0 | 96.2 ± 8.4 |
| volume_alto | alta | 8 | 100.0 | 100.0 | 100.0 | 100.0 |

## Cenários detectados, por tipo × intensidade (%)

| Tipo | Intensidade | Cenários | IF | IF sem rótulos | Regras | IF ∪ regras |
|---|---|---:|---:|---:|---:|---:|
| dml_incomum | baixa | 8 | 61.3 ± 4.0 | 7.5 ± 10.5 | 37.5 | 62.5 |
| dml_incomum | media | 8 | 36.2 ± 4.0 | 7.5 ± 6.5 | 75.0 | 75.0 |
| dml_incomum | alta | 8 | 92.5 ± 6.5 | 22.5 ± 18.4 | 100.0 | 100.0 |
| falha_auth | baixa | 8 | 83.8 ± 10.3 | 0.0 | 100.0 | 100.0 |
| falha_auth | media | 8 | 65.0 ± 7.9 | 0.0 | 100.0 | 100.0 |
| falha_auth | alta | 8 | 83.8 ± 10.3 | 1.2 ± 4.0 | 100.0 | 100.0 |
| fora_horario | baixa | 8 | 91.2 ± 15.6 | 26.2 ± 15.0 | 87.5 | 98.8 ± 4.0 |
| fora_horario | media | 8 | 98.8 ± 4.0 | 81.2 ± 8.8 | 62.5 | 100.0 |
| fora_horario | alta | 8 | 100.0 | 97.5 ± 5.3 | 100.0 | 100.0 |
| leitura_atipica | baixa | 4 | 37.5 ± 13.2 | 2.5 ± 7.9 | 25.0 | 50.0 |
| leitura_atipica | media | 4 | 40.0 ± 12.9 | 25.0 ± 11.8 | 25.0 | 50.0 |
| leitura_atipica | alta | 4 | 87.5 ± 17.7 | 47.5 ± 18.4 | 50.0 | 92.5 ± 12.1 |
| rajada | baixa | 8 | 5.0 ± 6.5 | 10.0 ± 5.3 | 0.0 | 5.0 ± 6.5 |
| rajada | media | 8 | 82.5 ± 15.8 | 71.2 ± 6.0 | 25.0 | 87.5 ± 11.8 |
| rajada | alta | 8 | 87.5 | 87.5 | 62.5 | 87.5 |
| sem_permissao | baixa | 8 | 53.8 ± 16.7 | 1.2 ± 4.0 | 100.0 | 100.0 |
| sem_permissao | media | 8 | 100.0 | 20.0 ± 14.7 | 100.0 | 100.0 |
| sem_permissao | alta | 8 | 100.0 | 43.8 ± 26.5 | 100.0 | 100.0 |
| volume_alto | baixa | 8 | 55.0 ± 18.8 | 13.8 ± 10.9 | 50.0 | 71.2 ± 8.4 |
| volume_alto | media | 8 | 93.8 ± 13.5 | 78.8 ± 13.2 | 75.0 | 96.2 ± 8.4 |
| volume_alto | alta | 8 | 100.0 | 100.0 | 100.0 | 100.0 |

## Cenários: Isolation Forest (maioria das sementes) × regras

| Tipo | Ambos | Só IF | Só regras | Nenhum |
|---|---:|---:|---:|---:|
| dml_incomum | 13 | 2 | 4 | 5 |
| falha_auth | 18 | 0 | 6 | 0 |
| fora_horario | 20 | 4 | 0 | 0 |
| leitura_atipica | 4 | 4 | 0 | 4 |
| rajada | 7 | 8 | 0 | 9 |
| sem_permissao | 20 | 0 | 4 | 0 |
| volume_alto | 17 | 4 | 1 | 2 |

## Sensibilidade na validação: hiperparâmetros (F1 %)

| Hiperparâmetro | Valor | F1 | PR-AUC |
|---|---|---:|---:|
| n_estimators | 200 (escolhido) | 61.0 ± 2.6 | 0.548 |
| n_estimators | 500 | 60.4 ± 2.2 | 0.549 |
| n_estimators | 100 | 60.3 ± 3.1 | 0.542 |
| max_samples | 1024 (escolhido) | 61.0 ± 2.6 | 0.548 |
| max_samples | 0.1 | 60.3 ± 2.0 | 0.547 |
| max_samples | 256 | 47.6 ± 2.5 | 0.408 |
| max_features | 0.5 (escolhido) | 61.0 ± 2.6 | 0.548 |
| max_features | 1.0 | 59.9 ± 1.8 | 0.549 |
| max_features | 0.75 | 58.7 ± 1.2 | 0.537 |
| log1p | True (escolhido) | 61.0 ± 2.6 | 0.548 |
| log1p | False | 56.7 ± 3.5 | 0.512 |

## Sensibilidade na validação: tamanho da janela

| Janela (min) | Melhor configuração | F1 (%) | PR-AUC | Cenários detectados (%) | Alertas |
|---:|---|---:|---:|---:|---:|
| 1 | `ne100-ms0.1-mf0.75-log1` | 58.2 ± 1.3 | 0.554 | 82.0 | 422 |
| 5 | `ne200-ms1024-mf0.5-log1` | 61.0 ± 2.6 | 0.548 | 70.2 | 191 |
| 15 | `ne500-ms1024-mf0.5-log0` | 68.8 ± 1.3 | 0.643 | 67.8 | 106 |

## Tempo de processamento

- leitura e eventos: 5.7 s (36579 linhas/s)
- agregação por janela: 3.5 s
- treino do modelo: 0.17 s (média)
- score do teste: 0.037 s (média)
- busca em grade (validação): 71 s
