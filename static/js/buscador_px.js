// ==========================================
// BUSCADOR INTELIGENTE UNIFICADO
// ==========================================

function abrirModalControl() {
    const modal = document.getElementById('modal_control');
    if(modal) modal.classList.remove('hidden');
}

function cerrarModalControl() {
    const modal = document.getElementById('modal_control');
    const menu = document.getElementById('menu_select_control');
    const input = document.getElementById('buscador_control_input');
    
    if(modal) modal.classList.add('hidden');
    if(menu) menu.classList.add('hidden');
    if(input) {
        input.value = '';
        filtrarControles();
    }
}

function toggleDropdownControl() {
    const menu = document.getElementById('menu_select_control');
    if(menu) {
        menu.classList.toggle('hidden');
        if (!menu.classList.contains('hidden')) {
            document.getElementById('buscador_control_input').focus();
        }
    }
}

function filtrarControles() {
    const input = document.getElementById('buscador_control_input');
    if(!input) return;
    
    // Lo que escribió el usuario (Ej: "juan" o "30420")
    const filter = input.value.toLowerCase();
    
    // TRUCO 1: Le quitamos los puntos a lo que escribió el usuario
    const filterSinPuntos = filter.replace(/\./g, '');

    const nodos = document.querySelectorAll('.opcion-control');

    nodos.forEach(nodo => {
        if(nodo.getAttribute('data-texto') === 'todos') return; 

        // El texto que viene de la BD (Ej: "Juan Pérez 30.420.069")
        const textoOriginal = nodo.getAttribute('data-texto').toLowerCase();
        
        // TRUCO 2: El texto de la BD pero le quitamos los puntos (Ej: "juan pérez 30420069")
        const textoSinPuntos = textoOriginal.replace(/\./g, '');

        // MAGIA: Si el texto coincide de forma normal O ignorando los puntos, ¡se muestra!
        if (textoOriginal.includes(filter) || textoSinPuntos.includes(filterSinPuntos)) {
            nodo.style.display = "block";
        } else {
            nodo.style.display = "none";
        }
    });
}

function seleccionarControlCustom(id, textoVisible) {
    const inputOculto = document.getElementById('control_paciente_id');
    if(inputOculto) inputOculto.value = id;
    
    const textoBtn = document.getElementById('texto_select_control');
    if(textoBtn) {
        textoBtn.innerText = textoVisible;
        textoBtn.classList.remove('text-gray-500');
        textoBtn.classList.add('text-gray-900', 'font-bold', 'bg-blue-50');
    }
    
    const menu = document.getElementById('menu_select_control');
    if(menu) menu.classList.add('hidden');
    
    const inputBusqueda = document.getElementById('buscador_control_input');
    if(inputBusqueda) {
        inputBusqueda.value = '';
        filtrarControles();
    }
}

// ==========================================
// EVENTOS GLOBALES (Se ejecutan al cargar)
// ==========================================
document.addEventListener("DOMContentLoaded", function() {
    
    const inputBusqueda = document.getElementById('buscador_control_input');
    if (inputBusqueda) {
        inputBusqueda.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault(); 
                let busqueda = this.value.trim();

                // TRUCO 3 (Para la tecla Enter): 
                // Si el médico escribió PUROS NÚMEROS (sin letras), le inyectamos
                // los puntos de miles automáticamente antes de mandarlo a Django.
                if (/^\d+$/.test(busqueda)) {
                    busqueda = busqueda.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
                }

                window.location.href = '?q=' + encodeURIComponent(busqueda);
            }
        });
    }

    // CERRAR MENÚ AL HACER CLIC AFUERA
    document.addEventListener('click', function(event) {
        const boton = document.getElementById('btn_select_control');
        const menu = document.getElementById('menu_select_control');
        const input = document.getElementById('buscador_control_input');
        
        if (boton && menu && !boton.contains(event.target) && !menu.contains(event.target) && event.target !== input) {
            menu.classList.add('hidden');
        }
    });

});