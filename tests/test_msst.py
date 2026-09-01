"""El catalogo de modelos y el argv que se le pasa a MSST.

Todo esto se puede probar sin el repo de MSST delante ni pesos descargados:
son rutas y listas de argumentos.
"""

from pathlib import Path

import pytest

from remaster.msst import (
    DEFAULT_CATALOG,
    FORMATS,
    MsstError,
    MsstModel,
    MsstRepo,
    MsstVariant,
    build_command,
    catalog_with_status,
    find_model,
    load_catalog,
)


@pytest.fixture
def repo(tmp_path):
    return MsstRepo(root=tmp_path / "msst", data_dir=tmp_path / "data")


@pytest.fixture
def model():
    return MsstModel(
        id="demo",
        name="Demo",
        model_type="bs_roformer",
        config="repo:configs/demo.yaml",
        checkpoint="data:demo/pesos.ckpt",
        stems=("vocals", "other"),
    )


# --------------------------------------------------------------------------
# El catalogo que se distribuye
# --------------------------------------------------------------------------

def test_the_shipped_catalog_parses():
    models = load_catalog()
    assert models, "el catalogo no puede estar vacio"
    for model in models:
        assert model.id and model.name and model.model_type
        assert model.config and model.checkpoint


def test_model_ids_are_unique():
    ids = [m.id for m in load_catalog()]
    assert len(ids) == len(set(ids))


def test_every_reference_has_a_known_prefix():
    for model in load_catalog():
        refs = [model.config, model.checkpoint]
        refs += [v.checkpoint for v in model.variants]
        for ref in refs:
            assert ref.startswith(("repo:", "data:", "/")), ref


def test_variant_ids_are_unique_within_a_model():
    for model in load_catalog():
        ids = [v.id for v in model.variants]
        assert len(ids) == len(set(ids)), model.id


def test_bandit_runs_on_cpu_only():
    """Metal no soporta float64 y la familia Bandit lo usa en sus buffers.

    Si algun dia alguien quita esta marca, `model.to("mps")` vuelve a
    reventar con "Cannot convert a MPS Tensor to float64".
    """
    models = {m.id: m for m in load_catalog()}
    assert models["bandit_v2"].cpu_only
    assert models["bandit_plus"].cpu_only


def test_downloadable_models_declare_a_size():
    for model in load_catalog():
        if model.checkpoint_url:
            assert model.size_mb > 0, model.id
        for var in model.variants:
            if var.checkpoint_url:
                assert var.size_mb > 0, f"{model.id}/{var.id}"


def test_a_local_catalog_overrides_by_id(tmp_path):
    extra = tmp_path / "mios.toml"
    extra.write_text(
        '[[model]]\n'
        'id = "bandit_v2"\n'
        'name = "El mio"\n'
        'model_type = "bandit_v2"\n'
        'config = "repo:x.yaml"\n'
        'checkpoint = "data:x.ckpt"\n',
        encoding="utf-8",
    )
    models = {m.id: m for m in load_catalog(extra=extra)}
    assert models["bandit_v2"].name == "El mio"
    # Y los demas siguen ahi.
    assert "bs_roformer_viperx" in models


def test_a_missing_local_catalog_is_not_an_error(tmp_path):
    assert load_catalog(extra=tmp_path / "no-existe.toml") == load_catalog()


def test_find_model_names_the_alternatives():
    with pytest.raises(MsstError) as exc:
        find_model(load_catalog(), "no-existe")
    assert "bandit_v2" in str(exc.value)


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------

def test_repo_prefix_resolves_inside_the_clone(repo):
    assert repo.resolve("repo:configs/x.yaml") == repo.root / "configs/x.yaml"


def test_data_prefix_resolves_outside_the_clone(repo):
    """Lo que descargamos nunca se escribe dentro del repo ajeno."""
    resolved = repo.resolve("data:bandit_v2/x.ckpt")
    assert resolved == repo.data_dir / "bandit_v2/x.ckpt"
    assert repo.root not in resolved.parents


def test_a_reference_without_prefix_must_be_absolute(repo):
    assert repo.resolve("/tmp/x.ckpt") == Path("/tmp/x.ckpt")
    with pytest.raises(MsstError):
        repo.resolve("configs/x.yaml")


def test_problems_reports_a_missing_repo(repo):
    assert repo.problems()


def test_problems_is_empty_when_everything_is_there(repo):
    repo.root.mkdir(parents=True)
    (repo.root / "inference.py").touch()
    (repo.root / "venv" / "bin").mkdir(parents=True)
    (repo.root / "venv" / "bin" / "python").touch()
    assert repo.problems() == []


# --------------------------------------------------------------------------
# Variantes por idioma
# --------------------------------------------------------------------------

def test_no_variant_means_the_default_checkpoint(model):
    assert model.variant(None) is None
    assert model.checkpoint_ref(None) == "data:demo/pesos.ckpt"


def test_a_variant_swaps_the_checkpoint(model):
    spa = MsstVariant(id="spa", label="espanol", checkpoint="data:demo/spa.ckpt")
    model = MsstModel(**{**model.__dict__, "variants": (spa,)})
    assert model.checkpoint_ref(model.variant("spa")) == "data:demo/spa.ckpt"


def test_an_unknown_variant_is_rejected(model):
    with pytest.raises(MsstError):
        model.variant("klingon")


def test_the_slug_carries_the_language(model):
    spa = MsstVariant(id="spa", label="espanol", checkpoint="data:demo/spa.ckpt")
    model = MsstModel(**{**model.__dict__, "variants": (spa,)})
    assert model.slug(None) == "demo"
    assert model.slug(model.variant("spa")) == "demo_spa"


# --------------------------------------------------------------------------
# El argv
# --------------------------------------------------------------------------

def test_the_command_carries_the_three_coupled_arguments(repo, model, tmp_path):
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out")
    assert argv[0] == str(repo.python_bin())
    assert argv[1] == str(repo.inference_py())
    assert "--model_type" in argv and argv[argv.index("--model_type") + 1] == "bs_roformer"
    assert argv[argv.index("--config_path") + 1] == str(repo.root / "configs/demo.yaml")
    assert argv[argv.index("--start_check_point") + 1] == str(repo.data_dir / "demo/pesos.ckpt")


def test_flac24_needs_both_flags(repo, model, tmp_path):
    """`--pcm_type PCM_24` a secas ya escribe flac, pero la combinacion que
    de verdad pide 24 bits es esta; y `--flac_file` con FLOAT cae a 16 bits.
    """
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out", fmt="flac24")
    assert "--flac_file" in argv
    assert argv[argv.index("--pcm_type") + 1] == "PCM_24"


def test_wav32_does_not_ask_for_flac(repo, model, tmp_path):
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out", fmt="wav32")
    assert "--flac_file" not in argv
    assert argv[argv.index("--pcm_type") + 1] == "FLOAT"


def test_an_unknown_format_is_rejected(repo, model, tmp_path):
    with pytest.raises(MsstError):
        build_command(repo, model, tmp_path / "in", tmp_path / "out", fmt="mp3")


def test_optional_flags_are_off_by_default(repo, model, tmp_path):
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out")
    for flag in ("--extract_instrumental", "--use_tta", "--force_cpu"):
        assert flag not in argv


def test_optional_flags_can_be_turned_on(repo, model, tmp_path):
    argv = build_command(
        repo, model, tmp_path / "in", tmp_path / "out",
        extract_instrumental=True, use_tta=True, force_cpu=True,
    )
    for flag in ("--extract_instrumental", "--use_tta", "--force_cpu"):
        assert flag in argv


def test_a_cpu_only_model_forces_cpu_without_being_asked(repo, model, tmp_path):
    model = MsstModel(**{**model.__dict__, "cpu_only": True})
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out")
    assert "--force_cpu" in argv


def test_the_output_template_keeps_names_flat(repo, model, tmp_path):
    """El default de MSST mete cada pista en su propia subcarpeta."""
    argv = build_command(repo, model, tmp_path / "in", tmp_path / "out")
    template = argv[argv.index("--filename_template") + 1]
    assert "/" not in template


def test_every_format_maps_to_flags():
    for flags in FORMATS.values():
        assert "--pcm_type" in flags


# --------------------------------------------------------------------------
# Lo que ve la UI
# --------------------------------------------------------------------------

def test_status_reports_what_is_on_disk(repo, model):
    status = repo.status(model)
    assert status["config_ok"] is False
    assert status["checkpoint_ok"] is False

    config = repo.resolve(model.config)
    config.parent.mkdir(parents=True)
    config.touch()
    assert repo.status(model)["config_ok"] is True


def test_catalog_with_status_is_serializable(repo):
    entries = catalog_with_status(load_catalog(), repo)
    assert len(entries) == len(load_catalog())
    for entry in entries:
        assert set(entry) >= {"id", "name", "stems", "ready", "variants", "cpu_only"}
        assert isinstance(entry["stems"], list)


def test_the_catalog_file_ships_with_the_package():
    assert DEFAULT_CATALOG.is_file()


# --------------------------------------------------------------------------
# El sample rate declarado tiene que ser el de verdad
# --------------------------------------------------------------------------

def test_every_model_declares_a_sample_rate():
    """Se enseña en la interfaz para decidir: no puede faltar en ninguno."""
    for model in load_catalog():
        assert model.sample_rate > 0, model.id


def test_the_declared_sample_rate_matches_the_config():
    """El catalogo repite un dato que vive en el YAML de MSST, asi que puede
    quedarse viejo cuando el repo se actualiza. Esto lo detecta.

    Se salta si no hay un clon del repo a mano (en CI, por ejemplo).
    """
    import os
    import re

    root = os.environ.get("MSST_REPO_DIR")
    if not root:
        pytest.skip("sin MSST_REPO_DIR, no hay configs contra los que comparar")
    repo = MsstRepo(root=Path(root).expanduser(), data_dir=Path("/dev/null"))

    comprobados = 0
    for model in load_catalog():
        if not model.config.startswith("repo:"):
            continue  # el config se descarga; no esta en disco todavia
        config = repo.resolve(model.config)
        if not config.is_file():
            continue
        match = re.search(r"^\s*sample_rate:\s*(\d+)", config.read_text(), re.M)
        if not match:
            continue
        real = int(match.group(1))
        assert model.sample_rate == real, (
            f"{model.id}: el catalogo dice {model.sample_rate} Hz "
            f"y {config.name} dice {real} Hz"
        )
        comprobados += 1
    assert comprobados, "no he podido comprobar ningun config"
