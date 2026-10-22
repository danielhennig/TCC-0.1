# Metodologia experimental do SACLA

Este documento descreve o experimento do TCC numa forma que pode ser adaptada para a
monografia. Os números vêm da execução de referência (`sim-s42-28d`) e dos arquivos em
`results/`. As decisões técnicas, com as fontes e as evidências, estão em
[decisoes.md](decisoes.md), referenciadas aqui como D*n*.

## 1. Objetivo e questões

O SACLA (Sistema de Análise Comportamental de Logs de Acesso) procura janelas de atividade
atípicas de cada usuário de um banco PostgreSQL. Ele lê só o log nativo do servidor (formato
JSON) e usa um único algoritmo de aprendizado não supervisionado, o Isolation Forest. O sistema
auxilia a identificação: um alerta indica comportamento atípico para o usuário, não um ataque.

Questões investigadas:
1. Com que precisão e cobertura o Isolation Forest sinaliza acessos anômalos injetados, por
   tipo e intensidade?
2. O modelo detecta um tipo de anomalia que não existe em nenhuma fase anterior ao teste?
3. O que o modelo acrescenta a um conjunto de regras simples escritas por um especialista?
4. Quanto os resultados dependem dos hiperparâmetros, do tamanho da janela, da semente do
   modelo e da semente da simulação?

## 2. Ambiente

- **Banco:** PostgreSQL 18.6 em Docker, com a base de exemplo Pagila (locadora de filmes: 15
  tabelas, 51.805 locações, 51.061 pagamentos), em versão fixada e com hash conferido (D1, D2).
- **Logs:** `jsonlog` com uma linha por comando concluído (`log_min_duration_statement = 0`),
  erros com o texto do comando, conexões, desconexões e falhas de autenticação (D5–D11).
- **Linhas retornadas e tabelas:** o log nativo não informa quantas linhas um comando
  retornou. O módulo `auto_explain` (com `log_analyze`, sem cronometrar cada nó) registra o
  plano de execução com a contagem real de linhas e as tabelas acessadas (D12).
- **Custo medido** (1.000 comandos × 5 repetições por condição):
  - a configuração completa aumentou a latência mediana em cerca de 10%;
  - gerou ~10 KB de log por comando, ou 21 MB por 4 semanas com gzip.

  É um custo relevante para produção e está registrado como limitação.
- **Execução:** tudo roda em containers com versões fixadas (Python 3.13, pandas 3.0,
  scikit-learn 1.9). Um único comando recria o ambiente do zero.

## 3. Usuários e comportamento normal

São 10 usuários em 5 papéis, com privilégios mínimos concedidos por `GRANT` (D20):

| Papel | Usuários | Rotina | Origem |
|---|---|---|---|
| atendente | 4 | turnos 08–14 ou 13–19, segunda a sábado; consultas de cadastro e registro de locações e pagamentos | rede local |
| gerente | 2 | 09–18, segunda a sexta, hora extra ocasional; consultas amplas, alterações de cadastro, estornos | rede local (10% externa) |
| analista | 2 | 09–18, segunda a sexta; só leitura agregada e relatórios | rede local (20% externa) |
| serviço de aplicação | 1 | 24 h, com pico às 20h; catálogo, disponibilidade, locação online | rede de aplicação |
| serviço batch | 1 | rotina diária às 02:00; leituras grandes, atualização em lote, atualização de view materializada | rede de aplicação |

As chegadas seguem processos de Poisson com taxa diária variável. Cada usuário tem um gerador
aleatório próprio, derivado da semente e do nome. O tráfego normal inclui ruído legítimo, para
que nenhuma anomalia seja separável por um único atributo trivial (D21):
- erros de digitação;
- senhas digitadas errado;
- tentativas ocasionais em tabelas sem permissão;
- hora extra;
- extrações semanais grandes do analista;
- a rotina noturna e volumosa do serviço batch.

## 4. Anomalias e ground truth

Seis tipos de anomalia, cada um em três intensidades, mais um tipo **inédito** que só aparece
no teste (D22):

| Tipo | Descrição | Baixa / média / alta |
|---|---|---|
| rajada | taxa de comandos muito acima da normal do usuário, por 3–8 min | 3× / 8× / 20× |
| fora do horário | sessão em horário em que o usuário nunca trabalha | 5 / 20 / 60 comandos |
| volume alto | leitura de muitas linhas | 2 mil / 10 mil / tabelas inteiras |
| sem permissão | tentativas em tabelas sem `GRANT` | 1–2 / 5 / 15 tentativas |
| falha de autenticação | tentativas de login com senha errada | 3 / 10 / 40 tentativas |
| DML incomum | UPDATE/DELETE permitido, mas em volume atípico | 5 / 50 / 500 linhas |
| leitura atípica (inédito) | paginação lenta em tabelas permitidas, em horário e ritmo normais | 8 / 20 / 40 páginas |

- **Quantidades** (28 dias): 20 cenários no treino (contaminação realista), 5 por tipo ×
  intensidade na validação (90) e 8 no teste (144). Somam-se 12 cenários do tipo inédito no
  teste, um por papel × intensidade.
- **Ground truth:** cada cenário é registrado num manifesto (`manifest.csv`) com usuário,
  tipo, intensidade e intervalo. O log e os eventos executados não carregam nenhum rótulo
  (D23). O SACLA nunca lê o manifesto; só a avaliação o lê.

## 5. Geração dos logs com horários simulados

Quatro semanas de atividade não podem ser executadas em tempo real. Por isso (D13):
1. A agenda é gerada em tempo simulado.
2. Os eventos são executados em sequência, o mais rápido possível (~100 s).
3. Cada linha do log recebe o horário simulado do seu evento.

A ligação usa um comentário opaco em cada comando, que é removido em seguida. O log
remapeado tem o mesmo formato do jsonlog original, e o SACLA o lê como leria um log real.
Verificações automáticas garantem a qualidade do remapeamento:
- nenhuma das 207.769 linhas de cliente ficou sem evento;
- a ordem cronológica foi preservada;
- as linhas de cada plano coincidem com as recebidas pelo cliente em 94.174 comparações.

## 6. Processamento pelo SACLA

1. **Leitura** (módulo 1): arquivos do jsonlog, inclusive comprimidos, em ordem de rotação.
   Linhas malformadas ou truncadas são contadas e ignoradas (D26).
2. **Eventos** (módulo 2a): comandos concluídos, comandos com erro, falhas de autenticação,
   conexões e desconexões. Cada plano do `auto_explain` é associado ao seu comando (D27, D28).
   Os eventos extraídos conferem, usuário por usuário, com o que o executor fez (D31).
3. **Janelas e atributos** (módulo 2b): os eventos são agregados por usuário em janelas fixas
   de 5 minutos. Só existem janelas com atividade. Cada janela recebe 28 atributos (D29):

| Grupo | Atributos |
|---|---|
| Volume de comandos | total; por tipo (SELECT, INSERT, UPDATE, DELETE, DDL, outros) |
| Erros e acesso | comandos com erro; erros de permissão; falhas de autenticação; conexões |
| Dados | linhas retornadas (soma e máximo); linhas afetadas |
| Desempenho | duração média, máxima e p95 dos comandos |
| Diversidade | tabelas distintas; IPs distintos |
| Tempo | hora do dia (seno e cosseno); fim de semana |
| Relativos ao histórico do usuário | z robusto (mediana e MAD) de comandos e de linhas; raridade da hora; fração de tabelas, IPs e formatos de consulta nunca vistos |

Os atributos relativos usam só o histórico do próprio usuário no período de treino. Nenhuma
informação de validação ou teste é usada nesse cálculo, o que é verificado por teste
automatizado.

## 7. Rotulagem e divisão temporal

- **Rótulo** (D30): uma janela `[t, t+5 min)` do usuário *u* é anômala se algum cenário do
  manifesto do mesmo usuário tem intervalo que a intercepta. O rótulo serve só para avaliar.
- **Divisão temporal:** semanas 1–2 para treino, 3 para validação, 4 para teste.

| Split | Janelas | Anômalas | Contaminação |
|---|---:|---:|---:|
| Treino | 9.109 | 34 | 0,37% |
| Validação | 4.684 | 176 | 3,76% |
| Teste | 4.662 | 337 | 7,23% |

O treino contém anomalias, como aconteceria num histórico real. O modelo é treinado sem
rótulos, com todas as janelas do treino.

## 8. Modelo e protocolo de avaliação

- **Isolation Forest** (scikit-learn). O score de anomalia é `−score_samples` (maior = mais
  atípico). Uma janela é sinalizada quando o score passa de um limiar explícito (D32).
- **Ajuste, só na validação** (D33): grade de 54 combinações de hiperparâmetros
  (`n_estimators` ∈ {100, 200, 500}, `max_samples` ∈ {256, 1024, 10%}, `max_features` ∈
  {0,5; 0,75; 1,0}, `log(1+x)` nas contagens sim/não), cada uma com 5 sementes. Para cada
  combinação, o limiar de melhor F1. Escolha: maior F1 médio, com desempate por PR-AUC.
- **Escolhida:** 500 árvores, 1.024 amostras por árvore, 50% dos atributos por árvore e
  `log(1+x)`. F1 de 57,0% na validação.
- **Teste, usado uma única vez:** a configuração escolhida, com 10 sementes do modelo e o
  limiar de cada semente vindo da validação. Reporta-se média ± desvio.

**Comparações:**
- **IF sem rótulos:** padrões do scikit-learn e limiar no quantil 99% dos scores do próprio
  treino. É o que se obtém sem nenhuma janela rotulada.
- **Regras** (D34): seis regras de especialista com limiares fixados antes da avaliação:
  - 3 ou mais falhas de login;
  - 1 ou mais erros de permissão;
  - comandos acima de 3 × o p99 do usuário;
  - linhas retornadas acima do máximo do usuário;
  - linhas afetadas acima do máximo do usuário;
  - hora nunca vista para o usuário.
- **IF ∪ regras:** uma janela é sinalizada se qualquer um dos dois a sinaliza.

**Métricas:**
- por janela: precisão, recall, F1, falsos positivos, ROC-AUC e PR-AUC;
- por tipo e intensidade: recall;
- por cenário: um cenário é detectado se ao menos uma de suas janelas é sinalizada;
- tempo de cada etapa.

**Análises complementares, sem tocar o teste:**
- sensibilidade a cada hiperparâmetro e ao tamanho da janela (1, 5 e 15 min), na validação;
- robustez, com a configuração congelada reavaliada em duas novas simulações (sementes 7 e
  2026; D36).

## 9. Resultados

### 9.1 Teste (figura `comparacao_detectores`)

| Detector | Precisão | Recall | F1 | Falsos positivos | Cenários detectados | ROC-AUC | PR-AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
| IF ajustado | 74,5 ± 1,9 | 55,8 ± 2,1 | 63,8 ± 1,1 | 65 | 69,8 ± 2,3 | 0,939 | 0,644 |
| IF sem rótulos | 64,7 ± 2,6 | 24,7 ± 2,3 | 35,8 ± 2,7 | 45 | 33,8 ± 3,3 | 0,902 | 0,456 |
| Regras | 96,7 | 68,5 | 80,2 | 8 | 73,1 | — | — |
| IF ∪ regras | 80,0 ± 1,9 | 78,5 ± 0,9 | 79,2 ± 0,9 | 66 | 84,7 ± 1,1 | — | — |

Valores em %; média ± desvio nas 10 sementes do modelo. A prevalência no teste é 7,2%, que é o
PR-AUC de um detector aleatório.

### 9.2 Por tipo e intensidade (figuras `deteccao_tipo_intensidade` e `scores_por_tipo`)

- O IF detecta bem o que é volumoso ou fora da rotina:
  - fora do horário: 80–100% dos cenários;
  - rajada média e alta: 88–90%;
  - volume alto médio e alto: 99–100%;
  - sem permissão médio e alto: 100%.
- As intensidades baixas, desenhadas na faixa do ruído normal, são as mais difíceis. A rajada
  baixa (3× a taxa) não é detectada por nenhum detector.
- **Tipo inédito:**
  - o IF detecta 25–55% dos cenários conforme a intensidade, e a união IF ∪ regras chega a
    50–75%;
  - os três cenários dos analistas são detectados só pelo IF;
  - os do serviço de aplicação não são detectados por ninguém, porque a paginação lenta se
    mistura ao tráfego normal do serviço.

### 9.3 IF e regras (figura `cenarios_if_regras`)

As regras vencem o IF sozinho. A união, porém:
- eleva o recall de 68,5% para 78,5%;
- eleva os cenários detectados de 73,1% para 84,7%.

Os cenários que só o IF detecta concentram-se em rajadas (8), acessos fora do horário (4) e no
tipo inédito (3). Os que só as regras detectam concentram-se em DML incomum, falhas de
autenticação e erros de permissão: tipos em que um único evento já é sinal forte e uma regra
de limiar fixo o captura sem custo.

### 9.4 Sensibilidade e robustez (figuras `sensibilidade_*` e `robustez_simulacoes`)

- O resultado é estável quanto ao número de árvores e à fração de atributos.
- Os parâmetros que importam:
  - `max_samples` = 256 reduz o F1 de validação de 57,0% para 45,3%;
  - não aplicar `log(1+x)` reduz o F1 para 51,9%.
- **Janela de 1 min:** detecta mais cenários (79,6% contra 65,6% na validação), com o dobro de
  alertas.
- **Janela de 15 min:** tem F1 por janela maior (62,2%), mas detecta menos cenários. O F1 por
  janela não é comparável entre tamanhos.
- **Robustez:** nas três simulações, o F1 do IF foi 62,4 ± 1,2 e o das regras 78,8 ± 4,2. O
  resultado do IF depende pouco da simulação.

### 9.5 Custo (figura `tempos_etapas`)

Quatro semanas de log (207.769 linhas) são processadas em cerca de 10 s:

| Etapa | Tempo |
|---|---:|
| Leitura e eventos | 5,7 s |
| Agregação | 3,2 s |
| Treino | 0,43 s |
| Score do teste | 0,09 s |
| Relatório com explicações | 0,65 s |

## 10. Relatório e uso real

Para cada janela sinalizada, o relatório (CSV e HTML) mostra:
- usuário, período, score e posição;
- percentil do score no histórico;
- os atributos mais raros para aquele usuário, em linguagem direta. Exemplo: "linhas
  retornadas (soma): 112.507 (acima do máximo do histórico; mediana 61, máximo 10.072)".

Janelas seguidas do mesmo usuário formam episódios (D42, D43). No teste foram 233 janelas
sinalizadas em 158 episódios, 111 deles com ao menos um cenário injetado.

Os comandos `sacla fit` e `sacla score` aplicam o mesmo fluxo a qualquer jsonlog, sem
rótulos. O limiar é definido como a fração de alertas aceita num período normal (D39, D40).
Aplicado aos logs da simulação, o fluxo de uso real reproduziu exatamente os scores do modelo
avaliado.

## 11. Ameaças à validade

**Validade interna e de construto**
- **Dados sintéticos.** O tráfego é simulado com rotinas regulares. Usuários reais variam mais,
  o que tende a reduzir a precisão de qualquer detector, e mais ainda a das regras de
  "máximo do usuário".
- **Mesmo autor nos dois lados.** O mesmo autor desenhou as anomalias, os atributos e as
  regras. As regras foram escritas conhecendo os seis tipos, o que favorece a baseline. O tipo
  inédito e a análise por tipo mitigam, mas não eliminam, esse viés.
- **Horários sintéticos e execução sequencial.** Não há concorrência real. As durações são
  reais, mas medidas sem contenção (D13).
- **Atributos de novidade inativos no modelo** (D41). Com o histórico calculado sobre o
  próprio treino, as frações de tabelas, IPs e formatos de consulta inéditos valem 0 em todo o
  treino, e o Isolation Forest não as usa.
- **Saturação do score.** O Isolation Forest não distingue um valor "um pouco acima" do máximo
  do treino de um valor "100× acima" (D41).
- **Contaminação e deriva.** Anomalias no treino elevam o "máximo do usuário" e mascaram
  cenários parecidos no teste (`dml_incomum`, D38). O crescimento das tabelas faz a rotina
  batch legítima parecer anômala no teste (D43).

**Validade externa**
- Uma base pequena (Pagila), um único SGBD e 10 usuários.
- Três redes simulam a origem das conexões; IPs reais seriam mais diversos.
- O `auto_explain` com análise tem custo de ~10% na latência, o que limita o uso direto em
  produção.

**Validade de conclusão**
- **Teste usado uma vez.** Hiperparâmetros e limiares vêm só da validação.
- **Variação medida.** 10 sementes do modelo e 3 simulações. Três simulações ainda são poucas
  para afirmações fortes sobre a variação entre conjuntos de dados.
- **Otimismo da validação rotulada.** O limiar escolhido na validação supõe rótulos que um
  ambiente real não tem; a variante sem rótulos mostra o caso pessimista.

## 12. Reprodutibilidade

Com o Docker em execução:

```bash
python tasks.py setup        # imagens, banco e Pagila do zero
python tasks.py simulate     # 28 dias, semente 42 (~2,5 min)
python tasks.py features     # conjunto rotulado (janela de 5 min)
python tasks.py evaluate     # ajuste, teste, regras, sensibilidade (~5 min)
python tasks.py robustness --exp-id sim-s42-28d__w5m__ne500-ms1024-mf0.5-log1
python tasks.py report       # relatório do teste e figuras
```

- **Sementes:** simulação 42 (robustez: 7 e 2026), ajuste 0–4, avaliação 0–9.
- **Configuração:** em `config/*.yaml`; a resolvida e as versões ficam em cada pasta de
  experimento.
- **Determinismo:** refazer a avaliação produz as mesmas métricas; só os tempos variam.
