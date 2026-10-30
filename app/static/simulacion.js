/* Pantalla de simulación: consulta el estado al servidor y lo anima.
   El servidor es quien avanza el reloj y registra todo; esta página solo muestra.
   Dos lados con las mismas solicitudes: «El Roble hoy» (pide a ojo, de boca en boca)
   y «El Roble con SALVAMED» (modelo de la clase 12 y avisos por Telegram). */
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
  var estimacion = {}; // id → {mu, sigma} de la última consulta, para mostrar cuando cambian
  var cambios = {}; // id → {texto, hasta}
  var retenido = { roble: {}, salvamed: {} }; // unidades que el camión todavía no deja en el estante
  var mostrado = { roble: {}, salvamed: {} }; // último número que se mostró en cada estante
  var colaCamion = { roble: Promise.resolve(), salvamed: Promise.resolve() };
  var repartos = 0, pausadoPorCamion = false;

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
  function idDeIndice(indice) {
    var t = estado && estado.roble.tarjetas[indice];
    return t ? t.id : null;
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
    estado = datos;
    seleccionado = datos.seleccionado;
    reloj = {
      minuto: datos.reloj.minuto, recibido: performance.now(), velocidad: datos.reloj.velocidad,
      corriendo: datos.reloj.corriendo, md: datos.reloj.minutos_dia, mps: datos.reloj.minutos_por_segundo,
    };
    $("seg-dia").textContent = Math.round(reloj.md / reloj.mps);
    var nuevos = datos.eventos.filter(function (e) { return e.id > ultimoEvento; });
    // Lo que trae un camión no se ve en el estante hasta que la caja cae.
    nuevos.forEach(function (e) { if (e.tipo === "recepcion") retener(e); });
    pintarControles();
    pintarServicios();
    pintarEstantes();
    pintarEstantesRoble();
    pintarMarcador();
    if (nuevos.length) {
      ultimoEvento = nuevos[nuevos.length - 1].id;
      pintarEventos(nuevos);
      animar(nuevos, salto || nuevos.length > 40);
    }
    pintarPausa();
    pintarGrafica();
    pintarReloj();
    if ($("resumen").open) pintarResumen();
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
    else if (repartos > 0) { texto = "🚚 Entregando pedidos…"; clase = "sim-estado--espera"; }
    else if (r.corriendo) { texto = "▶ Corriendo a ×" + r.velocidad; clase = "sim-estado--corre"; }
    else { texto = "⏸ En pausa"; clase = ""; }
    var caja = $("estado-reloj");
    caja.textContent = texto;
    caja.className = "sim-estado " + clase;
    raiz.classList.toggle("sim--esperando", r.esperando > 0);
  }

  function crearEstante(cont, t, i, lado) {
    var tarjeta = el("button", "estante");
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
    if (lado === "salvamed") tarjeta.addEventListener("click", function () { seleccionado = t.id; pintarGrafica(); marcarSeleccion(); consultar(); });
    else tarjeta.disabled = true;
    cont.appendChild(tarjeta);
    return tarjeta;
  }

  // El número visible descuenta lo que el camión todavía no deja; si cambia, sale un globo con la diferencia.
  function mostrar(tarjeta, lado, t, escala) {
    var valor = Math.max(t.existencia - (retenido[lado][t.id] || 0), 0);
    tarjeta.querySelector(".estante__numero").textContent = valor;
    tarjeta.querySelector(".estante__relleno").style.width = Math.min(valor / escala * 100, 100) + "%";
    tarjeta.querySelector(".estante__camino").style.left = Math.min(valor / escala * 100, 100) + "%";
    var antes = mostrado[lado][t.id];
    if (antes !== undefined && antes !== valor) {
      tarjeta.classList.remove("estante--pulso"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--pulso");
      var delta = valor - antes;
      var globo = el("span", "estante__delta" + (delta > 0 ? " estante__delta--mas" : ""), (delta > 0 ? "+" : "−") + Math.abs(delta));
      tarjeta.appendChild(globo);
      setTimeout(function () { globo.remove(); }, 1400);
    }
    mostrado[lado][t.id] = valor;
  }

  function dato(pie, etiqueta, simbolo, valor, clase, ayuda) {
    var fila = el("span", "estante__dato" + (clase ? " estante__dato--" + clase : ""));
    fila.title = ayuda;
    var e = el("span", "estante__etiqueta", etiqueta);
    if (simbolo) e.appendChild(el("small", "estante__simbolo", " " + simbolo));
    fila.appendChild(e);
    fila.appendChild(el("span", "estante__valor", valor));
    pie.appendChild(fila);
  }

  function pintarEstantes() {
    var cont = $("estantes");
    estado.tarjetas.forEach(function (t, i) {
      var tarjeta = cont.querySelector('[data-id="' + t.id + '"]') || crearEstante(cont, t, i, "salvamed");
      var escala = Math.max(escalas[t.id] || 0, t.existencia + (t.en_camino || 0), (t.R || 0) + (t.sugerida || 0), 1);
      escalas[t.id] = escala;
      tarjeta.className = "estante estante--" + (t.existencia <= 0 ? "vacio" : t.estado) + (t.id === seleccionado ? " estante--elegido" : "");
      tarjeta.querySelector(".estante__unidad").textContent = t.unidad;
      tarjeta.querySelector(".estante__camino").style.width = Math.min((t.en_camino || 0) / escala * 100, 100) + "%";
      mostrar(tarjeta, "salvamed", t, escala);
      var marcaR = tarjeta.querySelector(".estante__marca--r"), marcaIS = tarjeta.querySelector(".estante__marca--is");
      marcaR.hidden = t.R === null; marcaIS.hidden = t.IS === null;
      if (t.R !== null) { marcaR.style.left = Math.min(t.R_pedido / escala * 100, 100) + "%"; marcaR.title = "Pedir al llegar a " + t.R_pedido + " (R " + num(t.R, 1) + ")"; }
      if (t.IS !== null) { marcaIS.style.left = Math.min(t.IS / escala * 100, 100) + "%"; marcaIS.title = "IS " + num(t.IS, 1); }
      // ¿Cambió la estimación? Se muestra unos segundos: así se ve que el programa aprende.
      var previo = estimacion[t.id];
      if (previo && t.sigma !== null && previo.sigma !== null && (Math.abs(previo.sigma - t.sigma) >= 0.05 || Math.abs(previo.mu - t.mu) >= 0.05)) {
        cambios[t.id] = { texto: "↻ μD " + num(previo.mu, 1) + " → " + num(t.mu, 1) + " · σD " + num(previo.sigma, 1) + " → " + num(t.sigma, 1), hasta: performance.now() + 6000 };
      }
      estimacion[t.id] = { mu: t.mu, sigma: t.sigma };
      var pie = tarjeta.querySelector(".estante__pie");
      pie.textContent = "";
      if (t.R === null) pie.textContent = t.dias < 2 ? "Sin plan: " + t.dias + " de 2 días completos" : (t.pendientes[0] || "Plan pendiente");
      else {
        dato(pie, "Consumo promedio", "μD", num(t.mu, 1), "", "Consumo promedio por día, estimado con los despachos");
        dato(pie, "Variación diaria", "σD", "±" + num(t.sigma, 1), "", "Cuánto varía el consumo de un día a otro");
        dato(pie, "Pedir al llegar a", "R", String(t.R_pedido), "r", "Punto de reorden R = " + num(t.R, 1) + ", redondeado hacia arriba");
        dato(pie, "Reserva de seguridad", "IS", num(t.IS, 0), "is", "Inventario de seguridad para los días de más consumo mientras llega el pedido");
        var cambio = cambios[t.id] && cambios[t.id].hasta > performance.now() ? cambios[t.id].texto : null;
        if (cambio) pie.appendChild(el("span", "estante__cambio", cambio));
        else if (t.calidad === "provisional") pie.appendChild(el("span", "estante__dias", "Calculado con " + t.dias + " días"));
      }
      tarjeta.querySelector(".estante__llega").textContent = t.camino ? "🚚 +" + t.camino.cantidad + " llega " + t.camino.llega : "";
    });
  }

  function pintarEstantesRoble() {
    var cont = $("estantes-roble");
    estado.roble.tarjetas.forEach(function (t, i) {
      var tarjeta = cont.querySelector('[data-id="' + t.id + '"]') || crearEstante(cont, t, i, "roble");
      var escala = Math.max(escalas["r" + t.id] || 0, t.existencia + (t.en_camino || 0), t.nivel || 0, 1);
      escalas["r" + t.id] = escala;
      tarjeta.className = "estante estante--" + (t.existencia <= 0 ? "vacio" : t.estado);
      tarjeta.querySelector(".estante__unidad").textContent = t.unidad;
      tarjeta.querySelector(".estante__camino").style.width = Math.min((t.en_camino || 0) / escala * 100, 100) + "%";
      mostrar(tarjeta, "roble", t, escala);
      var marcaR = tarjeta.querySelector(".estante__marca--r"), marcaIS = tarjeta.querySelector(".estante__marca--is");
      marcaIS.hidden = true;
      marcaR.hidden = false; marcaR.style.left = Math.min(t.nivel / escala * 100, 100) + "%"; marcaR.title = "Lo de siempre: " + t.nivel;
      var pie = tarjeta.querySelector(".estante__pie");
      pie.textContent = "";
      dato(pie, "Lo de siempre", "", String(t.nivel), "r", "Lo que se pide por costumbre (≈ " + num(t.habito, 0) + " días de consumo). No sale de un cálculo.");
      if (t.anotado) pie.appendChild(el("span", "estante__aviso", "📣 Avisado: el gerente pide mañana"));
      tarjeta.querySelector(".estante__llega").textContent = t.en_camino ? "🚚 +" + t.en_camino + " en camino" : "";
    });
  }

  function marcarSeleccion() {
    document.querySelectorAll("#estantes .estante").forEach(function (t) {
      t.classList.toggle("estante--elegido", parseInt(t.dataset.id, 10) === seleccionado);
    });
  }

  function pintarServicios() {
    if (servicios.length) return;
    var lista = $("servicios");
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

  // ------------------------------------------------------------ marcador y resumen

  function filasMarcador() {
    var m = estado.marcador;
    function fila(nombre, f) { return [nombre, f(m.roble), f(m.salvamed)]; }
    return [
      fila("Envíos por mensajería", function (l) { return l.envios + " · " + dinero(l.envios_q); }),
      fila("Emergencias (recargo pagado)", function (l) { return l.emergencias + " · " + dinero(l.recargo_q); }),
      fila("Unidades que faltaron (valor al costo)", function (l) { return l.faltantes_u + " · " + dinero(l.faltantes_q); }),
      fila("Dinero parado en bodega (promedio)", function (l) { return dinero(l.bodega_promedio_q); }),
      fila("Costo de mantener acumulado", function (l) { return dinero(l.mantener_q); }),
      fila("Total (envíos + recargos + mantener)", function (l) { return dinero(l.total); }),
      fila("Total contando lo que faltó", function (l) { return dinero(l.total_con_faltantes); }),
    ];
  }

  function pintarTabla(tabla, filas, cabeza) {
    tabla.innerHTML = "";
    var thead = el("thead"), tr = el("tr");
    cabeza.forEach(function (h, i) { tr.appendChild(el("th", i ? "num" : "", h)); });
    thead.appendChild(tr);
    tabla.appendChild(thead);
    var tbody = el("tbody");
    filas.forEach(function (f, i) {
      var fila = el("tr", i >= filas.length - 2 ? "fila--total" : "");
      f.forEach(function (celda, j) { fila.appendChild(el("td", j ? "num" : "", celda)); });
      tbody.appendChild(fila);
    });
    tabla.appendChild(tbody);
  }

  function pintarMarcador() {
    var m = estado.marcador;
    pintarTabla($("marcador"), filasMarcador(), ["Después de " + m.dias + (m.dias === 1 ? " día" : " días"), "El Roble hoy", "Con SALVAMED"]);
    function chips(id, l) {
      var cont = $(id);
      cont.innerHTML = "";
      [
        ["📦 " + l.envios + (l.envios === 1 ? " envío" : " envíos"), ""],
        ["🚨 " + l.emergencias + (l.emergencias === 1 ? " emergencia" : " emergencias"), l.emergencias ? "chip--alerta" : ""],
        ["❌ " + l.faltantes_u + " no despachadas", l.faltantes_u ? "chip--alerta" : ""],
      ].forEach(function (x) { cont.appendChild(el("span", "chip chip--fijo " + x[1], x[0])); });
    }
    chips("chips-roble", m.roble);
    chips("chips-salvamed", m.salvamed);
    var cuentas = {};
    estado.dinero.por_servicio.forEach(function (x) { cuentas[x[0]] = x[2]; });
    document.querySelectorAll(".sim-servicio").forEach(function (li) {
      li.querySelector(".sim-servicio__cuenta").textContent = cuentas[li.dataset.servicio] || 0;
    });
  }

  function pintarResumen() {
    var m = estado.marcador, s = estado.supuestos;
    $("resumen-nota").textContent = "Corrida #" + estado.corrida.id + " · " + estado.corrida.titulo + " · semilla " + estado.corrida.semilla + " · " + m.dias +
      " días simulados · envío " + dinero(s.costo_envio) + " por pedido, emergencia con recargo del " + num(s.recargo * 100, 0) + " % en " + num(s.emergencia_horas, 0) +
      " h, costo de mantener " + num(s.tasa * 100, 0) + " % anual. Supuestos declarados, no datos del sanatorio.";
    function tarjeta(id, titulo, l) {
      var cont = $(id);
      cont.innerHTML = "";
      cont.appendChild(el("h3", "", titulo));
      cont.appendChild(el("p", "sim-resumen__grande", dinero(l.total_con_faltantes)));
      cont.appendChild(el("p", "sutil", "costo total contando lo que faltó, en " + m.dias + " días"));
      var ul = el("ul", "sim-resumen__lista");
      [
        "📦 " + l.envios + " envíos · " + dinero(l.envios_q),
        "🚨 " + l.emergencias + " emergencias · recargo " + dinero(l.recargo_q),
        "❌ " + l.faltantes_u + " unidades que faltaron · " + dinero(l.faltantes_q),
        "🏦 dinero parado en bodega " + dinero(l.bodega_promedio_q) + " · mantener " + dinero(l.mantener_q),
      ].forEach(function (x) { ul.appendChild(el("li", "", x)); });
      cont.appendChild(ul);
    }
    tarjeta("resumen-roble", "Sin SALVAMED (El Roble hoy)", m.roble);
    tarjeta("resumen-salvamed", "Con SALVAMED", m.salvamed);
    pintarTabla($("resumen-tabla"), filasMarcador(), ["Después de " + m.dias + " días", "El Roble hoy", "Con SALVAMED"]);
  }

  function abrirResumen() {
    if (!estado) return;
    pintarResumen();
    var d = $("resumen");
    if (!d.open) d.showModal();
  }

  // ------------------------------------------------------------ bitácora

  var iconos = {
    despacho: "💊", aviso: "⚠️", pausa: "⏸", pedido: "✅", reanudar: "▶", recepcion: "🚚", cierre: "🌙", faltante: "❌",
    inicio: "🏁", aumento: "📈", emergencia: "🚨", anotado: "📣", fin: "🏁",
  };

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
      var lado = e.datos && e.datos.lado;
      var li = el("li", "evento evento--" + e.tipo + (lado ? " evento--" + lado : ""));
      li.appendChild(el("span", "evento__icono", iconos[e.tipo] || "•"));
      var cuerpo = el("span", "evento__texto");
      cuerpo.appendChild(el("span", "evento__hora", e.hora));
      cuerpo.appendChild(document.createTextNode(" " + textoEvento(e)));
      li.appendChild(cuerpo);
      lista.insertBefore(li, lista.firstChild);
    });
    while (lista.children.length > 60) lista.removeChild(lista.lastChild);
  }

  // ------------------------------------------------------------ animaciones

  function animar(nuevos, salto) {
    var despachos = nuevos.filter(function (e) { return e.tipo === "despacho"; });
    if (salto) despachos = despachos.slice(-4);
    var intervalo = Math.max(Math.min(450 / Math.max(despachos.length, 1), 220), 60);
    despachos.forEach(function (e, i) { setTimeout(function () { solicitud(e); }, i * intervalo); });
    nuevos.forEach(function (e) {
      if (e.tipo === "recepcion") camion(e);
      if (e.tipo === "faltante" || e.tipo === "emergencia") falta(e);
      if (e.tipo === "fin") abrirResumen();
    });
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
      (e.datos.items || []).forEach(function (x) {
        document.querySelectorAll('.estante[data-id="' + x[0] + '"]').forEach(function (tarjeta) {
          tarjeta.classList.remove("estante--venta"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--venta");
        });
      });
      persona.classList.add("cliente--sale");
      setTimeout(function () { persona.remove(); }, 600);
    }, 900);
  }

  function falta(e) {
    var lado = e.datos.lado || "salvamed";
    var origen = document.querySelector('.sim-servicio[data-servicio="' + e.datos.servicio + '"]');
    if (origen) { origen.classList.remove("sim-servicio--falta"); void origen.offsetWidth; origen.classList.add("sim-servicio--falta"); }
    var id = lado === "roble" ? idDeIndice(e.datos.indice) : e.medicamento_id;
    var cont = lado === "roble" ? $("estantes-roble") : $("estantes");
    var tarjeta = cont && cont.querySelector('.estante[data-id="' + id + '"]');
    if (tarjeta) { tarjeta.classList.remove("estante--falta"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--falta"); }
  }

  // ---- camión 2D: entra al lado que recibe, se detiene en cada estante, deja una caja «+N» y sale

  var ANCHO_CAMION = 128, ALTO_CAMION = 62;

  function paradasDe(e) {
    var lado = e.datos.lado || "salvamed";
    return (e.datos.items || []).map(function (x) {
      return { id: lado === "roble" ? idDeIndice(x[0]) : x[0], cantidad: x[1] };
    }).filter(function (x) { return x.id && x.cantidad > 0; });
  }

  function retener(e) {
    var lado = e.datos.lado || "salvamed";
    paradasDe(e).forEach(function (x) { retenido[lado][x.id] = (retenido[lado][x.id] || 0) + x.cantidad; });
  }

  function dibujoCamion(lado, urgente) {
    var color = urgente ? "#dc2626" : lado === "roble" ? "#52525b" : "#0f766e";
    var letrero = urgente ? "EMERGENCIA" : lado === "roble" ? "EL ROBLE" : "SALVAMED";
    function rueda(x) {
      return '<g class="camion2d__rueda" style="transform-origin:' + x + 'px 52px">' +
        '<circle cx="' + x + '" cy="52" r="8.5" fill="#27272a"/><circle cx="' + x + '" cy="52" r="3.4" fill="#d4d4d8"/>' +
        '<rect x="' + (x - 1) + '" y="44.5" width="2" height="4" fill="#a1a1aa"/></g>';
    }
    return '<svg viewBox="0 0 128 62" width="' + ANCHO_CAMION + '" height="' + ALTO_CAMION + '" aria-hidden="true">' +
      '<rect x="3" y="6" width="80" height="38" rx="4" fill="#fff" stroke="' + color + '" stroke-width="2.5"/>' +
      '<rect x="3" y="6" width="80" height="9" rx="4" fill="' + color + '" opacity=".14"/>' +
      '<text x="43" y="32" text-anchor="middle" font-size="' + (urgente ? 9.5 : 11.5) + '" font-weight="700" font-family="Inter, system-ui, sans-serif" fill="' + color + '">' + letrero + '</text>' +
      '<path d="M86 17 h22 l15 15 v13 h-37 z" fill="' + color + '"/>' +
      '<path d="M91 21 h15 l10 10 h-25 z" fill="#e0f2fe"/>' +
      '<rect x="118" y="36" width="5" height="4" rx="1" fill="#fde68a"/>' +
      '<rect x="1" y="44" width="124" height="5" rx="2" fill="#3f3f46"/>' +
      rueda(22) + rueda(62) + rueda(104) + "</svg>";
  }

  function esperar(ms) { return new Promise(function (listo) { setTimeout(listo, ms); }); }

  function empezarReparto() {
    repartos++;
    if (repartos === 1 && estado && estado.reloj.corriendo && !estado.reloj.esperando) {
      pausadoPorCamion = true;
      controlar("pausar");
    }
  }

  function terminarReparto() {
    repartos = Math.max(repartos - 1, 0);
    if (repartos === 0 && pausadoPorCamion) {
      pausadoPorCamion = false;
      controlar("reanudar");
    }
  }

  function soltar(lado, id, cantidad) {
    var queda = (retenido[lado][id] || 0) - cantidad;
    if (queda > 0) retenido[lado][id] = queda; else delete retenido[lado][id];
    if (!estado) return;
    if (lado === "roble") pintarEstantesRoble(); else pintarEstantes();
    var cont = lado === "roble" ? $("estantes-roble") : $("estantes");
    var tarjeta = cont && cont.querySelector('.estante[data-id="' + id + '"]');
    if (tarjeta) { tarjeta.classList.remove("estante--llena"); void tarjeta.offsetWidth; tarjeta.classList.add("estante--llena"); }
  }

  function camion(e) {
    var lado = e.datos.lado || "salvamed";
    var paradas = paradasDe(e);
    var urgente = e.datos.origen === "emergencia";
    colaCamion[lado] = colaCamion[lado].then(function () {
      return repartir(lado, paradas, urgente).catch(function () {
        paradas.forEach(function (x) { delete retenido[lado][x.id]; });
        if (estado) { pintarEstantes(); pintarEstantesRoble(); }
      });
    });
  }

  function repartir(lado, paradas, urgente) {
    var panel = $("lado-" + lado);
    var cont = lado === "roble" ? $("estantes-roble") : $("estantes");
    if (!panel || !cont || !paradas.length || document.hidden) {
      paradas.forEach(function (x) { soltar(lado, x.id, x.cantidad); });
      return Promise.resolve();
    }
    empezarReparto();
    panel.classList.remove("sim-lado-panel--recibe"); void panel.offsetWidth; panel.classList.add("sim-lado-panel--recibe");
    var capa = el("div", "reparto");
    var aviso = el("div", "reparto__aviso" + (urgente ? " reparto__aviso--urgente" : ""),
      (urgente ? "🚨 Llegó la emergencia" : "🚚 Llegó el pedido") + " · " + paradas.length + (paradas.length === 1 ? " medicamento" : " medicamentos"));
    var camion2d = el("div", "camion2d");
    camion2d.innerHTML = dibujoCamion(lado, urgente);
    capa.appendChild(aviso);
    capa.appendChild(camion2d);
    panel.appendChild(capa);
    var y = 0;

    function punto(id) {
      var base = panel.getBoundingClientRect();
      var tarjeta = cont.querySelector('.estante[data-id="' + id + '"]');
      var r = tarjeta.getBoundingClientRect();
      return {
        tarjeta: tarjeta, base: base, barra: tarjeta.querySelector(".estante__barra").getBoundingClientRect(),
        x: r.left - base.left + r.width / 2 - ANCHO_CAMION / 2, y: r.top - base.top - ALTO_CAMION * 0.45,
      };
    }
    function mover(x, nuevaY) { y = nuevaY; camion2d.style.transform = "translate(" + Math.round(x) + "px," + Math.round(nuevaY) + "px)"; }

    var inicio = punto(paradas[0].id);
    camion2d.classList.add("camion2d--quieto");
    mover(-ANCHO_CAMION - 30, inicio.y);
    void camion2d.offsetWidth;
    camion2d.classList.remove("camion2d--quieto");

    var cadena = esperar(40);
    paradas.forEach(function (parada) {
      var p;
      cadena = cadena.then(function () {
        p = punto(parada.id);
        camion2d.classList.add("camion2d--anda");
        mover(p.x, p.y);
        return esperar(520);
      }).then(function () {
        camion2d.classList.remove("camion2d--anda");
        var caja = el("div", "caja-reparto", "+" + parada.cantidad);
        var color = getComputedStyle(p.tarjeta).getPropertyValue("--color").trim();
        if (color) caja.style.setProperty("--color", color);
        caja.style.transform = "translate(" + Math.round(p.x + 30) + "px," + Math.round(p.y + 8) + "px)";
        capa.appendChild(caja);
        void caja.offsetWidth;
        var hastaX = p.barra.left - p.base.left + p.barra.width / 2 - 22, hastaY = p.barra.top - p.base.top - 12;
        caja.style.transform = "translate(" + Math.round(hastaX) + "px," + Math.round(hastaY) + "px) scale(.85)";
        return esperar(380).then(function () {
          caja.classList.add("caja-reparto--cae");
          soltar(lado, parada.id, parada.cantidad);
          setTimeout(function () { caja.remove(); }, 300);
          return esperar(160);
        });
      });
    });
    return cadena.then(function () {
      camion2d.classList.add("camion2d--anda");
      mover(panel.getBoundingClientRect().width + 40, y);
      return esperar(600);
    }).then(function () {
      capa.remove();
      terminarReparto();
    }, function (error) {
      capa.remove();
      terminarReparto();
      throw error;
    });
  }

  // ------------------------------------------------------------ pausa (Telegram)

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
    if (p.mismo_envio && p.mismo_envio.length) {
      lineas.push(["En el mismo envío", p.mismo_envio.join(", ") + " (ya en «pedir pronto»: el envío se paga una vez)"]);
    }
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
    $("confirmar-web").dataset.pedido = p.pedido;
  }

  // ------------------------------------------------------------ gráfica

  function pintarGrafica() {
    if (!estado) return;
    var s = estado.serie;
    var t = porId(s.medicamento_id);
    $("grafica-titulo").textContent = "Existencias de " + (t ? t.nombre + " " + t.presentacion : "") + " en el tiempo (con SALVAMED)";
    var svg = $("grafica");
    var ancho = 900, alto = 210, margen = 34;
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
  $("ver-resumen").addEventListener("click", abrirResumen);
  $("terminar").addEventListener("click", function () {
    if (confirm("¿Terminar la demostración y mostrar el resumen? El reloj se queda en pausa.")) controlar("terminar");
  });
  $("cerrar-resumen").addEventListener("click", function () { $("resumen").close(); });
  $("copiar-resumen").addEventListener("click", function () {
    var boton = this;
    var m = estado.marcador;
    var texto = ["Después de " + m.dias + " días\tEl Roble hoy\tCon SALVAMED"].concat(filasMarcador().map(function (f) { return f.join("\t"); })).join("\n");
    navigator.clipboard.writeText(texto).then(function () {
      boton.textContent = "Copiado ✓";
      setTimeout(function () { boton.textContent = "Copiar tabla para el informe"; }, 1500);
    });
  });

  consultar();
  setInterval(consultar, 500);
  setInterval(pintarReloj, 100);
})();
