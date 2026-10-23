"""Linha de comando do SACLA (uso real, sem rótulos): `python -m sacla.cli fit|score`.

fit    lê o log de um período considerado normal e salva o detector (modelo, históricos e
       limiar);
score  lê o log de um novo período e gera o relatório de anomalias (CSV e HTML).
monitor        pontua as janelas das fontes de config/monitor.yaml assim que fecham (laço);
monitor-fit    ajusta o detector de uma fonte do monitor.
Datas em ISO 8601 com fuso (ex.: 2026-03-16T03:00:00Z); o intervalo é [--since, --until).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from dataclasses import replace
from pathlib import Path

from sacla.config import ConfigError, SaclaSettings, load_settings, setup_logging
from sacla.monitor import Monitor, fit_source, load_monitor_settings, run_forever
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
    p_mon = sub.add_parser("monitor", help="monitor contínuo das fontes (config/monitor.yaml)")
    p_mon.add_argument("--once", action="store_true", help="uma passada só (depuração)")
    p_fit = sub.add_parser("monitor-fit", help="ajusta o detector de uma fonte do monitor")
    p_fit.add_argument("--source", required=True, help="id da fonte em config/monitor.yaml")
    p_fit.add_argument("--since", help="início do histórico (padrão: o sugerido na fonte)")
    p_fit.add_argument("--until", help="fim do histórico (padrão: o sugerido na fonte)")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        if args.command == "monitor":
            return _monitor(once=args.once)
        if args.command == "monitor-fit":
            return _monitor_fit(args.source, args.since, args.until)
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


def _monitor(once: bool) -> int:
    # A cada passada o pipeline diria quantos arquivos leu; no laço, só os alertas interessam.
    logging.getLogger("sacla.pipeline").setLevel(logging.WARNING)
    monitor = Monitor(load_monitor_settings())
    if once:
        for result in monitor.tick():
            print(
                f"{result.source_id}: {result.windows} janelas, {result.alerts} alertas "
                f"({result.new_alerts} novos)"
            )
        return 0
    stop = {"now": False}

    def request_stop(signum: int, frame: object) -> None:
        log.info("sinal %d recebido; encerrando o monitor", signum)
        stop["now"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_forever(monitor, lambda: stop["now"])
    return 0


def _monitor_fit(source_id: str, since: str | None, until: str | None) -> int:
    monitor_settings = load_monitor_settings()
    source = monitor_settings.source(source_id)
    since_ms = parse_instant(since) if since else source.fit_since_ms
    until_ms = parse_instant(until) if until else source.fit_until_ms
    result = fit_source(monitor_settings, source, load_settings(), since_ms, until_ms)
    for key, value in result.info.items():
        print(f"{key}: {value}")
    return 0


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
