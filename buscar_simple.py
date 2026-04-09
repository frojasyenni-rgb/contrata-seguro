#!/usr/bin/env python3
"""
CONTRATA SEGURO - Buscador de antecedentes laborales
SCBA (46 tribunales) + PJN Capital Federal con deteccion de CAPTCHA
"""
import requests
from bs4 import BeautifulSoup
import json, time, sys, re

SCBA_USUARIO = "Azul2205"
SCBA_PASSWORD = "Indiabeagle2205"

NOMBRE = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "MOSTEYRO"
PARTES = NOMBRE.upper().split()
APELLIDO = PARTES[0]
FILTRAR_POR = PARTES

SCBA_JURISDICCIONES = [
    ("San Isidro", "24", [("GAM681","TT1"),("GAM682","TT2"),("GAM683","TT3"),("GAM1096","TT4"),("GAM1097","TT5"),("GAM1098","TT6"),("GAM2133","TT7 Pilar")]),
    ("La Plata", "6", [("GAM301 ","TT1"),("GAM302 ","TT2"),("GAM303 ","TT3"),("GAM304 ","TT4"),("GAM1048 ","TT5")]),
    ("Lomas de Zamora","16", [("GAM363 ","TT1"),("GAM364 ","TT2"),("GAM365 ","TT3"),("GAM366 ","TT4"),("GAM1053 ","TT5")]),
    ("Quilmes", "23", [("GAM611 ","TT1"),("GAM612 ","TT2"),("GAM613 ","TT3"),("GAM1051 ","TT4"),("GAM1052 ","TT5"),("GAM2976 ","TT1 Varela")]),
    ("Avellaneda", "80", [("GAM101 ","TT1"),("GAM102 ","TT2"),("GAM103 ","TT3"),("GAM104 ","TT4"),("GAM2010 ","TT5")]),
    ("San Martin", "25", [("GAM461 ","TT1"),("GAM462 ","TT2"),("GAM463 ","TT3"),("GAM1049 ","TT4"),("GAM1050 ","TT5"),("GAM2134 ","TT6")]),
    ("Moron", "19", [("GAM381 ","TT1"),("GAM382 ","TT2"),("GAM383 ","TT3"),("GAM384 ","TT4"),("GAM1046 ","TT5"),("GAM1047 ","TT6")]),
    ("La Matanza", "14", [("GAM341 ","TT1"),("GAM342 ","TT2"),("GAM343 ","TT3"),("GAM344 ","TT4"),("GAM1044 ","TT5"),("GAM1045 ","TT6")]),
]

BASE_SCBA = "https://mev.scba.gov.ar"
BASE_PJN  = "https://scw.pjn.gov.ar"
HDR = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
       "Accept":"text/html,application/xhtml+xml,*/*;q=0.8","Accept-Language":"es-AR,es;q=0.9"}

# Senales que indican que el PJN rechazo la consulta por verificador no resuelto
PJN_CAPTCHA_SIGNALS = [
    "campo verificador",
    "fcMsg",
    "VER DESAFIO",
    "VER DESAFIO",
    "resuelva el desafio",
    "Presione el bot",
    "verificador",
]

def pjn_tiene_captcha(html):
    html_lower = html.lower()
    return any(s.lower() in html_lower for s in PJN_CAPTCHA_SIGNALS)

def progreso(pct, texto, detalle="", causas=0):
    msg = {"pct": pct, "texto": texto, "detalle": detalle, "causas": causas}
    print("PROGRESO:" + json.dumps(msg, ensure_ascii=False), flush=True)

def parsear_causas(html, tribunal, depto, fuente):
    if "Total Expedientes" not in html:
        return []
    causas = []
    soup = BeautifulSoup(html, "lxml")
    filas = soup.find_all("tr")
    i = 0
    while i < len(filas):
        fila = filas[i]
        link = fila.find("a")
        if not link:
            i += 1; continue
        caratula = link.get_text(strip=True)
        if len(caratula) < 10 or any(x in caratula for x in ["Ayuda","Sets","Perfil","Buscar","Jurisdic","UsuarioMEV","Nueva Busqueda"]):
            i += 1; continue
        celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
        estado = ""; expediente = ""; fecha = ""; ultima = ""
        if i + 1 < len(filas):
            sig_celdas = [c.get_text(" ", strip=True) for c in filas[i+1].find_all("td")]
            if len(sig_celdas) >= 2:
                for cel in sig_celdas:
                    if re.match(r'\d{2}/\d{2}/\d{4}', cel):
                        if not fecha: fecha = cel
                    elif re.match(r'[A-Z]{2}\s*-\s*\d+', cel):
                        expediente = cel
                    elif cel in ["A DESPACHO","EN ARCHIVO","ARCHIVADO","PARALIZADO","ACTIVO"]:
                        estado = cel
                    elif len(cel) > 10 and "-" in cel and not ultima:
                        ultima = cel
        if not expediente and len(celdas) > 1:
            estado     = celdas[1] if len(celdas)>1 else ""
            expediente = celdas[2] if len(celdas)>2 else ""
            fecha      = celdas[4] if len(celdas)>4 else ""
            ultima     = celdas[5] if len(celdas)>5 else ""
        causas.append({"caratula":caratula,"estado":estado,"expediente":expediente,
                       "fecha_inicio":fecha,"ultima_actuacion":ultima,
                       "juzgado":f"{tribunal} - {depto}","fuente":fuente})
        i += 2
    if len(FILTRAR_POR) > 1:
        causas = [c for c in causas if all(p in c["caratula"] for p in FILTRAR_POR)]
    return causas

def buscar_scba():
    progreso(2, "SCBA - Iniciando sesion...", "Provincia de Buenos Aires")
    s = requests.Session()
    s.headers.update(HDR)
    try:
        s.get(f"{BASE_SCBA}/loguin.asp", timeout=15)
        r = s.post(f"{BASE_SCBA}/loguin.asp?familiadepto=",
                   data={"usuario":SCBA_USUARIO,"clave":SCBA_PASSWORD,
                         "DeptoRegistrado":"aa","Submit1":"Ingresar"},
                   timeout=15, allow_redirects=True)
        r.encoding = "latin-1"
        if "Usuario apto" not in r.text and "UsuarioMEV" not in r.text:
            progreso(3, "SCBA - Error de login", "No se pudo acceder")
            return []
    except Exception as e:
        progreso(3, "SCBA - Error de conexion", str(e))
        return []

    progreso(3, "SCBA - Sesion iniciada", "Consultando 46 tribunales de trabajo...")
    causas_scba = []
    total_tt = sum(len(tts) for _,_,tts in SCBA_JURISDICCIONES)
    consultados = 0

    for depto, valor, tribunales in SCBA_JURISDICCIONES:
        try:
            s.post(f"{BASE_SCBA}/POSloguin.asp",
                   data={"TipoDto":"CC","DtoJudElegido":valor,"Aceptar":"Aceptar"},
                   timeout=15, allow_redirects=True)
        except Exception:
            pass
        time.sleep(0.3)
        for gam, nombre_tt in tribunales:
            consultados += 1
            pct = int(3 + (consultados / total_tt) * 60)
            progreso(pct, f"SCBA — {depto}",
                     f"Tribunal del Trabajo {nombre_tt} ({consultados}/{total_tt})",
                     len(causas_scba))
            try:
                s.get(f"{BASE_SCBA}/Busqueda.asp", timeout=15)
                r_bus = s.post(f"{BASE_SCBA}/Busqueda.asp", data={
                    "OpcionBusqueda":"","busca":"","JuzgadoElegido":gam,
                    "radio":"xCa","caratula":APELLIDO,
                    "NCausa":"","NInterno":"","Set":"","SetNovedades":"","Desde":"","Hasta":"",
                    "TipoCausa":"Am","Buscar":"Buscar",
                }, timeout=25)
                r_bus.encoding = "latin-1"
                html = r_bus.text
                if "Total Expedientes" not in html and "No arroja" not in html:
                    if "MuestraCausas" in r_bus.url:
                        r2 = s.get(r_bus.url, timeout=10)
                        r2.encoding = "latin-1"
                        html = r2.text
                nuevas = parsear_causas(html, nombre_tt, depto, "SCBA")
                if nuevas:
                    causas_scba.extend(nuevas)
                    progreso(pct, f"SCBA — {depto}",
                             f"TT {nombre_tt}: {len(nuevas)} causa(s) ★", len(causas_scba))
            except Exception:
                pass
            time.sleep(0.3)

    progreso(65, "SCBA completado", f"{len(causas_scba)} causa(s)", len(causas_scba))
    return causas_scba

def buscar_pjn():
    """
    Retorna (causas, estado)
    estado: 'ok' | 'sin_resultados' | 'captcha_required' | 'error'
    """
    try:
        progreso(66, "Capital Federal — Conectando...", "Verificando acceso al sistema judicial")
        s = requests.Session()
        s.headers.update(HDR)
        r_test = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
        r_test.encoding = "utf-8"

        # Detectar CAPTCHA en la pagina inicial
        if pjn_tiene_captcha(r_test.text):
            progreso(70, "Capital Federal — Validacion requerida",
                     "Se requiere validacion manual. Registrando para proceso interno...")
            return [], "captcha_required"

        soup_test = BeautifulSoup(r_test.text, "lxml")
        vs_test = soup_test.find("input", {"name":"javax.faces.ViewState"})
        if not vs_test:
            progreso(70, "Capital Federal — Sin acceso", "Sistema en mantenimiento")
            return [], "error"

        progreso(67, "Capital Federal — Conectado", "Consultando Camara Nacional del Trabajo...")
    except Exception as e:
        progreso(70, "Capital Federal — Error", str(e))
        return [], "error"

    causas_pjn = []
    camaras = [
        ("7", "Camara Nacional del Trabajo"),
        ("5", "Camara Fed. Seguridad Social"),
    ]

    for idx, (cam_val, cam_nombre) in enumerate(camaras):
        pct_base = 68 + idx * 12
        progreso(pct_base, f"Capital Federal — {cam_nombre}",
                 f"Buscando '{APELLIDO}' como actor y demandado...")
        try:
            r0 = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
            r0.encoding = "utf-8"
            soup0 = BeautifulSoup(r0.text, "lxml")
            vs = soup0.find("input", {"name":"javax.faces.ViewState"})
            if not vs:
                continue

            r1 = s.post(f"{BASE_PJN}/scw/home.seam", timeout=25, data={
                "javax.faces.ViewState": vs.get("value",""),
                "formPublica": "formPublica",
                "formPublica:camaraPartes": cam_val,
                "formPublica:nomIntervParte": APELLIDO,
                "formPublica:tipoInterv": "",
                "formPublica:buscarPorParteButton": "Buscar",
            }, headers={"Content-Type":"application/x-www-form-urlencoded"})
            r1.encoding = "utf-8"

            # DETECCION CLAVE: verificar si el PJN rechazo por verificador
            if pjn_tiene_captcha(r1.text):
                progreso(pct_base+2, f"Capital Federal — {cam_nombre}",
                         "Requiere validacion manual. Registrando...")
                return causas_pjn, "captcha_required"

            soup1 = BeautifulSoup(r1.text, "lxml")
            tabla = next((t for t in soup1.find_all("table")
                          if any(x in t.get_text().lower() for x in ["expediente","caratula","car\u00e1tula"])), None)
            if not tabla:
                progreso(pct_base+4, f"Capital Federal — {cam_nombre}", "Sin resultados")
                continue

            nuevas = []
            for fila in tabla.find_all("tr")[1:]:
                celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
                if len(celdas) < 2 or len(celdas[0]) < 5: continue
                caratula = celdas[0]
                cn = caratula.upper()
                sep = cn.find(" C/ ")
                actor_parte = cn[:sep] if sep >= 0 else ""
                rol = "ACTOR" if (actor_parte and all(re.search(r"\b" + re.escape(p) + r"\b", actor_parte) for p in FILTRAR_POR)) else "DEMANDADO"
                nuevas.append({
                    "caratula":         caratula,
                    "expediente":       celdas[1] if len(celdas)>1 else "",
                    "juzgado":          celdas[2] if len(celdas)>2 else "",
                    "fecha_inicio":     celdas[3] if len(celdas)>3 else "",
                    "ultima_actuacion": celdas[4] if len(celdas)>4 else "",
                    "estado":           celdas[5] if len(celdas)>5 else "",
                    "fuente":           "PJN",
                    "rol":              rol,
                })
            causas_pjn.extend(nuevas)
            progreso(pct_base+5, f"Capital Federal — {cam_nombre}",
                     f"{len(nuevas)} causa(s) encontrada(s)", len(causas_pjn))
        except Exception as e:
            progreso(pct_base+2, f"Capital Federal — {cam_nombre}", f"Error: {e}")
        time.sleep(1)

    estado = "ok" if causas_pjn else "sin_resultados"
    progreso(92, "Capital Federal completado", f"{len(causas_pjn)} causa(s)", len(causas_pjn))
    return causas_pjn, estado

# ── MAIN ──────────────────────────────
progreso(1, "Iniciando busqueda...", "Preparando consulta...")
causas_scba = buscar_scba()
causas_pjn, estado_pjn = buscar_pjn()
causas_total = causas_scba + causas_pjn
progreso(98, "Finalizando...", f"Total: {len(causas_total)} causa(s)")

resultado = {
    "nombre":      NOMBRE.upper(),
    "total":       len(causas_total),
    "causas_scba": len(causas_scba),
    "causas_pjn":  len(causas_pjn),
    "estado_pjn":  estado_pjn,
    "causas":      causas_total,
}
with open("resultado.json", "w", encoding="utf-8") as f:
    json.dump(resultado, f, ensure_ascii=False, indent=2)
progreso(100, "Busqueda completada", f"{len(causas_total)} causa(s)", len(causas_total))
print("RESULTADO:" + json.dumps(resultado, ensure_ascii=False), flush=True)
