/* Feedback de envío para los formularios PLANOS de la página de reservas.
 *
 * ==Por qué existe.== Un envío que tarda (el backend respondiendo, un proveedor lento) no decía
 * nada: el huésped no sabía si su clic había entrado, y el reflejo natural — volver a pulsar —
 * mandaba el formulario dos veces. Este script marca el formulario con `aria-busy="true"` y
 * deshabilita sus botones en cuanto el envío arranca; el CSS dibuja el spinner, y el lector de
 * pantalla anuncia el estado por el atributo.
 *
 * Reglas, y son deliberadas:
 * - SOLO formularios planos. Un formulario de HTMX (`hx-post`) se salta: htmx ya gobierna su propio
 *   estado con `hx-disabled-elt`, y deshabilitarle los botones por fuera pelearía con él.
 * - Se corre en la fase de CAPTURA del `submit`, que dispara DESPUÉS de la validación nativa del
 *   navegador: un formulario inválido nunca deja el botón bloqueado.
 * - `pageshow` con `persisted` rearma el formulario cuando el navegador lo restaura desde el
 *   bfcache al volver atrás; sin eso, el huésped encontraría su botón muerto.
 *
 * Sin JavaScript el formulario sigue funcionando igual: esto es mejora, no requisito.
 */
(function () {
  function begin(form) {
    if (form.hasAttribute("hx-post")) {
      return;
    }
    form.setAttribute("aria-busy", "true");
    var buttons = form.querySelectorAll("button[type=submit], button:not([type])");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].disabled = true;
    }
  }

  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (form && form.tagName === "FORM") {
        begin(form);
      }
    },
    true
  );

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) {
      return;
    }
    var busy = document.querySelectorAll('form[aria-busy="true"]');
    for (var i = 0; i < busy.length; i += 1) {
      busy[i].removeAttribute("aria-busy");
      var buttons = busy[i].querySelectorAll("button[disabled]");
      for (var j = 0; j < buttons.length; j += 1) {
        buttons[j].disabled = false;
      }
    }
  });
})();
