# Decisões técnicas do SACLA

Registro das decisões de projeto, com a fonte na documentação oficial e, quando houver,
a evidência empírica obtida no próprio ambiente. Atualizado ao fim de cada fase.

Evidências da Fase 1: `python tasks.py smoke` (linhas reais em
`tests/fixtures/jsonlog_smoke.jsonl` e verificações em `results/ambiente/smoke_checks.json`)
e `python tasks.py bench` (`results/ambiente/bench_auto_explain__n1000x5_seed20260302.*`).

## 1. Banco de dados e base de exemplo

**D1 — PostgreSQL 18.6 com pgvector 0.8.7** (`pgvector/pgvector:0.8.7-pg18-bookworm`, fixado
por digest). A proposta falava em "PostgreSQL ≥ 15", mas o schema atual do Pagila usa
`uuidv7()` e colunas geradas `VIRTUAL` (PG 18+) e executa `CREATE EXTENSION vector`, que não
vem na imagem oficial do PostgreSQL. A imagem do pgvector é a oficial do PostgreSQL com a
extensão compilada e inclui os módulos contrib (`auto_explain`).

**D2 — Pagila fixado no commit `ce6e1142`** (o repositório não publica tags), com SHA-256 dos
dois arquivos SQL conferidos no build (`ADD --checksum`). As contagens de 15 tabelas são
validadas a cada `setup` (ex.: rental 51.805, payment 51.061, customer 999, staff 1.500).

**D3 — Partições de `payment` até 2027-12.** O Pagila traz partições mensais até 2026-07; um
`INSERT` com `payment_date = now()` falharia sem elas. Criadas pelo provisionamento.

**D4 — `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.** O dump do Pagila concede `ALL` no
schema a `PUBLIC`; os usuários simulados não devem criar objetos.

## 2. Configuração dos logs (`docker/postgres/sacla.conf`)

Fonte: [Error Reporting and Logging](https://www.postgresql.org/docs/18/runtime-config-logging.html).

**D5 — `log_destination='jsonlog'` com `logging_collector=on`.** O jsonlog exige o coletor
("logging_collector must be enabled to generate JSON-format log output"). O coletor também
cria um arquivo `.log` (stderr), quase vazio; o SACLA lê apenas `*.json`.

**D6 — `log_statement='none'` + `log_min_duration_statement=0`.** A proposta previa
`log_statement='all'` junto com a duração, "para que a duração saia junto com o comando". A
documentação diz o contrário: "the text of statements that are logged because of
log_statement will not be repeated in the duration log message". Sem `log_statement`,
cada comando concluído gera **uma** linha `duration: X ms  statement: <texto>`.
*Evidência:* verificação `uma_linha_por_comando`.

**D7 — `log_min_error_statement='error'` (padrão).** Comandos que falham aparecem na linha
`ERROR` no campo `statement`, inclusive erros de sintaxe, que nem o `log_statement='all'`
registraria. *Evidência:* `erro_permissao_42501` (`statement` = o SELECT em `staff`) e
`erro_sintaxe_42601` (`statement` = `SELEC 1`).

**D8 — Protocolo simples nos simuladores (`psycopg.ClientCursor`).** "For clients using
extended query protocol, durations of the Parse, Bind, and Execute steps are logged
independently." A [documentação do psycopg](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html)
garante o protocolo simples com `ClientCursor`. O parser do SACLA também aceitará o formato
estendido, contando só a linha `execute`.
*Evidência:* `protocolo_estendido_em_3_linhas` — linhas `parse <unnamed>`, `bind <unnamed>` e
`execute <unnamed>` separadas, com os parâmetros no campo `detail`.

**D9 — `log_connections='receipt,authentication,authorization'`, `log_disconnections=on`.**
No PG 18 o parâmetro virou uma lista ("on" equivale a esses três). Falhas de autenticação são
registradas sempre.

**D10 — `log_timezone='UTC'`.** Os horários de expediente são aplicados na agregação, com o
fuso configurado (America/Sao_Paulo).

**D11 — `compute_query_id=on`.** Preenche `query_id`. *Evidência:* 10 de 10 linhas de duração
com `query_id` preenchido.

### Fatos do formato observados no ambiente (importantes para o parser — Fase 3)

| Linha | Observação |
|---|---|
| `connection received` | sem `user`, `backend_type="not initialized"`; o usuário só aparece nas linhas seguintes da mesma `session_id` |
| `connection authenticated` / `authorized` | com `user`; `authorized` traz o `application_name` na mensagem |
| `FATAL 28P01` (senha errada) | **traz `user`** (o usuário atacado) e `remote_host`; **não traz `application_name`**; `detail` indica a linha do `pg_hba.conf` |
| `ERROR 42501` | mensagem `permission denied for table <tabela>`; comando no campo `statement`; `query_id` preenchido |
| `ERROR 42601` | comando no campo `statement`; `query_id = 0` |
| plano do `auto_explain` | protocolo **simples**: vem **antes** da linha de duração (mesma sessão). Protocolo **estendido**: vem **depois** da linha `execute` |
| `BEGIN` / `COMMIT` | linhas de duração próprias, sem plano |
| `timestamp` | precisão de milissegundos; `session_start` com precisão de segundos |

## 3. Linhas retornadas e afetadas (item 2 da proposta)

Fonte: [auto_explain](https://www.postgresql.org/docs/18/auto-explain.html).

**D12 — `auto_explain` com `log_analyze=on` e `log_timing=off`, formato JSON.** O log padrão
não informa quantas linhas um comando retornou. Com `log_analyze`, o plano registrado traz a
contagem real de linhas de cada nó; `log_timing=off` evita cronometrar cada nó ("it may be
useful to set this parameter to off when only actual row counts, and not exact times, are
needed"). Regras de extração:

- **linhas retornadas** = `Actual Rows` do nó raiz;
- **linhas afetadas** (INSERT/UPDATE/DELETE sem `RETURNING`) = `Actual Rows` do filho do
  `ModifyTable` (a raiz mostra 0);
- **tabelas acessadas** = todos os `Relation Name` do plano.

*Evidência:* `linhas_retornadas_auto_explain` (51.805 no plano = 51.805 no cliente) e
`linhas_afetadas_update` (1 = 1). No `INSERT` em `payment` sem `RETURNING`, a raiz
`ModifyTable` mostrou `Actual Rows = 0`, como esperado.

**Overhead medido** (`python tasks.py bench`: 5 repetições × 1.000 comandos por condição, mesma
carga em ordem rotativa, latência medida no cliente):

| Condição | Mediana (µs) | p95 (µs) | Overhead da mediana | Bytes de log/comando |
|---|---:|---:|---:|---:|
| sem registro de comandos | 790,3 | 2.235,6 | — | 0,5 |
| só comandos (`log_min_duration_statement=0`) | 821,9 | 2.266,9 | +4,0% | 539,2 |
| **SACLA** (comandos + auto_explain JSON, `log_timing=off`) | 865,2 | 2.995,4 | **+9,5%** | 10.079,0 |
| auto_explain JSON com `log_timing=on` | 919,9 | 3.142,5 | +16,4% | 11.348,6 |
| auto_explain em texto, `log_timing=off` | 842,4 | 2.640,7 | +6,6% | 3.767,1 |

Duas execuções anteriores (com uma versão preliminar da carga) deram, na mesma ordem,
+1,5% / +9,7% / +14,5% (sem a condição em texto) e +3,9% / +11,6% / +21,0% / +8,9%. A variação
entre execuções é de alguns pontos percentuais; os valores devem ser lidos como ordem de
grandeza. Os bytes de log por comando são estáveis entre execuções.

**Por que JSON e não texto:** o texto gera 2,7× menos log e um overhead da mesma ordem, dentro
da variação entre execuções. Mas o JSON entrega `Relation Name` e `Actual Rows` como campos tipados. No texto, as
tabelas teriam de ser extraídas por expressões regulares que dependem do tipo de nó
(`Seq Scan on`, `Index Scan using … on`, `Update on`, `CTE Scan on`…), o que é frágil. O
volume maior (~2 GB brutos estimados para a simulação completa) é contornado comprimindo os
logs com gzip na coleta.

**Limitações:** (i) overhead de ~10% na latência mediana e ~10 KB de log por comando, o que
desaconselha usar a configuração em produção sem avaliação; (ii) comandos que falham não
geram plano, então ficam sem contagem de linhas; (iii) comandos utilitários (DDL, `BEGIN`)
não passam pelo executor e também não têm plano.

## 4. Horário dos acessos (item 3 da proposta)

**D13 — Agenda simulada + execução sequencial acelerada + remapeamento exato (`retime`).**
Implementação na Fase 2. A agenda é gerada em tempo simulado (4 semanas), executada o mais
rápido possível e depois cada linha de log recebe o horário simulado do seu evento. A ligação
é feita por um comentário opaco `/* simlab:e=<id> */` nos comandos, que o `retime` remove;
pelo `session_id` nas linhas de conexão; e pela ordem de execução nas falhas de login. A
Fase 1 confirmou dois fatos necessários: a linha `FATAL 28P01` não traz `application_name`, o
que impede usá-lo como marcador; e cada sessão tem `session_id` e `line_num` próprios.
**Limitação metodológica:** os horários são sintéticos e a execução é sequencial, sem
concorrência real. As durações são reais, mas medidas sem contenção.

**Implementação (Fase 2, `src/simlab/retime.py`).** Duas passagens sobre o log bruto:

1. **Índice:** para cada linha, `session_id`, PID, horário real e a marca, se houver. Cada
   sessão do log é ligada a uma sessão simulada:
   - pela marca dos seus comandos;
   - pelo PID, se a sessão não tiver comandos;
   - pela ordem de execução, nas sessões com `FATAL 28P01`. Aqui o usuário da linha do log
     precisa coincidir com o do evento.
2. **Reescrita:** `timestamp` e `session_start` passam para o tempo simulado. O `session_id`
   é recalculado (início simulado + PID), o "session time" da desconexão é refeito e a marca
   é removida de `message` e `statement`, inclusive do `Query Text` dentro do plano.

Detalhes:
- O deslocamento real dentro de um evento é somado ao horário simulado, mas limitado para
  nunca alcançar o evento seguinte.
- A linha de desconexão é gravada pelo servidor de forma assíncrona, às vezes depois de linhas
  de eventos posteriores. Uma janela de reordenação (`retime_reorder_window`) põe a saída em
  ordem simulada.
- Saída: um arquivo `.json.gz` por dia (UTC), no mesmo formato do jsonlog.

**Verificações que fazem a execução falhar** (`retime_report.json`):
- linha de cliente sem evento;
- sessão do log ligada a duas sessões simuladas;
- número ou usuário das falhas de login diferente do agendado;
- comando executado sem a sua linha de duração (ou de erro);
- linhas do plano diferentes das recebidas pelo cliente;
- saída fora de ordem.

Além disso, `verify.py` cruza o manifesto com o log remapeado: dentro do intervalo de cada
cenário, o log do usuário precisa ter pelo menos tantos comandos e falhas de login quanto o
cenário gerou.

## 5. Ambiente

**D14 — Três redes Docker** (`rede_lan` 172.30.10.0/24, `rede_app` 172.30.20.0/24,
`rede_externa` 172.30.30.0/24). O container `sacla` está nas três; a rede usada depende do
nome do banco na conexão (`db-lan`, `db-app`, `db-externa`). *Evidência:*
`remote_host_por_rede` — 172.30.10.50, 172.30.20.50 e 172.30.30.50 conforme a rede.

**D15 — Healthcheck sem conexão.** O healthcheck verifica a porta 5432 em LISTEN em
`/proc/net/tcp`. Um `pg_isready` periódico apareceria nos logs analisados. Durante o initdb o
servidor temporário não escuta TCP, então "healthy" significa carga concluída.
*Evidência:* nenhuma conexão de healthcheck no log.

**D16 — Carga do Pagila fora do log.** A configuração do SACLA é incluída no
`postgresql.conf` por um script do initdb, depois que o servidor temporário já subiu.
*Evidência:* a primeira linha do jsonlog é a inicialização definitiva do servidor.

**D17 — Sessão administrativa silenciosa.** O provisionamento e o benchmark desligam o
registro de comandos na própria sessão (`SET log_min_duration_statement = -1` etc.), porque
enviam senhas e não fazem parte do comportamento estudado. *Evidência:* o usuário `postgres`
só aparece em linhas de conexão e desconexão, e nenhuma linha contém senha.

**D18 — `sslmode=disable` e `gssencmode=disable` nos clientes.** O servidor do laboratório não
usa TLS; sem isso o libpq poderia fazer duas tentativas de conexão por login, o que dobraria
as linhas de falha de autenticação.

**D19 — Python em container** (`python:3.13.17-slim-bookworm`, fixado por digest, dependências
em `requirements.txt` e `requirements.lock`) e orquestração por `tasks.py`, porque o Windows
não tem `make`.

## 6. Simulação de acessos (Fase 2)

**D20 — Perfis.** 10 usuários em 5 papéis, com privilégios mínimos (`config/profiles.yaml`):

| Usuário(s) | Papel | Comportamento | Origem | Pode | Não pode |
|---|---|---|---|---|---|
| atendente_01..04 | atendente | turnos manhã (08–14) e tarde (13–19), seg–sáb | lan | ler cadastro, catálogo e locações; registrar locação e pagamento | `staff`, relatórios de loja, DELETE |
| gerente_01..02 | gerente | 09–18 seg–sex, hora extra ocasional | lan (10% externa) | SELECT em tudo; alterar cliente, filme, funcionário; estornar pagamento | DELETE em locação; UPDATE em pagamento |
| analista_01..02 | analista | 09–18 seg–sex | lan (20% externa) | só leitura de catálogo, locações, pagamentos e views de relatório | clientes, endereços, `staff` |
| svc_locadora | servico_app | 24 h, curva horária (pico às 20h), conexão reciclada a cada hora | app | catálogo, disponibilidade, cliente, locação online | `staff`, alterar pagamento |
| svc_batch | servico_batch | rotina diária às 02:00 | app | leituras grandes, UPDATE em lote, `REFRESH MATERIALIZED VIEW` (privilégio MAINTAIN) | demais escritas |

As ações de cada papel estão em `config/queries.yaml` (SQL parametrizado). Chegadas seguem um
processo de Poisson com taxa diária variável (lognormal); para o serviço, a taxa é modulada
por hora (afinamento). Cada usuário usa um gerador aleatório próprio, derivado da semente e do
nome: mudar um usuário ou as anomalias não altera o tráfego dos demais.

**D21 — Ruído legítimo.** Sem ele, cada anomalia seria separável por um único atributo. Há:
- erro de digitação: `SELCT`, 0,5% dos SELECT fora de transação, gera 42601;
- senha errada antes de entrar: 2% das sessões interativas;
- engano de permissão: 4% por usuário e dia de trabalho, gera 42501;
- hora extra do gerente;
- extração semanal do analista, que lê milhares de linhas;
- rotina noturna do serviço batch: leituras grandes e UPDATE em lote, de madrugada.

Várias intensidades "baixa" das anomalias ficam de propósito na mesma faixa desse ruído.

**D22 — Anomalias** (`config/anomalies.yaml`): seis tipos × três intensidades, mais o tipo
inédito `leitura_atipica`, que aparece só no teste.

| Tipo | Quando | Baixa / Média / Alta |
|---|---|---|
| rajada | dentro de uma sessão do usuário | 3× / 8× / 20× a taxa normal do usuário, por 3–8 min |
| fora_horario | horário em que o usuário nunca trabalha, nova sessão (60% rede externa) | 5 / 20 / 60 comandos |
| volume_alto | dentro de uma sessão | 2 mil / 10 mil / tabelas inteiras (rental + payment) |
| sem_permissao | dentro de uma sessão | 1–2 / 5 / 15 tentativas negadas |
| falha_auth | qualquer horário (70% rede externa) | 3 / 10 / 40 tentativas |
| dml_incomum | dentro de uma sessão | UPDATE/DELETE permitido em 5 / 50 / 500 linhas |
| leitura_atipica (inédito) | dentro de uma sessão | 8 / 20 / 40 páginas de 50 linhas, a cada 30–60 s |

As anomalias "dentro de uma sessão" usam a conexão já aberta pelo usuário, para que a nova
conexão não denuncie o cenário. As ações de uma sessão nunca se intercalam: um comando de
anomalia não cai dentro de uma transação aberta por uma ação normal.

**Quantidades** (para 28 dias): 20 cenários no treino (tipo e intensidade sorteados), 5 por
tipo × intensidade na validação e 8 no teste, mais 12 do tipo inédito no teste (um por papel ×
intensidade, D37). A distância mínima entre cenários do mesmo usuário é de 30 min.

**D23 — Rótulos isolados.** `events.parquet` (lido pelo executor e pelo retime) não tem
nenhuma coluna de rótulo; o cenário de cada evento só serve para calcular o intervalo do
manifesto (`manifest.csv`). *Evidência:* `test_exported_events_carry_no_label_information`.

**D24 — Executor sequencial.** Um evento por vez, na ordem simulada, com `ClientCursor` e
marca por comando. Como um comando bloqueado por lock de outra sessão simulada travaria a
execução inteira, as sessões usam `lock_timeout = 5 s`; um bloqueio apareceria como erro
inesperado (55P03), que faz a execução falhar. Só 42501 e 42601 são esperados.

**D25 — Execução de referência** (`python tasks.py simulate`: 28 dias, semente 42; evidências
em `results/simulacao/sim-s42-28d/`):

| Medida | Valor |
|---|---|
| Eventos / comandos / falhas de login | 110.738 / 107.909 / 733 |
| Sessões (conexões) | 1.048 |
| Cenários anômalos (treino / validação / teste) | 20 / 90 / 156 (144 + 12 do tipo inédito) |
| Erros provocados (42501 / 42601) / erros inesperados | 324 / 127 / 0 |
| Execução | 101 s (~1.100 eventos/s); pipeline completo ~2,5 min |
| Log bruto / com gzip / remapeado com gzip | 427 MB (com rotação) / 21 MB / 21 MB |
| Linhas remapeadas / sem evento / fora de ordem | 207.769 / 0 / 0 |
| Linhas do plano comparadas com o cliente / divergências | 94.174 / 0 |
| Cenários coerentes com o log remapeado | 266 de 266 |

Valores da simulação refeita após as correções da D37 (a primeira versão tinha 264 cenários e
números da mesma ordem).

Atividade anômala (comandos e falhas de login dos cenários sobre o total de comandos, erros e
falhas de login do split): treino 0,44%, validação 4,35%, teste 8,41%. O teste concentra mais
anomalias para que cada tipo × intensidade tenha 8 casos; a contaminação por janela será
medida na Fase 3.

**Achado — `REFRESH MATERIALIZED VIEW` gera plano no auto_explain**, mesmo com
`log_nested_statements = off`: a consulta interna da view é registrada como plano de topo,
com as linhas gravadas na view (16), enquanto o cliente recebe `rowcount = -1`. *Evidência:*
eventos 26 e 4137 da execução de 2 dias. **Consequência para a Fase 3:** as linhas do plano
de um comando utilitário não são "linhas retornadas ao cliente"; o parser precisa classificar
o comando antes de atribuir as linhas do plano.

## 7. Leitura, tratamento e agregação (Fase 3)

**D26 — Leitura (`sacla/reader.py`, módulo 1).**
- Lê arquivos `.json`, `.json.gz`, `.jsonl` e `.jsonl.gz` em ordem de nome, que acompanha a
  rotação.
- Uma linha que não é JSON, não é objeto ou não tem horário válido é descartada e contada como
  malformada.
- A última linha de um arquivo, sem quebra de linha e incompleta (arquivo ainda em gravação),
  é contada à parte como truncada. Um `.gz` cortado no meio também: as linhas anteriores ao
  corte são mantidas.
- Campos ausentes ou de tipo errado viram nulos; campos desconhecidos são ignorados, como
  recomenda a documentação ("applications … should ignore unknown fields").
- O horário aceita deslocamento numérico (`-03`) e as abreviações de
  `reader.zone_offsets_min` (padrão `UTC` e `GMT`). Uma abreviação desconhecida (ex.: `CET`) é
  contada à parte, com erro explícito, em vez de sumir entre as malformadas. Recomendado:
  `log_timezone = 'UTC'`.

**D27 — Eventos (`sacla/events.py`, módulo 2a).** Cada linha de cliente vira um de cinco
eventos:

| Evento | Origem no log |
|---|---|
| comando concluído | `duration: … statement:` ou `… execute <nome>:` |
| comando com erro | `ERROR` com o campo `statement` |
| falha de autenticação | `FATAL` com SQLSTATE 28P01 ou 28000 |
| conexão | `connection authorized` |
| desconexão | `disconnection` |

Regras:
- As linhas `parse` e `bind` do protocolo estendido não são comandos.
- Linhas sem `user` (`connection received`) herdam o usuário da sessão.
- Processos de fundo e usuários administrativos (`postgres`) são ignorados.
- O plano do auto_explain é ligado ao comando pela sessão **e** pelo texto (`Query Text` = texto
  do comando). No protocolo simples o plano chega antes; no estendido, depois da linha
  `execute`. Os dois casos são tratados.
- Linhas e tabelas do plano só são atribuídas a SELECT/INSERT/UPDATE/DELETE: o plano de um
  `REFRESH MATERIALIZED VIEW` não vira "linhas retornadas".
- Partições contam como a tabela pai (`payment_pAAAA_MM` → `payment`).
- Num erro de permissão, a tabela vem da mensagem (`permission denied for table staff`).

**D28 — Classificação do comando (`sacla/sqlclass.py`).** Classes: SELECT, INSERT, UPDATE,
DELETE, DDL, TCL e OTHER. A classe vem da primeira palavra-chave fora de comentários, literais
e parênteses. Parênteses iniciais são ignorados (`(SELECT …) UNION …` é SELECT), e um `WITH` é
classificado pelo comando principal depois das CTEs (`WITH … UPDATE` é UPDATE). TCL
(`BEGIN`/`COMMIT`) não entra na contagem de comandos nem nas durações.

**D29 — Atributos por usuário × janela (`sacla/features.py`, módulo 2b).** Janelas fixas
`[t, t+5 min)`, alinhadas ao relógio. Só existem janelas com pelo menos um evento do usuário.

| Atributo | Definição |
|---|---|
| `n_commands` | comandos (concluídos ou com erro), sem TCL |
| `n_select` … `n_other` | comandos por classe |
| `n_errors`, `n_permission_denied` | comandos com erro; com SQLSTATE 42501 |
| `n_auth_failures`, `n_connections` | falhas de autenticação; conexões autorizadas |
| `rows_returned_sum`, `rows_returned_max` | linhas retornadas (nó raiz do plano) |
| `rows_affected_sum` | linhas afetadas por INSERT/UPDATE/DELETE (filho do ModifyTable) |
| `duration_mean_ms`, `duration_max_ms`, `duration_p95_ms` | duração dos comandos concluídos |
| `n_tables`, `n_ips` | tabelas distintas (do plano ou da mensagem de permissão); IPs distintos |
| `hour_sin`, `hour_cos`, `weekend` | hora do início da janela (fuso local), codificação cíclica; sábado/domingo |
| `z_commands` | (comandos − mediana do usuário) / max(1,4826·MAD, 1) |
| `z_rows_returned` | idem, sobre log(1 + linhas retornadas) |
| `hour_surprisal` | −log da frequência (suavização de Laplace, α = 1) daquela hora no histórico do usuário |
| `new_tables_frac`, `new_ips_frac`, `new_query_shapes_frac` | fração de tabelas, IPs e formatos de consulta (`query_id`) da janela nunca vistos no histórico do usuário |

Notas:
- **Histórico sem vazamento.** Os atributos relativos usam só janelas que *terminam* até o fim
  do treino (`history_end`, de `run.json`). Além disso, a montagem do conjunto recusa
  tamanhos de janela que não dividem os limites dos splits (ex.: 7 min), para que nenhuma
  janela atravesse de um split para outro. Testes:
  `test_changing_data_after_history_end_does_not_change_baselines` e
  `test_window_crossing_history_end_is_not_part_of_history`.
- **Sem rótulos no histórico.** O histórico usa todas as janelas do treino, inclusive as
  anômalas, porque o SACLA é não supervisionado e não conhece rótulos; a contaminação do
  treino (0,37% das janelas) fica documentada.
- **Usuário sem histórico.** Recebe referência neutra: z sobre zero, hora com probabilidade
  1/24, tudo "inédito".
- **Formatos de consulta inéditos.** Este atributo vai além da lista mínima. Foi incluído porque
  o tipo inédito (`leitura_atipica`) usa tabelas permitidas e já conhecidas; o único sinal
  genérico é o formato de consulta novo para o usuário. **Ver D41 e D45:** com o histórico
  calculado sobre o próprio treino (v1), as três frações de novidade valiam 0 em todas as
  janelas de treino e o Isolation Forest não as usava; desde a D45, cada janela de treino é
  comparada com os outros dias do usuário.
- **Transformação `log1p`** das contagens: é decisão do modelo e será avaliada na validação
  (Fase 4).

**D30 — Regra de rótulo (`evaluation/labels.py`).** A janela `[t, t+Δ)` do usuário *u* é
anômala se, e somente se, existe um cenário do manifesto com usuário *u* e intervalo
`[início, fim)` tal que `início < t+Δ` e `t < fim`. Detalhes:
- Os intervalos são semiabertos: uma janela que termina exatamente no início de um cenário, ou
  começa exatamente no fim dele, não é anômala.
- Com vários cenários na mesma janela, todos ficam em `scenario_ids`; tipo, intensidade e
  "inédito" vêm do de maior sobreposição, e `overlap_frac` registra a fração coberta.
- O split de cada janela vem do seu início.
- Testes: `tests/test_labels.py`.

**D31 — Conferência com o executor (`evaluation/crosscheck.py`).** Ao montar o conjunto de
dados, os eventos do SACLA são comparados, por usuário, com o que o executor de fato fez
(comandos, comandos com erro, falhas de login, conexões e linhas). Qualquer diferença faz a
montagem falhar sem gravar o conjunto de dados; só o resumo é gravado, para diagnóstico.

Resultado na execução de referência (`python tasks.py features`, janela de 5 min):

| Medida | Valor |
|---|---|
| Linhas lidas / malformadas / truncadas | 207.769 / 0 / 0 (29 arquivos) |
| Eventos / planos sem par / planos ilegíveis | 110.738 / 0 / 0 |
| Linhas ignoradas | 2.829 = `connection received` (1.781) + `connection authenticated` (1.048) |
| Conferência (comandos, erros, falhas de login, conexões, linhas) | 107.909 · 451 · 733 · 1.048 · 2.743.519 — **iguais ao executor para os 10 usuários** |
| Janelas: treino / validação / teste | 9.109 / 4.684 / 4.662 |
| Janelas anômalas (contaminação) | 34 (0,37%) / 176 (3,76%) / 337 (7,23%) |
| Tempo: leitura + eventos / agregação | 5,7 s / 3,2 s |

A execução de 2 dias passa pela mesma conferência (8.224 comandos, iguais por usuário).

## 8. Modelo, avaliação e baseline (Fase 4)

**D32 — Isolation Forest (`sacla/model.py`, módulo 3).** É o único algoritmo, do
scikit-learn.
- O score de anomalia é `−score_samples`: quanto maior, mais anômala a janela.
- `contamination` fica em `'auto'`. No scikit-learn esse parâmetro só define o limiar
  (`offset_`) e não muda as árvores; por isso o SACLA usa um limiar de score explícito, e
  ajustar `contamination` e o limiar seria ajustar a mesma coisa duas vezes.
- `log(1 + x)` nas contagens e volumes é um hiperparâmetro (`log1p`), escolhido na validação.
- `n_jobs = 1`: com ~9 mil janelas, o treino paralelo foi mais lento (0,58 s contra 0,45 s com
  500 árvores), por causa do custo de coordenação.

**D33 — Protocolo de avaliação (`evaluation/experiment.py`).**
1. **Treino:** split de treino inteiro, sem rótulos, inclusive as janelas anômalas
   (contaminação real de 0,37%).
2. **Ajuste, só na validação:** grade completa de `n_estimators` {100, 200, 500} ×
   `max_samples` {256, 1024, 0,1} × `max_features` {0,5; 0,75; 1,0} × `log1p` {sim, não} = 54
   combinações, cada uma com 5 sementes. Para cada uma, o limiar de melhor F1 na validação.
   Escolhe-se a combinação de maior F1 médio (desempate: PR-AUC).
3. **Teste, usado uma vez:** a configuração escolhida é treinada com 10 sementes; o limiar de
   cada semente vem da validação. São reportadas média e desvio. As 10 sementes medem a
   variação do modelo; o teste não influencia nenhuma escolha.
4. **Configuração sem rótulos:** padrões do scikit-learn e limiar no quantil 99% dos scores do
   próprio treino. Mostra o que se obtém sem nenhuma janela rotulada, porque ajustar na
   validação é otimista: em produção não há rótulos.
5. **Métricas no teste:**
   - por janela: precisão, recall, F1, FP (contagem e taxa), ROC-AUC e PR-AUC;
   - por tipo × intensidade: recall por janela;
   - por cenário: o cenário é detectado se ao menos uma de suas janelas é sinalizada;
   - tempo de cada etapa.

**D34 — Baseline de regras (`config/rules.yaml`, `evaluation/rules_baseline.py`).** Seis
regras, com limiares fixados antes de rodar a avaliação. As estatísticas por usuário vêm só do
treino.

| Regra | Dispara quando |
|---|---|
| falhas_autenticacao | 3 ou mais falhas de login na janela |
| erro_permissao | 1 ou mais erros de permissão |
| rajada | comandos acima de 3 × o p99 do usuário |
| volume | linhas retornadas acima do máximo do usuário |
| dml_volume | linhas afetadas acima do máximo do usuário |
| hora_inedita | hora nunca vista no histórico do usuário |

As regras foram escritas pensando nos seis tipos conhecidos; o tipo inédito não tem regra, de
propósito. Para responder se o Isolation Forest "agrega algo além das regras", são
reportados:
- os três detectores lado a lado;
- a combinação IF ∪ regras;
- por cenário, o que só o IF detecta, o que só as regras detectam, o que ambos detectam e o
  que nenhum detecta.

**D35 — Sensibilidade, só na validação.**
- Hiperparâmetros: varia um de cada vez, com os demais na configuração escolhida (sai da
  grade).
- Tamanho da janela (1, 5 e 15 min): o conjunto é reagregado e a grade é refeita para cada
  tamanho. O F1 por janela não é comparável entre tamanhos, porque a unidade muda; por isso
  também se compara a fração de cenários detectados.

**D36 — Robustez à semente da simulação.** A configuração escolhida fica congelada e é
reavaliada em duas novas simulações (sementes 7 e 2026), com o limiar escolhido na validação
de cada uma (`python tasks.py robustness --exp-id <experimento>`).

**Ambiente:** testes, lint, conjuntos e avaliação rodam no serviço `analise` (mesma imagem,
sem IPs fixos). Por isso podem rodar junto com uma simulação, que ocupa os IPs fixos do
serviço `sacla`.

**D37 — Correções na simulação antes da avaliação final.** A primeira rodada da avaliação
expôs três defeitos. Eles foram corrigidos, e a simulação, os conjuntos e a avaliação foram
refeitos do zero. Nenhum hiperparâmetro, atributo ou regra foi alterado em função do teste.
- **Tipo inédito confundido com o usuário.** Todos os cenários de intensidade alta caíam no
  mesmo atendente. Agora o plano é balanceado: cada papel × intensidade aparece o mesmo número
  de vezes (`count_per_role_intensity`). Em execuções mais curtas, usa-se um subconjunto em
  diagonal que cobre todos os papéis e intensidades.
- **Paginação passando do fim da tabela.** Isso gerava páginas vazias, com zero linhas. O
  deslocamento agora dá a volta dentro da tabela, e a configuração rejeita tabelas menores que
  uma página.
- **Sensibilidade de `max_samples` incompleta.** A coluna misturava inteiros e frações e
  perdia valores. Agora ela é gravada como texto.

**D38 — Resultados da Fase 4 (v1).** Arquivados em
`results/historico/v1-historico-na-amostra/`. São os resultados com os atributos relativos do
treino calculados na amostra (D41); a versão corrigida está na D46. O teste tem 4.662
janelas, 337 delas anômalas (7,23%), de 156
cenários. Configuração escolhida na validação: 500 árvores, `max_samples` 1024,
`max_features` 0,5 e `log1p`.

| Detector (teste, semente 42) | Precisão | Recall | F1 | FP | Cenários detectados | ROC-AUC | PR-AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| Isolation Forest (ajustado) | 74,5 ± 1,9 | 55,8 ± 2,1 | 63,8 ± 1,1 | 65 | 69,8 ± 2,3 | 0,939 | 0,644 |
| Isolation Forest (sem rótulos) | 64,7 ± 2,6 | 24,7 ± 2,3 | 35,8 ± 2,7 | 45 | 33,8 ± 3,3 | 0,902 | 0,456 |
| Regras | 96,7 | 68,5 | 80,2 | 8 | 73,1 | — | — |
| Isolation Forest ∪ regras | 80,0 ± 1,9 | 78,5 ± 0,9 | 79,2 ± 0,9 | 66 | 84,7 ± 1,1 | — | — |

Entre as três simulações (sementes 42, 7 e 2026; média ± desvio das médias), o F1 foi:

| Detector | F1 entre simulações | Cenários detectados |
|---|---:|---:|
| IF ajustado | 62,4 ± 1,2 | 66,9 ± 3,2 |
| IF sem rótulos | 35,9 ± 2,4 | 32,1 ± 3,6 |
| Regras | 78,8 ± 4,2 | 73,1 ± 1,3 |
| IF ∪ regras | 79,0 ± 3,0 | 85,0 ± 3,5 |

O resultado do IF quase não depende da semente da simulação (desvio de 1,2 ponto no F1). As
regras variam mais (4,2 pontos).

**Interpretação**
1. **As regras vencem o IF sozinho, como esperado.** Elas foram escritas conhecendo os seis
   tipos, e o comportamento normal simulado é muito regular. A principal ameaça à validade é
   esse viés de quem projeta os dois lados (ver item 6).
2. **O IF agrega detecção.** A combinação IF ∪ regras eleva o recall de 68,5% para 78,5% e os
   cenários detectados de 73,1% para 84,7%, ao custo de 8 → 66 falsos positivos. Os cenários
   pegos só pelo IF (maioria das sementes) concentram-se em:
   - `rajada` (8 cenários);
   - `fora_horario` (4);
   - o tipo inédito `leitura_atipica` (3).

   Os cenários pegos só pelas regras concentram-se em `dml_incomum` (7), `falha_auth` (5) e
   `sem_permissao` (5). Nesses três tipos, um único evento já é sinal forte, e uma regra de
   limiar fixo o captura sem custo.
3. **Tipo inédito (`leitura_atipica`).** O IF detecta 25–55% dos cenários conforme a
   intensidade, e a união chega a 50–75%. As regras pegam alguns cenários pela regra `volume`
   (15 das 49 janelas), mas só para usuários de pouco volume: gerentes e um atendente. Isso é
   coincidência do desenho, não uma regra para o tipo. Por cenário:
   - os 3 dos analistas são detectados só pelo IF;
   - os 3 dos gerentes, só pelas regras;
   - 1 de atendente, por ambos;
   - os 3 do serviço `svc_locadora` e 2 de atendentes, por nenhum.

   Para o serviço, que já lê clientes o dia todo, a paginação lenta se mistura ao tráfego
   normal.
4. **Sem rótulos o desempenho cai pela metade** (F1 35,8). Em particular, `falha_auth` fica
   em 0%. Uma janela só com falhas de login tem quase todos os atributos zerados e parece
   "pouca atividade", que é comum. O quantil 99% do treino é um limiar conservador. Ajustar o
   limiar na validação é a parte que mais depende de rótulos.
5. **O que ninguém detecta.**
   - `rajada` baixa (3× a taxa por 2–10 min) fica dentro da variação normal de uma janela de
     5 min.
   - `dml_incomum` média tem recall menor que a baixa (15% contra 49% no IF). Há dois motivos:
     - **Sorteio de papéis.** A intensidade média caiu em atendentes e no serviço, para quem
       UPDATE é rotina. A baixa caiu em gerentes, para quem DELETE é raro. É a mesma confusão
       papel × intensidade corrigida no tipo inédito (D37); nos tipos conhecidos ela foi mantida
       porque o sorteio é aleatório e documentado.
     - **Contaminação do treino.** Um cenário `dml_incomum` médio caiu no treino em
       `svc_locadora` e elevou o máximo de linhas afetadas desse usuário para 52. Isso mascara
       os cenários de 50 linhas tanto para a regra `dml_volume` quanto para o IF.

     Esse efeito é real e vale para qualquer referência por máximo: anomalias no histórico
     elevam o limiar.
   - O IF não tem atributo relativo de linhas afetadas (há z robusto só para comandos e linhas
     retornadas). Isso é uma limitação registrada, não corrigida: alterar atributos depois de
     ver o teste violaria o protocolo.
6. **Ameaças à validade.**
   - Dados sintéticos com rotina regular.
   - O mesmo autor desenha anomalias, atributos e regras.
   - Um sorteio de papéis por tipo.
   - Contaminação de treino pequena (0,37%).
   - Três simulações apenas.
7. **Janela.** A janela de 1 min detecta mais cenários na validação (79,6% contra 65,6%),
   porém com o dobro de alertas (394 contra 181). A de 15 min tem F1 maior porque cada janela
   agrega mais sinal, mas detecta menos cenários. A janela padrão de 5 min é o meio-termo, sem
   ajuste no teste.
8. **Custo.** Leitura e eventos levam 5,7 s (36,7 mil linhas/s). A agregação, 3,2 s. O treino,
   0,43 s, e o score do teste, 0,09 s. O SACLA processa quatro semanas de log em ~10 s.

## 9. Relatórios, uso real e figuras (Fase 5)

**D39 — Uso real: `sacla fit` e `sacla score` (`sacla/pipeline.py`, `sacla/cli.py`).** O SACLA
roda sobre qualquer jsonlog, sem rótulos e sem a bancada:
- `fit --logs <pasta> --model <arquivo> [--since] [--until]`: lê o período considerado normal,
  ajusta os históricos por usuário, o Isolation Forest e o limiar. Salva tudo num arquivo
  (joblib), inclusive a configuração usada (janela, fuso, aliases), para que o `score` monte as
  janelas do mesmo jeito.
- `score --logs <pasta> --model <arquivo> --out <pasta> [--since] [--until]`: pontua um novo
  período com os históricos já ajustados e gera o relatório.
- Hiperparâmetros: os escolhidos na validação da avaliação vigente (`config/sacla.yaml`,
  `detector`; hoje a v2, D46). A lista de atributos com `log1p` passou de `model.yaml` para
  `sacla.yaml`, porque é do SACLA; a avaliação lê a mesma lista.
- `python tasks.py report` confere se o `detector` do `sacla.yaml` é o escolhido no experimento
  e se a taxa de alertas corresponde ao limiar da validação. Se não for, avisa e mostra os
  valores certos em `relatorio/resumo_anexo.json` (`uso_real`).

*Evidência de equivalência* (conferida na v1 e de novo na v2): `fit` no período de treino da
simulação (`--until 2026-03-16T03:00:00Z`) e `score` no de teste produziram as mesmas 9.109
janelas de treino e os **mesmos scores** que a semente 0 do experimento nas 4.662 janelas de
teste (diferença máxima 0,0). O caminho de uso real é, portanto, o mesmo que foi avaliado.

**D40 — Limiar sem rótulos no uso real.** Em produção não há rótulos para escolher o limiar.
Ele é o score acima do qual fica uma fração fixa das janelas do próprio histórico
(`detector.train_alert_rate`). O valor é a fração que o limiar escolhido na validação
(semente 0) produz no treino:
- v2: 2,13%; o `fit` dá limiar 0,5331 contra 0,5332 do experimento, e só 1 das 4.662 janelas de
  teste muda de lado;
- v1: 1,44%; 0,5544 contra 0,5547, com 3 janelas mudando de lado.

A taxa é um parâmetro de operação: diz quantos alertas o analista aceita receber num período
normal.

**D41 — Achado: atributos constantes no treino não influenciam o Isolation Forest.** O
scikit-learn só corta as árvores em atributos que variam na amostra. Nas janelas de treino,
`new_tables_frac`, `new_ips_frac` e `new_query_shapes_frac` valem sempre 0, porque o histórico
é o próprio treino: tudo o que aparece no treino já é "conhecido". `n_ddl` também é sempre 0.
*Evidência:* no modelo avaliado, forçar qualquer um desses quatro atributos a 1 em todas as
janelas de teste não muda nenhum score; multiplicar `n_commands` por 100 muda até 0,049.
Consequências:
- O IF avaliado usa, na prática, 24 dos 28 atributos. A novidade (13 das 49 janelas do tipo
  inédito têm tabela nunca vista) só aparece na explicação dos alertas, não no score. O que o
  IF detecta do tipo inédito vem de volume, hora e duração.
- Uma correção possível é calcular os atributos relativos das janelas de treino fora da
  amostra (ex.: histórico de outros dias, "deixando um dia de fora"). Isso muda a Fase 3 e
  exige refazer a avaliação com um teste já observado.
- **Situação:** corrigido na D45 (opção aprovada na revisão da Fase 5). Os resultados acima
  (D38) passam a ser a v1, arquivada em `results/historico/v1-historico-na-amostra/`.

**Achado relacionado: o score satura fora da faixa do treino.** Os cortes das árvores ficam
dentro da faixa de valores do treino. Uma janela com 300 comandos, quando o máximo do treino
era 8, percorre as árvores como a janela de 8 comandos. Uma rajada extrema pode ter score
parecido com o de uma janela normal "de borda". Isso explica parte da vantagem das regras, que
comparam com o máximo sem saturar, e o ganho da união IF ∪ regras.
*Evidência:* `tests/test_pipeline.py`, em que a rajada de 300 comandos fica entre as 10
primeiras janelas, mas não necessariamente em 1º.

**D42 — Explicação dos alertas (`sacla/explain.py`).** Para cada atributo de
`report.explain_features`, mede-se a fração das janelas do histórico do próprio usuário com
valor maior ou igual ao da janela (cauda superior).
- Entram até 3 atributos (`top_features`) com cauda ≤ 5% (`max_tail_fraction`), do mais raro
  ao menos raro. Entre empates (ex.: vários acima do máximo), vence o que mais passa do
  máximo.
- O texto é legível para o analista: "linhas retornadas (soma): 112.507 (acima do máximo do
  histórico; mediana 61, máximo 10.072)", "horário incomum para o usuário: 03h (0,0% das
  janelas do histórico)" e "tabelas nunca acessadas pelo usuário: staff".
- **Por que a cauda empírica e não o z robusto?** Ela não depende de escala nem de piso para o
  MAD (muitos atributos têm MAD 0) e se lê diretamente ("acima do máximo", "atingido em só
  0,4% das janelas").
- **Limite declarado:** é uma explicação descritiva do que mais foge do normal do usuário, não
  a atribuição interna do modelo. Quando nenhum atributo isolado é raro, o alerta diz que "a
  combinação de valores é que é incomum".
- Hora em seno/cosseno e os z robustos ficam de fora: a raridade da hora e as contagens já
  dizem o mesmo de forma legível.

**D43 — Relatório (`sacla/report.py`, `templates/relatorio.html.j2`).**
- Saídas: `alertas.csv` (uma linha por janela sinalizada), `episodios.csv`, `usuarios.csv` e
  `relatorio.html`.
- Cada alerta tem score, posição entre todas as janelas analisadas, percentil do score no
  histórico, explicação e resumo da janela (comandos por tipo, erros, falhas de login, linhas,
  tabelas e IPs).
- **Episódio:** janelas sinalizadas do mesmo usuário separadas por até 1 janela
  (`episode_gap_windows`). É a unidade que o analista examina; o HTML lista os episódios do
  mais atípico ao menos atípico.
- **Sem rótulos e seguro:** o relatório não tem colunas de ground truth (teste
  `test_alerts_have_position_percentile_and_no_label_columns`). O HTML escapa tudo o que vem do
  log (nomes de usuário, tabelas, IPs), com teste específico. O aviso fixo diz que um alerta
  indica comportamento atípico, não um ataque.
- **Relatório do experimento** (`python tasks.py report`): o mesmo código, aplicado ao período
  de teste com o modelo avaliado (semente 0). Fica em
  `results/experimentos/<exp_id>/relatorio/`. Antes de gerar, confere que o modelo salvo
  reproduz os scores gravados pela avaliação. À parte, `anexo_*_vs_manifesto.csv` cruza
  alertas e episódios com o manifesto; é a única saída da Fase 5 que usa rótulos.

**Resultado no teste** (semente 0):
- v2: 295 janelas sinalizadas em 195 episódios; 125 contêm ao menos uma janela de cenário e
  70 não contêm nenhuma. Gerar o relatório com as explicações levou 0,6 s.
- v1: 233 janelas em 158 episódios (111 com cenário e 47 sem).

O limiar da semente 0 na v2 é mais baixo (2,13% do treino acima dele, contra 1,44%), por isso
ela sinaliza mais janelas. Na média das 10 sementes, os falsos positivos ficaram iguais (65).

**Achado — deriva de dados no serviço batch** (v1 e v2). Os 6 episódios de maior score são a
rotina noturna legítima do `svc_batch`, um por noite do teste. A explicação mostra o motivo: o lote
passou a ler 4.290–4.619 linhas, acima do máximo do histórico (4.014). As tabelas `rental` e
`payment` crescem com a simulação, e o lote lê mais a cada semana.
- É uma deriva lenta dos dados, não uma anomalia de acesso.
- Responde por 8 dos falsos positivos da semente 0.
- Sugere reajustar o histórico periodicamente (janela móvel), o que fica como trabalho futuro.
- Mostra também o valor das explicações: o analista descarta esses alertas em segundos.

**D44 — Figuras (`evaluation/plots.py`, `results/experimentos/<exp_id>/figuras/`).**
- Formato: PNG (300 dpi) e PDF com fontes embutidas, 6,3 pol (~16 cm) de largura, textos em
  pt-BR com vírgula decimal. `legendas.md` traz uma legenda sugerida para cada figura.
- **Cores:** paleta de referência da skill de visualização, conferida com o validador dela
  (`validate_palette.js`), não a olho.
  - Os quatro detectores (azul, laranja, água, amarelo) passam como pares vizinhos em barras.
  - Nas curvas PR/ROC, com qualquer par podendo se tocar, quatro cores reprovam (laranja ×
    amarelo, ΔE 13,7 < 15). Por isso só três séries ali; a união aparece na comparação.
  - "Ambos" usa a cor da união; "só IF" e "só regras", as cores de cada detector.
  - Mapas de calor usam uma rampa de um só matiz (azul).
  - O contraste baixo do amarelo e do água é compensado por rótulos e pelas tabelas em
    `tables/`.
- As figuras são feitas para impressão (só modo claro). Os PDFs não gravam data de criação:
  refazê-las gera arquivos idênticos (conferido por hash), exceto `tempos_etapas`, que mostra o
  tempo medido em cada execução.

## 10. Correção dos atributos relativos no treino (v2)

**D45 — Histórico "deixando um dia de fora" para as janelas de treino.** Corrige o defeito da
D41. Opção escolhida na revisão da Fase 5, entre manter o defeito como limitação ou corrigir e
refazer a avaliação.
- **Regra** (`features.training_history: leave_one_day_out`, em `config/sacla.yaml`): cada
  janela do treino é comparada com o histórico do mesmo usuário **sem o dia local dela**.
  Valem para todos os atributos relativos: z robustos, raridade da hora e frações de novidade.
  Exemplo: uma tabela que o usuário só leu num dia do treino conta como inédita naquele dia.
- **O que não muda:**
  - janelas de validação e teste, e o uso real (`sacla score`), continuam comparadas com o
    treino inteiro;
  - os históricos salvos são os mesmos;
  - os atributos absolutos e as regras (que usam só atributos absolutos e horas) são os
    mesmos.
- **Por quê:** treino e teste passam a ter atributos relativos da mesma natureza, calculados
  "fora da amostra". Antes, o treino era comparado consigo mesmo: a novidade era sempre 0 e os
  z e a raridade da hora eram menores do que seriam para uma janela nova.
- **Usuário com um só dia de histórico:** naquele dia fica sem referência e é tratado como
  usuário sem histórico.
- **O modo antigo continua disponível** (`in_sample`), para reproduzir a v1.
- **Conjuntos refeitos sozinhos:** o resumo de cada conjunto rotulado passou a guardar uma
  impressão digital da configuração de leitura, eventos e atributos (`settings_fingerprint`).
  A avaliação e a robustez remontam o conjunto quando ela muda. Antes, só uma nova simulação
  disparava a remontagem.
- **Testes:** `test_training_windows_are_compared_with_the_other_days_only` (com uma tabela
  lida em um único dia: inédita no modo novo, 0 no antigo; validação, teste e históricos
  idênticos nos dois modos), `test_user_with_a_single_history_day_has_no_reference_on_that_day`
  e `tests/test_dataset.py`.

**Transparência metodológica.** A v2 usa o conjunto de teste uma segunda vez. Para que isso
não vire escolha guiada pelo teste:
- o defeito foi achado sem olhar desempenho: um atributo constante no treino e o efeito nulo
  de forçá-lo (D41);
- a correção é estrutural, decidida antes de qualquer número da v2;
- o protocolo da v2 é o mesmo da v1: grade, seleção na validação, sementes e regras;
- a v1 fica arquivada e as duas são reportadas lado a lado (D46). A monografia deve apresentar
  a v2 como versão corrigida, sem esconder a v1.

**D46 — Resultados da avaliação corrigida (v2) e comparação com a v1.** Mesmo protocolo da
D33, com os atributos da D45. Resultados em
`results/experimentos/sim-s42-28d__w5m__ne200-ms1024-mf0.5-log1/` e
`results/robustez/sim-s42-28d__w5m__ne200-ms1024-mf0.5-log1/`.
- **Configuração escolhida na validação:** 200 árvores, `max_samples` 1024, `max_features` 0,5
  e `log1p`. A v1 escolhera 500 árvores. A diferença entre 200 e 500 é pequena (F1 de validação
  61,0 contra 60,4).
- **Validação:** F1 de 61,0% (v1: 57,0%).

*Evidência de que a correção funcionou:*
- Nas janelas de treino, a novidade deixou de ser constante: 83 janelas com IP inédito, 13 com
  formato de consulta inédito e 2 com tabela inédita (antes, 0 em todas).
- Forçar cada fração de novidade a 1 no teste agora muda o score em até 0,076 (IP), 0,052
  (formato) e 0,015 (tabela); antes, nada mudava.
- Só `n_ddl` continua constante, porque não há DDL na simulação.

**Teste, v1 × v2** (média ± desvio nas 10 sementes; %):

| Detector | F1 v1 | F1 v2 | Cenários v1 | Cenários v2 | ROC-AUC v1 → v2 | PR-AUC v1 → v2 |
|---|---:|---:|---:|---:|---:|---:|
| IF ajustado | 63,8 ± 1,1 | **68,1 ± 2,0** | 69,8 ± 2,3 | **75,5 ± 4,7** | 0,939 → 0,953 | 0,644 → 0,700 |
| IF sem rótulos | 35,8 ± 2,7 | 39,3 ± 2,3 | 33,8 ± 3,3 | 36,3 ± 3,6 | 0,902 → 0,918 | 0,456 → 0,507 |
| Regras | 80,2 | 80,2 | 73,1 | 73,1 | — | — |
| IF ∪ regras | 79,2 ± 0,9 | 79,7 ± 1,3 | 84,7 ± 1,1 | 86,2 ± 1,3 | — | — |

Na v2, o IF ajustado teve precisão 76,5 ± 3,7%, recall 61,7 ± 4,3% e, em média, 65 falsos
positivos (os mesmos da v1). As regras não mudam, o que confere com a D45: elas só usam
atributos absolutos e horas.

**Robustez, v1 × v2** (três simulações; média ± desvio das médias por simulação):

| Detector | F1 v1 | F1 v2 | Cenários v1 | Cenários v2 |
|---|---:|---:|---:|---:|
| IF ajustado | 62,4 ± 1,2 | 67,2 ± 1,6 | 66,9 ± 3,2 | 71,4 ± 6,2 |
| IF sem rótulos | 35,9 ± 2,4 | 38,2 ± 2,2 | 32,1 ± 3,6 | 33,7 ± 3,4 |
| Regras | 78,8 ± 4,2 | 78,8 ± 4,2 | 73,1 ± 1,3 | 73,1 ± 1,3 |
| IF ∪ regras | 79,0 ± 3,0 | 80,0 ± 2,3 | 85,0 ± 3,5 | 85,9 ± 4,3 |

A melhora do IF (+4 a +5 pontos de F1) aparece nas três simulações, não só na de referência.

**Tipo inédito (`leitura_atipica`), cenários detectados pelo IF:**

| Intensidade | v1 | v2 |
|---|---:|---:|
| baixa | 27,5% | 37,5% |
| média | 25,0% | 40,0% |
| alta | 55,0% | 87,5% |

Por cenário (maioria das sementes), na v2:
- só o IF: 4 cenários (v1: 3);
- ambos: 4 (v1: 1);
- só as regras: 0 (v1: 3);
- nenhum: 4 (v1: 5).

O IF passou a detectar, na maioria das sementes, um cenário do serviço de aplicação e os três
dos gerentes. Continuam sem detecção dois cenários de atendentes e dois do serviço.

**Outros tipos:** o IF melhorou em `dml_incomum` (média: 15% → 36%), `fora_horario` baixa
(80% → 91%), `volume_alto` baixa (27,5% → 55%) e `sem_permissao` baixa (42,5% → 54%).
`rajada` ficou parecida (média 90% → 82,5%; alta 87,5% nas duas). `falha_auth` variou para os
dois lados (baixa 91% → 84%; média 62,5% → 65%; alta 77,5% → 84%). A variação entre sementes
é maior na v2 (desvio de F1 2,0 contra 1,1), porque 200 árvores são menos estáveis que 500.

**Sensibilidade (validação, v2):**
- `max_samples` = 256 continua o pior caso (F1 47,6%);
- sem `log(1+x)`, o F1 cai para 56,7%;
- janela de 1 min: 82,0% dos cenários com 422 alertas; 5 min: 70,2% com 191; 15 min: F1 68,8%
  e 67,8% dos cenários com 106.

**Interpretação.**
1. Corrigir o cálculo fora da amostra deu ao IF o sinal de novidade que o desenho previa (D29).
   Os maiores ganhos estão no tipo inédito, como esperado.
2. As regras continuam com o maior F1 sozinhas, pelos mesmos motivos da D38. A distância para
   o IF caiu de 16,4 para 12,1 pontos.
3. A união IF ∪ regras segue como a melhor cobertura: 86,2% dos cenários.
4. Mais estabilidade pediria fixar 500 árvores. Mas a escolha é feita só na validação, onde 200
   e 500 empatam dentro do desvio; mudar agora seria escolher pelo teste.

## 11. Monitor contínuo e painel local (Fase 6)

**D47 — Escopo ampliado: painel local e monitor em lotes curtos, fora da avaliação.** O
escopo original fechava "interface web" e "tempo real". A pedido, foram acrescentados um painel
local e um monitor que pontua as janelas assim que fecham, como **protótipo de uso
operacional**. Limites:
- **Fora da avaliação do TCC.** Nenhum resultado das fases 4 e 5 muda. *Evidência:* depois da
  Fase 6, o conjunto de 5 min tem os mesmos 28 atributos e os mesmos rótulos nas 18.455
  janelas; o relatório do experimento tem os mesmos 295 alertas em 195 episódios; o CSV ganhou
  só as colunas de banco.
- **Não é tempo real.** É processamento em lotes de uma janela: atraso de até 5 min mais a
  tolerância de gravação do log (60 s).
- **Só local.** O painel fica publicado em `127.0.0.1`, sem autenticação; é para uma pessoa,
  na própria máquina.
- **Continuam fora:** API REST, bloqueio automático, vários algoritmos de ML, Grafana e
  Kafka/Spark.
- **Vale confirmar com o orientador** se o painel entra no texto como ferramenta de apoio.

**D48 — Monitor (`sacla/monitor.py`, `sacla/store.py`; `config/monitor.yaml`).**
- **Fontes:** cada fonte é um servidor PostgreSQL com jsonlog numa pasta acessível ao
  container. Tem um relógio:
  - **ao vivo:** o agora menos a tolerância;
  - **reprodução:** começa numa data e anda N vezes mais rápido. Serve para ver o monitor
    sem tráfego ao vivo, por exemplo a semana de teste a 288×, um dia a cada 5 min.
- **Ciclo** (a cada 5 s):
  1. calcula o ponto de corte, alinhado ao início de uma janela;
  2. se ele passou da última janela pontuada, lê os eventos do período com uma janela de
     margem antes, que recupera o pareamento de plano e comando na borda;
  3. pontua só as janelas completas, com o mesmo detector do `sacla fit` (`pipeline.Detector`)
     e a mesma explicação do relatório (`report.describe_windows`).
- **Arquivos:** como os arquivos, em ordem de nome, são cronológicos, o monitor pula os que
  terminam antes do período e os que começam depois. Isso usa um cache do primeiro horário de
  cada arquivo. Na reprodução, cada passada lê um arquivo (~7 mil linhas, 0,3 s) em vez dos 8
  da semana.
- **Estado:** SQLite em modo WAL num volume Docker (`sacla_monitor`), porque num bind mount do
  Windows o travamento do SQLite não é confiável. A chave (fonte, usuário, início da janela)
  torna as gravações idempotentes: reprocessar não duplica, e a triagem de um alerta
  existente é mantida.
- **Concorrência entre monitor e painel:**
  - uma trava com prazo impede duas análises da mesma fonte;
  - a análise lê o estado só depois de travar;
  - as ações do painel que mexem no estado (limpar, reprocessar, reiniciar a reprodução,
    ajustar e reprocessar) esperam a trava (`Store.locked`), então uma análise em curso nunca
    desfaz uma dessas ações;
  - o ponteiro só avança se ninguém o mudou durante a análise (compare-and-set).
- **Reprocessar com outro detector:** alertas ainda não triados que o detector atual deixou de
  gerar são removidos. Os triados ficam, como registro do analista. Voltar o ponteiro
  (`rewind`) só vale para trás; para a frente, pularia janelas.
- **Robustez:**
  - o detector é recarregado quando o arquivo muda, e a gravação é atômica;
  - fonte sem detector fica "aguardando ajuste";
  - o erro de uma fonte fica registrado sem parar as outras;
  - o `reset`/`simulate` preserva o volume de estado.

*Evidência de coerência com o lote:* a semana de teste inteira, passada pelo monitor (168
passadas de 1 h simulada), deu **4.662 janelas e 296 alertas, exatamente os mesmos (usuário,
janela) do `sacla score`** com o mesmo detector. São 296, e não os 295 da avaliação, porque o
limiar do uso real é a taxa de alertas do histórico (D40): uma janela muda de lado.

**D49 — Painel (`src/dashboard/`, Streamlit 1.66.0 + Altair 6.3.0; `python tasks.py dashboard`).** Pacote e módulos com nomes em inglês, como manda a regra do projeto; os textos da interface são em pt-BR.
- **Páginas:**
  - Visão geral: indicadores, estado de cada fonte, alertas no tempo por banco e usuários com
    mais alertas;
  - Bancos supervisionados: bancos por fonte; ações "Analisar agora", "Ajustar detector" e
    "Reprocessar" (mantendo ou apagando a triagem);
  - Alertas: filtros por fonte, banco, usuário, status, período e score; episódios; detalhe
    com indicadores, explicação, bancos inéditos, gráfico de 3 h em volta e triagem com nota;
    exportação em CSV;
  - Usuários: score por janela e horários comparados ao histórico;
  - Dados: fontes, últimas análises, exportação e limpeza do estado.
- **Alertas no painel:** contador de alertas novos na barra lateral, atualizado a cada 20 s, e
  um aviso na tela quando o número sobe. O status de triagem aparece sempre com ícone e rótulo.
- **Período por fonte:** cada fonte conta o período a partir da própria janela mais recente.
  Uma fonte ao vivo (outubro) e uma reprodução (março) aparecem juntas sem uma esconder a
  outra.
- **Triagem pelo episódio certo:** a seleção guarda a chave do episódio (fonte, usuário e
  início), não a posição na tabela, que muda quando chegam alertas.
- **Alertas sem banco** aparecem como "(sem banco)" nos filtros, em vez de sumirem.
- **Sem valores fixos no código:** atualização, limite de alertas, horas em volta do alerta e
  agrupamento por hora/dia ficam em `config/monitor.yaml` (`dashboard`).
- **Sem rótulos:** o painel só importa `sacla`. Um teste de arquitetura garante que ele não
  importa `simlab` nem `evaluation`, e que o `sacla` não depende da interface.
- **Conteúdo do log:** é mostrado como texto, em tabelas ou com a marcação escapada, nunca
  como HTML.
- **Gráficos:** seguem a skill de visualização. A paleta foi validada nos fundos claro e escuro
  do Streamlit; no claro, amarelo e água ficam abaixo de 3:1, por isso todo gráfico tem a
  tabela dos dados ao lado. A linha do tempo se interrompe onde não há atividade.
- **Versões:**
  - a 1.65.0 (com mais de duas semanas) exige `pyarrow < 26`, e o projeto fixa a 26.0.0 usada
    nos resultados; para não mexer no ambiente avaliado, ficou a 1.66.0, publicada 8 dias
    antes;
  - o Altair é a versão que a Streamlit resolve, fixada por ser usado diretamente.
- *Evidência:* `tests/test_dashboard.py` abre cada página com o `AppTest` do Streamlit sobre
  um estado real do monitor, inclusive o detalhe de um episódio com a triagem. Capturas feitas
  com o Edge sem interface conferiram o layout. A revisão de código da fase achou 10 pontos
  (concorrência, período, seleção, nomes em português, valores fixos); todos foram corrigidos,
  com testes.

**D50 — Bancos (`dbname`).** Os eventos passaram a ter o banco da linha do log. Linhas sem o
campo herdam o banco da sessão, como já acontecia com o usuário. As janelas ganharam a coluna
`dbs`.
- **A chave e os atributos do modelo não mudam:** o banco é contexto para filtros, alertas e
  relatório.
- A referência de cada usuário guarda os bancos do histórico, e o painel avisa quando um alerta
  envolve um banco que o usuário nunca acessou. É só contexto: não entra no score.
- `sacla.__version__` foi para 0.2.0: detectores salvos antes são recusados com a mensagem
  de refazer o `fit`.
- Na simulação há um só banco (`pagila`); os testes cobrem um segundo banco (`rh`).

**Limitações da Fase 6.**
- No laboratório não há histórico ao vivo representativo: a fonte `laboratorio` fica
  "aguardando ajuste" até alguém ajustar um detector com um período normal do próprio servidor.
- O arquivo de log ativo é relido inteiro a cada análise (até 100 MB pela rotação
  configurada). Ler a partir de um deslocamento fica como melhoria futura.
- A saturação do score (D41) vale também aqui: nos testes sintéticos, uma rajada de 300
  comandos às 00h ficou abaixo do limiar. Para alertar nesses casos, a combinação IF ∪ regras
  (a melhor cobertura na avaliação, D46) seria o próximo passo, levando as regras para o
  pacote `sacla`.
