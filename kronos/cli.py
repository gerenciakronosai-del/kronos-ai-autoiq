"""Interfaz de linea de comandos de Kronos.

    python -m kronos demo                      simulacion completa autocontenida
    python -m kronos importar DAT_*.zip        importa histórico de HistData.com
    python -m kronos decide --data velas.csv   decision JSON de la ultima vela
    python -m kronos backtest --data velas.csv informe con veredicto
    python -m kronos validar  --data velas.csv dentro vs fuera de muestra
    python -m kronos paper --data velas.csv    replay contra el broker simulado
    python -m kronos indicadores --data x.csv  volcado de indicadores
    python -m kronos datos --out velas.csv     genera una serie sintetica
    python -m kronos config-init               escribe config/default.json
    python -m kronos selftest                  ejecuta la bateria de tests

`decide` escribe SOLO el JSON en stdout (los avisos van a stderr), de forma que
se pueda encadenar directamente con un script ejecutor.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from kronos import __version__
from kronos.backtest.engine import Backtester, BacktestConfig
from kronos.backtest.report import render
from kronos.config import AppConfig, default_config
from kronos.core import indicators as ind
from kronos.core.candle import Candle, Series
from kronos.data import loader, synthetic
from kronos.risk.manager import RiskParams
from kronos.strategy.base import Confidence, Signal
from kronos.strategy.registry import available

RAIZ = Path(__file__).resolve().parent.parent


def _err(msg: str) -> None:
    print(f"[kronos] {msg}", file=sys.stderr)


def _cargar_series(args) -> Series:
    if getattr(args, "sintetico", False) or not getattr(args, "data", None):
        _err("sin --data: usando serie sintetica reproducible (seed=42)")
        return synthetic.generate(
            synthetic.SyntheticParams(n=getattr(args, "n", 3000)), seed=getattr(args, "seed", 42)
        )
    p = Path(args.data)
    return loader.load_json(p, args.symbol) if p.suffix.lower() == ".json" else loader.load_csv(p, args.symbol)


def _config(args) -> AppConfig:
    cfg = AppConfig.load(args.config) if getattr(args, "config", None) else default_config()
    if getattr(args, "payout", None) is not None:
        cfg.backtest["payout"] = args.payout
    if getattr(args, "expiry", None) is not None:
        cfg.backtest["expiry_velas"] = args.expiry
    if getattr(args, "balance", None) is not None:
        cfg.riesgo["balance_inicial"] = args.balance
    if getattr(args, "confianza_minima", None):
        cfg.riesgo["confianza_minima"] = args.confianza_minima
    return cfg


# --------------------------------------------------------------------- #
def cmd_decide(args) -> int:
    """Emite la decision JSON para la ultima vela cerrada."""
    if args.stdin:
        datos = json.load(sys.stdin)
        velas = [
            Candle(
                ts=loader.parse_timestamp(str(d.get("timestamp", d.get("ts", 0)))),
                open=float(d["open"]), high=float(d["high"]),
                low=float(d["low"]), close=float(d["close"]),
                volume=float(d.get("volume", 0.0)),
            )
            if isinstance(d, dict)
            else Candle(
                ts=loader.parse_timestamp(str(d[0])), open=float(d[1]), high=float(d[2]),
                low=float(d[3]), close=float(d[4]), volume=float(d[5]) if len(d) > 5 else 0.0,
            )
            for d in datos
        ]
        series = Series(sorted(velas, key=lambda c: c.ts), symbol=args.symbol)
    else:
        series = _cargar_series(args)

    cfg = _config(args)
    signal = cfg.strategy().evaluate(series)
    print(signal.to_json(full=args.full, indent=2 if args.pretty else None))
    return 0


def cmd_backtest(args) -> int:
    series = _cargar_series(args)
    cfg = _config(args)
    bt = Backtester(cfg.strategy(), cfg.backtest_config(), cfg.risk_params())
    resultado = bt.run(series)

    if args.json:
        print(json.dumps(resultado.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render(resultado, max_trades=args.mostrar_trades))

    if args.exportar:
        destino = Path(args.exportar)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(
                {
                    "resumen": resultado.to_dict(),
                    "trades": [
                        {**asdict(t), "resultado": str(t.resultado)} for t in resultado.trades
                    ],
                },
                indent=2, ensure_ascii=False, default=str,
            ),
            encoding="utf-8",
        )
        _err(f"resultados exportados a {destino}")

    # Codigo de salida util en CI: 0 solo si hay edge significativo.
    return 0 if resultado.significativo else 1


def cmd_cripto(args) -> int:
    """Descarga histórico de Binance. API publica: sin claves ni dependencias."""
    import sys as _sys

    from kronos.data import binance

    def progreso(hechas: int, total: int) -> None:
        print(f"\r  {hechas:,} / {total:,} velas", end="", file=_sys.stderr, flush=True)

    try:
        serie = binance.descargar(args.symbol, args.timeframe, args.velas, progreso)
    except binance.BinanceError as e:
        _err(f"\n{e}")
        return 2
    print(file=_sys.stderr)

    destino = loader.save_csv(serie, args.out)
    dias = max(1, (serie[-1].ts - serie[0].ts) / 86400)
    print(f"  {len(serie):,} velas -> {destino}")
    print(f"  Rango: {serie[0].dt:%Y-%m-%d} .. {serie[-1].dt:%Y-%m-%d}  ({dias:.0f} dias)")
    print(f"  Precio: {serie[0].close:,.4g} -> {serie[-1].close:,.4g}")
    print()
    print("  Siguiente paso, ya con stop y objetivo en vez de vencimiento fijo:")
    print(f"    python -m kronos explorar --data {destino} --modo stops --rr 2.0")
    return 0


def cmd_descargar(args) -> int:
    """Baja historico de IQ Option a CSV, para poder barrerlo estadisticamente.

    Los instrumentos OTC no publican historico en ningun sitio: si quieres saber
    si se comportan como el mercado real o son otra cosa, hay que pedirselo al
    propio broker.
    """
    import sys as _sys

    from kronos.broker.base import TipoCuenta
    from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker
    from kronos.core.candle import Candle, Series

    try:
        broker = IQOptionBroker(tipo_cuenta=TipoCuenta.DEMO)
        _err(f"conectando para descargar {args.symbol}...")
        broker.conectar()
    except BrokerNoDisponible as e:
        _err(str(e))
        return 2

    def progreso(hechas: int, total: int) -> None:
        print(f"\r  {hechas:,} / {total:,} velas", end="", file=_sys.stderr, flush=True)

    try:
        crudas = broker.descargar_velas(args.symbol, args.timeframe, args.velas,
                                        progreso=progreso)
    except BrokerNoDisponible as e:
        _err(f"\n{e}")
        return 2
    finally:
        broker.cerrar()
    print(file=_sys.stderr)

    velas = []
    for c in crudas:
        try:
            velas.append(Candle(ts=int(c["from"]), open=float(c["open"]),
                                high=float(c["max"]), low=float(c["min"]),
                                close=float(c["close"]),
                                volume=float(c.get("volume", 0) or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    if not velas:
        _err("el broker no devolvio ninguna vela utilizable")
        return 1

    serie = Series(sorted(velas, key=lambda v: v.ts), symbol=args.symbol,
                   timeframe=args.timeframe)
    destino = loader.save_csv(serie, args.out)
    inicio, fin = serie[0].dt, serie[-1].dt
    dias = max(1, (serie[-1].ts - serie[0].ts) / 86400)
    print(f"  {len(serie):,} velas -> {destino}")
    print(f"  Rango (UTC): {inicio:%Y-%m-%d %H:%M} .. {fin:%Y-%m-%d %H:%M}  ({dias:.1f} dias)")
    print()
    print("  Siguiente paso: barrer estas velas con el mismo rigor que el mercado real")
    print(f"    python -m kronos explorar --data {destino} --spread 0.5")
    return 0


def cmd_broker(args) -> int:
    """Comprueba la conexion con IQ Option y mide el payout REAL.

    El payout es el unico dato que no se puede sacar de un backtest y el que
    decide si algo puede ser rentable: hay que preguntarselo al broker.
    """
    from kronos.broker.base import TipoCuenta
    from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker

    if args.real:
        _err("modo REAL solicitado; requiere KRONOS_ALLOW_REAL=1 en el entorno")
    tipo = TipoCuenta.REAL if args.real else TipoCuenta.DEMO

    try:
        broker = IQOptionBroker(tipo_cuenta=tipo)
        print(f"  Conectando a IQ Option ({tipo})...")
        broker.conectar()
    except BrokerNoDisponible as e:
        _err(f"{e}")
        return 2

    try:
        if args.crudo:
            # La API no oficial cambia de forma entre cuentas y versiones. Ver
            # la estructura real es la unica manera de adaptarse sin adivinar.
            import json as _json
            datos = broker.crudo()
            for nombre, valor in datos.items():
                print(f"--- {nombre} ---")
                try:
                    texto = _json.dumps(valor, indent=2, default=str, ensure_ascii=False)
                except Exception:
                    texto = repr(valor)
                print(texto[:4000] + (" ...(recortado)" if len(texto) > 4000 else ""))
            return 0

        info = broker.diagnostico(symbol=args.symbol, expiracion_seg=args.expiry_seg)
        print("=" * 66)
        print(f"  DIAGNOSTICO DE BROKER - {args.symbol}")
        print("=" * 66)
        print(f"  Cuenta:            {info['tipo_cuenta']}")
        print(f"  Conectado:         {'si' if info['conectado'] else 'NO'}")
        if "balance" in info:
            print(f"  Balance:           {info['balance']:,.2f}")
        else:
            print(f"  Balance:           ERROR {info.get('balance_error')}")
        print(f"  Activos abiertos:  {info['activos_abiertos']}")
        print(f"  {args.symbol} operable ahora: {info['symbol_operable']}")
        lista = info.get("lista_abiertos") or []
        if lista:
            print(f"  Disponibles: {', '.join(lista[:12])}"
                  + (f" ... (+{len(lista) - 12})" if len(lista) > 12 else ""))
        if info.get("solo_otc"):
            print()
            print("  AVISO: solo hay activos OTC abiertos -> el mercado real esta")
            print("  cerrado (noche o fin de semana). Los OTC son precios sinteticos")
            print("  del propio broker: un backtest sobre el par real no aplica ahi.")
        print()
        if "payout" in info:
            p = info["payout"]
            umbral = info["umbral_equilibrio"]
            print(f"  PAYOUT REAL:       {p * 100:.1f}%")
            print(f"  UMBRAL EQUILIBRIO: {umbral * 100:.2f}% de aciertos")
            print()
            print("  Es el numero que decide todo: por debajo de ese winrate,")
            print("  cualquier estrategia pierde dinero de forma sostenida.")
        else:
            print(f"  PAYOUT:            ERROR {info.get('payout_error')}")
            print("  Sin payout no se puede evaluar nada. Suele fallar con el")
            print("  mercado cerrado (fines de semana) o el activo no disponible.")
        print("=" * 66)
        return 0 if info["conectado"] and "payout" in info else 1
    finally:
        broker.cerrar()


def cmd_duelo(args) -> int:
    """Compara los aciertos de la IA y de las reglas sobre las mismas velas."""
    from kronos.research import duelo

    registros = duelo.cargar_registro(args.registro)
    series = _cargar_series(args)
    resultado = duelo.evaluar(
        registros, series, expiry_velas=args.expiry or 5,
        payout=args.payout or 0.80, spread_pips=args.spread,
    )
    print(duelo.informe(resultado))
    if resultado.emparejados == 0:
        _err("ninguna decision del registro casa con las velas del CSV: "
             "comprueba que --data es el mismo fichero que uso el motor")
        return 2
    return 0


def cmd_explorar(args) -> int:
    """Barre muchas hipotesis a la vez y dice cuantas sobreviven."""
    from kronos.research.barrido import barrer, evaluar, informe
    from kronos.research.hipotesis import CATALOGO, rsi_extremo

    series = _cargar_series(args)

    if args.modo == "stops":
        # Modo direccional: el payoff lo elige uno con stop y objetivo, no el
        # broker. Cambia el umbral de equilibrio de 54.35% a 1/(1+rr).
        from kronos.backtest.stops import evaluar_con_stops, informe as informe_stops
        from kronos.data.binance import valor_pip

        vp = valor_pip(series) if args.cripto else 0.0001
        corte = int(len(series) * args.split)
        dentro, fuera = series[:corte], series[corte:]
        resultados = []
        for nombre, generador in CATALOGO.items():
            sen_f = generador(fuera)
            resultados.append(evaluar_con_stops(
                fuera, sen_f, rr=args.rr, atr_mult=args.atr_mult,
                max_velas=args.max_velas, spread_pips=args.spread,
                valor_pip=vp, nombre=nombre,
            ))
        print(f"  {len(series):,} velas | fuera de muestra: {len(fuera):,}")
        print(f"  objetivo:riesgo = {args.rr:.1f}:1  ->  umbral {100 / (1 + args.rr):.1f}% "
              f"| stop a {args.atr_mult}x ATR | spread {args.spread} pips")
        print()
        print(informe_stops(resultados, top=args.top))
        buenos = [r for r in resultados if r.n >= 100 and r.esperanza_r > 0
                  and r.p_valor < 0.05 / max(1, len(resultados))]
        print()
        print(f"  Tras corregir por {len(resultados)} pruebas: "
              f"{len(buenos)} hipotesis con esperanza positiva y significativa")
        return 0 if buenos else 1

    expiries = tuple(int(x) for x in args.expiries.split(","))
    expiries = tuple(int(x) for x in args.expiries.split(","))
    hallazgos = barrer(series, CATALOGO, expiries=expiries, payout=args.payout or 0.80,
                       split=args.split, spread_pips=args.spread)
    print(informe(hallazgos, top=args.top))

    if args.sensibilidad:
        # La prueba que separa una señal real de un espejismo: casi todo el
        # edge aparente en horizontes cortos vive por debajo del spread.
        print()
        print("=" * 88)
        print(f"  SENSIBILIDAD AL SPREAD - {args.sensibilidad}")
        print("=" * 88)
        gen = CATALOGO.get(args.sensibilidad, rsi_extremo)
        corte = int(len(series) * args.split)
        fuera = series[corte:]
        senales = gen(fuera)
        exp = expiries[-1]
        print(f"  {'spread':>9}{'winrate':>10}{'edge':>9}{'payout min':>12}")
        print("  " + "-" * 40)
        for pips in (0.0, 0.2, 0.5, 1.0, 1.5):
            r = evaluar(fuera, senales, exp, args.payout or 0.80, spread_pips=pips)
            nec = (1 / r.winrate - 1) if r.winrate > 0 else float("inf")
            print(f"  {pips:>7.1f}p{r.winrate * 100:>9.2f}%{r.edge * 100:>+8.2f}%"
                  f"{nec * 100:>11.1f}%")
        print("=" * 88)
    return 0 if any(h.superviviente for h in hallazgos) else 1


def cmd_importar(args) -> int:
    """Convierte ficheros M1 de HistData.com al CSV canonico de Kronos."""
    series = loader.load_histdata(args.origen, symbol=args.symbol,
                                  tz_offset_horas=args.tz_offset,
                                  resample_a=args.timeframe)
    destino = loader.save_csv(series, args.out)

    inicio, fin = series[0].dt, series[-1].dt
    dias = max(1, (series[-1].ts - series[0].ts) // 86400)
    esperadas = dias * 1440
    huecos = sum(
        1 for a, b in zip(series, series[1:]) if b.ts - a.ts > series.timeframe * 5
    )
    print(f"  {len(series)} velas -> {destino}")
    print(f"  Rango (UTC)          {inicio:%Y-%m-%d %H:%M} .. {fin:%Y-%m-%d %H:%M}  ({dias} dias)")
    print(f"  Cobertura            {len(series)/esperadas*100:.1f}% del calendario natural")
    print(f"  Huecos > 5 velas     {huecos}  (los fines de semana cuentan aqui, es normal)")
    print(f"  Timeframe inferido   {series.timeframe}s")

    # A ~0.4% de actividad, que es lo que da la estrategia por defecto.
    estimadas = int(len(series) * 0.004)
    print(f"\n  Operaciones estimadas con la estrategia por defecto: ~{estimadas}")
    if estimadas < 400:
        faltan = int(400 / 0.004) - len(series)
        print(f"  Insuficiente para concluir. Descarga ~{faltan} velas mas")
        print(f"  (unos {faltan//(1440*5//7)} dias naturales) antes de sacar conclusiones.")
    else:
        print("  Muestra suficiente para que el contraste estadistico diga algo.")
    return 0


def cmd_validar(args) -> int:
    """Divide el historico y compara dentro y fuera de muestra.

    Es la prueba que de verdad importa. Un sistema ajustado a los datos con los
    que se diseno casi siempre luce bien en ellos; lo unico que dice algo es si
    el edge sobrevive en el tramo que nunca se miro.
    """
    series = _cargar_series(args)
    cfg = _config(args)
    corte = int(len(series) * args.split)
    if corte < 100 or len(series) - corte < 100:
        _err(f"serie demasiado corta para dividir ({len(series)} velas)")
        return 2

    dentro, fuera = series[:corte], series[corte:]
    resultados = []
    for etiqueta, tramo in (("DENTRO DE MUESTRA", dentro), ("FUERA DE MUESTRA", fuera)):
        r = Backtester(cfg.strategy(), cfg.backtest_config(), cfg.risk_params()).run(tramo)
        resultados.append((etiqueta, r))
        print(f"\n### {etiqueta} ({len(tramo)} velas)")
        print(render(r))

    (_, r_in), (_, r_out) = resultados
    print()
    print("=" * 72)
    print("  COMPARATIVA DENTRO / FUERA DE MUESTRA")
    print("=" * 72)
    print(f"  {'':<22}{'dentro':>14}{'fuera':>14}")
    print(f"  {'operaciones':<22}{r_in.n:>14}{r_out.n:>14}")
    print(f"  {'winrate':<22}{r_in.winrate*100:>13.2f}%{r_out.winrate*100:>13.2f}%")
    print(f"  {'edge':<22}{r_in.edge*100:>13.2f}%{r_out.edge*100:>13.2f}%")
    print(f"  {'p-valor':<22}{r_in.p_value:>14.4f}{r_out.p_value:>14.4f}")
    print("=" * 72)

    if r_out.decisivas < 30:
        print("  Tramo fuera de muestra sin operaciones suficientes: no concluye nada.")
        return 1
    if r_out.edge <= 0 < r_in.edge:
        print("  SOBREAJUSTE: el edge desaparece fuera de muestra. No desplegar.")
        return 1
    if not r_out.significativo:
        print("  El edge no se sostiene con significancia fuera de muestra.")
        return 1
    print("  El edge sobrevive fuera de muestra. Siguiente paso: cuenta demo real.")
    return 0


def cmd_demo(args) -> int:
    _err(f"demo: serie sintetica de {args.n} velas, payout 80%, vencimiento 5 velas")
    series = synthetic.generate(synthetic.SyntheticParams(n=args.n), seed=args.seed)
    cfg = default_config()
    bt = Backtester(
        cfg.strategy(),
        BacktestConfig(payout=0.80, expiry_velas=5, ventana=150),
        RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.MEDIA),
    )
    resultado = bt.run(series)
    print(render(resultado, max_trades=10))
    print()
    print("  NOTA: la serie sintetica es un paseo aleatorio con deriva; no")
    print("  contiene estructura explotable, asi que el veredicto esperado es")
    print("  que NO hay ventaja. La demo comprueba que el pipeline completo")
    print("  funciona de extremo a extremo, no que la estrategia sirva.")
    return 0


def cmd_paper(args) -> int:
    """Replay vela a vela contra el broker simulado: mismo camino que en vivo."""
    from kronos.broker.paper import PaperBroker

    series = _cargar_series(args)
    cfg = _config(args)
    estrategia = cfg.strategy()
    rp = cfg.risk_params()
    bc = cfg.backtest_config()

    from kronos.risk.manager import RiskManager

    broker = PaperBroker(balance_inicial=rp.balance_inicial, payout_por_defecto=bc.payout)
    broker.conectar()
    rm = RiskManager(rp)
    ventana = max(bc.ventana, estrategia.min_bars)
    expiracion = bc.expiry_velas * series.timeframe
    operaciones = 0

    for i in range(estrategia.min_bars, len(series)):
        vela = series[i]
        for cerrada in broker.marcar_precio(series.symbol, vela.close, vela.ts):
            devolucion = cerrada.stake + cerrada.pnl if cerrada.pnl >= 0 else 0.0
            rm.on_close(cerrada.pnl, devolucion)
            if args.verboso:
                print(f"  cierre {cerrada.id:<10} {str(cerrada.estado):<8} pnl {cerrada.pnl:+8.2f} "
                      f"balance {broker.balance():.2f}")

        rm.on_new_bar(vela.ts)
        if rm.state.kill_switch:
            _err(f"kill switch activado: {rm.state.motivo_kill}")
            break

        signal = estrategia.evaluate(series[max(0, i - ventana + 1) : i + 1])
        decision = rm.evaluate(signal)
        if not decision.permitido:
            continue
        orden = broker.comprar(series.symbol, signal.decision, decision.stake, expiracion)
        if orden.estado.value == "RECHAZADA":
            _err(f"orden rechazada: {orden.detalle}")
            continue
        rm.on_open(decision.stake)
        operaciones += 1
        if args.verboso:
            print(f"  apertura {orden.id:<10} {str(orden.direccion):<5} {signal.confianza:<6} "
                  f"stake {decision.stake:.2f} @ {orden.precio_entrada}")

    ganadas = sum(1 for o in broker.historial if o.estado.value == "GANADA")
    decisivas = sum(1 for o in broker.historial if o.estado.value in ("GANADA", "PERDIDA"))
    print()
    resumen = (
        f"{ganadas}/{decisivas} ganadas ({ganadas/decisivas*100:.1f}% winrate)"
        if decisivas else "ninguna operacion decisiva"
    )
    print(f"  Paper trading sobre {series.symbol}: {operaciones} ordenes, {resumen}")
    print(f"  Balance {rp.balance_inicial:.2f} -> {broker.balance():.2f} "
          f"({broker.balance()-rp.balance_inicial:+.2f})")
    print(f"  Umbral de equilibrio con payout {bc.payout*100:.0f}%: "
          f"{100/(1+bc.payout):.1f}% de aciertos")
    return 0


def cmd_indicadores(args) -> int:
    series = _cargar_series(args)
    closes, highs, lows = series.closes, series.highs, series.lows
    rsi = ind.rsi(closes, 14)
    bb = ind.bollinger(closes, 20, 2.0)
    atr = ind.atr(highs, lows, closes, 14)
    adx = ind.adx(highs, lows, closes, 14)
    st = ind.stochastic(highs, lows, closes)
    mac = ind.macd(closes)

    n = min(args.ultimas, len(series))
    print(f"  {series.symbol} - ultimas {n} velas")
    print(f"  {'fecha':<17}{'close':>10}{'RSI':>8}{'%B':>8}{'ATR%':>8}{'ADX':>8}{'%K':>8}{'MACDh':>10}")
    print(f"  {'-'*77}")
    for i in range(len(series) - n, len(series)):
        c = series[i]
        f = lambda v, d=2: f"{v:.{d}f}" if v is not None else "-"  # noqa: E731
        atr_pct = (atr[i] / c.close * 100) if atr[i] else None
        print(f"  {c.dt.strftime('%Y-%m-%d %H:%M'):<17}{c.close:>10.5f}{f(rsi[i]):>8}"
              f"{f(bb.percent_b[i]):>8}{f(atr_pct, 3):>8}{f(adx.adx[i]):>8}"
              f"{f(st.k[i]):>8}{f(mac.histogram[i], 6):>10}")
    return 0


def cmd_datos(args) -> int:
    series = synthetic.generate(
        synthetic.SyntheticParams(n=args.n, timeframe=args.timeframe), seed=args.seed,
        symbol=args.symbol,
    )
    destino = loader.save_csv(series, args.out)
    print(f"  {len(series)} velas escritas en {destino}")
    return 0


def cmd_config_init(args) -> int:
    destino = Path(args.out)
    if destino.exists() and not args.forzar:
        _err(f"{destino} ya existe; usa --forzar para sobrescribir")
        return 1
    default_config().save(destino)
    print(f"  configuracion escrita en {destino}")
    return 0


def cmd_selftest(args) -> int:
    import unittest

    suite = unittest.defaultTestLoader.discover(str(RAIZ / "tests"), top_level_dir=str(RAIZ))
    resultado = unittest.TextTestRunner(verbosity=2 if args.verboso else 1).run(suite)
    return 0 if resultado.wasSuccessful() else 1


# --------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kronos",
        description="Kronos AI - AutoIQ: motor de decision y backtesting para opciones binarias.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Estrategias disponibles: " + ", ".join(available()),
    )
    p.add_argument("--version", action="version", version=f"kronos {__version__}")
    sub = p.add_subparsers(dest="comando", required=True)

    def comunes(sp, con_datos: bool = True):
        if con_datos:
            sp.add_argument("--data", help="CSV o JSON con velas OHLCV")
            sp.add_argument("--symbol", default="EURUSD", help="nombre del activo")
            sp.add_argument("--sintetico", action="store_true", help="fuerza datos sinteticos")
            sp.add_argument("--n", type=int, default=3000, help="velas sinteticas a generar")
            sp.add_argument("--seed", type=int, default=42, help="semilla del generador")
        sp.add_argument("--config", help="fichero JSON de configuracion")
        sp.add_argument("--payout", type=float, help="payout del broker (0.80 = 80%%)")
        sp.add_argument("--expiry", type=int, help="vencimiento en numero de velas")
        sp.add_argument("--balance", type=float, help="balance inicial")
        sp.add_argument("--confianza-minima", dest="confianza_minima",
                        choices=["ALTA", "MEDIA", "BAJA"], help="confianza minima para operar")

    sp = sub.add_parser("decide", help="decision JSON sobre la ultima vela")
    comunes(sp)
    sp.add_argument("--stdin", action="store_true", help="lee las velas como JSON por stdin")
    sp.add_argument("--full", action="store_true", help="incluye votos y contexto de indicadores")
    sp.add_argument("--pretty", action="store_true", help="JSON indentado")
    sp.set_defaults(func=cmd_decide)

    sp = sub.add_parser("backtest", help="simulacion historica con veredicto")
    comunes(sp)
    sp.add_argument("--json", action="store_true", help="salida en JSON en vez de informe")
    sp.add_argument("--mostrar-trades", type=int, default=0, help="ultimas N operaciones")
    sp.add_argument("--exportar", help="guarda resumen y trades en un JSON")
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("cripto", help="baja historico de Binance (API publica, sin claves)")
    sp.add_argument("--symbol", default="BTCUSDT")
    sp.add_argument("--timeframe", type=int, default=3600,
                    help="segundos por vela: 60, 300, 900, 3600, 14400, 86400...")
    sp.add_argument("--velas", type=int, default=50_000)
    sp.add_argument("--out", default="data/btcusdt_h1.csv")
    sp.set_defaults(func=cmd_cripto)

    sp = sub.add_parser("descargar", help="baja historico de IQ Option a CSV (incluidos OTC)")
    sp.add_argument("--symbol", default="EURUSD-OTC")
    sp.add_argument("--velas", type=int, default=20_000)
    sp.add_argument("--timeframe", type=int, default=60)
    sp.add_argument("--out", default="data/eurusd_otc.csv")
    sp.set_defaults(func=cmd_descargar)

    sp = sub.add_parser("broker", help="prueba la conexion con IQ Option y mide el payout real")
    sp.add_argument("--symbol", default="EURUSD")
    sp.add_argument("--expiry-seg", dest="expiry_seg", type=int, default=300)
    sp.add_argument("--real", action="store_true",
                    help="cuenta REAL; requiere ademas KRONOS_ALLOW_REAL=1")
    sp.add_argument("--crudo", action="store_true",
                    help="vuelca lo que devuelve la API tal cual, para diagnosticar")
    sp.set_defaults(func=cmd_broker)

    sp = sub.add_parser("duelo", help="quien acerto mas: el cerebro IA o las reglas")
    comunes(sp)
    sp.add_argument("--registro", default="data/decisiones.jsonl",
                    help="JSONL que escribe el motor en vivo")
    sp.add_argument("--spread", type=float, default=0.5, help="spread en pips")
    sp.set_defaults(func=cmd_duelo)

    sp = sub.add_parser("explorar", help="barre hipotesis con correccion por test multiple")
    comunes(sp)
    sp.add_argument("--expiries", default="1,3,5,10", help="vencimientos a probar")
    sp.add_argument("--split", type=float, default=0.6)
    sp.add_argument("--spread", type=float, default=0.5,
                    help="spread en pips; 0 infla los resultados (ver docs)")
    sp.add_argument("--top", type=int, default=25)
    sp.add_argument("--sensibilidad", nargs="?", const="RSI extremo",
                    help="analiza como el spread erosiona una hipotesis concreta")
    sp.add_argument("--modo", choices=["binarias", "stops"], default="binarias",
                    help="'stops' usa stop y objetivo en vez de vencimiento fijo")
    sp.add_argument("--rr", type=float, default=2.0,
                    help="objetivo:riesgo en modo stops (2.0 = umbral 33.3%%)")
    sp.add_argument("--atr-mult", dest="atr_mult", type=float, default=1.5,
                    help="distancia del stop en multiplos de ATR")
    sp.add_argument("--max-velas", dest="max_velas", type=int, default=48,
                    help="velas maximas esperando a que se resuelva")
    sp.add_argument("--cripto", action="store_true",
                    help="el activo es cripto: el 'pip' se calcula sobre el precio")
    sp.set_defaults(func=cmd_explorar)

    sp = sub.add_parser("importar", help="convierte ficheros M1 de HistData.com al CSV de Kronos")
    sp.add_argument("origen", nargs="+", help="zip, csv o carpeta descargada de HistData")
    sp.add_argument("--out", default="data/historico.csv")
    sp.add_argument("--symbol", default="EURUSD")
    sp.add_argument("--tz-offset", dest="tz_offset", type=int, default=loader.HISTDATA_TZ_OFFSET,
                    help="zona horaria del origen en horas (HistData es EST sin DST = -5)")
    sp.add_argument("--timeframe", type=int, default=None,
                    help="reagrupa a este timeframe en segundos (3600 = velas de 1 hora); "
                         "se hace fichero a fichero para no agotar la memoria")
    sp.set_defaults(func=cmd_importar)

    sp = sub.add_parser("validar", help="compara dentro y fuera de muestra (anti sobreajuste)")
    comunes(sp)
    sp.add_argument("--split", type=float, default=0.6,
                    help="fraccion de la serie usada como dentro de muestra")
    sp.set_defaults(func=cmd_validar)

    sp = sub.add_parser("demo", help="simulacion autocontenida de extremo a extremo")
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--n", type=int, default=12000,
                    help="velas a simular; con menos de ~10.000 no se junta muestra")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("paper", help="replay contra el broker simulado")
    comunes(sp)
    sp.add_argument("--verboso", action="store_true")
    sp.set_defaults(func=cmd_paper)

    sp = sub.add_parser("indicadores", help="volcado de indicadores")
    comunes(sp)
    sp.add_argument("--ultimas", type=int, default=20)
    sp.set_defaults(func=cmd_indicadores)

    sp = sub.add_parser("datos", help="genera una serie sintetica en CSV")
    sp.add_argument("--out", default="data/sintetico.csv")
    sp.add_argument("--n", type=int, default=5000)
    sp.add_argument("--timeframe", type=int, default=60)
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--symbol", default="SYNTH/USD")
    sp.set_defaults(func=cmd_datos)

    sp = sub.add_parser("config-init", help="escribe una configuracion por defecto")
    sp.add_argument("--out", default="config/default.json")
    sp.add_argument("--forzar", action="store_true")
    sp.set_defaults(func=cmd_config_init)

    sp = sub.add_parser("selftest", help="ejecuta la bateria de tests")
    sp.add_argument("--verboso", action="store_true")
    sp.set_defaults(func=cmd_selftest)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (loader.LoaderError, FileNotFoundError, KeyError, ValueError) as e:
        _err(f"error: {e}")
        return 2
    except KeyboardInterrupt:
        _err("interrumpido")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
