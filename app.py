"""
app.py
======
Servidor Flask (backend). Expone una API REST que el frontend consulta con
fetch. El scraping corre en un HILO aparte para no bloquear la interfaz.

Rutas:
    GET  /               -> página principal
    POST /api/extraer    -> inicia la extracción
    GET  /api/estado     -> estado, logs y resultados
    GET  /api/descargar  -> descarga el CSV
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, jsonify, render_template, request, send_file

from scraper import PerfilTikTok, ScraperError, TikTokScraper, VideoTikTok

from bigfive import ResultadoBigFive, analizar_bigfive

# ---------------------------------------------------------------------------
# CONFIGURACIÓN Y ESTADO COMPARTIDO
# ---------------------------------------------------------------------------

CARPETA_SALIDA: Path = Path(__file__).parent / "output"
SEPARADOR_COMENTARIOS: str = " | "
PATRON_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,24}$")

app = Flask(__name__)

# RLock = candado reentrante: evita que dos hilos modifiquen el estado
# al mismo tiempo (el hilo del scraper y las peticiones de Flask).
_candado = threading.RLock()

_estado: dict[str, Any] = {
    "estado": "inactivo",  # inactivo | procesando | completado | error
    "logs": [],
    "error": None,
    "perfil": None,
    "videos": [],
    "analisis_bigfive": None,
    "archivo_csv": None,
    "archivo_json": None,
}


def _reiniciar_estado() -> None:
    """Deja el estado listo para una nueva extracción."""
    with _candado:
        _estado.update(
            estado="procesando",
            logs=[],
            error=None,
            perfil=None,
            videos=[],
            analisis_bigfive=None,
            archivo_csv=None,
            archivo_json=None,
        )


def _agregar_log(mensaje: str) -> None:
    """Guarda un mensaje de progreso (el scraper llama a esta función)."""
    with _candado:
        _estado["logs"].append(mensaje)


# ---------------------------------------------------------------------------
# EXPORTACIÓN A CSV (solo librería nativa 'csv')
# ---------------------------------------------------------------------------


def exportar_csv(
    perfil: PerfilTikTok,
    videos: list[VideoTikTok],
    analisis_bigfive: Optional[ResultadoBigFive] = None,
) -> Path:
    """
    Genera 'reporte_tiktok_[username].csv' con cinco bloques:
      1) Resumen del perfil
      2) Lista de seguidores (muestra)
      3) Lista de seguidos (muestra)
      4) Matriz detallada de videos
      5) Estimación orientativa Big Five
    Usa utf-8-sig para que Excel muestre bien tildes y emojis.
    """
    CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
    nombre_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", perfil.username)
    ruta = CARPETA_SALIDA / f"reporte_tiktok_{nombre_seguro}.csv"

    with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.writer(archivo)

        # Bloque 1: resumen del perfil
        escritor.writerow(["RESUMEN DEL PERFIL"])
        escritor.writerow(["Nombre de usuario", perfil.username])
        escritor.writerow(["Nickname", perfil.nickname])
        escritor.writerow(["Biografía", perfil.biografia])
        escritor.writerow(["Seguidores", perfil.seguidores])
        escritor.writerow(["Seguidos", perfil.siguiendo])
        escritor.writerow([])  # fila en blanco para separar bloques

        # Bloques 2 y 3: listas de cuentas (seguidores y seguidos)
        listas = (
            ("SEGUIDORES", perfil.lista_seguidores),
            ("SEGUIDOS", perfil.lista_siguiendo),
        )
        for titulo, usuarios in listas:
            escritor.writerow([f"{titulo} (muestra de {len(usuarios)})"])
            escritor.writerow(["#", "Usuario", "Nickname"])
            for i, usuario in enumerate(usuarios, start=1):
                escritor.writerow([i, f"@{usuario.username}", usuario.nickname])
            escritor.writerow([])

        # Bloque 4: matriz de videos
        escritor.writerow(["DETALLE DE VIDEOS"])
        escritor.writerow(
            ["ID", "URL", "Likes", "Vistas", "Comentarios Totales",
             "Descripción", "Comentarios"]
        )
        for video in videos:
            escritor.writerow([
                video.video_id,
                video.url,
                video.likes,
                video.vistas,
                video.comentarios_total,
                video.descripcion,
                SEPARADOR_COMENTARIOS.join(video.comentarios),
            ])

        if analisis_bigfive is not None:
            escritor.writerow([])
            escritor.writerow(["ANÁLISIS BIG FIVE (estimación orientativa)"])
            escritor.writerow(["Aviso", analisis_bigfive.aviso])
            escritor.writerow(["Palabras analizadas",
                               analisis_bigfive.palabras_analizadas])
            escritor.writerow(["Coincidencias",
                               analisis_bigfive.coincidencias])
            escritor.writerow(["Confianza", analisis_bigfive.confianza])
            escritor.writerow(["Rasgo", "Puntaje", "Nivel", "Descripción",
                               "Palabras detectadas"])
            for rasgo in analisis_bigfive.rasgos:
                escritor.writerow([
                    rasgo.nombre,
                    rasgo.puntaje,
                    rasgo.nivel,
                    rasgo.descripcion,
                    SEPARADOR_COMENTARIOS.join(rasgo.evidencia),
                ])
    return ruta

# ---------------------------------------------------------------------------
# EXPORTACIÓN A Json 
# ---------------------------------------------------------------------------


def exportar_json(
    perfil: PerfilTikTok,
    videos: list[VideoTikTok],
    analisis_bigfive: Optional[ResultadoBigFive] = None,
) -> Path:
    """Genera el reporte JSON con perfil, videos y análisis Big Five."""
    CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
    nombre_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", perfil.username)
    ruta = CARPETA_SALIDA / f"reporte_tiktok_{nombre_seguro}.json"
    datos = {
        "perfil": asdict(perfil),
        "videos": [asdict(video) for video in videos],
        "analisis_bigfive": (
            asdict(analisis_bigfive) if analisis_bigfive is not None else None
        ),
    }
    with open(ruta, "w", encoding="utf-8") as archivo:
        # ensure_ascii=False conserva tildes y emojis; indent=4 lo ordena.
        json.dump(datos, archivo, ensure_ascii=False, indent=4)
    return ruta

# ---------------------------------------------------------------------------
# TAREA EN SEGUNDO PLANO
# ---------------------------------------------------------------------------


def _tarea_extraccion(username: str) -> None:
    """Corre el scraper asíncrono dentro de un hilo y guarda el resultado."""
    try:
        scraper = TikTokScraper(log=_agregar_log)
        # asyncio.run crea un bucle de eventos propio para este hilo.
        perfil, videos = asyncio.run(scraper.extraer(username))
        textos = [perfil.biografia, *(video.descripcion for video in videos)]
        analisis_bigfive = analizar_bigfive(textos)
        _agregar_log(
            "Análisis Big Five generado con la biografía y las descripciones "
            f"de {len(videos)} publicaciones."
        )
        ruta = exportar_csv(perfil, videos, analisis_bigfive)
        ruta_json = exportar_json(perfil, videos, analisis_bigfive)
        _agregar_log(f"Archivos generados: {ruta.name} y {ruta_json.name}")
        with _candado:
            _estado.update(
                estado="completado",
                perfil=asdict(perfil),
                videos=[asdict(v) for v in videos],
                analisis_bigfive=asdict(analisis_bigfive),
                archivo_csv=str(ruta),
                archivo_json=str(ruta_json),
            )
    except ScraperError as error:
        _registrar_error(str(error))
    except OSError as error:
        _registrar_error(f"No se pudieron escribir los reportes: {error}")
    except Exception as error:  # último recurso: que el hilo no muera mudo
        _registrar_error(f"Error inesperado: {error}")


def _registrar_error(mensaje: str) -> None:
    """Marca el estado como 'error' y deja el mensaje en los logs."""
    _agregar_log(f"ERROR: {mensaje}")
    with _candado:
        _estado.update(estado="error", error=mensaje)


# ---------------------------------------------------------------------------
# ENDPOINTS DE LA API
# ---------------------------------------------------------------------------


@app.route("/")
def inicio() -> str:
    """Sirve la interfaz gráfica."""
    return render_template("index.html")


@app.route("/api/extraer", methods=["POST"])
def extraer() -> tuple[Response, int]:
    """Valida el username e inicia la extracción en segundo plano."""
    datos = request.get_json(silent=True) or {}
    username = str(datos.get("username", "")).strip().lstrip("@")

    if not username:
        return jsonify({"error": "Ingresa un nombre de usuario."}), 400
    if not PATRON_USERNAME.match(username):
        return jsonify({"error": "Usuario inválido: usa letras, números, "
                                 "puntos o guiones bajos."}), 400

    with _candado:
        if _estado["estado"] == "procesando":
            return jsonify({"error": "Ya hay una extracción en curso."}), 409
        _reiniciar_estado()

    hilo = threading.Thread(
        target=_tarea_extraccion, args=(username,), daemon=True
    )
    hilo.start()
    return jsonify({"mensaje": "Extracción iniciada."}), 202


@app.route("/api/estado")
def estado() -> Response:
    """Devuelve el estado actual (el frontend lo consulta cada ~1.5 s)."""
    with _candado:
        return jsonify(dict(_estado, logs=list(_estado["logs"])))


@app.route("/api/descargar")
def descargar() -> Any:
    """Entrega el CSV generado como descarga."""
    with _candado:
        ruta_texto: Optional[str] = _estado["archivo_csv"]
    if not ruta_texto or not Path(ruta_texto).exists():
        return jsonify({"error": "Aún no hay un reporte disponible."}), 404
    ruta = Path(ruta_texto)
    return send_file(ruta, as_attachment=True, download_name=ruta.name,
                     mimetype="text/csv")

@app.route("/api/descargar_json")
def descargar_json() -> Any:
    """Entrega el JSON generado como descarga."""
    with _candado:
        ruta_texto: Optional[str] = _estado.get("archivo_json")
    if not ruta_texto or not Path(ruta_texto).exists():
        return jsonify({"error": "Aún no hay un reporte JSON disponible."}), 404
    ruta = Path(ruta_texto)
    return send_file(ruta, as_attachment=True, download_name=ruta.name,
                     mimetype="application/json")

if __name__ == "__main__":
    # debug=False: el modo debug reinicia el servidor y abriría el
    # navegador del scraper dos veces.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)