"""Genera el perfil de GitHub (README.md + svg/) a partir del portafolio publico.

    python generar.py                      # lee los datos publicados del portafolio
    python generar.py --datos ruta.json    # o un projects.json local (para probar)

Textos, reglas y numeros fijos viven en perfil.json; la estructura del README en plantilla.md.
Las imagenes salen en dos versiones (oscura y clara) y llevan incrustadas las fuentes del
portafolio (Bebas Neue y DM Mono) recortadas a los caracteres usados.
Unica dependencia: fontTools. La salida es determinista: mismos datos -> mismos archivos
(asi la tarea automatica solo guarda cambios cuando el portafolio cambia).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import re
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont

BASE = Path(__file__).resolve().parent
SALIDA = BASE / "svg"
ANCHO = 1200

ARCHIVO_FUENTE = {"display": "BebasNeue-Regular.ttf", "mono": "DMMono-Regular.ttf", "med": "DMMono-Medium.ttf"}
FAMILIA = {"display": "PDisplay", "mono": "PMono", "med": "PMonoMed"}
RESPALDO = {"display": "Impact,'Arial Narrow',sans-serif",
            "mono": "Consolas,'Courier New',monospace",
            "med": "Consolas,'Courier New',monospace"}

TEMAS = {
    "oscuro": dict(fondo="#080808", tarjeta="#0f0f0f", tarjeta2="#141414", borde="#2a2622", texto="#f0ede6",
                   tenue="#9c958c", oro="#c9a84c", oro2="#e8c97a", verde="#4ade80", ambar="#fbbf24",
                   gris="#7d766e", pista="#211f1b", punto="#c9a84c"),
    "claro": dict(fondo="#f7f5f0", tarjeta="#ffffff", tarjeta2="#fbfaf6", borde="#e2dccf", texto="#151412",
                  tenue="#5c574f", oro="#8a6b1c", oro2="#6f5514", verde="#15803d", ambar="#b45309",
                  gris="#8a847b", pista="#ece7dc", punto="#8a6b1c"),
}
COLOR_ESTATUS = {"active": "verde", "dev": "ambar", "paused": "gris", "planning": "gris"}
RANGO_PRIORIDAD = {"alta": 0, "media": 1, "baja": 2}


# ---------------------------------------------------------------------------------------------
# Fuentes: medir texto (para envolver y recortar) y recortar/incrustar como WOFF en base64
# ---------------------------------------------------------------------------------------------
class Fuente:
    def __init__(self, clave: str):
        self.ruta = BASE / "fuentes" / ARCHIVO_FUENTE[clave]
        tt = TTFont(self.ruta, recalcTimestamp=False)
        self.upm = tt["head"].unitsPerEm
        self.cmap = tt.getBestCmap()
        self.anchos = {cp: tt["hmtx"][g][0] for cp, g in self.cmap.items()}
        self.faltan: set[str] = set()

    def ancho(self, texto: str, tam: float, espaciado: float = 0.0) -> float:
        total = 0
        for ch in texto:
            w = self.anchos.get(ord(ch))
            if w is None:
                self.faltan.add(ch)
                w = self.anchos.get(ord("?"), self.upm // 2)
            total += w
        return total * tam / self.upm + espaciado * len(texto)

    def woff64(self, caracteres: set[str]) -> str:
        opciones = subset.Options()
        opciones.flavor = "woff"
        opciones.recalc_timestamp = False
        opciones.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]   # conserva autor y licencia (OFL)
        tt = TTFont(self.ruta, recalcTimestamp=False)
        sub = subset.Subsetter(options=opciones)
        sub.populate(text="".join(sorted(caracteres | {" "})))
        sub.subset(tt)
        buf = io.BytesIO()
        subset.save_font(tt, buf, opciones)
        return base64.b64encode(buf.getvalue()).decode("ascii")


FUENTES = {k: Fuente(k) for k in ARCHIVO_FUENTE}


def envolver(texto: str, clave: str, tam: float, ancho_max: float) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if FUENTES[clave].ancho(prueba, tam) <= ancho_max or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def recortar(texto: str, clave: str, tam: float, ancho_max: float, espaciado: float = 0.0) -> str:
    if FUENTES[clave].ancho(texto, tam, espaciado) <= ancho_max:
        return texto
    while texto and FUENTES[clave].ancho(texto + "…", tam, espaciado) > ancho_max:
        texto = texto[:-1]
    return texto.rstrip(" ·—-,") + "…"


def titulo_corto(titulo: str) -> str:
    return titulo.split(" — ")[0].strip()


# ---------------------------------------------------------------------------------------------
# Lienzo SVG
# ---------------------------------------------------------------------------------------------
class Lienzo:
    def __init__(self, alto: int, tema: str, titulo: str):
        self.alto, self.tema, self.c = alto, tema, TEMAS[tema]
        self.titulo = titulo
        self.partes: list[str] = []
        self.defs: list[str] = []
        self.css: list[str] = []
        self.usados: dict[str, set[str]] = {k: set() for k in ARCHIVO_FUENTE}

    def add(self, s: str) -> None:
        self.partes.append(s)

    def texto(self, x, y, s, clave="mono", tam=14, color="texto", anchor="start", espaciado=0.0,
              clase="", extra="") -> None:
        self.usados[clave].update(s)
        fill = self.c.get(color, color)
        ls = f' letter-spacing="{espaciado:g}"' if espaciado else ""
        cl = f' class="{clase}"' if clase else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FAMILIA[clave]},{RESPALDO[clave]}" '
                 f'font-size="{tam:g}" fill="{fill}" text-anchor="{anchor}"{ls}{cl}{extra}>{escape(s)}</text>')

    def tarjeta(self, x, y, w, h, r=16, relleno="tarjeta", borde="borde", extra="") -> None:
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" '
                 f'fill="{self.c[relleno]}" stroke="{self.c[borde]}" stroke-width="1"{extra}/>')

    def encabezado(self, y, titulo, sub) -> None:
        self.texto(0, y, titulo, "display", 34, "texto", espaciado=1)
        w = FUENTES["display"].ancho(titulo, 34, 1)
        self.texto(w + 16, y - 2, sub, "mono", 13, "tenue")

    def render(self) -> str:
        caras = []
        for k, chars in self.usados.items():
            if chars:
                caras.append(f"@font-face{{font-family:{FAMILIA[k]};src:url(data:font/woff;base64,"
                             f"{FUENTES[k].woff64(chars)}) format('woff');}}")
        css = "".join(caras) + "".join(self.css) + \
            "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{ANCHO}" height="{self.alto}" '
                f'viewBox="0 0 {ANCHO} {self.alto}" role="img" aria-label="{escape(self.titulo)}">'
                f"<title>{escape(self.titulo)}</title><style>{css}</style>{defs}"
                + "".join(self.partes) + "</svg>\n")


# ---------------------------------------------------------------------------------------------
# Piezas
# ---------------------------------------------------------------------------------------------
def pieza_banner(tema, perfil, resumen) -> Lienzo:
    L = Lienzo(340, tema, f"{perfil['nombre']} — {perfil['rol']}")
    c = L.c
    L.defs.append(f'<pattern id="puntos" width="22" height="22" patternUnits="userSpaceOnUse">'
                  f'<circle cx="1.5" cy="1.5" r="1.1" fill="{c["borde"]}"/></pattern>'
                  f'<radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">'
                  f'<stop offset="0" stop-color="{c["oro"]}" stop-opacity="0.22"/>'
                  f'<stop offset="1" stop-color="{c["oro"]}" stop-opacity="0"/></radialGradient>')
    L.css.append(
        ".flujo{stroke-dasharray:7 93;animation:fluye 3.6s linear infinite}"
        "@keyframes fluye{from{stroke-dashoffset:100}to{stroke-dashoffset:0}}"
        ".pulso{transform-box:fill-box;transform-origin:center;animation:pulso 3.6s ease-out infinite}"
        "@keyframes pulso{0%{transform:scale(1);opacity:.7}70%,100%{transform:scale(3.4);opacity:0}}"
        ".orbita{transform-box:view-box;transform-origin:930px 168px;animation:gira 60s linear infinite}"
        "@keyframes gira{to{transform:rotate(360deg)}}"
        ".entra{animation:entra .9s ease-out both}"
        "@keyframes entra{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}")
    L.tarjeta(0.5, 0.5, ANCHO - 1, 339, r=20, relleno="fondo")
    L.add(f'<rect x="1" y="1" width="{ANCHO - 2}" height="338" rx="20" fill="url(#puntos)" opacity="0.55"/>')
    L.add(f'<rect x="56" y="54" width="44" height="3" rx="1.5" fill="{c["oro"]}"/>')

    # Texto (izquierda)
    L.texto(112, 59, perfil["ubicacion"].upper(), "med", 12.5, "oro", espaciado=2.5, clase="entra")
    tam = 96
    while FUENTES["display"].ancho(perfil["nombre"].upper(), tam, 1) > 640:
        tam -= 2
    L.texto(54, 152, perfil["nombre"].upper(), "display", tam, "texto", espaciado=1, clase="entra")
    L.texto(56, 188, perfil["rol"], "med", 16.5, "oro2", clase="entra")
    for i, linea in enumerate(envolver(perfil["lema"], "mono", 14.5, 600)[:2]):
        L.texto(56, 222 + i * 22, linea, "mono", 14.5, "tenue", clase="entra")
    x = 56
    for area in perfil["areas"]:
        w = FUENTES["med"].ancho(area.upper(), 10.5, 1.2) + 22
        if x + w > 690:
            break
        L.add(f'<rect x="{x}" y="276" width="{w:.1f}" height="26" rx="13" fill="{c["tarjeta2"]}" '
              f'stroke="{c["borde"]}"/>')
        L.texto(x + w / 2, 293, area.upper(), "med", 10.5, "tenue", "middle", espaciado=1.2)
        x += w + 8

    # Red: CEDIS al centro y las sucursales alrededor; la mercancia "fluye" hacia cada tienda
    cx, cy, n = 930, 168, int(perfil["sucursales"])
    L.add(f'<circle cx="{cx}" cy="{cy}" r="150" fill="url(#halo)"/>')
    L.add(f'<g class="orbita"><circle cx="{cx}" cy="{cy}" r="74" fill="none" stroke="{c["borde"]}" '
          f'stroke-dasharray="2 7"/></g>')
    nodos = []
    for i in range(n):
        ang = -math.pi / 2 + i * 2 * math.pi / n + 0.09 * ((i * 7) % 3 - 1)
        r = 108 + 34 * (((i * 5) % 7) / 6)
        nodos.append((cx + r * math.cos(ang) * 1.18, cy + r * math.sin(ang) * 0.86))
    for i, (x2, y2) in enumerate(nodos):
        d = f"M{cx},{cy} L{x2:.1f},{y2:.1f}"
        L.add(f'<path d="{d}" stroke="{c["borde"]}" stroke-width="1"/>')
        L.add(f'<path d="{d}" pathLength="100" stroke="{c["oro"]}" stroke-width="2" stroke-linecap="round" '
              f'fill="none" class="flujo" style="animation-delay:-{(i * 0.37) % 3.6:.2f}s"/>')
    for i, (x2, y2) in enumerate(nodos):
        L.add(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="5" fill="none" stroke="{c["oro"]}" class="pulso" '
              f'style="animation-delay:-{(i * 0.53) % 3.6:.2f}s"/>')
        L.add(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="5" fill="{c["tarjeta2"]}" stroke="{c["oro2"]}" '
              f'stroke-width="1.5"/>')
    L.add(f'<circle cx="{cx}" cy="{cy}" r="30" fill="{c["oro"]}"/>')
    L.add(f'<circle cx="{cx}" cy="{cy}" r="30" fill="none" stroke="{c["oro"]}" class="pulso"/>')
    L.texto(cx, cy + 7, "CEDIS", "display", 20, "fondo", "middle", espaciado=1)
    L.texto(cx, 322, f"{n} SUCURSALES · {perfil['cedis']} CEDIS · {resumen['total']} SISTEMAS PROPIOS",
            "med", 11, "tenue", "middle", espaciado=2)
    return L


def pieza_impacto(tema, perfil, resumen) -> Lienzo:
    datos = [(resumen["total"], "PROYECTOS", "en el portafolio"),
             (resumen["produccion"], "EN PRODUCCIÓN", "en uso todos los días"),
             (resumen["desarrollo"], "EN DESARROLLO", "avanzando ahora"),
             (perfil["sucursales"], "SUCURSALES", f"+ {perfil['cedis']} centro de distribución")]
    alt = ", ".join(f"{v} {e.lower()}" for v, e, _ in datos)
    L = Lienzo(148, tema, f"En números: {alt}")
    L.css.append(".entra{animation:entra .8s ease-out both}"
                 "@keyframes entra{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}")
    gap, w = 16, (ANCHO - 3 * 16) / 4
    for i, (valor, etiqueta, sub) in enumerate(datos):
        x = i * (w + gap)
        L.add(f'<g class="entra" style="animation-delay:{i * 0.12:.2f}s">')
        L.tarjeta(x + 0.5, 0.5, w - 1, 146, r=16)
        L.add(f'<rect x="{x + 24}" y="24" width="28" height="3" rx="1.5" fill="{L.c["oro"]}"/>')
        L.texto(x + 22, 98, str(valor), "display", 66, "oro", espaciado=1)
        L.texto(x + 24, 122, etiqueta, "med", 12, "texto", espaciado=2)
        L.texto(x + 24, 138, sub, "mono", 11, "tenue")
        L.add("</g>")
    return L


def pieza_ahora(tema, perfil, p) -> Lienzo:
    lineas = envolver(p["current"], "mono", 15, 1040)[:3]
    alto = 150 + 24 * len(lineas)
    L = Lienzo(alto, tema, f"Ahora mismo: {titulo_corto(p['title'])}. {p['current']}")
    c = L.c
    L.css.append(".vivo{transform-box:fill-box;transform-origin:center;animation:vivo 2s ease-out infinite}"
                 "@keyframes vivo{0%{transform:scale(1);opacity:.8}100%{transform:scale(3);opacity:0}}")
    L.tarjeta(0.5, 0.5, ANCHO - 1, alto - 1, r=18)
    L.add(f'<rect x="0.5" y="24" width="4" height="{alto - 48}" rx="2" fill="{c["oro"]}"/>')
    L.add(f'<circle cx="44" cy="44" r="5" fill="{c["verde"]}"/>')
    L.add(f'<circle cx="44" cy="44" r="5" fill="none" stroke="{c["verde"]}" class="vivo"/>')
    L.texto(60, 49, "AHORA MISMO", "med", 12.5, "verde", espaciado=2.5)
    L.texto(ANCHO - 40, 49, f"{p['statusLabel'].upper()} · {p['progress']}%", "med", 12, "tenue", "end", espaciado=2)
    L.texto(38, 100, titulo_corto(p["title"]).upper(), "display", 40, "texto", espaciado=1)
    L.texto(40, 126, p["subtitle"], "mono", 13, "oro2")
    for i, linea in enumerate(lineas):
        L.texto(40, 160 + i * 24, linea, "mono", 15, "texto")
    return L


def pieza_proyectos(tema, perfil, destacados) -> Lienzo:
    filas = math.ceil(len(destacados) / 2)
    alto_t, gap = 132, 16
    alto = 64 + filas * alto_t + (filas - 1) * gap
    L = Lienzo(alto, tema, "Proyectos destacados en producción: " +
               ", ".join(f"{titulo_corto(p['title'])} ({p['progress']}%)" for p in destacados))
    c = L.c
    L.css.append(".barra{transform-box:fill-box;transform-origin:left center;animation:crece 1.4s cubic-bezier(.2,.8,.2,1) both}"
                 "@keyframes crece{from{transform:scaleX(0)}to{transform:scaleX(1)}}")
    L.encabezado(34, "PROYECTOS DESTACADOS", "en producción · prioridad alta · los más recientes primero")
    w = (ANCHO - gap) / 2
    for i, p in enumerate(destacados):
        x, y = (i % 2) * (w + gap), 64 + (i // 2) * (alto_t + gap)
        L.tarjeta(x + 0.5, y + 0.5, w - 1, alto_t - 1, r=16)
        L.texto(x + 24, y + 32, recortar(p["subtitle"].upper(), "med", 10.5, w - 48, 1.6), "med", 10.5, "oro",
                espaciado=1.6)
        L.texto(x + 23, y + 70, recortar(titulo_corto(p["title"]).upper(), "display", 32, w - 48, 0.5),
                "display", 32, "texto", espaciado=0.5)
        bx, by, bw = x + 24, y + 98, w - 230
        L.add(f'<rect x="{bx}" y="{by}" width="{bw:.1f}" height="6" rx="3" fill="{c["pista"]}"/>')
        L.add(f'<rect x="{bx}" y="{by}" width="{bw * p["progress"] / 100:.1f}" height="6" rx="3" '
              f'fill="{c["oro"]}" class="barra" style="animation-delay:{0.15 + i * 0.08:.2f}s"/>')
        L.texto(bx + bw + 12, by + 7, f"{p['progress']}%", "med", 13, "texto")
        etiqueta = p["statusLabel"].upper()
        col = c[COLOR_ESTATUS.get(p["status"], "gris")]
        ew = FUENTES["med"].ancho(etiqueta, 10, 1.4) + 30
        ex = x + w - 24 - ew
        L.add(f'<rect x="{ex:.1f}" y="{by - 9}" width="{ew:.1f}" height="24" rx="12" fill="none" stroke="{col}" '
              f'stroke-opacity="0.55"/>')
        L.add(f'<circle cx="{ex + 13:.1f}" cy="{by + 3}" r="3.5" fill="{col}"/>')
        L.texto(ex + 22, by + 7, etiqueta, "med", 10, col, espaciado=1.4)
    return L


def pieza_principios(tema, perfil) -> Lienzo:
    items = perfil["principios"]
    cols, gap = 3, 16
    w = (ANCHO - (cols - 1) * gap) / cols
    cuerpos = [envolver(t, "mono", 13, w - 56)[:3] for _, t in items]
    alto_t = 118 + 20 * max(len(x) for x in cuerpos)
    filas = math.ceil(len(items) / cols)
    alto = 64 + filas * alto_t + (filas - 1) * gap
    L = Lienzo(alto, tema, "Cómo trabajo: " + "; ".join(f"{a}: {b}" for a, b in items))
    L.encabezado(34, "CÓMO TRABAJO", "las reglas que sigo en cada sistema")
    for i, ((titulo, _), cuerpo) in enumerate(zip(items, cuerpos)):
        x, y = (i % cols) * (w + gap), 64 + (i // cols) * (alto_t + gap)
        L.tarjeta(x + 0.5, y + 0.5, w - 1, alto_t - 1, r=16)
        L.texto(x + 24, y + 54, f"{i + 1:02d}", "display", 40, "oro", espaciado=1)
        L.texto(x + 24, y + 84, recortar(titulo, "med", 15, w - 48), "med", 15, "texto")
        for j, linea in enumerate(cuerpo):
            L.texto(x + 24, y + 110 + j * 20, linea, "mono", 13, "tenue")
    return L


def pieza_stack(tema, perfil, conteo) -> Lienzo:
    top = conteo[: int(perfil["stack_top"])]
    filas = math.ceil(len(top) / 2)
    alto = 70 + filas * 40
    L = Lienzo(alto, tema, "Stack por número de proyectos: " + ", ".join(f"{t} {n}" for t, n in top))
    c = L.c
    L.css.append(".barra{transform-box:fill-box;transform-origin:left center;animation:crece 1.4s cubic-bezier(.2,.8,.2,1) both}"
                 "@keyframes crece{from{transform:scaleX(0)}to{transform:scaleX(1)}}")
    L.encabezado(34, "MI STACK, EN NÚMEROS", "cuántos proyectos del portafolio usan cada tecnología")
    maximo = top[0][1] if top else 1
    col_w, nombre_w = (ANCHO - 40) / 2, 220
    for i, (tech, n) in enumerate(top):
        x, y = (i // filas) * (col_w + 40), 78 + (i % filas) * 40
        L.texto(x, y + 12, recortar(tech, "med", 13.5, nombre_w - 12), "med", 13.5, "texto")
        bw = col_w - nombre_w - 70
        L.add(f'<rect x="{x + nombre_w}" y="{y + 2}" width="{bw:.1f}" height="12" rx="6" fill="{c["pista"]}"/>')
        L.add(f'<rect x="{x + nombre_w}" y="{y + 2}" width="{max(12, bw * n / maximo):.1f}" height="12" rx="6" '
              f'fill="{c["oro"]}" class="barra" style="animation-delay:{0.1 + i * 0.06:.2f}s"/>')
        L.texto(x + nombre_w + bw + 12, y + 13, f"{n}", "med", 13, "oro2")
    return L


# ---------------------------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------------------------
def cargar_datos(origen: str) -> dict:
    if re.match(r"^https?://", origen):
        req = urllib.request.Request(origen, headers={"User-Agent": "perfil-github-generador"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    return json.loads(Path(origen).read_text(encoding="utf-8"))


def contar_stack(proyectos, reglas) -> list[tuple[str, int]]:
    compiladas = [(re.compile(p, re.I), canon) for p, canon in reglas]
    conteo: dict[str, int] = {}
    for p in proyectos:
        techs = set()
        for lista in p.get("stack", {}).values():
            for t in lista:
                for rx, canon in compiladas:
                    if rx.search(t.strip()):
                        techs.add(canon)
                        break
        for t in techs:
            conteo[t] = conteo.get(t, 0) + 1
    return sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))


def picture(nombre: str, alt: str, enlace: str | None = None) -> str:
    img = (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="svg/{nombre}-oscuro.svg">\n'
           f'  <source media="(prefers-color-scheme: light)" srcset="svg/{nombre}-claro.svg">\n'
           f'  <img alt="{escape(alt, {chr(34): "&quot;"})}" src="svg/{nombre}-oscuro.svg" width="100%">\n'
           f'</picture>')
    return f'<a href="{enlace}">\n{img}\n</a>' if enlace else img


def celda(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datos", help="ruta o URL de projects.json (por defecto, la de perfil.json)")
    args = ap.parse_args()
    perfil = json.loads((BASE / "perfil.json").read_text(encoding="utf-8"))
    datos = cargar_datos(args.datos or perfil["datos_url"])
    proyectos = datos["projects"]

    resumen = {"total": len(proyectos),
               "produccion": sum(p["status"] == "active" for p in proyectos),
               "desarrollo": sum(p["status"] == "dev" for p in proyectos)}
    activos = [p for p in proyectos if p["status"] == "active"]
    destacados = sorted(activos, key=lambda p: (RANGO_PRIORIDAD.get(p.get("priority"), 9),
                                                -int((p.get("startDate") or "0000-00-00").replace("-", ""))))
    destacados = destacados[: int(perfil["destacados"])]
    reciente = max(proyectos, key=lambda p: (p.get("startDate") or "", p["id"]))
    conteo = contar_stack(proyectos, perfil["stack_reglas"])

    SALIDA.mkdir(exist_ok=True)
    piezas = {
        "banner": lambda t: pieza_banner(t, perfil, resumen),
        "impacto": lambda t: pieza_impacto(t, perfil, resumen),
        "ahora": lambda t: pieza_ahora(t, perfil, reciente),
        "proyectos": lambda t: pieza_proyectos(t, perfil, destacados),
        "principios": lambda t: pieza_principios(t, perfil),
        "stack": lambda t: pieza_stack(t, perfil, conteo),
    }
    alts = {}
    for nombre, fabrica in piezas.items():
        for tema in TEMAS:
            lienzo = fabrica(tema)
            alts[nombre] = lienzo.titulo
            (SALIDA / f"{nombre}-{tema}.svg").write_text(lienzo.render(), encoding="utf-8", newline="\n")

    orden = sorted(proyectos, key=lambda p: (p.get("startDate") or "", p["id"]), reverse=True)
    tabla = "\n".join(f"| **{celda(titulo_corto(p['title']))}** | {celda(p.get('type', ''))} | "
                      f"{celda(p['statusLabel'])} | {p['progress']} % |" for p in orden)
    valores = {
        "BANNER": picture("banner", alts["banner"], perfil["portafolio_url"]),
        "PRESENTACION": perfil["presentacion"],
        "IMPACTO": picture("impacto", alts["impacto"]),
        "AHORA": picture("ahora", alts["ahora"]),
        "PROYECTOS": picture("proyectos", alts["proyectos"], perfil["portafolio_url"]),
        "PRINCIPIOS": picture("principios", alts["principios"]),
        "STACK": picture("stack", alts["stack"]),
        "TOTAL": str(resumen["total"]),
        "TABLA": tabla,
        "PORTAFOLIO": perfil["portafolio_url"],
        "FECHA_DATOS": datos.get("meta", {}).get("lastUpdate", ""),
    }
    readme = (BASE / "plantilla.md").read_text(encoding="utf-8")
    for k, v in valores.items():
        readme = readme.replace("{{" + k + "}}", v)
    (BASE / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    faltan = {k: "".join(sorted(f.faltan)) for k, f in FUENTES.items() if f.faltan}
    print(f"OK: {resumen['total']} proyectos ({resumen['produccion']} en producción, {resumen['desarrollo']} en "
          f"desarrollo) | destacados: {len(destacados)} | ahora: {titulo_corto(reciente['title'])} | "
          f"stack: {len(conteo)} tecnologías")
    if faltan:
        print("AVISO: caracteres sin glifo en la fuente (se ven con la de respaldo):", faltan)


if __name__ == "__main__":
    main()
