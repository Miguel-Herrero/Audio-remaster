"""El HTML de la UI y el resto del paquete tienen que hablar el mismo
idioma. No hay infraestructura de tests de JS aqui, pero los puntos donde
los dos lados acuerdan un vocabulario si se pueden comprobar leyendo el
fichero — y son justo donde se rompen las cosas en silencio.

Caso real: `events.py` manda `outcome: "ran"` y la UI pintaba por
`data-state`, donde el valor equivalente se llama `done`. Como "ran" no
estaba en ninguna tabla, un paso recien ejecutado se quedaba con el
circulo de pendiente y la linea punteada. Nada fallaba; solo mentia.
"""

from __future__ import annotations

import re
from pathlib import Path

from remaster.events import OUTCOMES
from remaster.steps_meta import STEP_INFO

_INDEX = Path(__file__).resolve().parents[1] / "remaster" / "web" / "templates" / "index.html"


def _js_object_keys(name: str) -> set[str]:
    """Las claves de un `const <name> = { ... }` del <script>."""
    html = _INDEX.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \{{(.*?)\}};", html, re.S)
    assert match, f"no encuentro `const {name}` en index.html"
    return set(re.findall(r"(\w+):", match.group(1)))


def test_every_outcome_has_a_visual_state():
    """Si se añade un outcome a events.py hay que decirle a la UI como se
    pinta, o el paso se queda mudo.
    """
    assert set(OUTCOMES) <= _js_object_keys("OUTCOME_STATE")


def test_every_visual_state_has_a_glyph():
    states = _js_object_keys("OUTCOME_STATE")
    glyphs = _js_object_keys("GLYPH")
    # Los valores de OUTCOME_STATE son los estados que se pintan.
    html = _INDEX.read_text(encoding="utf-8")
    values = set(re.findall(r"\w+: '(\w+)'", re.search(r"const OUTCOME_STATE = \{(.*?)\};", html, re.S).group(1)))
    assert values <= glyphs, f"estados sin glifo: {values - glyphs}"
    assert "pending" in glyphs and "running" in glyphs
    assert states  # sanity


def test_every_visual_state_is_styled():
    """Cada estado tiene su regla CSS; si no, se ve como el de al lado."""
    html = _INDEX.read_text(encoding="utf-8")
    for state in ("done", "noop", "running", "skipped", "error"):
        assert f'.node[data-state="{state}"]' in html, state


def test_the_ui_does_not_hardcode_the_step_list():
    """Los pasos llegan por /api/steps; que no haya una copia en el HTML
    que se quede vieja.
    """
    html = _INDEX.read_text(encoding="utf-8")
    keys = [i.key for i in STEP_INFO]
    # `verify`, `project`, `align`, `separate`, `mux` y `loudness` si
    # aparecen: llevan panel propio o aviso propio. El resto no deberia.
    with_ui = {"verify", "project", "align", "separate", "mux", "loudness"}
    for key in keys:
        if key in with_ui:
            continue
        assert f"'{key}'" not in html, f"«{key}» esta escrito a mano en el HTML"


# --------------------------------------------------------------- las dos vistas

def test_every_tab_has_a_view():
    """Cada boton del nav apunta a un contenedor que existe."""
    html = _INDEX.read_text(encoding="utf-8")
    views = set(re.findall(r'class="tab" data-view="(\w+)"', html))
    assert views == {"episodes", "separar"}
    for view in views:
        assert f'id="view-{view}"' in html, view


def test_the_separate_view_has_its_own_log_and_buttons():
    """La vista Separar no puede reutilizar los ids de la de Episodios: si
    lo hiciera, un job de cada tipo se pisaria el log y el estado.
    """
    html = _INDEX.read_text(encoding="utf-8")
    for element in ("sep-input", "sep-out", "sep-models", "sep-btn",
                    "sep-cancel", "sep-log", "sep-status", "sep-preview"):
        assert f'id="{element}"' in html, element


def test_both_views_can_cancel():
    """Cancelar es requisito: una separacion en CPU puede durar mucho."""
    html = _INDEX.read_text(encoding="utf-8")
    assert 'id="run-cancel"' in html
    assert 'id="sep-cancel"' in html
    assert "/cancel" in html


def test_the_old_episode_hashes_still_work():
    """Los enlaces "#s03e01" son anteriores al nav y deben seguir abriendo
    su episodio: solo los que empiezan por "/" eligen vista.
    """
    html = _INDEX.read_text(encoding="utf-8")
    assert "raw.startsWith('/')" in html


def test_the_cancelled_state_is_handled():
    """`cancelled` no es `error`: lo pediste tu, no ha fallado nada."""
    html = _INDEX.read_text(encoding="utf-8")
    assert html.count("'cancelled'") >= 2
