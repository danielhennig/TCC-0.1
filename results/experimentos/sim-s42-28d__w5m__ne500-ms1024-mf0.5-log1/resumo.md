# Experimento sim-s42-28d__w5m__ne500-ms1024-mf0.5-log1

Configuração escolhida na validação: `{'n_estimators': 500, 'max_samples': 1024, 'max_features': 0.5, 'log1p': True}`.

## Teste (média ± desvio nas sementes; valores em %)

| Detector | Precisão | Recall | F1 | FP | Cenários detectados | ROC-AUC | PR-AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 74.5 ± 1.9 | 55.8 ± 2.1 | 63.8 ± 1.1 | 65 | 69.8 ± 2.3 | 0.939 ± 0.002 | 0.644 ± 0.008 |
| Isolation Forest (sem rótulos) | 64.7 ± 2.6 | 24.7 ± 2.3 | 35.8 ± 2.7 | 45 | 33.8 ± 3.3 | 0.902 ± 0.010 | 0.456 ± 0.033 |
| Regras | 96.7 | 68.5 | 80.2 | 8 | 73.1 | — | — |
| Isolation Forest ∪ regras | 80.0 ± 1.9 | 78.5 ± 0.9 | 79.2 ± 0.9 | 66 | 84.7 ± 1.1 | — | — |

## Recall por janela, por tipo × intensidade (%)

| Tipo | Intensidade | Janelas | IF | IF sem rótulos | Regras | IF ∪ regras |
|---|---|---:|---:|---:|---:|---:|
| dml_incomum | baixa | 8 | 48.8 ± 9.2 | 6.2 ± 6.6 | 37.5 | 60.0 ± 5.3 |
| dml_incomum | media | 8 | 15.0 ± 5.3 | 5.0 ± 6.5 | 75.0 | 75.0 |
| dml_incomum | alta | 8 | 86.2 ± 4.0 | 17.5 ± 16.9 | 100.0 | 100.0 |
| falha_auth | baixa | 11 | 93.6 ± 8.6 | 0.0 | 100.0 | 100.0 |
| falha_auth | media | 14 | 70.7 ± 2.3 | 0.0 | 92.9 | 92.9 |
| falha_auth | alta | 19 | 81.1 ± 4.4 | 0.0 | 100.0 | 100.0 |
| fora_horario | baixa | 24 | 26.7 ± 3.5 | 4.6 ± 1.3 | 83.3 | 87.5 |
| fora_horario | media | 37 | 63.8 ± 5.4 | 31.4 ± 6.8 | 56.8 | 83.5 ± 2.4 |
| fora_horario | alta | 46 | 71.1 ± 3.8 | 56.3 ± 3.5 | 100.0 | 100.0 |
| leitura_atipica | baixa | 8 | 13.8 ± 4.0 | 0.0 | 12.5 | 25.0 |
| leitura_atipica | media | 16 | 20.6 ± 3.0 | 9.4 ± 6.1 | 18.8 | 39.4 ± 3.0 |
| leitura_atipica | alta | 25 | 24.4 ± 3.5 | 4.8 ± 3.2 | 44.0 | 63.2 ± 1.7 |
| rajada | baixa | 14 | 0.7 ± 2.3 | 5.7 ± 3.0 | 0.0 | 0.7 ± 2.3 |
| rajada | media | 17 | 49.4 ± 7.4 | 42.4 ± 7.7 | 17.6 | 54.7 ± 6.8 |
| rajada | alta | 18 | 57.2 ± 2.7 | 56.7 ± 3.5 | 44.4 | 62.8 ± 2.7 |
| sem_permissao | baixa | 9 | 37.8 ± 5.7 | 2.2 ± 4.7 | 100.0 | 100.0 |
| sem_permissao | media | 14 | 75.0 ± 5.1 | 12.9 ± 10.0 | 100.0 | 100.0 |
| sem_permissao | alta | 17 | 97.1 ± 4.2 | 25.9 ± 16.9 | 100.0 | 100.0 |
| volume_alto | baixa | 8 | 27.5 ± 7.9 | 12.5 ± 10.2 | 50.0 | 52.5 ± 7.9 |
| volume_alto | media | 8 | 98.8 ± 4.0 | 77.5 ± 12.9 | 75.0 | 98.8 ± 4.0 |
| volume_alto | alta | 8 | 100.0 | 100.0 | 100.0 | 100.0 |

## Cenários detectados, por tipo × intensidade (%)

| Tipo | Intensidade | Cenários | IF | IF sem rótulos | Regras | IF ∪ regras |
|---|---|---:|---:|---:|---:|---:|
| dml_incomum | baixa | 8 | 48.8 ± 9.2 | 6.2 ± 6.6 | 37.5 | 60.0 ± 5.3 |
| dml_incomum | media | 8 | 15.0 ± 5.3 | 5.0 ± 6.5 | 75.0 | 75.0 |
| dml_incomum | alta | 8 | 86.2 ± 4.0 | 17.5 ± 16.9 | 100.0 | 100.0 |
| falha_auth | baixa | 8 | 91.2 ± 11.9 | 0.0 | 100.0 | 100.0 |
| falha_auth | media | 8 | 62.5 | 0.0 | 100.0 | 100.0 |
| falha_auth | alta | 8 | 77.5 ± 5.3 | 0.0 | 100.0 | 100.0 |
| fora_horario | baixa | 8 | 80.0 ± 10.5 | 13.8 ± 4.0 | 87.5 | 100.0 |
| fora_horario | media | 8 | 98.8 ± 4.0 | 63.7 ± 10.9 | 62.5 | 100.0 |
| fora_horario | alta | 8 | 100.0 | 96.2 ± 6.0 | 100.0 | 100.0 |
| leitura_atipica | baixa | 4 | 27.5 ± 7.9 | 0.0 | 25.0 | 50.0 |
| leitura_atipica | media | 4 | 25.0 | 30.0 ± 15.8 | 25.0 | 50.0 |
| leitura_atipica | alta | 4 | 55.0 ± 10.5 | 27.5 ± 14.2 | 50.0 | 75.0 |
| rajada | baixa | 8 | 1.2 ± 4.0 | 10.0 ± 5.3 | 0.0 | 1.2 ± 4.0 |
| rajada | media | 8 | 90.0 ± 12.9 | 71.2 ± 8.4 | 25.0 | 90.0 ± 12.9 |
| rajada | alta | 8 | 87.5 | 87.5 | 62.5 | 87.5 |
| sem_permissao | baixa | 8 | 42.5 ± 6.5 | 2.5 ± 5.3 | 100.0 | 100.0 |
| sem_permissao | media | 8 | 100.0 | 21.2 ± 15.6 | 100.0 | 100.0 |
| sem_permissao | alta | 8 | 100.0 | 45.0 ± 24.4 | 100.0 | 100.0 |
| volume_alto | baixa | 8 | 27.5 ± 7.9 | 12.5 ± 10.2 | 50.0 | 52.5 ± 7.9 |
| volume_alto | media | 8 | 98.8 ± 4.0 | 77.5 ± 12.9 | 75.0 | 98.8 ± 4.0 |
| volume_alto | alta | 8 | 100.0 | 100.0 | 100.0 | 100.0 |

## Cenários: Isolation Forest (maioria das sementes) × regras

| Tipo | Ambos | Só IF | Só regras | Nenhum |
|---|---:|---:|---:|---:|
| dml_incomum | 10 | 2 | 7 | 5 |
| falha_auth | 19 | 0 | 5 | 0 |
| fora_horario | 18 | 4 | 2 | 0 |
| leitura_atipica | 1 | 3 | 3 | 5 |
| rajada | 7 | 8 | 0 | 9 |
| sem_permissao | 19 | 0 | 5 | 0 |
| volume_alto | 16 | 2 | 2 | 4 |

## Sensibilidade na validação: hiperparâmetros (F1 %)

| Hiperparâmetro | Valor | F1 | PR-AUC |
|---|---|---:|---:|
| n_estimators | 500 (escolhido) | 57.0 ± 1.7 | 0.500 |
| n_estimators | 100 | 56.9 ± 2.3 | 0.495 |
| n_estimators | 200 | 56.7 ± 2.0 | 0.498 |
| max_samples | 1024 (escolhido) | 57.0 ± 1.7 | 0.500 |
| max_samples | 0.1 | 56.8 ± 0.9 | 0.493 |
| max_samples | 256 | 45.3 ± 1.5 | 0.369 |
| max_features | 0.5 (escolhido) | 57.0 ± 1.7 | 0.500 |
| max_features | 1.0 | 56.7 ± 0.5 | 0.502 |
| max_features | 0.75 | 55.2 ± 1.5 | 0.487 |
| log1p | True (escolhido) | 57.0 ± 1.7 | 0.500 |
| log1p | False | 51.9 ± 1.2 | 0.456 |

## Sensibilidade na validação: tamanho da janela

| Janela (min) | Melhor configuração | F1 (%) | PR-AUC | Cenários detectados (%) | Alertas |
|---:|---|---:|---:|---:|---:|
| 1 | `ne500-ms0.1-mf0.5-log1` | 52.7 ± 1.0 | 0.476 | 79.6 | 394 |
| 5 | `ne500-ms1024-mf0.5-log1` | 57.0 ± 1.7 | 0.500 | 65.6 | 181 |
| 15 | `ne500-ms1024-mf1-log0` | 62.2 ± 0.5 | 0.566 | 65.1 | 115 |

## Tempo de processamento

- leitura e eventos: 5.7 s (36708 linhas/s)
- agregação por janela: 3.2 s
- treino do modelo: 0.43 s (média)
- score do teste: 0.093 s (média)
- busca em grade (validação): 72 s
