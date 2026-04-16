#!/usr/bin/env python3
"""CONTRATA SEGURO - v3.4 - Fix parametros POST correctos para SCBA MEV"""
import requests, os, unicodedata
from bs4 import BeautifulSoup
import json, time, sys, re, logging
from urllib.parse import urljoin


def _setup_buscar_logger():
    """
    Logs solo por stderr: no mezclar con stdout (PROGRESO:/RESULTADO:).
    Nivel: env BUSCAR_SIMPLE_LOG_LEVEL (DEBUG, INFO, WARNING). Por defecto INFO.
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
    logger.propagate = False
    return logger


log = _setup_buscar_logger()

# Credenciales solo por entorno (p. ej. Railway); nunca en el código fuente.
SCBA_USUARIO = (os.environ.get("SCBA_USUARIO") or "").strip()
SCBA_PASSWORD = (os.environ.get("SCBA_PASSWORD") or "").strip()
SCBA_DEPTO_REGISTRO = (os.environ.get("SCBA_DEPTO_REGISTRO") or "").strip()

NOMBRE = sys.argv[1] if len(sys.argv) > 1 else "MOSTEYRO"
DNI_CUIL = sys.argv[2] if len(sys.argv) > 2 else ""


def _abort_sin_credenciales_scba():
    msg = "Defina SCBA_USUARIO y SCBA_PASSWORD en el entorno del servidor."
    log.error("%s", msg)
    print(msg, file=sys.stderr)
    out = {
        "nombre": NOMBRE,
        "dni_buscado": DNI_CUIL or None,
        "total": 0,
        "causas_scba": 0,
        "causas_pjn": 0,
        "estado_pjn": "error",
        "causas": [],
        "error_config": msg,
    }
    print(f"RESULTADO:{json.dumps(out, ensure_ascii=False)}", flush=True)
    sys.exit(1)

def normalizar(texto):
    return unicodedata.normalize("NFD", texto.upper()).encode("ascii", "ignore").decode("ascii")

def normalizar_dni(v):
    if not v: return ""
    d = re.sub(r'\D', '', v)
    if len(d) == 11: return d[2:10].lstrip('0') or d[2:10]
    if 7 <= len(d) <= 8: return d.lstrip('0') or d
    return ""

DNI_BUSCADO = normalizar_dni(DNI_CUIL)
PARTES = [normalizar(p) for p in NOMBRE.upper().split()]
APELLIDO = PARTES[0]
FILTRAR_POR = PARTES

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
    Evita falsos positivos por menciones sueltas a 'loguin.asp' en links/scripts.
    """
    if not html:
        return True
    t = html.lower()
    señales = [
        'name="usuariobase"',
        "name='usuariobase'",
        'name="passwordbase"',
        "name='passwordbase'",
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

def extraer_nids(html):
    return [(m.group(1), m.group(2).strip())
            for m in re.finditer(r'nidCausa=(\d+)[^"]*pidJuzgado=(GAM[\d\s]+)', html, re.IGNORECASE)]

def _ids_login_scba():
    """
    Prioriza el depto configurado (si existe) y luego prueba TODOS + deptos conocidos.
    Esto cubre usuarios creados fuera de 'Todos los Deptos'.
    """
    ids = []
    if SCBA_DEPTO_REGISTRO:
        ids.append(SCBA_DEPTO_REGISTRO)
    ids.extend(["", "0"])
    ids.extend([dep_id for _, dep_id, _ in SCBA_JURISDICCIONES])
    # Algunas instalaciones legacy de MEV usan siglas en el selector "Creado en".
    ids.extend(["LP", "SI", "LZ", "QM", "AV", "SM", "MO", "LM"])
    # Unicos preservando orden
    out = []
    seen = set()
    for v in ids:
        vv = (v or "").strip()
        if vv in seen:
            continue
        seen.add(vv)
        out.append(vv)
    return out


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

        payload_base["UsuarioBase"] = SCBA_USUARIO
        payload_base["PasswordBase"] = SCBA_PASSWORD

        # Identificar el nombre real del selector de depto en el formulario.
        dept_select_name = ""
        if form:
            for sel in form.find_all("select"):
                nm = (sel.get("name") or "").strip()
                if nm:
                    dept_select_name = nm
                    break

        intentos_depto = [""] + _ids_login_scba()
        log.info(
            "SCBA login: probando %s combinaciones de departamento (selector=%r)",
            len(intentos_depto),
            dept_select_name or "(ninguno)",
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
                log.info("SCBA login: OK (redirect posloguin) depto=%r", dep_id)
                return True
            if not is_login_page(r.text):
                log.info("SCBA login: OK (ya no es pagina de login) depto=%r", dep_id)
                return True
        log.error("SCBA login: fallo tras probar todos los departamentos")
        return False
    except Exception:
        log.exception("SCBA login: excepcion no controlada")
        return False

def hacer_busqueda(s, gam):
    """
    POST correcto al formulario real de SCBA MEV.
    Campos verificados inspeccionando el formulario HTML de mev.scba.gov.ar/Busqueda.asp
    """
    log.debug("SCBA busqueda POST JuzgadoElegido=%r caratula=%r", gam.strip(), APELLIDO)
    return s.post(
        f"{BASE}/Busqueda.asp",
        data={
            "OpcionBusqueda": "",
            "busca": "",
            "JuzgadoElegido": gam.strip(),   # campo correcto (no pidJuzgado)
            "radio": "xCa",                   # buscar por caratula
            "caratula": APELLIDO,             # campo correcto (no Caratula)
            "TipoCausa": "Ac",               # Ac = todas las causas activas (no Am)
            "Buscar": "Buscar",
        },
        timeout=20,
    )

def buscar_dni_expediente(s, nid, gam):
    try:
        log.debug("SCBA DNI: GET procesales nid=%s gam=%s", nid, gam)
        r = s.get(f"{BASE}/procesales.asp", params={"nidCausa": nid, "pidJuzgado": gam}, timeout=15)
        if not r.ok or is_login_page(r.text):
            log.debug(
                "SCBA DNI: sin datos (http=%s login_page=%s)",
                r.status_code,
                is_login_page(r.text),
            )
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        texto = ""
        for row in soup.find_all("tr"):
            t = row.get_text(" ", strip=True).upper()
            if "AUDIENCIA DE VISTA DE CAUSA" in t and "ACTA" in t:
                texto = t; break
        if not texto:
            t = soup.get_text(" ", strip=True).upper()
            idx = t.find("AUDIENCIA DE VISTA DE CAUSA")
            if idx >= 0: texto = t[idx:idx+3000]
        if not texto: return ""
        m = re.search(r"D\.?N\.?I\.?[:\s#N]*([\d][\d\.\s]{5,9}\d)", texto)
        if m:
            raw = m.group(1)
            if 7 <= len(re.sub(r"[\.\s]", "", raw)) <= 8:
                return re.sub(r"[\.\s]", "", raw).lstrip("0")
        return ""
    except Exception:
        log.debug("SCBA DNI: excepcion al leer expediente nid=%s", nid, exc_info=True)
        return ""

def validar_dni(encontrado):
    if not DNI_BUSCADO: return "no_validado"
    if not encontrado: return "no_encontrado"
    return "coincide" if encontrado.lstrip("0") == DNI_BUSCADO.lstrip("0") else "no_coincide"

def parsear_causas(html, depto, tribunal, gam):
    if is_login_page(html):
        log.warning(
            "SCBA parsear: respuesta es login (%s / %s gam=%s); no se parsean causas",
            depto,
            tribunal,
            gam,
        )
        return []
    soup = BeautifulSoup(html, "html.parser")
    nids = extraer_nids(html)
    causas, ni = [], 0
    for fila in soup.find_all("tr"):
        celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
        if len(celdas) < 2 or len(celdas[0]) < 5: continue
        caratula = celdas[0]
        cn = normalizar(caratula)

        # Encontrar separador C/ (con variantes)
        sep = -1
        for variante in [" C/ ", " C/", "C/ "]:
            idx = cn.find(variante)
            if idx >= 0:
                sep = idx
                break

        actor_parte = cn[:sep] if sep >= 0 else cn
        demandado_parte = cn[sep+1:] if sep >= 0 else ""

        # Determinar rol
        if es_actor(actor_parte):
            rol = "ACTOR"
        elif contiene_terminos(demandado_parte, FILTRAR_POR):
            rol = "DEMANDADO"
        elif contiene_terminos(cn, FILTRAR_POR):
            rol = "INDETERMINADO"
        else:
            ni += 1
            continue  # no pertenece al buscado

        nid, g = nids[ni] if ni < len(nids) else ("", gam.strip())
        causas.append({
            "caratula": caratula,
            "expediente": celdas[1] if len(celdas) > 1 else "",
            "juzgado": f"{tribunal} - {depto}",
            "fecha_inicio": celdas[3] if len(celdas) > 3 else "",
            "ultima_actuacion": celdas[4] if len(celdas) > 4 else "",
            "estado": celdas[5] if len(celdas) > 5 else "",
            "fuente": "SCBA",
            "rol": rol,
            "dni_actor": "",
            "dni_validacion": "no_validado",
            "_nid": nid,
            "_gam": g,
        })
        ni += 1
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
        log.info("SCBA: departamento %s pidDepartamento=%s (%s tribunales)", depto, dep_id, len(tribunales))
        # Seleccionar departamento judicial
        try:
            s.post(f"{BASE}/POSloguin.asp", data={"pidDepartamento": dep_id}, timeout=10)
        except Exception:
            log.warning("SCBA: POSloguin.asp fallo para depto %s (%s)", depto, dep_id, exc_info=True)
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
                        s.post(f"{BASE}/POSloguin.asp", data={"pidDepartamento": dep_id}, timeout=10)
                    except Exception:
                        log.debug("SCBA: re-POSloguin fallo", exc_info=True)
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
    actores = [c for c in todas if c["rol"] == "ACTOR" and c["_nid"]]
    if actores:
        log.info("SCBA: validando DNI en %s expediente(s) como ACTOR", len(actores))
        progreso(86, f"Verificando DNI ({len(actores)} como actor)...", "", len(todas))
        for i, c in enumerate(actores):
            progreso(86 + int((i / len(actores)) * 8), f"DNI {i+1}/{len(actores)}", c["caratula"][:50], len(todas))
            dni = buscar_dni_expediente(s, c["_nid"], c["_gam"])
            c["dni_actor"] = dni
            c["dni_validacion"] = validar_dni(dni)
            log.debug(
                "SCBA DNI actor %s/%s validacion=%s",
                i + 1,
                len(actores),
                c["dni_validacion"],
            )
            time.sleep(0.4)
    for c in todas:
        c.pop("_nid", None); c.pop("_gam", None)
    log.info("SCBA: fin con %s causa(s) acumuladas", len(todas))
    return todas

def buscar_pjn():
    BASE_PJN = "https://scw.pjn.gov.ar"
    def es_captcha_pjn(html):
        if not html:
            return False
        t = html.lower()
        # Evita falsos positivos por texto genérico; busca señales fuertes.
        señales_fuertes = [
            "g-recaptcha",
            "h-captcha",
            "cf-challenge",
            "turnstile",
            "recaptcha/api.js",
            "name=\"campoverificador\"",
            "id=\"campoverificador\"",
        ]
        if any(s in t for s in señales_fuertes):
            return True
        # Fallback semántico: desafío/verificador en contexto de bloqueo.
        return ("desafio" in t or "desafío" in t) and ("verificador" in t or "captcha" in t)
    try:
        log.info("PJN: iniciando home.seam (caratula=%r)", NOMBRE)
        s = requests.Session(); s.headers.update(HDR)
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
    "Inicio busqueda nombre=%r dni_cuil=%r dni_norm=%r filtro_partes=%s",
    NOMBRE,
    DNI_CUIL or "",
    DNI_BUSCADO or "",
    FILTRAR_POR,
)
scba = buscar_scba()
log.info("Tras SCBA: %s causa(s); inicio PJN", len(scba))
progreso(88, "PJN -- Capital Federal", "Camara Nacional del Trabajo", len(scba))
pjn, estado_pjn = buscar_pjn()
log.info("Tras PJN: %s causa(s) estado_pjn=%s", len(pjn), estado_pjn)
todas = scba + pjn
progreso(100, "Busqueda completada", f"{len(todas)} causa(s) encontrada(s)", len(todas))
log.info("Busqueda terminada total=%s (scba=%s pjn=%s)", len(todas), len(scba), len(pjn))
print(f"RESULTADO:{json.dumps({'nombre':NOMBRE,'dni_buscado':DNI_CUIL or None,'total':len(todas),'causas_scba':len(scba),'causas_pjn':len(pjn),'estado_pjn':estado_pjn,'causas':todas},ensure_ascii=False)}", flush=True)
