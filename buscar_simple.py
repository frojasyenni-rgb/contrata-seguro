#!/usr/bin/env python3
"""CONTRATA SEGURO - v3.4 - Fix parametros POST correctos para SCBA MEV"""
import requests, os, unicodedata
from bs4 import BeautifulSoup
import json, time, sys, re

# Credenciales solo por entorno (p. ej. Railway); nunca en el código fuente.
SCBA_USUARIO = (os.environ.get("SCBA_USUARIO") or "").strip()
SCBA_PASSWORD = (os.environ.get("SCBA_PASSWORD") or "").strip()
SCBA_DEPTO_REGISTRO = (os.environ.get("SCBA_DEPTO_REGISTRO") or "").strip()

NOMBRE = sys.argv[1] if len(sys.argv) > 1 else "MOSTEYRO"
DNI_CUIL = sys.argv[2] if len(sys.argv) > 2 else ""


def _abort_sin_credenciales_scba():
    msg = "Defina SCBA_USUARIO y SCBA_PASSWORD en el entorno del servidor."
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
SESSION_EXPIRED = ["UsuarioBase", "PasswordBase", "loguin.asp", "Ingrese los datos", "Iniciar Sesion"]

def is_login_page(html):
    return any(m.lower() in html.lower() for m in SESSION_EXPIRED)

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
        # Intento base (Todos los deptos)
        r = s.post(
            f"{BASE}/loguin.asp",
            data={"UsuarioBase": SCBA_USUARIO, "PasswordBase": SCBA_PASSWORD},
            timeout=15,
        )
        if not is_login_page(r.text):
            return True

        # Fallback: algunos usuarios requieren seleccionar depto de registro.
        for dep_id in _ids_login_scba():
            payload = {
                "UsuarioBase": SCBA_USUARIO,
                "PasswordBase": SCBA_PASSWORD,
                # Variantes defensivas de nombre de campo observadas en portales legacy.
                "pidDepartamento": dep_id,
                "Departamento": dep_id,
                "CreadoEn": dep_id,
                "depto": dep_id,
            }
            r2 = s.post(f"{BASE}/loguin.asp", data=payload, timeout=15)
            if not is_login_page(r2.text):
                return True
        return False
    except Exception:
        return False

def hacer_busqueda(s, gam):
    """
    POST correcto al formulario real de SCBA MEV.
    Campos verificados inspeccionando el formulario HTML de mev.scba.gov.ar/Busqueda.asp
    """
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
        r = s.get(f"{BASE}/procesales.asp", params={"nidCausa": nid, "pidJuzgado": gam}, timeout=15)
        if not r.ok or is_login_page(r.text): return ""
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
        return ""

def validar_dni(encontrado):
    if not DNI_BUSCADO: return "no_validado"
    if not encontrado: return "no_encontrado"
    return "coincide" if encontrado.lstrip("0") == DNI_BUSCADO.lstrip("0") else "no_coincide"

def parsear_causas(html, depto, tribunal, gam):
    if is_login_page(html):
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
    return causas

def buscar_scba():
    progreso(2, "SCBA -- Iniciando sesion...", "Provincia de Buenos Aires")
    s = requests.Session()
    s.headers.update(HDR)
    if not do_login(s):
        progreso(2, "Error de conexion SCBA", ""); return []
    todas, procesados = [], 0
    for depto, dep_id, tribunales in SCBA_JURISDICCIONES:
        # Seleccionar departamento judicial
        try: s.post(f"{BASE}/POSloguin.asp", data={"pidDepartamento": dep_id}, timeout=10)
        except Exception: pass
        for gam, tribunal in tribunales:
            procesados += 1
            pct = int((procesados / TOTAL) * 83) + 2
            progreso(pct, f"SCBA -- {depto}", f"{tribunal} ({procesados}/{TOTAL})", len(todas))
            try:
                r = hacer_busqueda(s, gam)
                if is_login_page(r.text):
                    do_login(s)
                    try: s.post(f"{BASE}/POSloguin.asp", data={"pidDepartamento": dep_id}, timeout=10)
                    except Exception: pass
                    r = hacer_busqueda(s, gam)
                    if is_login_page(r.text): continue
                nuevas = parsear_causas(r.text, depto, tribunal, gam)
                if nuevas:
                    progreso(pct, f"SCBA -- {depto}", f"{tribunal}: {len(nuevas)} causa(s)", len(todas) + len(nuevas))
                todas.extend(nuevas)
                time.sleep(0.2)
            except Exception: pass
    actores = [c for c in todas if c["rol"] == "ACTOR" and c["_nid"]]
    if actores:
        progreso(86, f"Verificando DNI ({len(actores)} como actor)...", "", len(todas))
        for i, c in enumerate(actores):
            progreso(86 + int((i / len(actores)) * 8), f"DNI {i+1}/{len(actores)}", c["caratula"][:50], len(todas))
            dni = buscar_dni_expediente(s, c["_nid"], c["_gam"])
            c["dni_actor"] = dni
            c["dni_validacion"] = validar_dni(dni)
            time.sleep(0.4)
    for c in todas:
        c.pop("_nid", None); c.pop("_gam", None)
    return todas

def buscar_pjn():
    BASE_PJN = "https://scw.pjn.gov.ar"
    CAPTCHA = ["campo verificador","fcMsg","VER DESAFIO","resuelva el desafio","Presione el bot"]
    try:
        s = requests.Session(); s.headers.update(HDR)
        r = s.get(f"{BASE_PJN}/scw/home.seam", timeout=15)
        if any(c.lower() in r.text.lower() for c in CAPTCHA): return [], "captcha_required"
        soup = BeautifulSoup(r.text, "html.parser")
        vs = soup.find("input", {"name": "javax.faces.ViewState"})
        vstate = vs["value"] if vs else ""
        causas = []
        for cod, nombre in [("CNT","Camara Nacional del Trabajo"),("CFSS","Camara Federal Seg. Social")]:
            try:
                r2 = s.post(f"{BASE_PJN}/scw/home.seam", data={
                    "javax.faces.ViewState": vstate, "formPublica": "formPublica",
                    "formPublica:caratula": NOMBRE, "formPublica:camara": cod,
                    "formPublica:btnSearch": "Buscar"}, timeout=20)
                if any(c.lower() in r2.text.lower() for c in CAPTCHA): return [], "captcha_required"
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
            except Exception: continue
        return causas, "ok"
    except Exception:
        return [], "error"

if not SCBA_USUARIO or not SCBA_PASSWORD:
    _abort_sin_credenciales_scba()

scba = buscar_scba()
progreso(88, "PJN -- Capital Federal", "Camara Nacional del Trabajo", len(scba))
pjn, estado_pjn = buscar_pjn()
todas = scba + pjn
progreso(100, "Busqueda completada", f"{len(todas)} causa(s) encontrada(s)", len(todas))
print(f"RESULTADO:{json.dumps({'nombre':NOMBRE,'dni_buscado':DNI_CUIL or None,'total':len(todas),'causas_scba':len(scba),'causas_pjn':len(pjn),'estado_pjn':estado_pjn,'causas':todas},ensure_ascii=False)}", flush=True)
