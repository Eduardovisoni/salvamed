/* Pantalla de simulación: consulta el estado al servidor y lo anima.
   El servidor es quien avanza el reloj y registra todo; esta página solo muestra. */
(function () {
  "use strict";
  var raiz = document.getElementById("sim");
  if (!raiz) return;

  var ultimoEvento = 0;
  var seleccionado = null;
  var estado = null;
  var ocupado = false;
  var escalas = {};
  var reloj = { minuto: 0, recibido: 0, velocidad: 1, corriendo: false, md: 720, mps: 10 };
  var colores = ["#0f766e", "#7c3aed", "#b45309", "#2563eb", "#be185d", "#15803d", "#c2410c", "#0e7490"];
  var servicios = [];

  function $(id) { return document.getElementById(id); }
  function dinero(v) { return "Q " + Number(v || 0).toLocaleString("es-GT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function num(v, d) { return v === null || v === undefined ? "—" : Number(v).toLocaleString("es-GT", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function el(etiqueta, clase, texto) {
    var nodo = document.createElement(etiqueta);
    if (clase) nodo.className = clase;
    if (texto !== undefined) nodo.textContent = texto;
    return nodo;
  }
  function porId(id) {
    if (!estado) return null;
    for (var i = 0; i < estado.tarjetas.length; i++) if (estado.tarjetas[i].id === id) return estado.tarjetas[i];
    return null;
  }

  // ------------------------------------------------------------ comunicación

  function consultar() {
    if (ocupado) return;
    ocupado = true;
    var url = "/simulacion/estado?desde=" + ultimoEvento + (seleccionado ? "&med=" + seleccionado : "");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { if (r.status === 409) { location.reload(); throw new Error("sin corrida"); } return r.json(); })
      .then(recibir)
      .catch(function () {})
      .then(function () { ocupado = false; });
  }

  function controlar(accion, valor) {
    var datos = new FormData();
    datos.append("accion", accion);
    datos.append("valor", valor || "");
    datos.append("desde", ultimoEvento);
    if (seleccionado) datos.append("med", seleccionado);
    ocupado = true;
    fetch("/simulacion/control", { method: "POST", body: datos, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) { recibir(d, accion === "dia" || accion === "evento"); })
      .catch(function () {})
      .then(function () { ocupado = false; });
  }

  // ------------------------------------------------------------ pintar

  function recibir(datos, salto) {
    if (estado && datos.corrida.id !== estado.corrida.id) { location.reload(); return; }
    var previo = estado;
    estado = datos;
    seleccionado = datos.seleccionado;
    reloj = {
      minuto: datos.reloj.minuto, recibido: performance.now(), velocidad: datos.reloj.velocidad,
      corriendo: datos.reloj.corriendo, md: datos.reloj.minutos_dia, mps: datos.reloj.minutos_por_segundo,
    };
    $("seg-dia").textContent = Math.round(reloj.md / reloj.mps);
    pintarControles();
    pintarServicios();
    pintarEstantes(previo);
    pintarDinero();
    var nuevos = datos.eventos.filter(function (e) { return e.id > ultimoEvento; });
    if (nuevos.length) {
      ultimoEvento = nuevos[nuevos.length - 1].id;
      pintarEventos(nuevos);
      animar(nuevos, salto || nuevos.length > 40);
    }
    pintarPausa();
    pintarGrafica();
    pintarReloj();
  }

  function pintarReloj() {
    if (!estado) return;
    var r = estado.reloj;
    $("reloj-dia").textContent = "Día " + r.dia + " · " + r.dia_semana;
    $("reloj-fecha").textContent = r.fecha;
    var extra = 0;
    if (reloj.corriendo) {
      extra = (performance.now() - reloj.recibido) / 1000 * reloj.mps * reloj.velocidad;
      extra = Math.min(extra, reloj.md - (reloj.minuto % reloj.md) - 0.01);
    }
    var base = parseInt(r.hora.slice(0, 2), 10) * 60 + parseInt(r.hora.slice(3, 5), 10);
    var total = base + Math.max(extra, 0);
    var h = Math.floor(total / 60), m = Math.floor(total % 60);
    $("reloj-hora").textContent = (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function pintarControles() {
    var r = estado.reloj;
    var play = $("play");
    play.textContent = r.pausa_manual ? (r.minuto === 0 ? "▶ Iniciar" : "▶ Reanudar") : "⏸ Pausar";
    play.disabled = false;
    document.querySelectorAll("[data-velocidad]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(parseInt(b.dataset.velocidad, 10) === r.velocidad));
    });
    document.querySelectorAll("[data-accion]").forEach(function (b) { b.disabled = r.esperando > 0; });
    var texto, clase;
    if (r.esperando) { texto = "⏸ Esperando confirmación del encargado" + (r.esperando > 1 ? " (" + r.esperando + " avisos)" : ""); clase = "sim-estado--espera"; }
    else if (r.corriendo) { texto = "▶ Corriendo a ×" + r.velocidad; clase = "sim-estado--corre"; }
    else { texto = "⏸ En pausa"; clase = ""; }
    var caja = $("estado-reloj");
    caja.textContent = texto;
    caja.className = "sim-estado " + clase;
    raiz.classList.toggle("sim--esperando", r.esperando > 0);
  }

  function pintarEstantes(previo) {
    var cont = $("estantes");
    estado.tarjetas.forEach(function (t, i) {
      var tarjeta = cont.querySelector('[data-id="' + t.id + '"]');
      if (!tarjeta) {
        tarjeta = el("button", "estante");
        tarjeta.type = "button";
        tarjeta.dataset.id = t.id;
        tarjeta.style.setProperty("--color", colores[i % colores.length]);
        tarjeta.innerHTML =
          '<span class="estante__nombre"></span><span class="estante__presentacion"></span>' +
          '<span class="estante__numero"></span><span class="estante__unidad"></span>' +
          '<span class="estante__barra"><span class="estante__relleno"></span><span class="estante__camino"></span>' +
          '<span class="estante__marca estante__marca--is"></span><span class="estante__marca estante__marca--r"></span></span>' +
          '<span class="estante__pie"></span><span class="estante__llega"></span>';
        tarjeta.querySelector(".estante__nombre").textContent = t.nombre;
        tarjeta.querySelector(".estante__presentacion").textContent = t.presentacion;
        tarjeta.addEventListener("click", function () { seleccionado = t.id; pintarGrafica(); marcarSeleccion(); consultar(); });
        cont.appendChild(tarjeta);
      }
      var escala = Math.max(escalas[t.id] || 0, t.existencia + (t.en_camino || 0), (t.R || 0) + (t.sugerida || 0), 1);
      escalas[t.id] = escala;
      tarjeta.className = "estante estante--" + t.estado + (t.id === seleccionado ? " estante--elegido" : "");
      tarjeta.querySelector(".estante__numero").textContent = t.existencia;
      tarjeta.querySelector(".estante__unidad").textContent = t.unidad;
      tarjeta.querySelector(".estante__relleno").style.width = Math.min(t.existencia / escala * 100, 100) + "%";
      tarjeta.querySelector(".estante__camino").style.left = Math.min(t.existencia / escala * 100, 100) + "%";
      tarjeta.querySelector(".estante__camino").style.width = Math.min((t.en_camino || 0) / escala * 100, 100) + "%";
      var marcaR = tarjeta.querySelector(".estante__marca--r"), marcaIS = tarjeta.querySelector(".estante__marca--is");
      marcaR.hidden = t.R === null; marcaIS.hidden = t.IS === null;
      if (t.R !== null) { marcaR.style.left = Math.min(t.R / escala * 100, 100) + "%"; marcaR.title = "R " + num(t.R, 1); }
      if (t.IS !== null) { marcaIS.style.left = Math.min(t.IS / escala * 100, 100) + "%"; marcaIS.title = "IS " + num(t.IS, 1); }
      var pie = tarjeta.querySelector(".estante__pie");
      if (t.R === null) pie.textContent = t.dias < 2 ? "Sin plan: " + t.dias + " de 2 días completos" : (t.pendientes[0] || "Plan pendiente");
      else pie.textContent = "μD " + num(t.mu, 1) + " · σD " + num(t.sigma, 1) + " · R " + num(t.R, 0) + " · IS " + num(t.IS, 0) + (t.calidad === "provisional" ? " · " + t.dias + " d" : "");
      tarjeta.querySelector(".estante__llega").textContent = t.camino ? "🚚 +" + t.camino.cantidad + " llega " + t.camino.llega : "";
      if (previo) {
        var antes = null;
        previo.tarjetas.forEach(function (x) { if (x.id === t.id) antes = x; });
        if (antes && antes.existencia !== t.existencia) {
          tarjeta.classList.remove("estante--pulso"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--pulso");
          var delta = t.existencia - antes.existencia;
          var globo = el("span", "estante__delta" + (delta > 0 ? " estante__delta--mas" : ""), (delta > 0 ? "+" : "−") + Math.abs(delta));
          tarjeta.appendChild(globo);
          setTimeout(function () { globo.remove(); }, 1400);
        }
      }
    });
  }

  function marcarSeleccion() {
    document.querySelectorAll(".estante").forEach(function (t) {
      t.classList.toggle("estante--elegido", parseInt(t.dataset.id, 10) === seleccionado);
    });
  }

  function pintarServicios() {
    var lista = $("servicios");
    if (servicios.length) return;
    servicios = estado.servicios.map(function (s) { return s.nombre; });
    estado.servicios.forEach(function (s) {
      var li = el("li", "sim-servicio");
      li.dataset.servicio = s.nombre;
      li.appendChild(el("span", "sim-servicio__icono", s.icono));
      li.appendChild(el("span", "sim-servicio__nombre", s.nombre));
      li.appendChild(el("span", "sim-servicio__cuenta", "0"));
      lista.appendChild(li);
    });
  }

  function pintarDinero() {
    var d = estado.dinero;
    var filas = [["Costo de lo consumido (" + d.numero_despachos + " despachos)", dinero(d.consumido), "cuentas__total"]];
    d.por_servicio.forEach(function (x) { filas.push(["· " + x[0], dinero(x[1])]); });
    filas.push(["Compras recibidas", dinero(d.compras)]);
    filas.push(["Comprometido en pedidos", dinero(d.compromisos)]);
    filas.push(["Unidades que no se pudieron despachar", String(d.faltantes), d.faltantes ? "cuentas__alerta" : ""]);
    var cuentas = {};
    d.por_servicio.forEach(function (x) { cuentas[x[0]] = x[2]; });
    document.querySelectorAll(".sim-servicio").forEach(function (li) {
      li.querySelector(".sim-servicio__cuenta").textContent = cuentas[li.dataset.servicio] || 0;
    });
    var dl = $("dinero");
    dl.innerHTML = "";
    filas.forEach(function (f) {
      var div = el("div", f[2] || "");
      div.appendChild(el("dt", "", f[0]));
      div.appendChild(el("dd", "", f[1]));
      dl.appendChild(div);
    });
    $("caja-total").textContent = d.unidades + " unidades";
  }

  var iconos = { despacho: "💊", aviso: "⚠️", pausa: "⏸", pedido: "✅", reanudar: "▶", recepcion: "🚚", cierre: "🌙", faltante: "❌", inicio: "🏁", aumento: "📈" };

  function textoEvento(e) {
    if (e.tipo === "despacho") {
      var partes = (e.datos.items || []).map(function (x) { var t = porId(x[0]); return x[1] + "× " + (t ? t.nombre : "?"); });
      return e.datos.servicio + ": " + partes.join(", ") + " · " + dinero(e.datos.costo);
    }
    return e.texto;
  }

  function pintarEventos(nuevos) {
    var lista = $("eventos");
    nuevos.forEach(function (e) {
      if (e.tipo === "despacho" && nuevos.length > 60) return;
      var li = el("li", "evento evento--" + e.tipo);
      li.appendChild(el("span", "evento__icono", iconos[e.tipo] || "•"));
      var cuerpo = el("span", "evento__texto");
      cuerpo.appendChild(el("span", "evento__hora", e.hora));
      cuerpo.appendChild(document.createTextNode(" " + textoEvento(e)));
      li.appendChild(cuerpo);
      lista.insertBefore(li, lista.firstChild);
    });
    while (lista.children.length > 60) lista.removeChild(lista.lastChild);
  }

  function animar(nuevos, salto) {
    var despachos = nuevos.filter(function (e) { return e.tipo === "despacho"; });
    if (salto) despachos = despachos.slice(-4);
    var intervalo = Math.max(Math.min(450 / Math.max(despachos.length, 1), 220), 60);
    despachos.forEach(function (e, i) { setTimeout(function () { solicitud(e); }, i * intervalo); });
  }

  function solicitud(e) {
    var pasillo = $("pasillo");
    if (!pasillo || document.hidden) return;
    var indice = Math.max(servicios.indexOf(e.datos.servicio), 0);
    var origen = document.querySelector('.sim-servicio[data-servicio="' + e.datos.servicio + '"]');
    if (origen) { origen.classList.remove("sim-servicio--pide"); void origen.offsetWidth; origen.classList.add("sim-servicio--pide"); }
    var persona = el("span", "cliente", "🧑‍⚕️");
    persona.title = e.datos.servicio;
    persona.style.top = "calc(" + ((indice + 0.5) / Math.max(servicios.length, 1) * 100) + "% - 16px)";
    pasillo.appendChild(persona);
    requestAnimationFrame(function () { requestAnimationFrame(function () { persona.classList.add("cliente--caminando"); }); });
    setTimeout(function () {
      var ticket = el("span", "ticket", textoEvento(e));
      ticket.style.top = persona.style.top;
      pasillo.appendChild(ticket);
      (e.datos.items || []).forEach(function (x) {
        var tarjeta = document.querySelector('.estante[data-id="' + x[0] + '"]');
        if (tarjeta) { tarjeta.classList.remove("estante--venta"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--venta"); }
      });
      setTimeout(function () { ticket.remove(); }, 1500);
      persona.classList.add("cliente--sale");
      setTimeout(function () { persona.remove(); }, 600);
    }, 900);
  }

  function pintarPausa() {
    var p = estado.pausa;
    var caja = $("pausa");
    if (!p) { caja.hidden = true; return; }
    caja.hidden = false;
    var r = estado.reloj;
    $("pausa-titulo").textContent = "⚠️ " + p.nombre + " llegó al punto de reorden · " + r.fecha + " " + r.hora + (p.en_cola > 1 ? " · " + p.en_cola + " avisos en cola" : "");
    $("pausa-supuesto").textContent = estado.corrida.demora_horas ? "una demora de gestión de " + estado.corrida.demora_horas + " h virtuales" : "respuesta inmediata en tiempo virtual";
    var lineas = [
      ["Posición de inventario", p.posicion + " (existencia " + p.existencia + ")"],
      ["μD (" + p.dias + " días completos)", num(p.mu, 2) + " por día"],
      ["σD", num(p.sigma, 2)],
      ["σL = σD · √L", num(p.sigma, 2) + " × √" + num(p.L, 0) + " = " + num(p.sigma_L, 2)],
      ["Z para p = " + num(p.p, 1) + " %", num(p.Z, 3)],
      ["IS = Z · σL", num(p.Z, 3) + " × " + num(p.sigma_L, 2) + " = " + num(p.IS, 2)],
      ["R = μD · L + IS", num(p.mu, 2) + " × " + num(p.L, 0) + " + " + num(p.IS, 2) + " = " + num(p.R, 2)],
      ["¿Pedir?", p.posicion + " ≤ " + num(p.R, 2) + " → sí"],
      ["EOQ = √(2DS / H)", num(p.Q, 2) + " → pedir " + p.sugerida + (p.paquete > 1 ? " (paquetes de " + p.paquete + ")" : "")],
      ["Llegará en L", num(p.L, 0) + " días de operación"],
    ];
    var calculo = $("calculo");
    calculo.innerHTML = "<h3>El cálculo</h3>";
    var tabla = el("table", "datos");
    lineas.forEach(function (l) {
      var tr = el("tr");
      tr.appendChild(el("th", "", l[0]));
      tr.appendChild(el("td", "formula", l[1]));
      tabla.appendChild(tr);
    });
    calculo.appendChild(tabla);
    var estados = { pendiente: "Enviando…", enviado: "✓ Enviado", fallido: "✗ Falló", simulado: "Transporte simulado (sin Telegram real)", sin_destino: "Sin chat configurado" };
    var cont = $("telegram-estado");
    cont.innerHTML = "";
    p.mensajes.forEach(function (m) {
      var linea = el("p", "telegram-linea telegram-linea--" + m.estado, (estados[m.estado] || m.estado) + (m.chat ? " · chat " + m.chat : "") + (m.intentos ? " · intentos " + m.intentos : ""));
      if (m.error) linea.appendChild(el("span", "campo__error bloque", m.error));
      cont.appendChild(linea);
    });
    $("telegram-texto").textContent = p.mensajes.length ? p.mensajes[0].texto : "";
    var boton = $("confirmar-web");
    boton.dataset.pedido = p.pedido;
  }

  // ------------------------------------------------------------ gráfica

  function pintarGrafica() {
    if (!estado) return;
    var s = estado.serie;
    var t = porId(s.medicamento_id);
    $("grafica-titulo").textContent = "Existencias de " + (t ? t.nombre + " " + t.presentacion : "") + " en el tiempo";
    var svg = $("grafica");
    var ancho = 900, alto = 260, margen = 34;
    var md = s.minutos_dia;
    var fin = Math.max(s.ahora, md * 5) + md * 0.3;
    var tope = 1;
    s.puntos.forEach(function (p) { tope = Math.max(tope, p[1]); });
    s.planes.forEach(function (p) { tope = Math.max(tope, p[2] || 0); });
    tope *= 1.12;
    function x(m) { return margen + Math.min(m / fin, 1) * (ancho - margen - 6); }
    function y(v) { return alto - 22 - v / tope * (alto - 34); }
    var partes = [];
    for (var d = 0; d * md <= fin; d++) {
      partes.push('<line class="sim-grafica__dia" x1="' + x(d * md) + '" x2="' + x(d * md) + '" y1="8" y2="' + (alto - 22) + '"></line>');
      if (fin / md < 20 || d % 2 === 0) partes.push('<text class="sim-grafica__texto" x="' + (x(d * md) + 3) + '" y="' + (alto - 8) + '">D' + (d + 1) + "</text>");
    }
    [0, 0.5, 1].forEach(function (f) {
      var v = tope / 1.12 * f;
      partes.push('<text class="sim-grafica__texto" x="2" y="' + (y(v) + 4) + '">' + Math.round(v) + "</text>");
    });
    var ruta = "", ultimo = null;
    s.puntos.forEach(function (p) {
      if (ultimo === null) ruta = "M" + x(p[0]) + " " + y(p[1]);
      else ruta += " H" + x(p[0]) + " V" + y(p[1]);
      ultimo = p;
    });
    if (ultimo) ruta += " H" + x(s.ahora);
    var rutaR = "", rutaIS = "";
    s.planes.forEach(function (p) {
      var hasta = Math.min(p[1], s.ahora);
      if (hasta <= p[0]) return;
      if (p[2] !== null) rutaR += "M" + x(p[0]) + " " + y(p[2]) + " H" + x(hasta) + " ";
      if (p[3] !== null) rutaIS += "M" + x(p[0]) + " " + y(p[3]) + " H" + x(hasta) + " ";
    });
    partes.push('<path class="grafica__is" d="' + rutaIS + '"></path>');
    partes.push('<path class="grafica__r" d="' + rutaR + '"></path>');
    partes.push('<path class="grafica__existencia" d="' + ruta + '"></path>');
    s.marcas.forEach(function (m) {
      var cx = x(m[0]);
      partes.push('<line class="grafica__marca grafica__marca--' + m[1] + '" x1="' + cx + '" x2="' + cx + '" y1="8" y2="' + (alto - 22) + '"></line>');
      var ty = m[1] === "recepcion" ? 20 : m[1] === "pedido" ? 34 : 48;
      partes.push('<text class="sim-grafica__marca sim-grafica__marca--' + m[1] + '" x="' + (cx + 3) + '" y="' + ty + '">' + m[2].replace(/</g, "") + "</text>");
    });
    partes.push('<line class="sim-grafica__ahora" x1="' + x(s.ahora) + '" x2="' + x(s.ahora) + '" y1="4" y2="' + (alto - 22) + '"></line>');
    svg.innerHTML = partes.join("");
  }

  // ------------------------------------------------------------ eventos de la página

  $("play").addEventListener("click", function () {
    if (!estado) return;
    this.disabled = true;
    controlar(estado.reloj.pausa_manual ? "reanudar" : "pausar");
  });
  document.querySelectorAll("[data-velocidad]").forEach(function (b) {
    b.addEventListener("click", function () { controlar("velocidad", b.dataset.velocidad); });
  });
  document.querySelectorAll("[data-accion]").forEach(function (b) {
    b.addEventListener("click", function () { controlar(b.dataset.accion); });
  });
  $("confirmar-web").addEventListener("click", function () {
    var boton = this;
    boton.disabled = true;
    fetch("/simulacion/confirmar/" + boton.dataset.pedido, { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function () { consultar(); })
      .catch(function () {})
      .then(function () { boton.disabled = false; });
  });

  consultar();
  setInterval(consultar, 500);
  setInterval(pintarReloj, 100);
})();
