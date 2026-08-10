"""
Monitor de vacantes MEP (apps.mep.go.cr/formulario)
-----------------------------------------------------
Busca la palabra "filosofía" (con o sin tilde, mayúsc/minúsc) en la
tabla de vacantes, recorriendo cada Dirección Regional del <select>,
porque el contenido se carga vía Blazor Server (SignalR) y no existe
en el HTML estático.

Requiere: pip install playwright && playwright install chromium
Variables de entorno necesarias:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = "https://apps.mep.go.cr/formulario"
STATE_FILE = Path("state.json")
PALABRA_OBJETIVO = "filosofia"  # normalizada sin tilde


def normalizar(texto: str) -> str:
    """Quita tildes y pasa a minúsculas para comparar sin depender de acentos."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_tildes.lower()


def obtener_texto_todas_las_regiones(page) -> str:
    """Selecciona cada opción del combo de Dirección Regional y concatena
    el texto de la tabla resultante (recorriendo paginación si existe)."""
    opciones = page.eval_on_selector_all(
        "#regionalSelect option", "els => els.map(e => e.value).filter(v => v)"
    )

    texto_total = []
    for valor in opciones:
        page.select_option("#regionalSelect", valor)
        # Blazor Server actualiza la tabla vía WebSocket; esperamos a que
        # el contenido dejе de decir "Seleccione una Dirección Regional..."
        try:
            page.wait_for_function(
                """() => {
                    const t = document.querySelector('.mud-table-body');
                    return t && !t.innerText.includes('Seleccione una');
                }""",
                timeout=8000,
            )
        except Exception:
            pass
        page.wait_for_timeout(1200)  # margen extra para el render

        # Recorrer páginas si el paginador tiene más de una página
        while True:
            body = page.query_selector(".mud-table-body")
            if body:
                texto_total.append(body.inner_text())

            siguiente = page.query_selector('button[aria-label="Next page"]')
            if siguiente and siguiente.is_enabled():
                siguiente.click()
                page.wait_for_timeout(800)
            else:
                break

    return "\n".join(texto_total)


def revisar_pagina() -> tuple[bool, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        texto = obtener_texto_todas_las_regiones(page)
        browser.close()

    encontrado = PALABRA_OBJETIVO in normalizar(texto)
    return encontrado, texto


def cargar_estado_previo() -> bool:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text()).get("encontrado", False)
    return False


def guardar_estado(encontrado: bool) -> None:
    STATE_FILE.write_text(json.dumps({"encontrado": encontrado}))


def notificar_telegram(mensaje: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": mensaje},
        timeout=15,
    )


def main():
    encontrado, _texto = revisar_pagina()
    ya_estaba_antes = cargar_estado_previo()

    # Notificar solo en la TRANSICIÓN de "no aparece" -> "aparece",
    # para no recibir el mismo aviso cada 30 minutos.
    if encontrado and not ya_estaba_antes:
        notificar_telegram(
            "🔔 ¡Apareció 'Filosofía' en las vacantes del MEP!\n"
            f"Revisa ya: {URL}"
        )
        print("Palabra encontrada. Notificación enviada.")
    elif encontrado:
        print("Palabra sigue presente, ya se había notificado antes.")
    else:
        print("Palabra no encontrada en esta corrida.")

    guardar_estado(encontrado)


if __name__ == "__main__":
    sys.exit(main())
