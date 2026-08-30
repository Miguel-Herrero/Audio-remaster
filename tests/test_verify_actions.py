"""La capa que traduce hallazgos a instrucciones (build_actions).

El informe tenia todos los datos y no servia de nada: no decia que hacer
con ellos. Estos tests fijan que cada hallazgo salga convertido en una
accion concreta, y sobre todo que NO salga ninguna cuando no hay nada que
hacer — un informe que siempre pide algo se ignora enseguida.
"""

from __future__ import annotations


from remaster.steps.verify import (
    COMFORTABLE_HEADROOM_DB,
    STATUS_DOUBLE_GAIN,
    STATUS_OFF,
    Action,
    EnvelopeCheck,
    GlobalCheck,
    ItemCheck,
    VerifyReport,
    WindowCheck,
    build_actions,
    render_html,
)


def _report(**overrides) -> VerifyReport:
    base = dict(
        episode="s01e01", rpp_path="/tmp/s01e01.RPP",
        generated_at="2026-08-30T12:00:00", tolerance_db=2.0, item_tolerance_db=6.0,
        es_track="ES VOX", en_track="EN VOX", global_gain_db=-0.8,
        envelope_points=10, item_count=3, muted_items=0,
        sources=("es_vocals.flac",), fx_names=("JS: utility/volume",),
        stats_path="/tmp/render_stats.html", stats_modified="30/08/2026 11:30",
        rpp_modified="30/08/2026 11:00",
    )
    base.update(overrides)
    return VerifyReport(**base)


def _item(status: str, delta_db: float, start: float = 10.0) -> ItemCheck:
    return ItemCheck(
        index=0, start=start, end=start + 3.0, source="en_vocals.flac",
        envelope_db=delta_db, envelope_max_db=delta_db, item_gain_db=0.0,
        global_gain_db=-0.8, total_gain_db=delta_db, es_lufs=-20.0, en_lufs=-20.0 - delta_db,
        delta_db=delta_db, peak_dbfs=-8.0, status=status,
    )


def _healthy_global() -> GlobalCheck:
    return GlobalCheck(
        es_lufs_i=-23.9, en_lufs_i=-23.4, delta_db=-0.5,
        es_peak_dbfs=-6.0, en_peak_dbfs=-4.8, es_clips=0,
        es_lra=11.4, en_lra=15.6, headroom_db=6.0,
    )


def test_nothing_wrong_produces_no_chores():
    report = _report(global_check=_healthy_global())
    assert build_actions(report) == ()


def test_healthy_report_says_so_in_plain_words():
    html = render_html(_report(global_check=_healthy_global()))
    assert "Nada que tocar" in html


def test_stale_measurement_is_the_first_thing_reported():
    """Si el proyecto se guardo despues de medir, lo demas puede estar
    describiendo una version que ya no existe. Va primero.
    """
    report = _report(
        global_check=_healthy_global(),
        items=(_item(STATUS_DOUBLE_GAIN, 6.0),),
        stats_is_stale=True, stats_modified="30/08 11:30", rpp_modified="30/08 12:10",
    )
    actions = build_actions(report)
    assert "Vuelve a medir" in actions[0].title
    assert actions[0].urgency == "alta"
    assert "Calculate loudness of selected tracks via dry run render" in actions[0].how


def test_missing_measurement_tells_you_how_to_get_it():
    report = _report(stats_path=None)
    actions = build_actions(report)
    assert "Calculate loudness of selected tracks via dry run render" in actions[0].how
    # y NO las instrucciones viejas, que no existen como tal en REAPER
    assert "Dry run (no output file)" not in actions[0].how


def test_english_patches_become_one_action_with_one_line_per_spot():
    report = _report(
        global_check=_healthy_global(),
        items=(_item(STATUS_DOUBLE_GAIN, 6.0, start=317.0), _item(STATUS_DOUBLE_GAIN, 2.8, start=294.0)),
    )
    action = next(a for a in build_actions(report) if "audio en inglés" in a.title)
    assert action.urgency == "alta"
    assert len(action.spots) == 2
    # en orden de reproduccion (4:54 antes que 5:17), no por gravedad
    assert action.spots[0] == "4:54.000 → 4:57.000 (3.0s): ES baja 2.8 dB (suena más alto que EN)"
    assert action.spots[1] == "5:17.000 → 5:20.000 (3.0s): ES baja 6.0 dB (suena más alto que EN)"
    assert "envolvente de volumen" in action.how


def test_spots_are_listed_in_playback_order_but_trimmed_by_severity():
    """Recortar y ordenar son decisiones distintas: si sobran tramos se
    cae el menos grave, pero lo que queda se lista de principio a fin.
    """
    from remaster.steps.verify import MAX_SPOTS_LISTED

    # uno leve al principio y muchos graves despues: el leve es el que sobra
    items = [_item(STATUS_OFF, 6.1, start=1.0)]
    items += [_item(STATUS_OFF, 20.0, start=100.0 + 10 * n) for n in range(MAX_SPOTS_LISTED)]
    report = _report(global_check=_healthy_global(), items=tuple(items))
    action = next(a for a in build_actions(report) if "Escucha" in a.title)

    assert len(action.spots) == MAX_SPOTS_LISTED
    assert "0:01.000" not in action.spots[0]  # el leve se ha caido
    starts = [spot.split(" →")[0] for spot in action.spots]
    assert starts == sorted(starts, key=lambda t: (int(t.split(":")[0]), float(t.split(":")[1])))


def test_deviating_items_are_framed_as_listen_first_not_fix_blindly():
    report = _report(global_check=_healthy_global(), items=(_item(STATUS_OFF, -13.5),))
    action = next(a for a in build_actions(report) if "Escucha" in a.title)
    assert action.urgency == "media"
    assert "no corregirlos a ciegas" in action.why
    # delta negativo -> hay que SUBIR el castellano
    assert action.spots[0] == "0:10.000 → 0:13.000 (3.0s): ES sube 13.5 dB (suena más bajo que EN)"


def test_low_headroom_gives_the_exact_number_to_dial_in():
    tight = GlobalCheck(
        es_lufs_i=-23.9, en_lufs_i=-23.4, delta_db=-0.5,
        es_peak_dbfs=-0.8, en_peak_dbfs=-4.8, es_clips=0,
        es_lra=11.4, en_lra=15.6, headroom_db=0.8,
    )
    report = _report(global_check=tight, global_gain_db=-0.81)
    action = next(a for a in build_actions(report) if "sature" in a.title)
    # sin recortes esto es precaucion, no averia: no puede gritar como si
    # el audio ya estuviera roto, o se ignora el aviso cuando si lo este
    assert action.urgency == "media"
    assert "NO distorsiona" in action.why
    assert "es precaución, no avería" in action.why
    # -0.81 menos lo que falta para el margen comodo (3.0 - 0.8 = 2.2)
    expected = -0.81 - (COMFORTABLE_HEADROOM_DB - 0.8)
    assert f"{expected:+.2f} dB" in action.how
    assert "JS: utility/volume" in action.how
    # y avisa de que mide la pista de voz sola, no la mezcla final
    assert "mezcla final" in action.how


def test_actual_clipping_is_urgent_and_says_so():
    clipping = GlobalCheck(
        es_lufs_i=-23.9, en_lufs_i=-23.4, delta_db=-0.5,
        es_peak_dbfs=0.0, en_peak_dbfs=-4.8, es_clips=44,
        es_lra=11.4, en_lra=15.6, headroom_db=0.0,
    )
    action = next(a for a in build_actions(_report(global_check=clipping)) if "sature" in a.title)
    assert action.urgency == "alta"
    assert "44 muestras recortadas" in action.why
    assert "distorsión audible" in action.why


def test_tight_but_not_dangerous_headroom_is_only_informative():
    ok_ish = GlobalCheck(
        es_lufs_i=-23.9, en_lufs_i=-23.4, delta_db=-0.5,
        es_peak_dbfs=-2.0, en_peak_dbfs=-4.8, es_clips=0,
        es_lra=11.4, en_lra=15.6, headroom_db=2.0,
    )
    action = next(a for a in build_actions(_report(global_check=ok_ish)) if "sature" in a.title)
    assert action.urgency == "info"


def test_comfortable_headroom_raises_nothing():
    report = _report(global_check=_healthy_global())
    assert not any("saturar" in a.title for a in build_actions(report))


def test_envelope_verdict_is_reported_as_information_not_a_chore():
    report = _report(
        global_check=_healthy_global(),
        envelope=EnvelopeCheck(sd_with=1.25, sd_without=2.19, windows_out_with=2,
                               windows_out_without=18, windows_total=25, verdict="x"),
    )
    action = next(a for a in build_actions(report) if "envolvente" in a.title.lower())
    assert action.urgency == "info"
    assert "déjala como está" in action.title
    assert action.how == "No tienes que hacer nada."


def test_envelope_that_hurts_asks_to_redo_it():
    report = _report(
        global_check=_healthy_global(),
        envelope=EnvelopeCheck(sd_with=2.80, sd_without=1.10, windows_out_with=18,
                               windows_out_without=2, windows_total=25, verdict="x"),
    )
    action = next(a for a in build_actions(report) if "envolvente" in a.title.lower())
    assert "ya no ayuda" in action.title.lower()
    assert "--force" in action.how


def test_long_stretches_are_separate_from_single_moments():
    report = _report(
        global_check=_healthy_global(),
        windows=(WindowCheck(start=360.0, end=480.0, es_level=-27.8, en_level=-31.0, delta_db=3.17),),
    )
    action = next(a for a in build_actions(report) if "tramos largos" in a.title)
    assert "dos minutos seguidos" in action.why
    assert action.spots[0] == "6:00.000 → 8:00.000 (120.0s): ES baja 3.2 dB (suena más alto que EN)"


def test_report_leads_with_actions_and_hides_the_technical_detail():
    report = _report(global_check=_healthy_global(), items=(_item(STATUS_DOUBLE_GAIN, 6.0),))
    html = render_html(report)
    assert html.index("Qué tengo que hacer") < html.index("Detalle técnico")
    assert "<details>" in html  # el detalle va plegado
    assert "Impuesto" not in html  # jerga traducida del ingles, fuera


def test_actions_are_all_well_formed():
    report = _report(
        global_check=_healthy_global(),
        items=(_item(STATUS_DOUBLE_GAIN, 6.0), _item(STATUS_OFF, -13.5)),
        stats_is_stale=True,
    )
    for action in build_actions(report):
        assert isinstance(action, Action)
        assert action.urgency in {"alta", "media", "info"}
        assert action.title and action.why and action.how
        assert not action.title.endswith(".")  # titulos, no frases
