"""Ejecucion sin panel: `python -m kronos.live --data data/eurusd.csv`.

Util para dejar el bot corriendo en un servidor sin Streamlit. Escribe cada
decision en el JSONL de registro, que el panel puede leer despues.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import time

from kronos.ia.cerebro import CerebroIA, CerebroNoDisponible
from kronos.ia.coste import estimar_coste_diario
from kronos.live.feed import FeedReplay, FeedSintetico
from kronos.live.motor import ConfigMotor, MotorEnVivo
from kronos.risk.manager import RiskParams
from kronos.strategy.base import Confidence


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kronos.live", description="Motor en vivo sin panel.")
    p.add_argument("--data", help="CSV a reproducir; si falta, se usa feed sintetico")
    p.add_argument("--velocidad", type=float, default=60.0, help="multiplicador de tiempo")
    p.add_argument("--intervalo", type=float, default=5.0, help="segundos por ciclo")
    p.add_argument("--expiry", type=int, default=5, help="vencimiento en velas")
    p.add_argument("--payout", type=float, default=0.80)
    p.add_argument("--balance", type=float, default=1000.0)
    p.add_argument("--modelo", default="claude-opus-5")
    p.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    p.add_argument("--ejecutor", default="ia", choices=["ia", "local"])
    p.add_argument("--sin-ia", action="store_true", help="solo el cerebro local (coste cero)")
    p.add_argument("--cada-ciclo", action="store_true",
                   help="consulta la API en cada ciclo, no solo al cerrar vela (12x coste)")
    p.add_argument("--registro", default="data/decisiones.jsonl")
    p.add_argument("--duracion", type=float, default=0.0, help="segundos; 0 = indefinido")
    p.add_argument("--broker", choices=["papel", "iq"], default="papel",
                   help="'iq' opera contra IQ Option en cuenta DEMO")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--confianza", choices=["BAJA", "MEDIA", "ALTA"], default="MEDIA",
                   help="confianza minima para operar; BAJA genera mas ordenes "
                        "(util para probar la fontaneria, no para evaluar)")
    args = p.parse_args(argv)

    # Sin cerebro IA no puede ser el ejecutor: sin esto, cada decision se veta
    # con SIN_DECISION_IA y el bot no opera nunca sin decir por que.
    if args.sin_ia and args.ejecutor == "ia":
        args.ejecutor = "local"
        print("[kronos] --sin-ia: el ejecutor pasa a 'local'", file=sys.stderr)

    broker = None
    if args.broker == "iq":
        # Contra un broker real, las velas tienen que venir del propio broker:
        # decidir sobre un CSV y ejecutar contra IQ Option no prueba nada.
        from kronos.broker.base import TipoCuenta
        from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker
        from kronos.live.feed import FeedIQOption
        try:
            broker = IQOptionBroker(tipo_cuenta=TipoCuenta.DEMO)
            broker.conectar()
        except BrokerNoDisponible as e:
            print(f"[kronos] {e}", file=sys.stderr)
            return 2
        info = broker.diagnostico(args.symbol, args.expiry * 60)
        print(f"[kronos] IQ Option DEMO | balance {info.get('balance', '?')} "
              f"| payout {info.get('payout', 0) * 100:.1f}% "
              f"| umbral {(info.get('umbral_equilibrio') or 0) * 100:.2f}%", file=sys.stderr)
        if not info.get("symbol_operable"):
            print(f"[kronos] AVISO: {args.symbol} no esta operable ahora "
                  "(mercado cerrado o activo no disponible)", file=sys.stderr)
        if "payout" in info:
            args.payout = info["payout"]  # el payout real manda sobre el configurado
        feed = FeedIQOption(broker, symbol=args.symbol, timeframe=60)
        print(f"[kronos] {feed.descripcion()}", file=sys.stderr)
    elif args.data:
        feed = FeedReplay(args.data, velocidad=args.velocidad)
    else:
        feed = FeedSintetico(velocidad=args.velocidad)

    cerebro = None
    if not args.sin_ia:
        try:
            cerebro = CerebroIA(modelo=args.modelo, effort=args.effort)
            ok, detalle = cerebro.prueba_de_conexion()
            print(f"[kronos] {detalle}", file=sys.stderr)
            if not ok:
                return 2
        except CerebroNoDisponible as e:
            print(f"[kronos] {e}", file=sys.stderr)
            return 2

    solo_cierre = not args.cada_ciclo
    seg = feed.timeframe if solo_cierre else args.intervalo
    if not args.sin_ia:
        print(f"[kronos] coste estimado: ~${estimar_coste_diario(seg, args.modelo):,.2f}/dia",
              file=sys.stderr)

    motor = MotorEnVivo(
        feed=feed,
        config=ConfigMotor(
            intervalo_seg=args.intervalo, solo_en_cierre_de_vela=solo_cierre,
            expiry_velas=args.expiry, payout=args.payout,
            cerebro_ejecutor=args.ejecutor, usar_ia=not args.sin_ia,
            registro=args.registro,
        ),
        riesgo=RiskParams(balance_inicial=args.balance,
                          confianza_minima=Confidence(args.confianza)),
        cerebro=cerebro,
        broker=broker,
    )

    signal.signal(signal.SIGINT, lambda *_: motor.detener())
    motor.iniciar()
    print(f"[kronos] {feed.descripcion()} — Ctrl+C para parar", file=sys.stderr)

    inicio = time.time()
    try:
        while motor.corriendo:
            time.sleep(2)
            s = motor.snapshot()
            st = s["stats"]
            aviso = ""
            if s.get("feed_estancado"):
                aviso = f"  <== SIN VELAS {s['seg_sin_velas'] / 60:.0f} min"
            linea = (f"  ciclos {st.consultas:>5} | ordenes {st.ordenes:>4} "
                     f"| {st.ganadas}G/{st.perdidas}P | balance {s['balance']:>9,.2f} "
                     f"| IA ${s['coste_total']:.4f}{aviso}")
            # Rellenar hasta el ancho de la consola: sin esto, una linea corta
            # no borra los restos de la anterior y el estado queda ilegible.
            ancho = shutil.get_terminal_size((100, 24)).columns
            print("\r" + linea.ljust(max(ancho - 1, len(linea))), end="", flush=True)
            if args.duracion and time.time() - inicio > args.duracion:
                break
    except KeyboardInterrupt:
        pass
    finally:
        motor.detener()

    s = motor.snapshot()
    st = s["stats"]
    umbral = s["umbral_equilibrio"]
    print(f"\n\n  Ciclos: {st.consultas} | Ordenes: {st.ordenes} "
          f"| Ganadas {st.ganadas} / Perdidas {st.perdidas} / Empates {st.empates}")
    if st.decisivas:
        print(f"  Winrate {st.winrate * 100:.1f}% frente al umbral de equilibrio "
              f"{umbral * 100:.1f}%")
    if st.acuerdos + st.desacuerdos:
        print(f"  Acuerdo IA/local: {st.tasa_acuerdo * 100:.0f}% "
              f"({st.acuerdos} de {st.acuerdos + st.desacuerdos})")
    # Se separan los dos numeros a proposito: si no coinciden, hubo movimientos
    # en la cuenta que el bot no hizo (operaciones manuales, comisiones, bonos).
    print(f"  PnL del bot (solo sus ordenes): {st.pnl_bot:+,.2f}")
    print(f"  Balance de la cuenta: {s['balance_inicial']:,.2f} -> {s['balance']:,.2f} "
          f"({s['pnl']:+,.2f})")
    ajeno = s["pnl"] - st.pnl_bot
    if abs(ajeno) > 0.01:
        print(f"  AVISO: {ajeno:+,.2f} de la cuenta NO viene de este bot "
              "(operaciones manuales, comisiones o bonos).")
    print(f"  Coste de la API: ${s['coste_total']:.4f} en {s['llamadas_ia']} llamadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
