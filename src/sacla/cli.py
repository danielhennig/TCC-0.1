"""Linha de comando do SACLA (uso real, sem rótulos): `python -m sacla.cli fit|score`.

fit    lê o log de um período considerado normal e salva o detector (modelo, históricos e
       limiar);
score  lê o log de um novo período e gera o relatório de anomalias (CSV e HTML).
Datas em ISO 8601 com fuso (ex.: 2026-03-16T03:00:00Z); o intervalo é [--since, --until).
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from sacla.config import ConfigError, SaclaSettings, load_settings, setup_logging
from sacla.pipeline import Detector, fit_detector, load_events, parse_instant, score_events
from sacla.report import display_time, write_report

log = logging.getLogger("sacla")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sacla", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("fit", "ajusta o detector com o log de um período considerado normal"),
        ("score", "analisa o log de um novo período e gera o relatório"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--logs", type=Path, required=True, help="arquivo ou pasta de jsonlog")
        p.add_argument("--model", type=Path, required=True, help="arquivo do detector (.joblib)")
        p.add_argument("--since", help="início do período (inclusive)")
        p.add_argument("--until", help="fim do período (exclusive)")
        if name == "fit":
            p.add_argument("--window", type=int, help="minutos por janela (padrão: sacla.yaml)")
            p.add_argument("--alert-rate", type=float, help="fração do histórico acima do limiar")
        else:
            p.add_argument("--out", type=Path, required=True, help="pasta do relatório")
            p.add_argument("--title", default="SACLA — relatório de acessos atípicos")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        since, until = parse_instant(args.since), parse_instant(args.until)
        if args.command == "fit":
            settings = _overrides(load_settings(), args.window, args.alert_rate)
            _warn_unaligned(settings, since, until)
            loaded = load_events(args.logs, settings, since, until)
            detector = fit_detector(loaded.events, settings, until)
            detector.save(args.model)
            print(f"histórico: {detector.history_text()}")
            print(f"limiar: {detector.model.threshold:.6f}")
            print(f"detector salvo em {args.model}")
            return 0
        detector = Detector.load(args.model)
        _warn_unaligned(detector.settings, since, until)
        loaded = load_events(args.logs, detector.settings, since, until)
        # Apresentação (episódios, quantos atributos, ...) segue o sacla.yaml atual.
        report = score_events(detector, loaded.events, args.title, load_settings().report)
        paths = write_report(report, args.out, detector.settings)
        print(
            f"{report.context.windows} janelas de {report.context.users} usuários; "
            f"{len(report.alerts)} sinalizadas em {len(report.episodes)} episódios"
        )
        top = report.episodes.sort_values("score_max", ascending=False).head(10)
        for ep in top.to_dict(orient="records"):
            print(
                f"  {ep['score_max']:.3f}  {ep['usuario']:<14} {display_time(ep['inicio'])}  "
                f"{ep['explicacao'].split(' | ')[0]}"
            )
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0
    except (ConfigError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2


def _overrides(settings: SaclaSettings, window: int | None, rate: float | None) -> SaclaSettings:
    if window is not None:
        settings = replace(settings, features=replace(settings.features, window_minutes=window))
    if rate is not None:
        settings = replace(settings, detector=replace(settings.detector, train_alert_rate=rate))
    return settings


def _warn_unaligned(settings: SaclaSettings, *instants: int | None) -> None:
    window_ms = settings.features.window_minutes * 60_000
    for instant in instants:
        if instant is not None and instant % window_ms:
            log.warning(
                "limite de período fora do início de uma janela de %d min: a janela da borda "
                "fica incompleta",
                settings.features.window_minutes,
            )


if __name__ == "__main__":
    sys.exit(main())
