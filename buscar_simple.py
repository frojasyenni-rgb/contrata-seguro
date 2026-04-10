#!/usr/bin/env python3
"""CONTRATA SEGURO - v3.1 - Busqueda de antecedentes laborales SCBA+PJN con validacion DNI/CUIL"""
import requests, os
from bs4 import BeautifulSoup
import json, time, sys, re

SCBA_USUARIO = os.environ.get("SCBA_USUARIO", "Azul2205")
SCBA_PASSWORD = os.environ.get("SCBA_PASSWORD", "Indiabeagle2205")

NOMBRE      = sys.argv[1] if len(sys.argv) > 1 else "MOSTEYRO"
DNI_CUIL    = sys.argv[2] if len(sys.argv) > 2 else ""

def normalizar_dni(v):
    if not v: return ""
    d = re.sub(r'\D', '', v)
    if len(d) == 11: return d[2:10].lstrip('0') or d[2:10]
    if 7 <= len(d) <= 8: return d.lstrip('0') or d
    return ""

DNI_BUSCADO  = normalizar_dni(DNI_CUIL)
PARTES       = NOMBRE.upper().split()
APELLIDO     = PARTES[0]
FILTRAR_POR  = PARTES

SCBA_JURISDICCIONES = [
    ("San Isidro",    "24", [("GAM681","TT1"),("GAM682","TT2"),("GAM683","TT3"),("GAM1096","TT4"),("GAM1097","TT5"),("GAM1098","TT6"),("GAM2133","TT7 Pilar")]),
    ("La Plata",      "6",  [("GAM301 ","TT1"),("GAM302 ","TT2"),("GAM303 ","TT3"),("GAM304 ","TT4"),("GAM1048 ","TT5")]),
    ("Lomas de Zamora","16",[("GAM363 ","TT1"),("GAM364 ","TT2"),("GAM365 ","TT3"),("GAM366 ","TT4"),("GAM1053 ","TT5")]),
    ("Quilmes",       "23", [("GAM611 ","TT1"),("GAM612 ","TT2"),("GAM613 ","TT3"),("GAM1051 ","TT4"),("GAM1052 ","TT5"),("GAM2976 ","TT1 Varela")]),
    ("Avellaneda",    "80", [("GAM101 ","TT1"),("GAM102 ","TT2"),("GAM103 ","TT3"),("GAM1049 ","TT4"),("GAM2010 ","TT5")]),
    ("San Martin",    "25", [("GAM461 ","TT1"),("GAM462 ","TT2"),("GAM463 ","TT3"),("GAM1054 ","TT4"),("GAM1055 ","TT5"),("GAM2134 ","TT6")]),
    ("Moron",         "19", [("GAM381 ","TT1"),("GAM382 ","TT2"),("GAM383 ","TT3"),("GAM1046 ","TT4"),("GAM1047 ","TT5"),("GAM2135 ","TT6")]),
    ("La Matanza",    "14", [("GAM341 ","TT1"),("GAM342 ","TT2"),("GAM343 ","TT3"),("GAM1044 ","TT4"),("GAM1045 ","TT5"),("GAM2136 ","TT6")]),
]

HDR   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BASE  = "https://mev.scba.gov.ar"
TOTAL = sum(len(t) for _, _, t in SCBA_JURISDICCIONES)

def progreso(pct, texto, detalle="", causas=0):
    print(f"PROGRESO:{json.dumps({'pct':pct,'texto':texto,'detalle':detalle,'causas':causas})}", flush=True)

def es_actor(actor_parte):
    return bool(actor_parte) and all(
        re.search(r"\b" + re.escape(p) + r"\b", actor_parte)
        for p in FILTRAR_POR
    )

def extraer_nids(html):
    return [(m.group(1), m.group(2).strip())
            for m in re.finditer(r'nidCausa=(\d+)[^"]*pidJuzgado=(GAM[\d\s]+)', html, re.IGNORECASE)]

def buscar_dni_expediente(s, nid, gam):
    try:
        r = s.get(f"{BASE}/procesales.asp", params={"nidCausa": nid, "pidJuzgado": gam}, timeout=15)
        if not r.ok: return ""
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
            dni = re.sub(r"[\.\s]", "", raw).lstrip("0")
            if 7 <= len(re.sub(r"[\.\s]", "", raw)) <= 8:
                return dni
        return ""
    except Exception:
        return ""

def validar_dni(encontrado):
    if not DNI_BUSCADO:   return "no_validado"
    if not encontrado:    return "no_encontrado"
    return "coincide" if encontrado.lstrip("0") == DNI_BUSCADO.lstrip("0") else "no_coincide"

def parsear_causas(html, depto, tribunal, gam):
    soup  = BeautifulSoup(html, "html.parser")
    nids  = extraer_nids(html)
    causas, ni = [], 0
    for fila in soup.find_all("tr"):
        celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
        if len(celdas) < 2 or len(celdas[0]) < 5: continue
        caratula    = celdas[0]
        cn          = caratula.upper()
        sep         = cn.find(" C/ ")
        actor_parte = cn[:sep] if sep >= 0 else ""
        rol         = "ACTOR" if es_actor(actor_parte) else "DEMANDADO"
        nid, g      = nids[ni] if ni < len(nids) else ("", gam.strip())
        causas.append({
            "caratula":         caratula,
            "expediente":       celdas[1] if len(celdas) > 1 else "",
            "juzgado":          f"{tribunal} - {depto}",
            "fecha_inicio":     celdas[3] if len(celdas) > 3 else "",
            "ultima_actuacion": celdas[4] if len(celdas) > 4 else "",
            "estado":           celdas[5] if len(celdas) > 5 else "",
            "fuente":           "SCBA",
            "rol":              rol,
            "dni_actor":        "",
            "dni_validacion":   "no_validado",
            "_nid":             nid,
            "_gam":             g,
        })
        ni += 1
    if len(FILTRAR_POR) > 1:
        causas = [c for c in causas if all(
            re.search(r"\b" + re.escape(p) + r"\b", c["caratula"].upper())
            for p in FILTRAR_POR
        )]
    return causas

def buscar_scba():
    progreso(2, "SCBA -- Iniciando sesion...", "Provincia de Buenos Aires")
    s = requests.Session()
    s.headers.update(HDR)
    try:
        s.post(f"{BASE}/loguin.asp", data={"UsuarioBase": SCBA_USUARIO, "PasswordBase": SCBA_PASSWORD}, timeout=15)
    except Exception as e:
        progreso(2, "Error de conexion SCBA", str(e)); return []
    todas, procesados = [], 0
    for depto, dep_id, tribunales in SCBA_JURISDICCIONES:
        try: s.post(f"{BASE}/POSloguin.asp", data={"pidDepartamento": dep_id}, timeout=10)
        except Exception: pass
        for gam, tribunal in tribunales:
            procesados += 1
            pct = int((procesados / TOTAL) * 83) + 2
            progreso(pct, f"SCBA -- {depto}", f"{tribunal} ({procesados}/{TOTAL})", len(todas))
            try:
                r = s.post(f"{BASE}/Busqueda.asp", data={"Caratula": APELLIDO, "TipoCausa": "Am", "pidJuzgado": gam}, timeout=20)
                todas.extend(parsear_causas(r.text, depto, tribunal, gam))
                time.sleep(0.2)
            except Exception: pass
    actores = [c for c in todas if c["rol"] == "ACTOR" and c["_nid"]]
    if actores:
        progreso(86, f"Verificando DNI ({len(actores)} como actor)...", "", len(todas))
        for i, c in enumerate(actores):
            progreso(86 + int((i / len(actores)) * 8), f"DNI {i+1}/{len(actores)}", c["caratula"][:50], len(todas))
            dni = buscar_dni_expediente(s, c["_nid"], c["_gam"])
            c["dni_actor"]      = dni
            c["dni_validacion"] = validar_dni(dni)
            time.sleep(0.4)
    for c in todas:
        c.pop("_nid", None); c.pop("_gam", None)
    return todas

def buscar_pjn():
    BASE_PJN = "https://scw.pjn.gov.ar"
    CAPTCHA  = ["campo verificador","fcMsg","VER DESAFIO","resuelva el desafio","Presione el bot"]
    try:
        s = requests.Session(); s.headers.update(HDR)
        r = s.get(f"{BASE_PJN}/scw/home.seam", timeout=15)
        if any(c.lower() in r.text.lower() for c in CAPTCHA): return [], "captcha_required"
        soup   = BeautifulSoup(r.text, "html.parser")
        vs     = soup.find("input", {"name": "javax.faces.ViewState"})
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
                    cn  = celdas[0].upper(); sep = cn.find(" C/ ")
                    actor_parte = cn[:sep] if sep >= 0 else ""
                    causas.append({
                        "caratula":         celdas[0],
                        "expediente":       celdas[1] if len(celdas) > 1 else "",
                        "juzgado":          celdas[2] if len(celdas) > 2 else nombre,
                        "fecha_inicio":     celdas[3] if len(celdas) > 3 else "",
                        "ultima_actuacion": celdas[4] if len(celdas) > 4 else "",
                        "estado":           celdas[5] if len(celdas) > 5 else "",
                        "fuente":           "PJN",
                        "rol":              "ACTOR" if es_actor(actor_parte) else "DEMANDADO",
                        "dni_actor":        "", "dni_validacion": "no_validado",
                    })
            except Exception: continue
        return causas, "ok"
    except Exception:
        return [], "error"

scba = buscar_scba()
progreso(88, "PJN -- Capital Federal", "Camara Nacional del Trabajo", len(scba))
pjn, estado_pjn = buscar_pjn()
todas = scba + pjn
progreso(100, "Busqueda completada", f"{len(todas)} causa(s) encontrada(s)", len(todas))
print(f"RESULTADO:{json.dumps({'nombre':NOMBRE,'dni_buscado':DNI_CUIL or None,'total':len(todas),'causas_scba':len(scba),'causas_pjn':len(pjn),'estado_pjn':estado_pjn,'causas':todas},ensure_ascii=False)}", flush=True)
