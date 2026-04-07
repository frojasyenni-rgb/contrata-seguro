def buscar_pjn():
    print("\n" + "═"*55)
    print(" [2/2] PJN — Capital Federal")
    print("═"*55)
    s = requests.Session()
    s.headers.update(HDR)
    causas_pjn = []

    # Verificar acceso básico (sin cortar por CAPTCHA)
    try:
        r_test = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
        r_test.encoding = "utf-8"
        soup_test = BeautifulSoup(r_test.text, "lxml")
        vs_test = soup_test.find("input", {"name":"javax.faces.ViewState"})
        if not vs_test:
            print(" PJN: sin ViewState — mantenimiento o bloqueado")
            return []
        print(" PJN: acceso OK, consultando cámaras...")
    except Exception as e:
        print(f" PJN: error de conexión — {e}")
        return []

    for cam_val, cam_nombre in [("7","Cámara Nacional del Trabajo"), ("5","Cámara Fed. Seg. Social")]:
        print(f"  Consultando {cam_nombre}...", end=" ", flush=True)
        try:
            r0 = s.get(f"{BASE_PJN}/scw/home.seam", timeout=8)
            r0.encoding = "utf-8"
            soup0 = BeautifulSoup(r0.text, "lxml")
            vs = soup0.find("input", {"name":"javax.faces.ViewState"})
            if not vs:
                print("sin ViewState")
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
            soup1 = BeautifulSoup(r1.text, "lxml")
            tabla = next((t for t in soup1.find_all("table") if any(x in t.get_text().lower() for x in ["expediente","carátula","caratula"])), None)
            if not tabla:
                print("sin resultados"); continue
            nuevas = []
            for fila in tabla.find_all("tr")[1:]:
                celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
                if len(celdas)<2 or len(celdas[0])<5: continue
                nuevas.append({
                    "caratula": celdas[0],
                    "expediente": celdas[1] if len(celdas)>1 else "",
                    "juzgado": celdas[2] if len(celdas)>2 else "",
                    "fecha_inicio": celdas[3] if len(celdas)>3 else "",
                    "ultima_actuacion": celdas[4] if len(celdas)>4 else "",
                    "estado": celdas[5] if len(celdas)>5 else "",
                    "fuente": "PJN",
                })
            causas_pjn.extend(nuevas)
            print(f"OK ({len(nuevas)} causas)")
        except Exception as e:
            print(f"error: {e}")
        time.sleep(1)

    print(f"\n  PJN total: {len(causas_pjn)} causa(s)")
    return causas_pjn
