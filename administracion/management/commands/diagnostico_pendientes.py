"""
Diagnóstico de facturas PENDIENTES que aparecen en el Histórico de Caja pero
NO en la cajita de deudas de un paciente.

La cajita de deudas busca por cédula normalizada (solo dígitos). Una factura
pendiente NO aparecerá ahí si su cedula_cliente está vacía, es nula, o tiene un
formato que no casa (puntos, "V-", etc.) — típicamente facturas creadas antes
de las normalizaciones. El Histórico, en cambio, muestra TODAS las facturas
(es un registro de transacciones), por eso se ven ahí.

Este comando solo REPORTA (no modifica nada). Permite confirmar el origen y
decidir qué hacer con cada caso.

Uso:
    python manage.py diagnostico_pendientes
"""
from django.core.management.base import BaseCommand
from administracion.models import Factura


def solo_digitos(valor):
    if not valor:
        return ''
    return ''.join(ch for ch in str(valor) if ch.isdigit())


class Command(BaseCommand):
    help = "Reporta facturas pendientes que no aparecerían en la cajita de deudas por cédula."

    def handle(self, *args, **opciones):
        pendientes = Factura.objects.filter(estado='Pendiente').order_by('id')
        total = pendientes.count()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nFacturas PENDIENTES en total: {total}\n"
        ))

        sin_cedula = []
        formato_raro = []
        total_sin_cedula = 0

        for fac in pendientes:
            ced = fac.cedula_cliente
            digitos = solo_digitos(ced)
            if not digitos:
                sin_cedula.append(fac)
                total_sin_cedula += float(fac.total or 0)
            elif ced != digitos:
                # Tiene dígitos pero el guardado no es canónico (puntos, V-, etc.)
                formato_raro.append((fac, digitos))

        # 1. Sin cédula: nunca aparecen en la cajita
        if sin_cedula:
            self.stdout.write(self.style.WARNING(
                f"[A] {len(sin_cedula)} factura(s) pendientes SIN cédula "
                f"(no aparecen en ninguna cajita de deudas; total ${total_sin_cedula:.2f}):"
            ))
            for fac in sin_cedula:
                nombre = fac.nombre_cliente or (fac.paciente.nombres if fac.paciente else '—')
                self.stdout.write(
                    f"    - Factura #{fac.id} | {nombre} | ${fac.total} | "
                    f"emitida {fac.fecha_emision.strftime('%d/%m/%Y') if fac.fecha_emision else '—'}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("[A] No hay pendientes sin cédula."))

        # 2. Formato no canónico: aparecen solo si se busca con el formato exacto
        self.stdout.write("")
        if formato_raro:
            self.stdout.write(self.style.WARNING(
                f"[B] {len(formato_raro)} factura(s) pendientes con cédula en formato NO canónico "
                f"(la migración 0019 ya debería haberlas limpiado; si aparecen, son nuevas):"
            ))
            for fac, digitos in formato_raro:
                self.stdout.write(
                    f"    - Factura #{fac.id} | guardada '{fac.cedula_cliente}' → debería ser '{digitos}'"
                )
        else:
            self.stdout.write(self.style.SUCCESS("[B] Todas las pendientes con cédula están en formato canónico."))

        # Resumen
        self.stdout.write("")
        ok = total - len(sin_cedula) - len(formato_raro)
        self.stdout.write(self.style.MIGRATE_HEADING("Resumen:"))
        self.stdout.write(f"  - {ok} pendiente(s) correctas (aparecen en su cajita al buscar la cédula).")
        self.stdout.write(f"  - {len(sin_cedula)} sin cédula (fantasma en histórico).")
        self.stdout.write(f"  - {len(formato_raro)} con formato no canónico.")
        if sin_cedula or formato_raro:
            self.stdout.write(self.style.NOTICE(
                "\nEstas facturas son anteriores a las normalizaciones o se crearon por flujos "
                "que no asignaban cédula. Decida: (1) dejarlas como registro histórico, "
                "(2) asignarles la cédula correcta a mano desde el admin, o "
                "(3) anularlas si fueron pruebas."
            ))
