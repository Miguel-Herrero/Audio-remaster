"""Lector del render_stats.html de REAPER (remaster/reaper/render_stats.py).

El HTML lleva los dB dos veces: como coordenada normalizada para pintar
el canvas y como texto. Hay que leer el texto — la coordenada no es dB.
"""

from __future__ import annotations

import pytest

from remaster.reaper.render_stats import (
    RenderStatsError,
    find_stats,
    newest_render_stats,
    parse_render_stats,
    parse_timecode,
)

_HTML = """<!DOCTYPE html>
<html><body>
<p>REAPER Render Sun Aug 30 11:29:55 2026</p>
<table>
<thead>
<tr><th>File</th><th>Length</th><th>Peak</th><th>Clips</th><th>Max LUFS-M</th>
<th>Max LUFS-S</th><th>LUFS-I</th><th>LRA</th></tr>
</thead>
<tbody>
<tr>
<td>EN VOX</td><td>53:02.805</td><td>-4.8</td><td>0</td>
<td>-14.6</td><td>-18.1</td><td>-23.4</td><td>15.6</td>
</tr>
<tr>
<td>ES VOX</td><td>53:02.805</td><td>-0.8</td><td>3</td>
<td>-10.5</td><td>-17.0</td><td>-23.9</td><td>11.4</td>
</tr>
</tbody>
</table>
<script type='text/javascript'>
const data_0={
  name:'EN VOX',
  length:3182.805,
  series:['Peak Hi','Peak Lo','Peak Hi','Peak Lo','LUFS-M','LUFS-S'],
  integrated:['LUFS-I',0.1300,'-23.43'],
  vals:[
[[0.0017,'0:05.459'],[0.0000,'-inf'],[0.0000,'-inf'],[0.0000,'-inf'],[0.0000,'-inf'],[1.2703,'-inf'],[1.2703,'-inf']],
[[0.0034,'0:10.918'],[0.0021,'-106.89'],[-0.0021,'-107.11'],[0.0025,'-104.39'],[-0.0024,'-104.68'],[0.8816,'-30.50'],[0.9081,'-28.75']],
[[0.0051,'1:16.378'],[0.0021,'-106.89'],[-0.0021,'-107.11'],[0.0025,'-104.39'],[-0.0024,'-104.68'],[0.8816,'-22.10'],[0.9081,'-21.40']]
  ]
};
const data_1={
  name:'ES VOX',
  length:3182.805,
  series:['Peak Hi','Peak Lo','Peak Hi','Peak Lo','LUFS-M','LUFS-S'],
  integrated:['LUFS-I',0.1300,'-23.90'],
  vals:[
[[0.0017,'0:05.459'],[0.0000,'-inf'],[0.0000,'-inf'],[0.0000,'-inf'],[0.0000,'-inf'],[1.2703,'-inf'],[1.2703,'-inf']],
[[0.0034,'0:10.918'],[0.0021,'-106.89'],[-0.0021,'-107.11'],[0.0025,'-104.39'],[-0.0024,'-104.68'],[0.8816,'-28.50'],[0.9081,'-26.75']],
[[0.0051,'1:16.378'],[0.0021,'-106.89'],[-0.0021,'-107.11'],[0.0025,'-104.39'],[-0.0024,'-104.68'],[0.8816,'-20.10'],[0.9081,'-19.40']]
  ]
};
</script>
</body></html>
"""


def _parse(tmp_path):
    path = tmp_path / "render_stats.html"
    path.write_text(_HTML)
    return parse_render_stats(path)


def test_reads_the_summary_row(tmp_path):
    es = find_stats(_parse(tmp_path), "ES VOX")
    assert es is not None
    assert es.length_s == pytest.approx(3182.805)
    assert es.peak_dbfs == pytest.approx(-0.8)
    assert es.clips == 3
    assert es.lufs_i == pytest.approx(-23.9)
    assert es.lra == pytest.approx(11.4)
    assert es.max_lufs_m == pytest.approx(-10.5)


def test_reads_the_time_series_from_text_not_from_canvas_coordinates(tmp_path):
    """El primer numero de cada par es la coordenada del canvas (0..1).
    Leerla en vez del texto daria niveles absurdos como -0.9 LUFS.
    """
    en = find_stats(_parse(tmp_path), "EN VOX")
    assert en.times == pytest.approx((5.459, 10.918, 76.378))
    assert en.lufs_s[1] == pytest.approx(-28.75)
    assert en.lufs_m[2] == pytest.approx(-22.10)
    assert en.lufs_s[0] == float("-inf")  # el silencio del principio


def test_series_of_both_tracks_line_up(tmp_path):
    stats = _parse(tmp_path)
    es, en = find_stats(stats, "ES VOX"), find_stats(stats, "EN VOX")
    assert len(es.times) == len(en.times) == 3
    assert es.times == en.times


def test_timecode_accepts_minutes_and_hours():
    assert parse_timecode("53:02.805") == pytest.approx(3182.805)
    assert parse_timecode("1:00:01.5") == pytest.approx(3601.5)
    assert parse_timecode("12.25") == pytest.approx(12.25)


def test_html_without_rows_raises(tmp_path):
    path = tmp_path / "vacio.html"
    path.write_text("<html><body>nada</body></html>")
    with pytest.raises(RenderStatsError):
        parse_render_stats(path)


def test_newest_render_stats_picks_the_most_recent(tmp_path):
    import os
    import time

    old = tmp_path / "render_stats.html"
    new = tmp_path / "render_stats2.html"
    old.write_text(_HTML)
    new.write_text(_HTML)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert newest_render_stats([tmp_path]) == new
    assert newest_render_stats([tmp_path / "no-existe"]) is None
