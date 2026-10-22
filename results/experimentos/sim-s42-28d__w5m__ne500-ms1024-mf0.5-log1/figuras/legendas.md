# Legendas sugeridas das figuras

- `comparacao_detectores`: Desempenho no teste (média ± desvio nas 10 sementes do modelo; as regras são determinísticas). Rótulo: F1.
- `curvas_pr_roc`: Curvas precisão–recall e ROC no teste (semente 0). Os pontos marcam o limiar usado por cada detector; as regras são um único ponto.
- `scores_por_tipo`: Score de anomalia das janelas de teste por tipo de cenário (semente 0). Caixas: quartis; bigodes: percentis 1 e 99; pontos: janelas anômalas. Linha: limiar.
- `deteccao_tipo_intensidade`: Cenários detectados (%) no teste, por tipo e intensidade (média nas sementes). Um cenário é detectado se ao menos uma de suas janelas é sinalizada.
- `cenarios_if_regras`: Cenários de teste detectados pelo Isolation Forest (maioria das sementes), pelas regras, por ambos ou por nenhum.
- `sensibilidade_hiperparametros`: F1 na validação variando um hiperparâmetro por vez (demais na configuração escolhida, em azul); média ± desvio nas 5 sementes de ajuste.
- `sensibilidade_janela`: Tamanho da janela na validação: F1, cenários detectados e alertas (cada tamanho com a sua melhor configuração). O F1 não é comparável entre tamanhos: a unidade muda.
- `tempos_etapas`: Tempo de cada etapa do SACLA sobre todo o log da simulação (treino e score: média das sementes).
- `robustez_simulacoes`: Robustez à semente da simulação: cada ponto cinza é uma simulação (média das sementes do modelo); o ponto colorido e a barra são a média ± desvio entre elas.
