"""
Servicios de inventario por lotes (FEFO: First Expired, First Out).

Hasta ahora los lotes solo se INCREMENTABAN (al registrarlos) y ninguna salida
los descontaba: el stock por lote quedaba inflado y la trazabilidad de
vencimientos era ficticia. Estos helpers centralizan el movimiento de lotes
para TODAS las salidas y entradas de stock.

Contrato:
- Los helpers tocan SOLO los lotes. El campo Medicamento.stock_actual lo sigue
  manejando cada vista (que ya tiene su propia lógica de validación, kardex y
  auditoría de controlados).
- Deben llamarse DENTRO de una transacción con el medicamento ya bloqueado
  (select_for_update), como ya hacen el despacho y la venta directa: el lock
  del medicamento serializa también el acceso a sus lotes.
- Son tolerantes con los datos históricos: si los lotes no alcanzan a cubrir
  la cantidad (porque las ventas viejas nunca los descontaron), la operación
  NO se bloquea — se consume lo que haya y se reporta el faltante para el log.
  El comando `reconciliar_lotes` permite cuadrar el histórico.
"""

import logging

logger = logging.getLogger('sipcre')


def descontar_lotes_fefo(medicamento, cantidad):
    """
    Consume `cantidad` unidades de los lotes del medicamento, empezando por el
    de vencimiento más próximo (FEFO) y, a igual vencimiento, el más antiguo.

    Devuelve el faltante (0 si los lotes cubrieron todo). Un faltante > 0
    significa que los lotes estaban descuadrados respecto a stock_actual
    (datos previos a FEFO); se loguea para auditoría pero no detiene la venta,
    porque stock_actual —la fuente de verdad operativa— sí tenía existencias.
    """
    restante = int(cantidad)
    if restante <= 0:
        return 0

    lotes = (medicamento.lotes
             .select_for_update()
             .filter(cantidad_actual__gt=0)
             .order_by('fecha_vencimiento', 'fecha_ingreso'))

    for lote in lotes:
        if restante <= 0:
            break
        consumido = min(lote.cantidad_actual, restante)
        lote.cantidad_actual -= consumido
        lote.save(update_fields=['cantidad_actual'])
        restante -= consumido

    if restante > 0:
        logger.warning(
            "FEFO: lotes descuadrados para '%s' (id=%s): faltaron %s unidades "
            "por descontar de lotes (stock_actual sí las cubría). "
            "Ejecutar 'manage.py reconciliar_lotes' para cuadrar el histórico.",
            medicamento.nombre, medicamento.pk, restante
        )
    return restante


def reintegrar_lotes(medicamento, cantidad):
    """
    Devuelve `cantidad` unidades a los lotes (devoluciones de pacientes,
    reintegros por cambio). Rellena en orden FEFO los lotes que tengan espacio
    (cantidad_actual < cantidad_ingresada), es decir, repone primero lo que el
    consumo FEFO drenó primero. Si sobra (no hay lotes con espacio), el
    excedente se suma al lote de vencimiento más lejano; si el medicamento no
    maneja lotes, no se hace nada (solo vive en stock_actual, como hasta ahora).

    Devuelve el excedente no asignado a ningún lote (0 en el caso normal).
    """
    restante = int(cantidad)
    if restante <= 0:
        return 0

    lotes_con_espacio = (medicamento.lotes
                         .select_for_update()
                         .order_by('fecha_vencimiento', 'fecha_ingreso'))

    ultimo_lote = None
    for lote in lotes_con_espacio:
        ultimo_lote = lote
        if restante <= 0:
            break
        espacio = lote.cantidad_ingresada - lote.cantidad_actual
        if espacio <= 0:
            continue
        repuesto = min(espacio, restante)
        lote.cantidad_actual += repuesto
        lote.save(update_fields=['cantidad_actual'])
        restante -= repuesto

    if restante > 0 and ultimo_lote is not None:
        # Sin espacio "histórico" donde reponer: va al lote más nuevo.
        ultimo_lote.cantidad_actual += restante
        ultimo_lote.save(update_fields=['cantidad_actual'])
        restante = 0

    return restante
