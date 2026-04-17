#!/usr/bin/env python3
"""CONTRATA SEGURO - v3.5 - SCBA MEV: login usuario/clave + selección DtoJudElegido en POSLoguin"""
import argparse
import requests, os, unicodedata
from bs4 import BeautifulSoup
import json, time, sys, re, logging
from urllib.parse import urljoin

from cuitonline_lookup import lookup_cuitonline
from pjn_session import es_captcha_pjn, load_cookies_file


class _StdoutLogHandler(logging.Handler):
    """
    Replica INFO+ al stdout como LOG:{json} para que api.py reenvíe por SSE.
    Los logs HTTP del edge (Railway, etc.) no incluyen stderr del worker: esto sí viaja en el cuerpo del stream.
    Desactivar: BUSCAR_SIMPLE_SSE_LOG=0
    """

    def __init__(self):
        super().__init__(level=logging.INFO)
        self._sse_min = getattr(
            logging,
            (os.environ.get("BUSCAR_SIMPLE_SSE_LOG_LEVEL") or "INFO").upper(),
            logging.INFO,
        )

    def emit(self, record):
        if record.levelno < self._sse_min:
            return
        try:
            payload = {"lvl": record.levelname, "msg": record.getMessage()}
            if record.exc_info and record.exc_info[0]:
                payload["exc_type"] = record.exc_info[0].__name__
            print(f"LOG:{json.dumps(payload, ensure_ascii=False)}", flush=True)
        except Exception:
            self.handleError(record)


def _setup_buscar_logger():
    """
    - stderr: trazas con timestamp (logs del contenedor / Railway "Deploy logs").
    - stdout LOG: mismos eventos INFO+ en JSON (consumidos por api.py -> SSE tipo 'log').
    Nivel stderr: BUSCAR_SIMPLE_LOG_LEVEL (default INFO).
    """
    logger = logging.getLogger("buscar_simple")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    level_name = (os.environ.get("BUSCAR_SIMPLE_LOG_LEVEL") or "INFO").upper()
    handler.setLevel(getattr(logging, level_name, logging.INFO))
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [buscar_simple][%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    if (os.environ.get("BUSCAR_SIMPLE_SSE_LOG") or "1").strip() != "0":
        logger.addHandler(_StdoutLogHandler())
    logger.propagate = False
    return logger


log = _setup_buscar_logger()

# Credenciales solo por entorno (p. ej. Railway); nunca en el código fuente.
SCBA_USUARIO = (os.environ.get("SCBA_USUARIO") or "").strip()
SCBA_PASSWORD = (os.environ.get("SCBA_PASSWORD") or "").strip()
SCBA_DEPTO_REGISTRO = (os.environ.get("SCBA_DEPTO_REGISTRO") or "").strip()


def _parse_args():
    p = argparse.ArgumentParser(
        description="Búsqueda SCBA + PJN por nombre o por CUIL (resolución vía CuitOnline)."
    )
    p.add_argument(
        "--cuil",
        metavar="DIGITOS",
        help="CUIT/CUIL de 11 dígitos: se obtiene la denominación en cuitonline.com y se busca con ese nombre.",
    )
    p.add_argument(
        "--caratula",
        choices=("apellido", "completo"),
        default="apellido",
        help="Texto enviado al campo carátula del MEV SCBA: solo primer apellido/palabra o nombre completo.",
    )
    p.add_argument(
        "nombre_tokens",
        nargs="*",
        help="Nombre y apellido (obligatorio si no se pasa --cuil). Ej.: GARCIA JUAN CARLOS",
    )
    p.add_argument(
        "--pjn-cookies-file",
        default="",
        metavar="RUTA",
        help="JSON de cookies PJN tras resolver el desafío en el sitio (ver API /pjn/prepare y /pjn/verify).",
    )
    return p.parse_args()


ARGS = _parse_args()
CUITONLINE_META = {}
PJN_COOKIES_FILE = (ARGS.pjn_cookies_file or "").strip()

if ARGS.cuil:
    log.info("Modo CUIL: resolviendo denominación en CuitOnline para %r", ARGS.cuil)
    _co = lookup_cuitonline(ARGS.cuil)
    if not _co.ok:
        msg = _co.error or "No se pudo resolver el CUIL/CUIT"
        log.error("CuitOnline: %s", msg)
        print(msg, file=sys.stderr)
        out = {
            "nombre": "",
            "dni_buscado": None,
            "total": 0,
            "causas_scba": 0,
            "causas_pjn": 0,
            "estado_pjn": "error",
            "causas": [],
            "error_config": msg,
            "modo_entrada": "cuil",
            "cuil_consultado": _co.digits,
            "cuitonline_error": msg,
        }
        print(f"RESULTADO:{json.dumps(out, ensure_ascii=False)}", flush=True)
        sys.exit(1)
    NOMBRE = _co.nombre.upper()
    CUITONLINE_META = {
        "modo_entrada": "cuil",
        "cuil_consultado": _co.digits,
        "nombre_resuelto_cuitonline": _co.nombre,
        "cuitonline_selector": _co.selector_origen,
        "cuitonline_url": _co.url,
    }
    log.info(
        "CuitOnline OK: nombre=%r origen_dom=%s",
        _co.nombre,
        _co.selector_origen,
    )
else:
    raw = " ".join(ARGS.nombre_tokens).strip()
    if not raw:
        NOMBRE = "MOSTEYRO"
        log.warning("Sin nombre ni --cuil: usando valor por defecto %r", NOMBRE)
    else:
        NOMBRE = raw.upper()
    CUITONLINE_META = {"modo_entrada": "nombre"}


def _abort_sin_credenciales_scba():
    msg = "Defina SCBA_USUARIO y SCBA_PASSWORD en el entorno del servidor."
    log.error("%s", msg)
    print(msg, file=sys.stderr)
    out = {
        "nombre": NOMBRE,
        "dni_buscado": None,
        "total": 0,
        "causas_scba": 0,
        "causas_pjn": 0,
        "estado_pjn": "error",
        "causas": [],
        "error_config": msg,
        "caratula_modo": ARGS.caratula,
        **CUITONLINE_META,
    }
    print(f"RESULTADO:{json.dumps(out, ensure_ascii=False)}", flush=True)
    sys.exit(1)

def normalizar(texto):
    return unicodedata.normalize("NFD", texto.upper()).encode("ascii", "ignore").decode("ascii")

PARTES = [normalizar(p) for p in NOMBRE.upper().split()]
APELLIDO = PARTES[0]
FILTRAR_POR = PARTES
CARATULA_BUSQUEDA = " ".join(PARTES) if ARGS.caratula == "completo" else APELLIDO

# GAM = codigo del juzgado en el sistema SCBA
SCBA_JURISDICCIONES = [
    ("San Isidro", "24", [("GAM681","TT1"),("GAM682","TT2"),("GAM683","TT3"),("GAM1096","TT4"),("GAM1097","TT5"),("GAM1098","TT6"),("GAM2133","TT7 Pilar")]),
    ("La Plata", "6", [("GAM301","TT1"),("GAM302","TT2"),("GAM303","TT3"),("GAM304","TT4"),("GAM1048","TT5")]),
    ("Lomas de Zamora","16",[("GAM363","TT1"),("GAM364","TT2"),("GAM365","TT3"),("GAM366","TT4"),("GAM1053","TT5")]),
    ("Quilmes", "23", [("GAM611","TT1"),("GAM612","TT2"),("GAM613","TT3"),("GAM1051","TT4"),("GAM1052","TT5"),("GAM2976","TT1 Varela")]),
    ("Avellaneda", "80", [("GAM101","TT1"),("GAM102","TT2"),("GAM103","TT3"),("GAM1049","TT4"),("GAM2010","TT5")]),
    ("San Martin", "25", [("GAM461","TT1"),("GAM462","TT2"),("GAM463","TT3"),("GAM1054","TT4"),("GAM1055","TT5"),("GAM2134","TT6")]),
    ("Moron", "19", [("GAM381","TT1"),("GAM382","TT2"),("GAM383","TT3"),("GAM1046","TT4"),("GAM1047","TT5"),("GAM2135","TT6")]),
    ("La Matanza", "14", [("GAM341","TT1"),("GAM342","TT2"),("GAM343","TT3"),("GAM1044","TT4"),("GAM1045","TT5"),("GAM2136","TT6")]),
]

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BASE = "https://mev.scba.gov.ar"
TOTAL = sum(len(t) for _, _, t in SCBA_JURISDICCIONES)
def is_login_page(html):
    """
    Detecta pantalla de login SCBA por estructura del formulario.
    El MEV actual usa name=\"usuario\" / \"clave\"; versiones viejas UsuarioBase/PasswordBase.
    """
    if not html:
        return True
    t = html.lower()
    señales = [
        'name="usuariobase"',
        "name='usuariobase'",
        'name="passwordbase"',
        "name='passwordbase'",
        'name="usuario"',
        "name='usuario'",
        'name="clave"',
        "name='clave'",
        "ingrese los datos del usuario",
    ]
    return any(s in t for s in señales)

def progreso(pct, texto, detalle="", causas=0):
    print(f"PROGRESO:{json.dumps({'pct':pct,'texto':texto,'detalle':detalle,'causas':causas})}", flush=True)

def contiene_terminos(texto, terminos):
    """Verifica palabras completas ignorando tildes. Usa espacios como delimitador."""
    t = " " + normalizar(texto) + " "
    return all((" " + p + " ") in t for p in terminos)

def es_actor(actor_parte):
    if not actor_parte: return False
    return contiene_terminos(actor_parte, FILTRAR_POR)

def _select_departamento_mev(form):
    """El <select> de departamento / creado en (no el primer select arbitrario)."""
    if not form:
        return None, ""
    ranked = []
    for sel in form.find_all("select"):
        nm = (sel.get("name") or "").strip()
        if not nm:
            continue
        nml = nm.lower()
        score = 0
        if any(
            x in nml
            for x in ("depto", "depart", "registr", "creado", "jurisd")
        ):
            score = 2
        elif len(form.find_all("select")) == 1:
            score = 1
        ranked.append((score, nm, sel))
    ranked.sort(key=lambda x: -x[0])
    if not ranked:
        return None, ""
    _, nm, sel = ranked[0]
    return sel, nm


def _valor_opcion_todos_departamentos(sel):
    """Valor del <option> tipo 'Todos los departamentos', o None si no hay match."""
    if not sel:
        return None
    for opt in sel.find_all("option"):
        txt = (opt.get_text() or "").strip()
        tnorm = normalizar(txt).replace(" ", "")
        raw = opt.get("value")
        val = "" if raw is None else str(raw).strip()
        if "TODOS" in tnorm and (
            "DEPARTAMENTO" in tnorm
            or "DEPARTAMENT" in tnorm
            or "DEPTO" in tnorm
            or "JUZGAD" in tnorm
            or "DEPART" in tnorm
        ):
            return val
        if tnorm in ("TODOS", "*TODOS*", "-TODOS-", "TODOSLOS"):
            return val
    return None


def _intentos_valor_departamento_login(form):
    """
    Solo 'todos los departamentos' (según el HTML) o vacío — no recorrer cada depto.
    Override: SCBA_DEPTO_REGISTRO en el entorno.
    """
    if SCBA_DEPTO_REGISTRO:
        v = SCBA_DEPTO_REGISTRO.strip()
        log.info("SCBA login: usando SCBA_DEPTO_REGISTRO=%r", v)
        return [v] if v else [""]
    sel, nm_sel = _select_departamento_mev(form)
    todos = _valor_opcion_todos_departamentos(sel)
    if todos is not None:
        log.info(
            "SCBA login: opción 'Todos' en select %r -> valor=%r",
            nm_sel,
            todos,
        )
        if todos == "":
            return [""]
        return [todos, ""]
    log.info(
        "SCBA login: no se detectó opción 'Todos' en %r; solo intento con valor vacío",
        nm_sel or "(sin select)",
    )
    return [""]


def _nombres_campos_credencial_login(form):
    """
    Devuelve (campo_usuario, campo_clave) según el <form> de loguin.asp.
    """
    if not form:
        return "usuario", "clave"
    user_field = pass_field = None
    for inp in form.find_all("input"):
        nm = (inp.get("name") or "").strip()
        if not nm:
            continue
        tipo = (inp.get("type") or "text").lower()
        if tipo == "password":
            pass_field = nm
        elif tipo in ("text", "email"):
            user_field = nm
    return (user_field or "usuario"), (pass_field or "clave")


def seleccionar_departamento_judicial(s, dep_id):
    """
    Tras el login, el MEV exige el POST del formulario en POSLoguin.asp:
    TipoDto=CC (departamento judicial), DtoJudElegido=<id numérico del mapa SCBA>.
    Un POST solo con pidDepartamento no activa la jurisdicción y Busqueda.asp responde
    \"servicio no disponible para esta jurisdicción\".
    """
    dep = str(dep_id).strip()
    url = f"{BASE}/POSLoguin.asp"
    return s.post(
        url,
        data={
            "TipoDto": "CC",
            "DtoJudElegido": dep,
            "Aceptar": "Aceptar",
        },
        timeout=15,
        headers={"Referer": url, "Origin": BASE},
    )


def do_login(s):
    try:
        login_url = f"{BASE}/loguin.asp"
        log.info("SCBA login: GET %s", login_url)
        r0 = s.get(login_url, timeout=15)
        if not r0.ok:
            log.warning("SCBA login: respuesta inicial HTTP %s", r0.status_code)
            return False

        soup = BeautifulSoup(r0.text, "html.parser")
        form = soup.find("form")
        action = form.get("action", "loguin.asp") if form else "loguin.asp"
        post_url = urljoin(login_url, action)
        log.debug("SCBA login: form action=%s post_url=%s", action, post_url)

        payload_base = {}
        if form:
            for inp in form.find_all("input"):
                name = (inp.get("name") or "").strip()
                if not name:
                    continue
                tipo = (inp.get("type") or "text").lower()
                if tipo in ("submit", "button", "image"):
                    continue
                payload_base[name] = inp.get("value", "")

        u_key, p_key = _nombres_campos_credencial_login(form)
        payload_base[u_key] = SCBA_USUARIO
        payload_base[p_key] = SCBA_PASSWORD

        _, dept_select_name = _select_departamento_mev(form)
        intentos_depto = _intentos_valor_departamento_login(form)
        log.info(
            "SCBA login: %s intento(s) de departamento (selector=%r valores=%s)",
            len(intentos_depto),
            dept_select_name or "(ninguno)",
            intentos_depto,
        )
        for dep_id in intentos_depto:
            payload = dict(payload_base)
            if dept_select_name:
                payload[dept_select_name] = dep_id
            # Variantes defensivas de nombre de campo (instancias legacy).
            payload["pidDepartamento"] = dep_id
            payload["Departamento"] = dep_id
            payload["CreadoEn"] = dep_id
            payload["depto"] = dep_id

            r = s.post(
                post_url,
                data=payload,
                timeout=20,
                headers={"Referer": login_url, "Origin": BASE},
            )
            url_fin = (r.url or "").lower()
            log.debug(
                "SCBA login POST depto=%r -> HTTP %s url_final=%s login_page=%s",
                dep_id,
                r.status_code,
                (r.url or "")[:120],
                is_login_page(r.text),
            )
            if "posloguin.asp" in url_fin:
                log.info("SCBA login: OK (redirect POSLoguin) depto=%r", dep_id)
                return True
            if not is_login_page(r.text):
                log.info("SCBA login: OK (ya no es pagina de login) depto=%r", dep_id)
                return True
        log.error("SCBA login: fallo tras probar valores de departamento: %s", intentos_depto)
        return False
    except Exception:
        log.exception("SCBA login: excepcion no controlada")
        return False

def hacer_busqueda(s, gam):
    """
    POST correcto al formulario real de SCBA MEV.
    Campos verificados inspeccionando el formulario HTML de mev.scba.gov.ar/Busqueda.asp
    """
    log.debug(
        "SCBA busqueda POST JuzgadoElegido=%r caratula=%r (modo_caratula=%s)",
        gam.strip(),
        CARATULA_BUSQUEDA,
        ARGS.caratula,
    )
    return s.post(
        f"{BASE}/Busqueda.asp",
        data={
            "OpcionBusqueda": "",
            "busca": "",
            "JuzgadoElegido": gam.strip(),   # campo correcto (no pidJuzgado)
            "radio": "xCa",                   # buscar por caratula
            "caratula": CARATULA_BUSQUEDA,    # apellido solo o nombre completo
            "TipoCausa": "Ac",               # Ac = todas las causas activas (no Am)
            "Buscar": "Buscar",
        },
        timeout=20,
    )

def _siguiente_tr_bloque_resultado(fila):
    """
    Cada causa en MEV ocupa dos <tr> dentro del mismo <tbody>.
    html.parser deja el <tbody> explícito; sin él, se usa la tabla padre.
    """
    tbody = fila.find_parent("tbody")
    if tbody:
        rows = tbody.find_all("tr", recursive=False)
    else:
        tbl = fila.find_parent("table")
        if not tbl:
            return None
        rows = tbl.find_all("tr", recursive=False)
    try:
        i = rows.index(fila)
    except ValueError:
        return None
    return rows[i + 1] if i + 1 < len(rows) else None


def parsear_causas(html, depto, tribunal, gam):
    if is_login_page(html):
        log.warning(
            "SCBA parsear: respuesta es login (%s / %s gam=%s); no se parsean causas",
            depto,
            tribunal,
            gam,
        )
        return []
    low = html.lower()
    if "no esta disponible" in low or "no está disponible" in low:
        log.debug(
            "SCBA parsear: MEV indica servicio no disponible (%s / %s gam=%s)",
            depto,
            tribunal,
            gam,
        )
        return []
    soup = BeautifulSoup(html, "html.parser")
    causas = []
    vistos = set()
    href_nid = re.compile(r"nidCausa=(\d+)", re.I)
    href_gam = re.compile(r"pidJuzgado=(GAM\d+)", re.I)

    for fila in soup.find_all("tr"):
        enlace = fila.find(
            "a",
            href=lambda h: h and "nidcausa=" in h.lower() and "pidjuzgado=" in h.lower(),
        )
        if not enlace:
            continue
        # Evitar <tr> contenedores con tablas anidadas: el enlace debe pertenecer a esta fila.
        if enlace.find_parent("tr") is not fila:
            continue
        href = enlace.get("href") or ""
        mc = href_nid.search(href)
        mg = href_gam.search(href)
        if not mc or not mg:
            continue
        nid = mc.group(1)
        if nid in vistos:
            continue
        caratula = enlace.get_text(" ", strip=True)
        if len(caratula) < 8:
            continue
        cn = normalizar(caratula)
        sep = -1
        for variante in (" C/ ", " C/", "C/ "):
            idx = cn.find(variante)
            if idx >= 0:
                sep = idx
                break
        actor_parte = cn[:sep] if sep >= 0 else cn
        demandado_parte = cn[sep + 1 :] if sep >= 0 else ""
        if es_actor(actor_parte):
            rol = "ACTOR"
        elif contiene_terminos(demandado_parte, FILTRAR_POR):
            rol = "DEMANDADO"
        elif contiene_terminos(cn, FILTRAR_POR):
            rol = "INDETERMINADO"
        else:
            continue

        vistos.add(nid)
        g = mg.group(1)
        estado_desp = ""
        expediente = ""
        fecha_inicio = ""
        ultima_actuacion = ""
        sig = _siguiente_tr_bloque_resultado(fila)
        if sig:
            tds = sig.find_all("td", recursive=False)
            if len(tds) >= 1:
                estado_desp = tds[0].get_text(" ", strip=True)
            if len(tds) >= 3:
                expediente = tds[2].get_text(" ", strip=True)
            if len(tds) >= 4:
                fecha_inicio = tds[3].get_text(" ", strip=True)
            if len(tds) >= 5:
                ultima_actuacion = tds[4].get_text(" ", strip=True)

        causas.append(
            {
                "caratula": caratula,
                "expediente": expediente,
                "juzgado": f"{tribunal} - {depto}",
                "fecha_inicio": fecha_inicio,
                "ultima_actuacion": ultima_actuacion,
                "estado": estado_desp,
                "fuente": "SCBA",
                "rol": rol,
                "dni_actor": "",
                "dni_validacion": "no_validado",
                "_nid": nid,
                "_gam": g,
            }
        )
    log.info("SCBA parsear: %s causa(s) en %s / %s (gam=%s)", len(causas), depto, tribunal, gam)
    return causas

def buscar_scba():
    progreso(2, "SCBA -- Iniciando sesion...", "Provincia de Buenos Aires")
    log.info("SCBA: iniciando sesion y recorrido de %s juzgados", TOTAL)
    s = requests.Session()
    s.headers.update(HDR)
    if not do_login(s):
        log.error("SCBA: do_login fallo; se devuelve lista vacia")
        progreso(2, "Error de conexion SCBA", ""); return []
    todas, procesados = [], 0
    for depto, dep_id, tribunales in SCBA_JURISDICCIONES:
        log.info("SCBA: departamento %s DtoJudElegido=%s (%s tribunales)", depto, dep_id, len(tribunales))
        # Seleccionar departamento judicial (formulario POSLoguin, no solo pidDepartamento)
        try:
            r_dep = seleccionar_departamento_judicial(s, dep_id)
            log.debug(
                "SCBA: POSLoguin selección depto HTTP %s url=%s",
                r_dep.status_code,
                (r_dep.url or "")[:100],
            )
        except Exception:
            log.warning("SCBA: POSLoguin.asp fallo para depto %s (%s)", depto, dep_id, exc_info=True)
        for gam, tribunal in tribunales:
            procesados += 1
            pct = int((procesados / TOTAL) * 83) + 2
            progreso(pct, f"SCBA -- {depto}", f"{tribunal} ({procesados}/{TOTAL})", len(todas))
            try:
                r = hacer_busqueda(s, gam)
                log.debug(
                    "SCBA busqueda %s/%s %s HTTP %s len(html)=%s",
                    procesados,
                    TOTAL,
                    gam,
                    r.status_code,
                    len(r.text or ""),
                )
                if is_login_page(r.text):
                    log.warning("SCBA: sesion cayo a login en %s/%s; re-login", depto, gam)
                    do_login(s)
                    try:
                        seleccionar_departamento_judicial(s, dep_id)
                    except Exception:
                        log.debug("SCBA: re-POSLoguin fallo", exc_info=True)
                    r = hacer_busqueda(s, gam)
                    if is_login_page(r.text):
                        log.warning("SCBA: sigue login tras reintento; se omite %s", gam)
                        continue
                nuevas = parsear_causas(r.text, depto, tribunal, gam)
                if nuevas:
                    progreso(pct, f"SCBA -- {depto}", f"{tribunal}: {len(nuevas)} causa(s)", len(todas) + len(nuevas))
                todas.extend(nuevas)
                time.sleep(0.2)
            except Exception:
                log.exception("SCBA: error en busqueda %s %s %s", depto, tribunal, gam)
    for c in todas:
        c.pop("_nid", None); c.pop("_gam", None)
    log.info("SCBA: fin con %s causa(s) acumuladas", len(todas))
    return todas

def buscar_pjn():
    BASE_PJN = "https://scw.pjn.gov.ar"
    try:
        log.info("PJN: iniciando home.seam (caratula=%r)", NOMBRE)
        s = requests.Session(); s.headers.update(HDR)
        if PJN_COOKIES_FILE and os.path.isfile(PJN_COOKIES_FILE):
            try:
                load_cookies_file(s, PJN_COOKIES_FILE)
                log.info("PJN: cookies de sesión humana cargadas desde %s", PJN_COOKIES_FILE)
            except Exception:
                log.exception("PJN: no se pudieron cargar cookies desde %s", PJN_COOKIES_FILE)
        r = s.get(f"{BASE_PJN}/scw/home.seam", timeout=15)
        log.debug("PJN: GET home HTTP %s len=%s", r.status_code, len(r.text or ""))
        if es_captcha_pjn(r.text):
            log.warning("PJN: captcha o bloqueo detectado en home")
            return [], "captcha_required"
        soup = BeautifulSoup(r.text, "html.parser")
        vs = soup.find("input", {"name": "javax.faces.ViewState"})
        vstate = vs["value"] if vs else ""
        if not vstate:
            log.warning("PJN: ViewState vacio; la busqueda puede fallar")
        causas = []
        for cod, nombre in [("CNT","Camara Nacional del Trabajo"),("CSS","Camara Federal Seg. Social")]:
            try:
                log.info("PJN: POST busqueda camara=%s (%s)", cod, nombre)
                r2 = s.post(f"{BASE_PJN}/scw/home.seam", data={
                    "javax.faces.ViewState": vstate, "formPublica": "formPublica",
                    "formPublica:expedienteTab-value": "porParte",
                    "formPublica:caratula": NOMBRE, "formPublica:camara": cod,
                    "formPublica:btnSearch": "Buscar"}, timeout=20)
                log.debug("PJN: POST %s HTTP %s len=%s", cod, r2.status_code, len(r2.text or ""))
                if es_captcha_pjn(r2.text):
                    log.warning("PJN: captcha en respuesta camara=%s", cod)
                    return [], "captcha_required"
                soup2 = BeautifulSoup(r2.text, "html.parser")
                for fila in soup2.select("table tr")[1:]:
                    celdas = [td.get_text(strip=True) for td in fila.find_all("td")]
                    if len(celdas) < 3 or len(celdas[0]) < 5: continue
                    cn = normalizar(celdas[0])
                    sep = cn.find(" C/ ")
                    actor_parte = cn[:sep] if sep >= 0 else cn
                    demandado_parte = cn[sep+1:] if sep >= 0 else ""
                    if es_actor(actor_parte): rol = "ACTOR"
                    elif contiene_terminos(demandado_parte, FILTRAR_POR): rol = "DEMANDADO"
                    else: rol = "INDETERMINADO"
                    causas.append({
                        "caratula": celdas[0],
                        "expediente": celdas[1] if len(celdas) > 1 else "",
                        "juzgado": celdas[2] if len(celdas) > 2 else nombre,
                        "fecha_inicio": celdas[3] if len(celdas) > 3 else "",
                        "ultima_actuacion": celdas[4] if len(celdas) > 4 else "",
                        "estado": celdas[5] if len(celdas) > 5 else "",
                        "fuente": "PJN", "rol": rol,
                        "dni_actor": "", "dni_validacion": "no_validado",
                    })
            except Exception:
                log.exception("PJN: error en camara %s", cod)
                continue
        log.info("PJN: fin con %s causa(s) estado=ok", len(causas))
        return causas, "ok"
    except Exception:
        log.exception("PJN: error general")
        return [], "error"

if not SCBA_USUARIO or not SCBA_PASSWORD:
    _abort_sin_credenciales_scba()

log.info(
    "Inicio busqueda nombre=%r filtro_partes=%s caratula_SCBA=%r modo_caratula=%s meta=%s",
    NOMBRE,
    FILTRAR_POR,
    CARATULA_BUSQUEDA,
    ARGS.caratula,
    CUITONLINE_META,
)
scba = buscar_scba()
log.info("Tras SCBA: %s causa(s); inicio PJN", len(scba))
progreso(88, "PJN -- Capital Federal", "Camara Nacional del Trabajo", len(scba))
pjn, estado_pjn = buscar_pjn()
log.info("Tras PJN: %s causa(s) estado_pjn=%s", len(pjn), estado_pjn)
todas = scba + pjn
progreso(100, "Busqueda completada", f"{len(todas)} causa(s) encontrada(s)", len(todas))
log.info("Busqueda terminada total=%s (scba=%s pjn=%s)", len(todas), len(scba), len(pjn))
_resultado = {
    "nombre": NOMBRE,
    "dni_buscado": None,
    "total": len(todas),
    "causas_scba": len(scba),
    "causas_pjn": len(pjn),
    "estado_pjn": estado_pjn,
    "causas": todas,
    "caratula_modo": ARGS.caratula,
    "caratula_scba_usada": CARATULA_BUSQUEDA,
    **CUITONLINE_META,
}
print(f"RESULTADO:{json.dumps(_resultado, ensure_ascii=False)}", flush=True)
