"""Conversion de los checkpoints de PyTorch Lightning.

Los pesos publicados de Bandit v2 son checkpoints de Lightning enteros. El
cargador de MSST desenvuelve `state_dict` pero deja el prefijo `model.` en
las claves y no descarta las de la funcion de perdida, y su `load_state_dict`
es estricto: sin esta conversion aborta con "Unexpected key(s) in state_dict".
"""

import pytest

torch = pytest.importorskip("torch")

from remaster.msst import fix_lightning_checkpoint


def _lightning_checkpoint():
    """Lo que hay dentro de un .ckpt recien bajado de Zenodo."""
    return {
        "epoch": 42,
        "global_step": 1000,
        "pytorch-lightning_version": "2.0.0",
        "optimizer_states": [{"algo": "adam"}],
        "lr_schedulers": [],
        "state_dict": {
            "model.capa.weight": torch.ones(2, 2),
            "model.capa.bias": torch.zeros(2),
            "loss_handler.escala": torch.ones(1),
        },
    }


def test_the_prefix_is_stripped(tmp_path):
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)

    fix_lightning_checkpoint(src, dst)

    keys = set(torch.load(dst, map_location="cpu", weights_only=False))
    assert keys == {"capa.weight", "capa.bias"}


def test_the_loss_keys_are_dropped(tmp_path):
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)

    fix_lightning_checkpoint(src, dst)

    keys = torch.load(dst, map_location="cpu", weights_only=False)
    assert not any(k.startswith("loss_handler") for k in keys)


def test_the_lightning_bookkeeping_does_not_survive(tmp_path):
    """`epoch`, `optimizer_states` y compania son justo lo que hacia fallar
    el `load_state_dict` estricto de MSST.
    """
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)

    fix_lightning_checkpoint(src, dst)

    keys = set(torch.load(dst, map_location="cpu", weights_only=False))
    assert keys.isdisjoint({"epoch", "global_step", "optimizer_states", "state_dict"})


def test_the_result_loads_strictly(tmp_path):
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)
    fix_lightning_checkpoint(src, dst)

    model = torch.nn.Module()
    model.capa = torch.nn.Linear(2, 2)
    model.load_state_dict(torch.load(dst, map_location="cpu", weights_only=False), strict=True)
    assert torch.equal(model.capa.weight, torch.ones(2, 2))


def test_the_number_of_keys_is_reported(tmp_path):
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)
    assert fix_lightning_checkpoint(src, dst) == 2


def test_an_already_flat_checkpoint_is_left_alone(tmp_path):
    """Convertir dos veces no debe estropear nada."""
    src, dst = tmp_path / "raw.ckpt", tmp_path / "fixed.ckpt"
    torch.save({"capa.weight": torch.ones(2, 2)}, src)

    fix_lightning_checkpoint(src, dst)

    assert set(torch.load(dst, map_location="cpu", weights_only=False)) == {"capa.weight"}


def test_the_destination_directory_is_created(tmp_path):
    src = tmp_path / "raw.ckpt"
    dst = tmp_path / "sin" / "crear" / "fixed.ckpt"
    torch.save(_lightning_checkpoint(), src)

    fix_lightning_checkpoint(src, dst)

    assert dst.is_file()
