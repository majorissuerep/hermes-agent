"""Installed consumer metadata and real image processing agree with the Pillow pin."""
from importlib import metadata
from pathlib import Path
import tomllib

import numpy as np
from packaging.requirements import Requirement
from PIL import Image
import pytest

from pm import workspace


def test_local_archive_inputs_survive_the_core_build_snapshot(tmp_path):
    source = Path(__file__).resolve().parents[2]
    document = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    inputs = [row["path"] for row in document["tool"]["uv"]["sources"].values()
              if isinstance(row, dict) and "path" in row]
    assert inputs, "this contract requires actual archived core inputs"
    workspace._copy_core_inputs(source, tmp_path)
    for relative in inputs:
        assert (tmp_path / relative).read_bytes() == (source / relative).read_bytes()


@pytest.mark.parametrize("consumer", ["browser-harness", "fastembed"])
def test_installed_consumer_requirements_accept_the_installed_pillow(consumer):
    try:
        requirements = metadata.requires(consumer)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{consumer} is not selected in this environment")
    pillow = metadata.version("Pillow")
    active = [Requirement(item) for item in requirements
              if Requirement(item).name.lower() == "pillow"
              and (Requirement(item).marker is None or Requirement(item).marker.evaluate())]
    assert active
    assert all(requirement.specifier.contains(pillow) for requirement in active)


def test_browser_contact_sheet_preserves_the_captured_image(tmp_path):
    from browser_harness.video_render import contact_sheet

    capture = tmp_path / "capture.png"
    Image.new("RGBA", (800, 450), (12, 180, 90, 255)).save(capture)
    output = tmp_path / "review.jpg"
    contact_sheet([{"path": capture, "label": "capture", "time": 0.5}], output, "Review")
    with Image.open(output) as sheet:
        sheet.load()
        assert sheet.mode == "RGB"
        red, green, blue = sheet.getpixel((208, 162))
    assert green > 150 and red < 40 and 60 < blue < 120


def test_fastembed_preprocessor_accepts_rgba_and_non_square_sizes():
    pytest.importorskip("fastembed")
    from fastembed.image.transform.operators import Compose

    processor = Compose.from_config({
        "do_resize": True, "size": {"height": 48, "width": 80},
        "do_center_crop": False, "do_rescale": True,
        "rescale_factor": 1 / 255, "do_normalize": True,
        "image_mean": [0.5, 0.5, 0.5], "image_std": [0.5, 0.5, 0.5],
    })
    [pixels] = processor([Image.new("RGBA", (160, 90), (255, 0, 127, 255))])
    assert pixels.shape == (3, 48, 80)
    assert np.isfinite(pixels).all()
    assert np.allclose(pixels[0], 1.0)
    assert np.allclose(pixels[1], -1.0)
    assert np.allclose(pixels[2], 2 * 127 / 255 - 1, atol=1e-6)
