"""
scraper.py
==========
Capa de extracción del proyecto. Usa Playwright (navegador asíncrono) para
cargar las páginas de TikTok y BeautifulSoup para leer el HTML.

Fases:
    A) Datos de perfil   (JSON de hidratación del HTML)
    B) Últimos 10 videos (likes, vistas, comentarios totales)
    C) Primeros 5 comentarios de cada video (respuesta de red + HTML)
    D) Lista de seguidores y de seguidos (nombres de cuenta)
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (cambia aquí lo que necesites)
# ---------------------------------------------------------------------------

# Carpeta donde el navegador guarda cookies y sesión. Se crea sola.
RUTA_USER_DATA: Path = Path(__file__).parent / "chrome_profile"

# None = usa Chromium de Playwright. "chrome" = usa tu Google Chrome instalado.
CANAL_NAVEGADOR: Optional[str] = "chrome"

# False = ver el navegador (recomendado: puedes resolver captchas o iniciar
# sesión a mano, necesario para ver las listas de seguidores).
MODO_HEADLESS: bool = False

MAX_VIDEOS: int = 10
MAX_COMENTARIOS: int = 5
MAX_RELACIONES: int = 30  # cuántos seguidores / seguidos guardar como máximo
ESPERA_CAPTCHA_SEG: int = 90  # tiempo máximo para resolver un captcha

# Carpeta para guardar capturas de depuración cuando algo falla.
CARPETA_DEPURACION: Path = Path(__file__).parent / "output"

# Selectores de los contadores del perfil (al hacer clic abren la lista).
SELECTOR_SEGUIDORES: str = '[data-e2e="followers-count"]'
SELECTOR_SIGUIENDO: str = '[data-e2e="following-count"]'

# JavaScript: hace scroll hacia abajo dentro de la ventana emergente de
# seguidores/seguidos para forzar la carga de más cuentas (lazy loading).
JS_SCROLL_VENTANA: str = """
() => {
    const ventana = document.querySelector('[data-e2e="follow-info-popup"]')
                    || document.body;
    ventana.querySelectorAll('*').forEach((el) => {
        if (el.scrollHeight > el.clientHeight + 20) {
            el.scrollTop = el.scrollHeight;
        }
    });
}
"""

# JavaScript: scroll en el panel de comentarios de un video.
JS_SCROLL_COMENTARIOS: str = """
() => {
    const panel = document.querySelector(
        '[class*="DivCommentListContainer"]');
    if (panel) { panel.scrollTop = panel.scrollHeight; }
    window.scrollBy(0, 600);
}
"""

# ---------------------------------------------------------------------------
# MODELOS DE DATOS
# ---------------------------------------------------------------------------


class ScraperError(Exception):
    """Error propio del scraper, con mensaje legible para el usuario."""


@dataclass
class UsuarioRelacion:
    """Una cuenta de la lista de seguidores o de seguidos."""

    username: str
    nickname: str = ""


@dataclass
class PerfilTikTok:
    """Datos del perfil (Fase A) y sus listas (Fase D)."""

    username: str
    nickname: str
    biografia: str
    seguidores: int
    siguiendo: int
    lista_seguidores: list[UsuarioRelacion] = field(default_factory=list)
    lista_siguiendo: list[UsuarioRelacion] = field(default_factory=list)


@dataclass
class VideoTikTok:
    """Datos de un video (Fases B y C)."""

    video_id: str
    url: str
    likes: int = 0
    vistas: int = 0
    comentarios_total: int = 0
    comentarios: list[str] = field(default_factory=list)
    descripcion: str = ""


def _a_entero(valor: Any) -> int:
    """Convierte un valor a int de forma segura (devuelve 0 si falla)."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# CLASE PRINCIPAL
# ---------------------------------------------------------------------------


class TikTokScraper:
    """Scraper de perfiles de TikTok con contexto persistente."""

    def __init__(
        self,
        ruta_user_data: Path = RUTA_USER_DATA,
        canal: Optional[str] = CANAL_NAVEGADOR,
        headless: bool = MODO_HEADLESS,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ruta_user_data = ruta_user_data
        self.canal = canal
        self.headless = headless
        # 'log' recibe texto; Flask lo usa para mostrar el progreso.
        self._log: Callable[[str], None] = log or print
        # Buffers que se llenan solos con las respuestas de red de TikTok.
        self._items_api: list[dict[str, Any]] = []
        self._comentarios_api: list[str] = []
        self._usuarios_api: list[dict[str, Any]] = []
        # Resumen de las llamadas de comentarios (sirve para diagnosticar).
        self._resumen_comentarios: list[str] = []

    # ------------------------- utilidades internas -------------------------

    async def _abrir_contexto(self, playwright: Any) -> BrowserContext:
        """Abre el navegador con contexto PERSISTENTE (guarda cookies)."""
        self.ruta_user_data.mkdir(parents=True, exist_ok=True)
        opciones: dict[str, Any] = {
            "user_data_dir": str(self.ruta_user_data),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 900},
            "locale": "es-EC",
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self.canal:
            opciones["channel"] = self.canal
        return await playwright.chromium.launch_persistent_context(**opciones)

    async def _on_response(self, respuesta: Response) -> None:
        """
        Escucha las respuestas de red de TikTok y guarda las que nos
        interesan: lista de videos, comentarios y lista de usuarios.
        """
        url = respuesta.url
        if not any(k in url for k in ("item_list", "comment/list", "user/list")):
            return
        try:
            datos = await respuesta.json()
        except (PlaywrightError, ValueError):
            if "comment/list" in url:
                self._resumen_comentarios.append(
                    f"HTTP {respuesta.status} sin JSON")
            return  # respuesta sin JSON válido: la ignoramos
        if not isinstance(datos, dict):
            return

        if "item_list" in url:
            self._items_api.extend(datos.get("itemList") or [])
        elif "comment/list" in url:
            lista = datos.get("comments") or []
            self._resumen_comentarios.append(
                f"HTTP {respuesta.status}, {len(lista)} comentarios")
            for comentario in lista:
                texto = str(comentario.get("text", "")).strip()
                if texto:
                    self._comentarios_api.append(texto)
        elif "user/list" in url:
            self._usuarios_api.extend(datos.get("userList") or [])

    async def _leer_json_hidratacion(
        self, page: Page, espera_max: int = ESPERA_CAPTCHA_SEG
    ) -> Optional[dict[str, Any]]:
        """
        Busca con BeautifulSoup el <script> con el JSON de la página
        ('__UNIVERSAL_DATA_FOR_REHYDRATION__' o 'SIGI_STATE').
        Reintenta cada 3 s por si hay captcha que resolver a mano.
        """
        transcurrido = 0
        while transcurrido <= espera_max:
            html = await page.content()
            sopa = BeautifulSoup(html, "html.parser")
            for id_script in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
                etiqueta = sopa.find("script", id=id_script)
                if etiqueta and etiqueta.string:
                    try:
                        return json.loads(etiqueta.string)
                    except json.JSONDecodeError:
                        self._log("JSON de hidratación corrupto, reintentando...")
            if transcurrido == 0:
                self._log("No se halló el JSON. Si hay captcha, resuélvelo "
                          "en la ventana del navegador...")
            await page.wait_for_timeout(3000)
            transcurrido += 3
        return None

    # ----------------------------- FASE A ----------------------------------

    async def _fase_a_perfil(self, page: Page, username: str) -> PerfilTikTok:
        """Extrae nickname, biografía, seguidores y seguidos (contadores)."""
        self._log(f"FASE A: abriendo el perfil @{username}...")
        await page.goto(
            f"https://www.tiktok.com/@{username}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        datos = await self._leer_json_hidratacion(page)
        if datos is None:
            raise ScraperError("No se pudo leer el JSON del perfil "
                               "(captcha o bloqueo de TikTok).")

        usuario: dict[str, Any] = {}
        stats: dict[str, Any] = {}

        # Estructura moderna: __UNIVERSAL_DATA_FOR_REHYDRATION__
        info = (datos.get("__DEFAULT_SCOPE__", {})
                .get("webapp.user-detail", {})
                .get("userInfo", {}))
        if info:
            usuario, stats = info.get("user", {}), info.get("stats", {})
        else:
            # Estructura antigua: SIGI_STATE
            modulo = datos.get("UserModule", {})
            usuarios = modulo.get("users", {})
            usuario = next(iter(usuarios.values()), {}) if usuarios else {}
            stats = next(iter(modulo.get("stats", {}).values()), {})

        if not usuario:
            raise ScraperError(f"El perfil @{username} no existe o es privado.")

        perfil = PerfilTikTok(
            username=usuario.get("uniqueId", username),
            nickname=usuario.get("nickname", ""),
            biografia=usuario.get("signature", ""),
            seguidores=_a_entero(stats.get("followerCount")),
            siguiendo=_a_entero(stats.get("followingCount")),
        )
        self._log(f"Perfil encontrado: {perfil.nickname} "
                  f"({perfil.seguidores} seguidores)")
        return perfil

    # ----------------------------- FASE B ----------------------------------

    async def _fase_b_videos(self, page: Page, username: str) -> list[VideoTikTok]:
        """Mapea los últimos videos con likes, vistas y comentarios."""
        self._log("FASE B: mapeando las últimas publicaciones...")
        for _ in range(3):  # scroll para forzar la carga de videos
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

        videos: list[VideoTikTok] = []
        vistos: set[str] = set()

        # Camino 1: datos que TikTok envió por su API interna.
        for item in self._items_api:
            video_id = str(item.get("id", ""))
            autor = str(item.get("author", {}).get("uniqueId", username))
            # Descartamos videos de otras cuentas (p. ej. recomendados).
            if not video_id or video_id in vistos:
                continue
            if autor.lower() != username.lower():
                continue
            vistos.add(video_id)
            est = item.get("stats", {})
            videos.append(VideoTikTok(
                video_id=video_id,
                url=f"https://www.tiktok.com/@{username}/video/{video_id}",
                descripcion=str(item.get("desc") or "").strip(),
                likes=_a_entero(est.get("diggCount")),
                vistas=_a_entero(est.get("playCount")),
                comentarios_total=_a_entero(est.get("commentCount")),
            ))

        # Camino 2 (respaldo): leer los enlaces del HTML con BeautifulSoup.
        if not videos:
            self._log("Sin datos de API; usando enlaces del HTML...")
            sopa = BeautifulSoup(await page.content(), "html.parser")
            for enlace in sopa.select('a[href*="/video/"]'):
                href = str(enlace.get("href", ""))
                video_id = href.split("?")[0].rstrip("/").split("/")[-1]
                if video_id and video_id not in vistos:
                    vistos.add(video_id)
                    videos.append(VideoTikTok(video_id=video_id, url=href))

        videos = videos[:MAX_VIDEOS]
        if not videos:
            raise ScraperError("No se encontraron videos en el perfil.")
        self._log(f"Se encontraron {len(videos)} videos.")
        return videos

    # ----------------------------- FASE C ----------------------------------

    async def _estimular_comentarios(self, page: Page) -> None:
        """
        Fuerza la carga perezosa de comentarios: mueve el mouse sobre la
        zona derecha de la página y gira la rueda (como lo haría una
        persona), además de hacer scroll con JavaScript.
        """
        for _ in range(3):
            try:
                await page.mouse.move(1000, 500)
                await page.mouse.wheel(0, 700)
            except PlaywrightError:
                pass
            await page.evaluate(JS_SCROLL_COMENTARIOS)
            await page.wait_for_timeout(1500)

    async def _esperar_comentarios_api(self, page: Page, segundos: int) -> None:
        """Espera hasta que la red entregue comentarios (máx. 'segundos')."""
        for _ in range(segundos):
            if self._comentarios_api:
                return
            await page.wait_for_timeout(1000)

    async def _abrir_panel_comentarios(self, page: Page) -> None:
        """Intenta abrir la pestaña/panel de comentarios si no se abrió."""
        intentos = (
            page.get_by_role("tab", name=re.compile("coment", re.IGNORECASE)),
            page.locator('[data-e2e="comment-icon"]'),
            page.get_by_text(re.compile(r"^Comentarios", re.IGNORECASE)),
        )
        for candidato in intentos:
            try:
                await candidato.first.click(timeout=3000)
                await page.wait_for_timeout(2500)
                return
            except PlaywrightError:
                continue  # probamos el siguiente candidato

    @staticmethod
    def _comentarios_desde_html(html: str) -> list[str]:
        """Respaldo: lee los comentarios del HTML con BeautifulSoup."""
        sopa = BeautifulSoup(html, "html.parser")
        textos: list[str] = []
        for selector in ('[data-e2e="comment-level-1"]',
                         '[class*="DivCommentContentContainer"]',
                         '[class*="CommentContent"]'):
            for nodo in sopa.select(selector):
                texto = nodo.get_text(strip=True)
                if texto:
                    textos.append(texto)
            if textos:
                break
        return textos

    async def _guardar_depuracion(self, page: Page) -> None:
        """Guarda una captura y el HTML para poder ver qué mostró TikTok."""
        try:
            CARPETA_DEPURACION.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(CARPETA_DEPURACION / "debug_video.png"))
            html = await page.content()
            (CARPETA_DEPURACION / "debug_video.html").write_text(
                html, encoding="utf-8")
            self._log("    Captura de depuración guardada en output/debug_video.png")
        except (PlaywrightError, OSError) as error:
            self._log(f"    No se pudo guardar la depuración: {error}")

    async def _fase_c_detalle(
        self, page: Page, video: VideoTikTok, depurar: bool = False
    ) -> None:
        """Entra al video, completa métricas faltantes y lee comentarios."""
        self._comentarios_api.clear()  # buffers limpios para ESTE video
        self._resumen_comentarios.clear()

        respuesta = await page.goto(
            video.url, wait_until="domcontentloaded", timeout=60000)
        if respuesta is not None and respuesta.status == 403:
            self._log("    TikTok devolvió HTTP 403 (acceso denegado). "
                      "Se omite este video.")
            return

        # Si las estadísticas vinieron vacías, las leemos del JSON del video.
        datos = await self._leer_json_hidratacion(page, espera_max=15)
        if datos:
            item = (datos.get("__DEFAULT_SCOPE__", {})
                    .get("webapp.video-detail", {})
                    .get("itemInfo", {})
                    .get("itemStruct", {}))
            video.descripcion = (
                video.descripcion or str(item.get("desc") or "").strip()
            )
            if video.vistas == 0:
                est = item.get("stats", {})
                video.likes = _a_entero(est.get("diggCount"))
                video.vistas = _a_entero(est.get("playCount"))
                video.comentarios_total = _a_entero(est.get("commentCount"))

        if video.comentarios_total == 0:
            self._log("    sin comentarios, se omite.")
            return

        await page.wait_for_timeout(3000)
        await self._estimular_comentarios(page)
        await self._esperar_comentarios_api(page, 6)

        # Si la red no entregó comentarios, abrimos el panel y reintentamos.
        if not self._comentarios_api:
            await self._abrir_panel_comentarios(page)
            await self._estimular_comentarios(page)
            await self._esperar_comentarios_api(page, 8)

        # dict.fromkeys elimina duplicados conservando el orden.
        comentarios = list(dict.fromkeys(self._comentarios_api))
        origen = "red"
        if not comentarios:
            comentarios = self._comentarios_desde_html(await page.content())
            origen = "HTML"

        video.comentarios = comentarios[:MAX_COMENTARIOS]
        detalle = ("; ".join(self._resumen_comentarios)
                   or "no se detectó ninguna llamada de comentarios")
        self._log(f"    {len(video.comentarios)} comentarios leídos "
                  f"({origen}). Red: {detalle}")

        if depurar and not video.comentarios:
            await self._guardar_depuracion(page)

    # ----------------------------- FASE D ----------------------------------

    async def _leer_lista_relaciones(
        self, page: Page, username: str, etiqueta: str, selector: str
    ) -> list[UsuarioRelacion]:
        """
        Abre el perfil, pulsa el contador (seguidores o seguidos) y lee la
        ventana emergente con la lista de cuentas.
        """
        self._usuarios_api.clear()
        usuarios: list[UsuarioRelacion] = []
        vistos: set[str] = set()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}",
                            wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            await page.click(selector, timeout=10000)
            await page.wait_for_timeout(3000)

            for _ in range(8):  # scroll hasta reunir las cuentas necesarias
                await page.evaluate(JS_SCROLL_VENTANA)
                await page.wait_for_timeout(1500)
                if len(self._usuarios_api) >= MAX_RELACIONES:
                    break

            # Camino 1: datos de la API interna.
            for item in self._usuarios_api:
                datos_usuario = item.get("user", item)
                cuenta = str(datos_usuario.get("uniqueId", "")).strip()
                if cuenta and cuenta not in vistos:
                    vistos.add(cuenta)
                    usuarios.append(UsuarioRelacion(
                        username=cuenta,
                        nickname=str(datos_usuario.get("nickname", "")),
                    ))

            # Camino 2 (respaldo): enlaces '/@usuario' dentro de la ventana.
            if not usuarios:
                sopa = BeautifulSoup(await page.content(), "html.parser")
                zona = sopa.select_one('[data-e2e="follow-info-popup"]') or sopa
                for enlace in zona.select('a[href^="/@"]'):
                    cuenta = str(enlace.get("href", "")).split("/@")[1]
                    cuenta = cuenta.split("?")[0].split("/")[0]
                    if (cuenta and cuenta not in vistos
                            and cuenta.lower() != username.lower()):
                        vistos.add(cuenta)
                        usuarios.append(UsuarioRelacion(username=cuenta))
        except PlaywrightError as error:
            self._log(f"  No se pudo abrir la lista de {etiqueta}: {error}")

        usuarios = usuarios[:MAX_RELACIONES]
        muestra = ", ".join(u.username for u in usuarios[:3]) or "ninguna"
        self._log(f"  {len(usuarios)} cuentas leídas en {etiqueta} "
                  f"(primeras: {muestra}).")
        return usuarios

    # ------------------------- MÉTODO PÚBLICO ------------------------------

    async def extraer(
        self, username: str
    ) -> tuple[PerfilTikTok, list[VideoTikTok]]:
        """Ejecuta las cuatro fases y devuelve (perfil, videos)."""
        username = username.strip().lstrip("@")
        if not username:
            raise ScraperError("Debes ingresar un nombre de usuario.")

        self._items_api.clear()
        self._comentarios_api.clear()
        self._usuarios_api.clear()
        self._resumen_comentarios.clear()
        try:
            async with async_playwright() as playwright:
                contexto = await self._abrir_contexto(playwright)
                try:
                    page = (contexto.pages[0] if contexto.pages
                            else await contexto.new_page())
                    page.on("response", self._on_response)

                    perfil = await self._fase_a_perfil(page, username)
                    videos = await self._fase_b_videos(page, username)

                    self._log("FASE C: leyendo comentarios de cada video...")
                    for i, video in enumerate(videos, start=1):
                        try:
                            self._log(f"  Video {i}/{len(videos)}...")
                            await self._fase_c_detalle(
                                page, video, depurar=(i == 1))
                        except PlaywrightError as error:
                            # Un video fallido no detiene todo el proceso.
                            self._log(f"  Video {i} omitido: {error}")
                        await asyncio.sleep(random.uniform(2, 4))  # pausa humana

                    self._log("FASE D: listas de seguidores y seguidos "
                              "(requiere sesión iniciada)...")
                    perfil.lista_seguidores = await self._leer_lista_relaciones(
                        page, username, "seguidores", SELECTOR_SEGUIDORES)
                    await asyncio.sleep(random.uniform(2, 4))
                    perfil.lista_siguiendo = await self._leer_lista_relaciones(
                        page, username, "seguidos", SELECTOR_SIGUIENDO)
                finally:
                    await contexto.close()
        except PlaywrightError as error:
            raise ScraperError(f"Error del navegador: {error}") from error

        self._log("Extracción finalizada con éxito.")
        return perfil, videos


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA: ejecuta `python scraper.py` para probar solo este archivo
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    async def _prueba() -> None:
        scraper = TikTokScraper()
        perfil, videos = await scraper.extraer("tiktok")  # cambia el usuario
        print(perfil)
        for video in videos:
            print(video)

    asyncio.run(_prueba())