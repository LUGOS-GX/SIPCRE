// ==========================================
// CONTROL DE SPINNERS PARA BOTONES DE CARGA
// ==========================================

document.addEventListener("DOMContentLoaded", function() {
    
    // Buscamos cualquier botón o enlace que tenga la clase 'btn-generar-pdf'
    const botonesPdf = document.querySelectorAll('.btn-generar-pdf');

    botonesPdf.forEach(btn => {
        btn.addEventListener('click', function(e) {
            
            // 1. Si el botón ya está cargando, evitamos que haga clic de nuevo
            if (this.classList.contains('is-loading')) {
                e.preventDefault();
                return;
            }

            // 2. Bloqueamos el botón visualmente
            this.classList.add('is-loading', 'opacity-75', 'cursor-not-allowed', 'pointer-events-none');

            // 3. Guardamos lo que decía el botón originalmente (Ej: "Descargar PDF")
            const textoOriginal = this.innerHTML;

            // 4. Le inyectamos el Spinner animado de Tailwind y cambiamos el texto
            this.innerHTML = `
                <svg class="animate-spin -ml-1 mr-2 h-5 w-5 text-current inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Generando...
            `;

            // 5. Como el PDF se abre en una pestaña nueva (target="_blank"), 
            // la página actual no se recarga. Por lo tanto, restauramos el botón a la normalidad 
            // después de 3.5 segundos para que el médico pueda volver a usarlo si lo necesita.
            setTimeout(() => {
                this.innerHTML = textoOriginal;
                this.classList.remove('is-loading', 'opacity-75', 'cursor-not-allowed', 'pointer-events-none');
            }, 3500);
        });
    });
});