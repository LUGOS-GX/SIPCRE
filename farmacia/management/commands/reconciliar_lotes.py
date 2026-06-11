"""
Reconcilia el stock por lotes con el stock real (stock_actual).

Antes de FEFO, las salidas (ventas, despachos, reactivos) bajaban stock_actual
pero NUNCA descontaban lotes: la suma de cantidad_actual de los lotes quedó
inflada respecto al stock real. Este comando consume ese excedente histórico
con el mismo criterio FEFO (se asume que lo vendido salió de los lotes de
vencimiento más próximo, que es lo que la farmacia hace físicamente).

Uso:
    python manage.py reconciliar_lotes            # simulación (no escribe nada)
    python manage.py reconciliar_lotes --aplicar  # ejecuta los ajustes
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from farmacia.models import Medicamento


class Command(BaseCommand):
    help = "Cuadra la suma de lotes con stock_actual consumiendo el excedente histórico en orden FEFO."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Aplica los cambios (sin esta bandera solo simula y reporta).')

    def handle(self, *args, **opciones):
        aplicar = opciones['aplicar']
        modo = "APLICANDO" if aplicar else "SIMULACIÓN (use --aplicar para ejecutar)"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Reconciliación de lotes — {modo}\n"))

        ajustados = 0
        descuadre_inverso = 0

        with transaction.atomic():
            # La consulta agregada (annotate + Sum genera GROUP BY) NO puede
            # llevar select_for_update: PostgreSQL prohíbe FOR UPDATE con
            # GROUP BY. El lock se toma por medicamento, justo antes de
            # ajustar sus lotes (que es donde de verdad importa).
            medicamentos = (Medicamento.objects
                            .annotate(total_lotes=Sum('lotes__cantidad_actual'))
                            .exclude(total_lotes__isnull=True))

            for med in medicamentos:
                total_lotes = med.total_lotes or 0
                exceso = total_lotes - med.stock_actual

                if exceso > 0:
                    # Lotes inflados: consumir el exceso en orden FEFO
                    self.stdout.write(
                        f"  {med.nombre}: lotes={total_lotes}, stock real={med.stock_actual} "
                        f"→ descontar {exceso} uds de lotes (FEFO)"
                    )
                    if aplicar:
                        # Ahora sí bloqueamos: re-leemos los lotes de este
                        # medicamento con FOR UPDATE (consulta simple, sin
                        # GROUP BY) para serializar frente a ventas concurrentes.
                        restante = exceso
                        lotes = (med.lotes.select_for_update()
                                 .filter(cantidad_actual__gt=0)
                                 .order_by('fecha_vencimiento', 'fecha_ingreso'))
                        for lote in lotes:
                            if restante <= 0:
                                break
                            consumido = min(lote.cantidad_actual, restante)
                            lote.cantidad_actual -= consumido
                            lote.save(update_fields=['cantidad_actual'])
                            restante -= consumido
                    ajustados += 1

                elif exceso < 0:
                    # Caso raro: lotes por DEBAJO del stock (entradas sin lote).
                    # No se inventan unidades en lotes: solo se reporta.
                    self.stdout.write(self.style.WARNING(
                        f"  {med.nombre}: lotes={total_lotes} < stock real={med.stock_actual} "
                        f"(faltan {-exceso} uds sin lote asignado — registrar un lote si se "
                        f"quiere trazabilidad completa de esas unidades)"
                    ))
                    descuadre_inverso += 1

            if not aplicar:
                transaction.set_rollback(True)

        self.stdout.write("")
        if ajustados == 0 and descuadre_inverso == 0:
            self.stdout.write(self.style.SUCCESS("Todo cuadrado: lotes y stock coinciden."))
        else:
            resumen = f"{ajustados} medicamento(s) con exceso en lotes"
            if descuadre_inverso:
                resumen += f", {descuadre_inverso} con unidades sin lote (solo informativo)"
            cierre = "ajustes aplicados." if aplicar else "ningún cambio escrito (simulación)."
            self.stdout.write(self.style.SUCCESS(f"{resumen} — {cierre}"))
