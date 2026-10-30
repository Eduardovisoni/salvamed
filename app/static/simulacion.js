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
  var escalasLado = {};

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
    pintarComparacion();
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
      if (t.R !== null) { marcaR.style.left = Math.min(t.R_pedido / escala * 100, 100) + "%"; marcaR.title = "Pedir al llegar a " + t.R_pedido + " (R " + num(t.R, 1) + ")"; }
      if (t.IS !== null) { marcaIS.style.left = Math.min(t.IS / escala * 100, 100) + "%"; marcaIS.title = "IS " + num(t.IS, 1); }
      var pie = tarjeta.querySelector(".estante__pie");
      pie.textContent = "";
      if (t.R === null) pie.textContent = t.dias < 2 ? "Sin plan: " + t.dias + " de 2 días completos" : (t.pendientes[0] || "Plan pendiente");
      else {
        [
          ["Consumo promedio", "μD", num(t.mu, 1), "", "Consumo promedio por día"],
          ["Variación diaria", "σD", "±" + num(t.sigma, 1), "", "Cuánto varía el consumo de un día a otro"],
          ["Pedir al llegar a", "R", String(t.R_pedido), "r", "Punto de reorden R = " + num(t.R, 1) + ", redondeado hacia arriba: al bajar a este nivel se pide"],
          ["Reserva de seguridad", "IS", num(t.IS, 0), "is", "Inventario de seguridad para los días de más consumo mientras llega el pedido"]
        ].forEach(function (d) {
          var fila = el("span", "estante__dato" + (d[3] ? " estante__dato--" + d[3] : ""));
          fila.title = d[4];
          var etiqueta = el("span", "estante__etiqueta", d[0]);
          etiqueta.appendChild(el("small", "estante__simbolo", "\u00a0" + d[1]));
          fila.appendChild(etiqueta);
          fila.appendChild(el("span", "estante__valor", d[2]));
          pie.appendChild(fila);
        });
        if (t.calidad === "provisional") pie.appendChild(el("span", "estante__dias", "Calculado con " + t.dias + " días"));
      }
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

  // ------------------------------------------------------------ El Roble hoy contra SALVAMED

  function pintarComparacion() {
    var k = estado.comparacion;
    $("comparar").hidden = !k;
    $("comparar-vieja").hidden = !!k;
    if (!k) return;
    var ahorro = k.ahorro;
    $("ahorro-etiqueta").textContent = ahorro >= 0 ? "Ahorro con SALVAMED" : "Con SALVAMED cuesta más:";
    $("ahorro").textContent = dinero(Math.abs(ahorro));
    $("ahorro").className = "comparar__monto" + (ahorro < 0 ? " comparar__monto--menos" : "");
    $("ahorro-pct").textContent = ahorro > 0 && k.ahorro_pct !== null ? num(k.ahorro_pct, 0) + " % menos que El Roble hoy" : "";
    var recargo = num(k.tarifas.recargo * 100, 0);
    var cuadra = k.demanda.roble === k.demanda.salvamed;
    var supuestos = $("supuestos");
    supuestos.innerHTML = "";
    [
      [(cuadra ? "✓ " : "✗ ") + "Mismas solicitudes: " + num(k.demanda.salvamed, 0) + " unidades pedidas por los servicios en los dos lados", cuadra ? "cuadra" : "texto-pedir"],
      ["Lo que no hay se compra de emergencia a +" + recargo + " %"],
      ["Cada pedido tarda entre L−1 y L+1 días"],
      [num(estado.reloj.minuto / estado.reloj.minutos_dia, 1) + " días simulados"],
    ].forEach(function (s) { supuestos.appendChild(el("li", s[1] || "", s[0])); });
    pintarLado("roble", k.roble, k, recargo);
    pintarLado("salvamed", k.salvamed, k, recargo);
    pintarSucesos($("eventos-roble"), k.roble.eventos);
    pintarSucesos($("eventos-salvamed"), k.salvamed.eventos);
    pintarTabla(k);
  }

  function pintarLado(clave, l, k, recargo) {
    var cuentas = $("cuentas-" + clave);
    cuentas.innerHTML = "";
    [
      ["Costo de inventario", dinero(l.total), "pedir + mantener + emergencias", "lado__total"],
      ["Pedidos", dinero(l.pedir), l.envios + (l.envios === 1 ? " envío × " : " envíos × ") + dinero(k.tarifas.S)],
      ["Mantener", dinero(l.mantener), "lo guardado, " + num(k.tarifas.tasa * 100, 0) + " % al año"],
      ["Emergencias", dinero(l.emergencia), l.emergencia_u + " unidades a +" + recargo + " %", l.emergencia_u ? "lado__alerta" : ""],
    ].forEach(function (c) {
      var div = el("div", c[3] || "");
      div.appendChild(el("dt", "", c[0]));
      var dd = el("dd", "", c[1]);
      dd.appendChild(el("small", "", c[2]));
      div.appendChild(dd);
      cuentas.appendChild(div);
    });
    var lista = $("meds-" + clave);
    var otro = k[clave === "roble" ? "salvamed" : "roble"].filas;
    l.filas.forEach(function (f, i) {
      var fila = lista.children[i];
      if (!fila) {
        fila = el("li", "lado__med");
        var nombre = el("span", "lado__nombre", f.nombre);
        nombre.appendChild(el("b", "lado__emerg"));
        fila.appendChild(nombre);
        var barra = el("span", "lado__barra");
        barra.appendChild(el("span", "lado__relleno"));
        barra.appendChild(el("span", "lado__camino"));
        fila.appendChild(barra);
        fila.appendChild(el("span", "lado__numero"));
        fila.appendChild(el("span", "lado__nota"));
        lista.appendChild(fila);
      }
      var escala = Math.max(escalasLado[f.id] || 0, f.existencia + f.en_camino, otro[i].existencia + otro[i].en_camino, 1);
      escalasLado[f.id] = escala;
      fila.className = "lado__med" + (f.existencia === 0 ? " lado__med--agotado" : "");
      fila.querySelector(".lado__relleno").style.width = Math.min(f.existencia / escala * 100, 100) + "%";
      var camino = fila.querySelector(".lado__camino");
      camino.style.left = Math.min(f.existencia / escala * 100, 100) + "%";
      camino.style.width = Math.min(f.en_camino / escala * 100, 100) + "%";
      fila.querySelector(".lado__numero").textContent = f.existencia;
      var emerg = fila.querySelector(".lado__emerg");
      emerg.textContent = f.emergencia_u ? "🛒 " + f.emergencia_u : "";
      emerg.title = f.emergencia_u ? f.emergencia_u + " " + f.unidad + " comprados de emergencia" : "";
      var nota = fila.querySelector(".lado__nota");
      nota.textContent = "";
      if (f.existencia === 0) nota.appendChild(el("b", "", f.en_camino ? "Agotado · " : "Agotado"));
      if (f.en_camino) nota.appendChild(document.createTextNode("🚚 +" + f.en_camino + " · " + f.llega));
      fila.title = f.nombre + ": " + f.existencia + " " + f.unidad + (f.en_camino ? ", +" + f.en_camino + " en camino" : "") +
        ". " + f.pedidos + " pedidos, " + f.emergencia_u + " unidades de emergencia.";
    });
  }

  function pintarSucesos(lista, sucesos) {
    var clave = sucesos.map(function (s) { return s.hora + s.texto; }).join("|");
    if (lista.dataset.clave === clave) return;
    lista.dataset.clave = clave;
    lista.innerHTML = "";
    if (!sucesos.length) { lista.appendChild(el("li", "sutil", "Todavía no pasa nada importante.")); return; }
    sucesos.forEach(function (s) {
      var li = el("li", "lado__evento--" + s.tipo);
      li.title = s.texto;
      li.appendChild(el("span", "evento__icono", iconos[s.tipo] || "•"));
      li.appendChild(el("span", "evento__hora", s.hora));
      li.appendChild(el("span", "lado__texto", s.texto));
      lista.appendChild(li);
    });
  }

  function pintarTabla(k) {
    var tabla = $("tabla-comparar");
    if (!tabla.closest("details").open) return;
    var filas = [
      '<thead><tr><th rowspan="2">Medicamento</th><th colspan="4" class="grupo--roble">El Roble hoy</th>' +
        '<th colspan="4" class="grupo--salvamed">Con SALVAMED</th><th rowspan="2" class="num">Ahorro</th></tr><tr>' +
        ["Pedidos", "Mantener", "Emergencias", "Total", "Pedidos", "Mantener", "Emergencias", "Total"].map(function (x) { return '<th class="num">' + x + "</th>"; }).join("") +
        "</tr></thead><tbody>",
    ];
    function celdas(f, pedidos) {
      return '<td class="num">' + pedidos + " · " + dinero(f.pedir) + '</td><td class="num">' + dinero(f.mantener) + '</td><td class="num">' +
        dinero(f.emergencia) + (f.emergencia_u ? " (" + f.emergencia_u + " u)" : "") + '</td><td class="num total">' + dinero(f.total) + "</td>";
    }
    function ahorro(v) {
      if (Math.abs(v) < 0.005) return '<td class="num">' + dinero(0) + "</td>";
      return '<td class="num ' + (v > 0 ? "positivo" : "negativo") + '">' + (v < 0 ? "−" : "") + dinero(Math.abs(v)) + "</td>";
    }
    k.roble.filas.forEach(function (r, i) {
      var s = k.salvamed.filas[i];
      filas.push("<tr><td>" + esc(r.nombre) + "</td>" + celdas(r, r.pedidos) + celdas(s, s.pedidos) + ahorro(r.total - s.total) + "</tr>");
    });
    filas.push("</tbody><tfoot><tr><td>Total</td>" + celdas(k.roble, k.roble.envios) + celdas(k.salvamed, k.salvamed.envios) + ahorro(k.ahorro) + "</tr></tfoot>");
    tabla.innerHTML = filas.join("");
    $("formulas").textContent =
      "Pedidos = envíos × " + dinero(k.tarifas.S) + " (SALVAMED junta en un envío los medicamentos del mismo proveedor que piden a la vez; ese envío se reparte entre ellos). " +
      "Mantener = costo × " + num(k.tarifas.tasa * 100, 0) + " % ÷ " + num(k.tarifas.A, 0) + " días × unidades guardadas cada día. " +
      "Emergencias = unidades que no había × costo × " + num(k.tarifas.recargo * 100, 0) + " %. " +
      "No incluye el precio normal de los medicamentos, igual que CT en la clase 12. " +
      "A mitad de la corrida un medicamento puede salir en rojo: SALVAMED ya pagó el envío de un pedido que El Roble todavía no hace porque aún no se le acaba.";
  }

  function esc(texto) { var d = document.createElement("div"); d.textContent = texto; return d.innerHTML; }

  var iconos = { despacho: "💊", aviso: "⚠️", pausa: "⏸", pedido: "✅", reanudar: "▶", recepcion: "🚚", cierre: "🌙", faltante: "❌", inicio: "🏁", aumento: "📈", agotado: "🔴", emergencia: "🛒" };

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
      ["¿Pedir?", p.posicion + " ≤ " + p.R_pedido + " (R " + num(p.R, 2) + " hacia arriba) → sí"],
      ["EOQ = √(2DS / H)", num(p.Q, 2) + " → hacia arriba: pedir " + p.sugerida + (p.paquete > 1 ? " (paquetes de " + p.paquete + ")" : "")],
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
    $("grafica-titulo").textContent = "Existencias de " + (t ? t.nombre + " " + t.presentacion : "") + ": SALVAMED y El Roble hoy";
    var svg = $("grafica");
    var ancho = 900, alto = 210, margen = 34;
    var md = s.minutos_dia;
    var fin = Math.max(s.ahora, md * 5) + md * 0.3;
    var tope = 1;
    s.puntos.forEach(function (p) { tope = Math.max(tope, p[1]); });
    s.planes.forEach(function (p) { tope = Math.max(tope, p[2] || 0); });
    (s.roble || []).forEach(function (p) { tope = Math.max(tope, p[1]); });
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
    var rutaRoble = "";
    (s.roble || []).forEach(function (p, i) {
      rutaRoble += i === 0 ? "M" + x(p[0]) + " " + y(p[1]) : " H" + x(p[0]) + " V" + y(p[1]);
    });
    if (rutaRoble) rutaRoble += " H" + x(s.ahora);
    partes.push('<path class="grafica__roble" d="' + rutaRoble + '"></path>');
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
  document.querySelector(".comparar__detalle").addEventListener("toggle", function () {
    if (estado && estado.comparacion) pintarTabla(estado.comparacion);
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
