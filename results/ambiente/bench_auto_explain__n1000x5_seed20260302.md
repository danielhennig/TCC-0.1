| Condição | Comandos | Mediana (µs) | p95 (µs) | Média (µs) | Overhead da mediana | Bytes de log/comando |
|---|---:|---:|---:|---:|---:|---:|
| sem_log | 5000 | 790.3 | 2235.6 | 900.7 | 0.0% | 0.5 |
| log_comandos | 5000 | 821.9 | 2266.9 | 933.0 | 4.0% | 539.2 |
| sacla | 5000 | 865.2 | 2995.4 | 1076.4 | 9.5% | 10079.0 |
| auto_explain_timing_on | 5000 | 919.9 | 3142.5 | 1142.7 | 16.4% | 11348.6 |
| auto_explain_texto | 5000 | 842.4 | 2640.7 | 1005.3 | 6.6% | 3767.1 |
