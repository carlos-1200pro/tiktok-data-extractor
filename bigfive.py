"""
bigfive.py
==========
Análisis de personalidad Big Five (modelo OCEAN) basado en un DICCIONARIO de
palabras en español. No usa librerías externas, así cumple la restricción del
proyecto (solo playwright, beautifulsoup4, csv y json).

Los cinco rasgos:
    O - Apertura a la experiencia
    C - Responsabilidad
    E - Extraversión
    A - Amabilidad
    N - Neuroticismo (inestabilidad emocional)

Idea del método (léxico):
    1. Se juntan los textos del perfil (biografía y descripciones de videos).
    2. Se normalizan (minúsculas, sin tildes) y se separan en palabras.
    3. Para cada rasgo se cuentan las palabras que indican un nivel ALTO
       y las que indican un nivel BAJO.
    4. Se calcula la densidad neta por cada 100 palabras y se convierte a un
       puntaje de 0 a 100 (50 = neutro) con la función tanh.

IMPORTANTE: es una estimación orientativa, no una prueba psicológica.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DEL MODELO
# ---------------------------------------------------------------------------

# Con una densidad neta de ESCALA_DENSIDAD coincidencias por cada 100
# palabras, el puntaje llega a ~88 (o ~12 si es negativa).
ESCALA_DENSIDAD: float = 4.0
UMBRAL_ALTO: float = 60.0
UMBRAL_BAJO: float = 40.0
MIN_PALABRAS: int = 20  # con menos texto el resultado no es confiable

AVISO: str = (
    "Estimación orientativa basada en un diccionario de palabras en español "
    "aplicado a la biografía y a las descripciones de los videos. No es una "
    "prueba psicológica ni un diagnóstico. En 'Palabras detectadas', + indica "
    "rasgo alto y - indica rasgo bajo. No se analizan comentarios de terceros."
)

RASGOS: tuple[str, ...] = (
    "apertura", "responsabilidad", "extraversion", "amabilidad", "neuroticismo",
)

NOMBRES: dict[str, str] = {
    "apertura": "Apertura a la experiencia",
    "responsabilidad": "Responsabilidad",
    "extraversion": "Extraversión",
    "amabilidad": "Amabilidad",
    "neuroticismo": "Neuroticismo",
}

DESCRIPCIONES: dict[str, str] = {
    "apertura": "Curiosidad, creatividad e interés por ideas y experiencias nuevas.",
    "responsabilidad": "Organización, constancia, disciplina y orientación a metas.",
    "extraversion": "Sociabilidad, energía y gusto por la interacción con otras personas.",
    "amabilidad": "Empatía, cooperación, cordialidad y trato respetuoso.",
    "neuroticismo": "Tendencia a emociones negativas como ansiedad, tristeza o enojo "
                    "(alto = menor estabilidad emocional).",
}

# ---------------------------------------------------------------------------
# DICCIONARIO (sin tildes y en minúsculas)
# Una entrada que termina en '*' es una raíz: 'creativ*' acepta 'creativo',
# 'creatividad', 'creativa', etc. Sin '*' debe coincidir la palabra exacta.
# Formato: rasgo -> (palabras del polo ALTO, palabras del polo BAJO)
# ---------------------------------------------------------------------------

LEXICO: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "apertura": (
        ("creativ*", "arte", "artist*", "imagin*", "curios*", "aprend*",
         "descubr*", "explor*", "viaj*", "aventur*", "cultur*", "musica",
         "inspir*", "innov*", "experiment*", "idea*", "poesia", "libro*",
         "lectur*", "filosof*", "cienc*", "disen*"),
        ("tradicion*", "rutin*", "costumbre*", "conservad*", "convencional*"),
    ),
    "responsabilidad": (
        ("meta", "metas", "objetivo*", "disciplin*", "trabaj*", "estudi*",
         "organiz*", "responsab*", "esfuerz*", "constan*", "planific*",
         "proyecto*", "logr*", "compromis*", "puntual*", "ahorr*",
         "progres*", "dedicac*", "tarea*"),
        ("flojer*", "procrastin*", "desorden*", "olvid*", "pereza", "vago*",
         "caos", "descuid*", "irresponsab*", "improvis*"),
    ),
    "extraversion": (
        ("fiesta*", "amig*", "bail*", "juntos", "salir", "party", "gente",
         "divert*", "jaj*", "social*", "evento*", "concierto*", "celebr*",
         "carnaval", "grupo*", "viral", "reunion*", "parche", "disfrut*"),
        ("soledad", "introvert*", "timid*", "aislad*", "privacidad",
         "retraid*"),
    ),
    "amabilidad": (
        ("gracias", "agradec*", "amor", "amores", "carino*", "cuid*", "ayud*",
         "apoy*", "amabl*", "respet*", "generos*", "solidari*", "bendic*",
         "familia*", "empati*", "ternur*", "abrazo*", "gratitud", "perdon*",
         "paz", "comparti*", "bondad", "sincer*"),
        ("odi*", "idiot*", "estupid*", "imbecil*", "asco", "basura",
         "mentiros*", "molest*", "pelea*", "insult*", "maldit*", "envid*",
         "burla*", "arrogan*", "egoist*"),
    ),
    "neuroticismo": (
        ("trist*", "ansied*", "ansios*", "estres*", "miedo*", "preocup*",
         "llor*", "deprim*", "dolor*", "angusti*", "nervios*", "enoj*",
         "rabia", "frustr*", "cansad*", "agobi*", "culpa*", "sufr*",
         "insegur*", "panic*", "temor*"),
        ("calma", "tranquil*", "sereni*", "relaj*", "equilibr*",
         "estabilidad", "confianza", "seguridad"),
    ),
}

# Emojis que aportan señal (se cuentan en el texto original).
EMOJIS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "extraversion": (("🎉", "🥳", "😂", "🤣", "🔥", "💃", "🕺", "🎶"), ()),
    "amabilidad": (("❤", "🤗", "🙏", "💕", "💖", "🥰"), ("🤬", "🖕")),
    "neuroticismo": (("😢", "😭", "😔", "😞", "😰", "😡", "😤"), ("😌", "🧘")),
}

# ---------------------------------------------------------------------------
# MODELOS DE DATOS
# ---------------------------------------------------------------------------


@dataclass
class RasgoBigFive:
    """Resultado de un rasgo."""

    clave: str
    nombre: str
    puntaje: float  # 0 a 100 (50 = neutro)
    nivel: str  # Alto / Medio / Bajo
    descripcion: str
    evidencia: list[str] = field(default_factory=list)


@dataclass
class ResultadoBigFive:
    """Resultado completo del análisis."""

    rasgos: list[RasgoBigFive]
    palabras_analizadas: int
    coincidencias: int
    confianza: str  # insuficiente / baja / media / alta
    aviso: str = AVISO


# ---------------------------------------------------------------------------
# FUNCIONES INTERNAS
# ---------------------------------------------------------------------------


def _preparar(entradas: tuple[str, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Separa las palabras exactas de las raíces (las que terminan en '*')."""
    exactas = frozenset(e for e in entradas if not e.endswith("*"))
    raices = tuple(e[:-1] for e in entradas if e.endswith("*"))
    return exactas, raices


# Se prepara una sola vez al cargar el módulo.
_PREPARADO = {
    rasgo: (_preparar(alto), _preparar(bajo))
    for rasgo, (alto, bajo) in LEXICO.items()
}


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: 'Música' -> 'musica'."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _tokenizar(texto: str) -> list[str]:
    """Convierte el texto en una lista de palabras normalizadas."""
    return re.findall(r"[a-z0-9]+", _normalizar(texto))


def _coincidencia(
    token: str, preparado: tuple[frozenset[str], tuple[str, ...]]
) -> Optional[str]:
    """Devuelve la entrada del diccionario que coincide con el token."""
    exactas, raices = preparado
    if token in exactas:
        return token
    for raiz in raices:
        if token.startswith(raiz):
            return raiz + "*"
    return None


def _nivel(puntaje: float) -> str:
    if puntaje >= UMBRAL_ALTO:
        return "Alto"
    if puntaje <= UMBRAL_BAJO:
        return "Bajo"
    return "Medio"


def _confianza(palabras: int, coincidencias: int) -> str:
    """Qué tan confiable es el resultado según la cantidad de texto."""
    if palabras < MIN_PALABRAS:
        return "insuficiente"
    if palabras < 100 or coincidencias < 5:
        return "baja"
    if palabras < 300 or coincidencias < 15:
        return "media"
    return "alta"


# ---------------------------------------------------------------------------
# FUNCIÓN PÚBLICA
# ---------------------------------------------------------------------------


def analizar_bigfive(textos: list[str]) -> ResultadoBigFive:
    """
    Calcula el puntaje (0-100) de los cinco rasgos a partir de una lista
    de textos (biografía, descripciones de videos, etc.).
    """
    crudo = " ".join(t for t in textos if t)
    tokens = _tokenizar(crudo)
    total_palabras = len(tokens)

    rasgos: list[RasgoBigFive] = []
    coincidencias_totales = 0

    for clave in RASGOS:
        prep_alto, prep_bajo = _PREPARADO[clave]
        altas: Counter[str] = Counter()
        bajas: Counter[str] = Counter()

        for token in tokens:
            hallada = _coincidencia(token, prep_alto)
            if hallada:
                altas[hallada] += 1
                continue
            hallada = _coincidencia(token, prep_bajo)
            if hallada:
                bajas[hallada] += 1

        # Los emojis se cuentan sobre el texto original.
        emo_alto, emo_bajo = EMOJIS.get(clave, ((), ()))
        for emoji in emo_alto:
            cantidad = crudo.count(emoji)
            if cantidad:
                altas[emoji] += cantidad
        for emoji in emo_bajo:
            cantidad = crudo.count(emoji)
            if cantidad:
                bajas[emoji] += cantidad

        neto = sum(altas.values()) - sum(bajas.values())
        coincidencias_totales += sum(altas.values()) + sum(bajas.values())

        # Densidad por cada 100 palabras -> puntaje de 0 a 100 con tanh.
        densidad = 100.0 * neto / max(total_palabras, 1)
        puntaje = 50.0 + 50.0 * math.tanh(densidad / ESCALA_DENSIDAD)

        evidencia = (
            [f"+{palabra}×{n}" for palabra, n in altas.most_common(4)]
            + [f"-{palabra}×{n}" for palabra, n in bajas.most_common(4)]
        )
        rasgos.append(RasgoBigFive(
            clave=clave,
            nombre=NOMBRES[clave],
            puntaje=round(puntaje, 1),
            nivel=_nivel(puntaje),
            descripcion=DESCRIPCIONES[clave],
            evidencia=evidencia,
        ))

    return ResultadoBigFive(
        rasgos=rasgos,
        palabras_analizadas=total_palabras,
        coincidencias=coincidencias_totales,
        confianza=_confianza(total_palabras, coincidencias_totales),
    )


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA: ejecuta `python bigfive.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    muestra = [
        "Amante del arte y la música, me encanta viajar y aprender cosas nuevas. "
        "Gracias a mi familia y mis amigos por apoyarme siempre ❤️",
        "Hoy fiesta con amigos jajaja, a bailar toda la noche 🎉🔥 "
        "Mañana toca estudiar y cumplir mis metas con disciplina, "
        "la constancia y el trabajo siempre dan resultados en el proyecto.",
        "Un día difícil, estoy cansado y con mucho estrés 😔 "
        "pero con calma y tranquilidad todo mejora, gracias por el cariño.",
    ]
    resultado = analizar_bigfive(muestra)
    print(f"Palabras: {resultado.palabras_analizadas} | "
          f"Coincidencias: {resultado.coincidencias} | "
          f"Confianza: {resultado.confianza}")
    for rasgo in resultado.rasgos:
        print(f"{rasgo.nombre:28s} {rasgo.puntaje:5.1f}  {rasgo.nivel:6s} "
              f"{', '.join(rasgo.evidencia)}")
