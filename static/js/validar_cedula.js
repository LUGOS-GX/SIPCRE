// ==========================================
// VALIDADOR UNIVERSAL DE CÉDULAS VENEZOLANAS
// ==========================================

document.addEventListener("DOMContentLoaded", function() {
    const inputsCedula = document.querySelectorAll('input[name*="cedula"], input[id*="cedula"]');
    
    inputsCedula.forEach(function(input) {
        
        // 1. EL TRUCO MAESTRO: Convertimos el campo a texto para que el navegador 
        // no pelee con los puntos, pero forzamos el teclado numérico en celulares.
        input.setAttribute('type', 'text');
        input.setAttribute('inputmode', 'numeric');

        // Creamos el mensaje de error visual
        const errorMsg = document.createElement('span');
        errorMsg.innerText = "Límite: 40 millones";
        errorMsg.className = "text-red-500 text-xs font-bold hidden absolute -bottom-5 left-0";
        
        if(input.parentElement) {
            input.parentElement.classList.add('relative');
            input.parentElement.appendChild(errorMsg);
        }

        let timeoutAlerta; 

        // 2. Formatear cuando el usuario escribe
        input.addEventListener('input', function(e) {
            // A) Quitamos TODO lo que no sea número (letras, símbolos y puntos viejos)
            let valorPuro = this.value.replace(/\D/g, ''); 
            
            if (valorPuro !== '') {
                let valorNumerico = parseInt(valorPuro, 10);
                
                // B) Validar Límite 40M
                if (valorNumerico > 40000000) {
                    // Cortamos el último dígito tecleado
                    valorPuro = valorPuro.slice(0, -1);
                    
                    if(parseInt(valorPuro, 10) > 40000000) {
                        valorPuro = '40000000';
                    }
                    
                    // Activamos alerta visual
                    this.classList.add('border-red-500', 'text-red-600', 'ring-red-500');
                    errorMsg.classList.remove('hidden');
                    
                    clearTimeout(timeoutAlerta);
                    timeoutAlerta = setTimeout(() => {
                        this.classList.remove('border-red-500', 'text-red-600', 'ring-red-500');
                        errorMsg.classList.add('hidden');
                    }, 2500);
                } else {
                    this.classList.remove('border-red-500', 'text-red-600', 'ring-red-500');
                    errorMsg.classList.add('hidden');
                }

                // C) Aplicar Puntos Separadores de Miles
                this.value = valorPuro.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
            } else {
                this.value = '';
                this.classList.remove('border-red-500', 'text-red-600', 'ring-red-500');
                errorMsg.classList.add('hidden');
            }
        });
    });

    // 3. LIMPIEZA ANTES DE ENVIAR (CRUCIAL)
    const formularios = document.querySelectorAll('form');
    formularios.forEach(form => {
        form.addEventListener('submit', function() {
            // Antes de enviarse, limpiamos los puntos para que Django lo guarde bien
            const cedulasEnForm = this.querySelectorAll('input[name*="cedula"], input[id*="cedula"]');
            cedulasEnForm.forEach(input => {
                input.value = input.value.replace(/\./g, '');
            });
        });
    });
});