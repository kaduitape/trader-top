// Polimento de UX do dashboard (Fase 18): barra de progresso de navegacao +
// estado de "carregando" nos botoes de formulario. Puramente cosmetico —
// nenhuma logica de negocio, nenhuma chamada assincrona propria (o
// navegador continua fazendo a navegacao/POST normal do HTML).
(function () {
    "use strict";

    function createProgressBar() {
        var bar = document.createElement("div");
        bar.id = "tp-progress";
        document.body.appendChild(bar);
        return bar;
    }

    var progress = document.getElementById("tp-progress") || createProgressBar();
    var trickleTimer = null;

    function startProgress() {
        window.clearInterval(trickleTimer);
        progress.style.width = "0%";
        progress.classList.add("tp-active");
        var current = 0;
        trickleTimer = window.setInterval(function () {
            current += (100 - current) * 0.12;
            progress.style.width = Math.min(current, 92) + "%";
        }, 120);
    }

    function isNavigable(anchor) {
        if (!anchor || !anchor.getAttribute) return false;
        var href = anchor.getAttribute("href");
        if (!href || href.charAt(0) === "#") return false;
        if (anchor.target && anchor.target !== "" && anchor.target !== "_self") return false;
        if (anchor.hasAttribute("download")) return false;
        return true;
    }

    document.addEventListener("click", function (event) {
        var anchor = event.target.closest ? event.target.closest("a") : null;
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (isNavigable(anchor)) {
            startProgress();
        }
    });

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        startProgress();

        var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
        if (submitButton && !submitButton.disabled) {
            submitButton.classList.add("tp-loading");
            submitButton.setAttribute("aria-busy", "true");
            // Nao desabilita via `disabled` puro: navegadores nao enviam o
            // valor de um botao desabilitado no submit, o que poderia
            // quebrar formularios que dependem do nome/valor do botao.
            window.setTimeout(function () {
                submitButton.style.pointerEvents = "none";
            }, 0);
        }
    });

    window.addEventListener("pageshow", function () {
        // Bloqueia a barra em 100% e some (cobre tambem o caso de voltar
        // pelo cache do navegador, onde nenhum evento de load novo dispara).
        window.clearInterval(trickleTimer);
        progress.style.width = "100%";
        window.setTimeout(function () {
            progress.classList.remove("tp-active");
            progress.style.width = "0%";
        }, 200);
    });

    // Filtro local e instantaneo das tabelas operacionais. Nao altera os
    // dados nem dispara requisicoes; apenas reduz visualmente as linhas.
    document.querySelectorAll("[data-table-filter]").forEach(function (input) {
        var selector = input.getAttribute("data-table-filter");
        var table = selector ? document.querySelector(selector) : null;
        if (!table) return;

        input.addEventListener("input", function () {
            var term = input.value.toLocaleLowerCase("pt-BR").normalize("NFD")
                .replace(/[\u0300-\u036f]/g, "");
            table.querySelectorAll("[data-filter-row]").forEach(function (row) {
                var content = row.textContent.toLocaleLowerCase("pt-BR").normalize("NFD")
                    .replace(/[\u0300-\u036f]/g, "");
                row.classList.toggle("tp-filter-hidden", content.indexOf(term) === -1);
            });
        });
    });
})();
