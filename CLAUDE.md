# Notas para agentes que trabajen en este repositorio

## Contexto

Kronos convierte reglas técnicas en decisiones de opciones binarias y mide si
esas reglas tienen ventaja real. El valor del proyecto no está en la estrategia
—cualquiera puede escribir otra— sino en que **el simulador no se engaña**.

## Comandos

```bash
python -m kronos selftest      # 359 tests, ~60 s, sin red
python -m kronos demo          # pipeline completo de extremo a extremo
```

La demo debe terminar en `NO DESPLEGAR - ESPERANZA NEGATIVA`. La serie sintética
es un paseo aleatorio: si algún día ese veredicto sale positivo, lo más probable
es que se haya colado un sesgo de look-ahead en el simulador. Trátalo como una
alarma, no como una mejora.

Sin dependencias externas y sin pytest: la batería usa `unittest` de la
biblioteca estándar.

## Invariantes que no se pueden romper

1. **Sin look-ahead.** El valor de un indicador en `i` solo puede depender de
   datos `<= i`. La estrategia solo recibe `series[:i+1]`. Si tocas
   `core/indicators.py` o `backtest/engine.py`, `test_sin_look_ahead` y
   `test_la_estrategia_solo_ve_el_pasado` son la red de seguridad.

2. **El winrate nunca se muestra solo.** Siempre junto al umbral de equilibrio
   `1/(1+payout)`, al edge y al p-valor. Un informe que solo muestre aciertos
   induce a desplegar sistemas en pérdida estructural.

3. **Nada de martingala.** Ninguna progresión de stake tras una pérdida, en
   ninguna forma. `test_sin_martingala_tras_perder` lo impide.

4. **El riesgo veta a la estrategia,** nunca al revés. Que se rechacen señales
   válidas es el funcionamiento normal.

5. **Salida ASCII** en los informes: la consola de Windows no siempre resuelve
   UTF-8. `test_informe_es_ascii` lo verifica.

6. **`decide` escribe solo JSON en stdout.** Los avisos van a stderr. Hay un
   script ejecutor al otro lado parseando esa salida.

7. **Credenciales solo por variable de entorno.** Nunca por CLI ni por fichero
   de configuración.

## Convenciones

- Los indicadores devuelven listas de la misma longitud que la entrada, con
  `None` en el calentamiento.
- Los umbrales viven en dataclasses de parámetros, nunca inline en la lógica.
- Los comentarios explican **por qué**, no qué hace la línea siguiente.
- Nombres y mensajes en español, sin tildes en el código (compatibilidad de
  consola); el README sí las lleva.

## Al añadir una estrategia

Hereda de `Strategy`, implementa `min_bars` y `evaluate`, y regístrala en
`strategy/registry.py`. Los tests de contrato de `tests/test_strategy.py`
deberían pasar sin cambios.

## Al tocar los parámetros por defecto

Valida con `python -m kronos validar` antes y después. Un cambio que mejora el
backtest y empeora el tramo fuera de muestra es sobreajuste, no una mejora.
