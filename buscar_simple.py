#!/usr/bin/env python3
"""
CONTRATA SEGURO — Buscador de antecedentes laborales
Busca en SCBA (46 tribunales, 8 departamentos) + PJN Capital Federal
Uso: python buscar_simple.py APELLIDO [NOMBRE]
"""
import requests
from bs4 import BeautifulSoup
import json, time, sys

SCBA_USUARIO  = "Azul2205"
SCBA_PASSWORD = "Indiabeagle2205"
NOMBRE        = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "MOSTEYRO"
PARTES        = NOMBRE.upper().split()
APELLIDO      = PARTES[0]   # Solo apellido para buscar en SCBA
FILTRAR_POR   = PARTES      # Todas las palabras deben estar en la carátula

SCBA_JURISDICCIONES = [
    ("San Isidro",     "24", [("GAM681","TT1"),("GAM682","TT2"),("GAM683","TT3"),("GAM1096","TT4"),("GAM1097","TT5"),("GAM1098","TT6"),("GAM2133","TT7 Pilar")]),
    ("La Plata",       "6",  [("GAM301  ","TT1"),("GAM302  ","TT2"),("GAM303  ","TT3"),("GAM304  ","TT4"),("GAM1048 ","TT5")]),
    ("Lomas de Zamora","16", [("GAM363  ","TT1"),("GAM364  ","TT2"),("GAM365  ","TT3"),("GAM366  ","TT4"),("GAM1053 ","TT5")]),
    ("Quilmes",        "23", [("GAM611  ","TT1"),("GAM612  ","TT2"),("GAM613  ","TT3"),("GAM1051 ","TT4"),("GAM1052 ","TT5"),("GAM2976 ","TT1 Varela")]),
    ("Avellaneda",     "80", [("GAM101  ","TT1"),("GAM102  ","TT2"),("GAM103  ","TT3"),("GAM104  ","TT4"),("GAM2010 ","TT5")]),
    ("San Martin",     "25", [("GAM461  ","TT1"),("GAM462  ","TT2"),("GAM463  ","TT3"),("GAM1049 ","TT4"),("GAM1050 ","TT5"),("GAM2134 ","TT6")]),
    ("Moron",          "19", [("GAM381  ","TT1"),("GAM382  ","TT2"),("GAM383  ","TT3"),("GAM384  ","TT4"),("GAM1046 ","TT5"),("GAM1047 ","TT6")]),
    ("La Matanza",     "14", [("GAM341  ","TT1"),("GAM342  ","TT2"),("GAM343  ","TT3"),("GAM344  ","TT4"),("GAM1044 ","TT5"),("GAM1045 ","TT6")]),
]

BASE_SCBA = "https://mev.scba.gov.ar"
BASE_PJN  = "https://scw.pjn.gov.ar"
HDR = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
       "Accept":"text/html,application/xhtml+xml,*/*;q=0.8","Accept-Language":"es-AR,es;q=0.9"}

def parsear_causas(html, tribunal, depto, fuente):
    if "Total Expedientes" not in html:
                return []
    causas = []
    soup = BeautifulSoup(html, "lxml")

    # El SCBA muestra los resultados en una tabla donde:
    # La carátula está en el link de la primera fila del resultado
    # La última actuación y expediente están en la fila siguiente
    # Buscamos el patrón: fila con checkbox + link (carátula) seguida de fila con detalles

    filas = soup.find_all("tr")
    i = 0
    while i < len(filas):
        fila = filas[i]
        link = fila.find("a")
        if not link:
            i += 1
            continue

        caratula = link.get_text(strip=True)
        # Filtrar links de navegación
        if len(caratula) < 10 or any(x in caratula for x in
            ["Ayuda","Sets","Perfil","Buscar","Jurisdic","UsuarioMEV","Nueva Búsqueda"]):
            i += 1
            continue

        celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]

        # Leer estado y expediente de las celdas de esta misma fila
        estado    = ""
        expediente = ""
        fecha     = ""
        ultima    = ""

        # Buscar si la siguiente fila tiene los detalles (última actuación, expediente)
        if i + 1 < len(filas):
            sig_celdas = [c.get_text(" ", strip=True) for c in filas[i+1].find_all("td")]
            # Patrón típico SCBA: [estado, expediente, expediente2, fecha, ultima_actuacion]
            if len(sig_celdas) >= 2:
                # Detectar si la celda parece una fecha (formato DD/MM/YYYY)
                import re
                for cel in sig_celdas:
                    if re.match(r'\d{2}/\d{2}/\d{4}', cel):
                        if not fecha: fecha = cel
                    elif re.match(r'[A-Z]{2}\s*-\s*\d+', cel):
                        expediente = cel
                    elif cel in ["A DESPACHO","EN ARCHIVO","ARCHIVADO","PARALIZADO","ACTIVO"]:
                        estado = cel
                    elif len(cel) > 10 and "-" in cel and not ultima:
                        ultima = cel

        # Si no encontramos bien en la siguiente fila, leer de las celdas actuales
        if not expediente and len(celdas) > 1:
            estado     = celdas[1] if len(celdas)>1 else ""
            expediente = celdas[2] if len(celdas)>2 else ""
            fecha      = celdas[4] if len(celdas)>4 else ""
            ultima     = celdas[5] if len(celdas)>5 else ""

        causas.append({
            "caratula":         caratula,
            "estado":           estado,
            "expediente":       expediente,
            "fecha_inicio":     fecha,
            "ultima_actuacion": ultima,
            "juzgado":          f"{tribunal} — {depto}",
            "fuente":           fuente,
        })
        i += 2  # saltar la fila de detalles

    # Filtrar por nombre completo si se pasaron varias palabras
    if len(FILTRAR_POR) > 1:
        causas = [c for c in causas if all(p in c["caratula"] for p in FILTRAR_POR)]

    return causas

# ══════════════════════════════════════════
# PARTE 1: SCBA
# ══════════════════════════════════════════
def buscar_scba():
    print("\n" + "═"*55)
    print("  [1/2] SCBA — Provincia de Buenos Aires")
    print("═"*55)
    s = requests.Session()
    s.headers.update(HDR)

    print("  Iniciando sesión...", end=" ", flush=True)
    s.get(f"{BASE_SCBA}/loguin.asp", timeout=15)
    r = s.post(f"{BASE_SCBA}/loguin.asp?familiadepto=",
        data={"usuario":SCBA_USUARIO,"clave":SCBA_PASSWORD,"DeptoRegistrado":"aa","Submit1":"Ingresar"},
        timeout=15, allow_redirects=True)
    r.encoding = "latin-1"
    if "Usuario apto" not in r.text and "UsuarioMEV" not in r.text:
        print("ERROR — Login fallido"); return []
    print("OK ✓")

    causas_scba = []
    total_tt = sum(len(tts) for _,_,tts in SCBA_JURISDICCIONES)
    consultados = 0

    for depto, valor, tribunales in SCBA_JURISDICCIONES:
        print(f"\n  Departamento: {depto}...", end=" ", flush=True)

        # ── CAMBIO DE JURISDICCIÓN ──────────────────────
        # Campos verificados directamente del HTML real:
        # TipoDto=CC (radio Departamento Judicial)
        # DtoJudElegido=VALOR (select con número de departamento)
        # Aceptar=Aceptar (submit)
        r_pos = s.post(f"{BASE_SCBA}/POSloguin.asp", data={
            "TipoDto":       "CC",      # Radio "Departamento Judicial" — verificado
            "DtoJudElegido": valor,     # Número del departamento — verificado
            "Aceptar":       "Aceptar", # Botón submit — verificado
        }, timeout=15, allow_redirects=True)
        r_pos.encoding = "latin-1"

        if depto.split()[0].lower() in r_pos.text.lower() or "Busqueda" in r_pos.url or "busqueda" in r_pos.url.lower():
            print("OK ✓")
        else:
            print("(verificando...)")
        time.sleep(0.5)

        # ── BUSCAR EN CADA TRIBUNAL ─────────────────────
        for gam, nombre_tt in tribunales:
            consultados += 1
            pct = int((consultados/total_tt)*100)
            print(f"    [{pct:3d}%] {nombre_tt} {depto:<20}", end=" ", flush=True)

            s.get(f"{BASE_SCBA}/Busqueda.asp", timeout=15)
            r_bus = s.post(f"{BASE_SCBA}/Busqueda.asp", data={
                "OpcionBusqueda":"","busca":"",
                "JuzgadoElegido": gam,
                "radio":          "xCa",
                "caratula":       APELLIDO,
                "NCausa":"","NInterno":"","Set":"","SetNovedades":"","Desde":"","Hasta":"",
                "TipoCausa":      "Am",
                "Buscar":         "Buscar",
            }, timeout=20)
            r_bus.encoding = "latin-1"
            html = r_bus.text

            # Seguir redirección si hay resultados
            if "Total Expedientes" not in html and "No arroja" not in html:
                if "MuestraCausas" in r_bus.url:
                    r2 = s.get(r_bus.url, timeout=10)
                    r2.encoding = "latin-1"
                    html = r2.text

            nuevas = parsear_causas(html, nombre_tt, depto, "SCBA")
            if nuevas:
                causas_scba.extend(nuevas)
                print(f"★ {len(nuevas)} CAUSA(S)")
            elif "No arroja" in html or "no existe" in html.lower():
                print("sin resultados")
            else:
                print("sin datos")

            time.sleep(0.4)

    print(f"\n  SCBA total: {len(causas_scba)} causa(s)")
    return causas_scba

# ══════════════════════════════════════════
# PARTE 2: PJN — CAPITAL FEDERAL
# ══════════════════════════════════════════
def buscar_pjn():
    print("\n" + "═"*55)
    print("  [2/2] PJN — Capital Federal")
    print("═"*55)
    s = requests.Session()
    s.headers.update(HDR)
    causas_pjn = []

    # ── Verificar CAPTCHA ANTES del loop ──────────────────
    try:
        r_test = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
        r_test.encoding = "utf-8"
        if "VER DESAFÍO" in r_test.text or "desafio" in r_test.text.lower() or "challenge" in r_test.text.lower():
            print("  PJN: CAPTCHA activo — no disponible temporalmente")
            return []
        soup_test = BeautifulSoup(r_test.text, "lxml")
        vs_test = soup_test.find("input", {"name":"javax.faces.ViewState"})
        if not vs_test:
            print("  PJN: sin ViewState — CAPTCHA o mantenimiento")
            return []
        print("  PJN: acceso OK, consultando cámaras...")
    except Exception as e:
        print(f"  PJN: error de conexión — {e}")
        return []

    for cam_val, cam_nombre in [("7","Cámara Nacional del Trabajo"), ("5","Cámara Fed. Seg. Social")]:
        print(f"  Consultando {cam_nombre}...", end=" ", flush=True)
        try:
            r0 = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
            r0.encoding = "utf-8"
            if "VER DESAFÍO" in r0.text or "desafio" in r0.text.lower():
                print("CAPTCHA")
                return []
            soup0 = BeautifulSoup(r0.text, "lxml")
            vs = soup0.find("input", {"name":"javax.faces.ViewState"})
            if not vs:
                print("sin ViewState")
                return []

            r1 = s.post(f"{BASE_PJN}/scw/home.seam", timeout=25, data={
                "javax.faces.ViewState":    vs.get("value",""),
                "formPublica":              "formPublica",
                "formPublica:camaraPartes": cam_val,
                "formPublica:nomIntervParte": APELLIDO,
                "formPublica:tipoInterv":   "",
                "formPublica:buscarPorParteButton": "Buscar",
            }, headers={"Content-Type":"application/x-www-form-urlencoded"})
            r1.encoding = "utf-8"

            soup1 = BeautifulSoup(r1.text, "lxml")
            tabla = next((t for t in soup1.find_all("table")
                         if any(x in t.get_text().lower() for x in ["expediente","carátula","caratula"])), None)

            if not tabla:
                print("sin resultados"); continue

            nuevas = []
            for fila in tabla.find_all("tr")[1:]:
                celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
                if len(celdas)<2 or len(celdas[0])<5: continue
                nuevas.append({
                    "caratula":         celdas[0],
                    "expediente":       celdas[1] if len(celdas)>1 else "",
                    "juzgado":          celdas[2] if len(celdas)>2 else "",
                    "fecha_inicio":     celdas[3] if len(celdas)>3 else "",
                    "ultima_actuacion": celdas[4] if len(celdas)>4 else "",
                    "estado":           celdas[5] if len(celdas)>5 else "",
                    "fuente":           "PJN",
                })
            causas_pjn.extend(nuevas)
            print(f"OK ({len(nuevas)} causas)")

        except Exception as e:
            print(f"error: {e}")
            time.sleep(1)

    print(f"\n  PJN total: {len(causas_pjn)} causa(s)")
    return causas_pjn
# ══════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════
print(f"""
╔══════════════════════════════════════════════════════╗
║         CONTRATA SEGURO — Búsqueda Judicial          ║
╠══════════════════════════════════════════════════════╣
║  Persona: {NOMBRE:<42}║
╚══════════════════════════════════════════════════════╝""")

causas_scba = buscar_scba()
causas_pjn  = buscar_pjn()
causas_total = causas_scba + causas_pjn

print("\n" + "═"*55)
print(f"  RESULTADO FINAL — {NOMBRE}")
print("═"*55)
print(f"  SCBA (Prov. Bs.As.):  {len(causas_scba)} causa(s)")
print(f"  PJN (Capital Fed.):   {len(causas_pjn)} causa(s)")
print(f"  TOTAL:                {len(causas_total)} causa(s)")

if causas_total:
    print(f"  Riesgo: {'ALTO' if len(causas_total)>=3 else 'MEDIO'}")
    print("\n  CAUSAS:")
    for i,c in enumerate(causas_total,1):
        print(f"\n  [{i}] {c['caratula']}")
        for k,v in [("Expediente",c.get("expediente")),("Juzgado",c.get("juzgado")),
                    ("Estado",c.get("estado")),("Inicio",c.get("fecha_inicio")),
                    ("Últ.act.",c.get("ultima_actuacion")),("Fuente",c.get("fuente"))]:
            if v: print(f"       {k+':':<12} {v}")
else:
    print("\n  ✓ Sin antecedentes laborales en SCBA ni PJN.")

with open("resultado.json","w",encoding="utf-8") as f:
    json.dump({"nombre":NOMBRE,"total":len(causas_total),"causas":causas_total},f,ensure_ascii=False,indent=2)
print(f"\n  Guardado en: resultado.json")
print("═"*55)
