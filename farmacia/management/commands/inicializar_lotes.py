"""
Crea un "lote inicial" para el stock histórico que quedó FUERA de todo lote.

Cuando el sistema se creó, el formulario de medicamento sumaba stock pero NO
creaba un lote; los lotes se empezaron a registrar encima después. Resultado:
en los medicamentos viejos, parte del stock real (stock_actual) no está
respaldado por ningún lote, y por eso FEFO no lo puede consumir.

Este comando detecta esa diferencia (stock_actual - suma de lotes) y crea un
lote por ese faltante, de modo que el 100% del stock quede trazable. Hereda la
fecha de vencimiento del medicamento si la tiene; si no, usa una fecha
placeholder lejana (31/12/2099) que se corrige luego en Gestión de Lotes.

Es la contraparte de `reconciliar_lotes`:
  - inicializar_lotes  -> stock SIN lote (lotes por DEBAJO del stock): crea el faltante.
  - reconciliar_lotes  -> lotes POR ENCIMA del stock: consume el sobrante (FEFO).

Uso:
    python manage.py inicializar_lotes            # simulación (no escribe nada)
    python manage.py inicializar_lotes --aplicar  # crea los lotes iniciales
"""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from farmacia.models import Medicamento, LoteMedicamento

FECHA_PLACEHOLDER = date(2099, 12, 31)


class Command(BaseCommand):
    help = "Crea lotes iniciales para el stock histórico que no está respaldado por ningún lote."

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Aplica los cambios (sin esta bandera solo simula y reporta).')

    def handle(self, *args, **opciones):
        aplicar = opciones['aplicar']
        modo = "APLICANDO" if aplicar else "SIMULACIÓN (use --aplicar para ejecutar)"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Inicialización de lotes — {modo}\n"))

        creados = 0

        with transaction.atomic():
            medicamentos = (Medicamento.objects
                            .annotate(total_lotes=Sum('lotes__cantidad_actual')))

            for med in medicamentos:
                total_lotes = med.total_lotes or 0
                faltante = med.stock_actual - total_lotes

                if faltante <= 0:
                    continue  # stock ya cubierto por lotes (o sobrante: eso lo ve reconciliar_lotes)

                venc = med.fecha_vencimiento or FECHA_PLACEHOLDER
                nota_venc = "" if med.fecha_vencimiento else " (vencimiento SIN DEFINIR: corregir en Gestión de Lotes)"

                self.stdout.write(
                    f"  {med.nombre}: stock real={med.stock_actual}, en lotes={total_lotes} "
                    f"→ crear lote inicial por {faltante} uds, vence {venc.strftime('%d/%m/%Y')}{nota_venc}"
                )

                if aplicar:
                    # Bloqueamos el medicamento para numerar el lote sin choques.
                    med_bloqueado = Medicamento.objects.select_for_update().get(pk=med.pk)
                    LoteMedicamento.objects.create(
                        medicamento=med_bloqueado,
                        numero_lote=LoteMedicamento.generar_numero_lote(med_bloqueado),
                        cantidad_ingresada=faltante,
                        cantidad_actual=faltante,
                        fecha_vencimiento=venc,
                    )
                creados += 1

            if not aplicar:
                transaction.set_rollback(True)

        self.stdout.write("")
        if creados == 0:
            self.stdout.write(self.style.SUCCESS("No hay stock huérfano: todo el inventario ya está respaldado por lotes."))
        else:
            cierre = "lotes iniciales creados." if aplicar else "ningún cambio escrito (simulación)."
            self.stdout.write(self.style.SUCCESS(f"{creados} medicamento(s) con stock huérfano — {cierre}"))
            if aplicar:
                self.stdout.write(self.style.WARNING(
                    "Revise en Gestión de Lotes los lotes con vencimiento 31/12/2099 "
                    "y corríjales la fecha real."
                ))
