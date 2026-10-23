"""SACLA — Sistema de Análise Comportamental de Logs de Acesso.

Módulos: leitura dos logs (reader), tratamento e agregação dos eventos (events, sqlclass,
features), Isolation Forest (model), explicação e relatório (explain, report) e o fluxo de uso
real (pipeline, cli: `python -m sacla.cli fit|score`). O pacote não depende da bancada
experimental (`simlab`) nem da avaliação (`evaluation`).
"""

__version__ = "0.2.0"
