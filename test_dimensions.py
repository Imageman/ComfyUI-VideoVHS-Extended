"""Geometry resolution and single-pass filter construction checks."""

import pytest
from frame_mapping import dimensions
from media_utils import audio_filter, resize_filter
from fractions import Fraction
from test_support import main_test


@pytest.mark.parametrize("source,width,height,expected", [
    ((1024, 1024), "1920", "1080", (1920, 1080)),
    ((640, 480), "", "", (640, 480)), ((641, 481), "", "", (642, 482)),
    ((640, 480), "1920", "", (1920, 1440)), ((640, 480), "", "1080", (1440, 1080)),
    ((4, 3), "6", "", (6, 4)), ((3, 4), "", "6", (4, 6)),
])
def test_dimensions(source: tuple[int, int], width: str, height: str, expected: tuple[int, int]) -> None:
    """Resolve defaults, omitted dimensions and nearest-even derived sizes."""
    assert dimensions(source, width, height) == expected


@pytest.mark.parametrize("width,height", [("0", ""), ("3", ""), ("", "-2"), ("x", "2"), ("99999", "2")])
def test_bad_dimensions(width: str, height: str) -> None:
    """Reject explicit odd, invalid and oversized dimensions."""
    with pytest.raises(ValueError):
        dimensions((640, 480), width, height)


def test_filters() -> None:
    """Keep filters in one chain and skip tempo changes at equal duration."""
    assert "crop=1920:1080:(iw-ow)/2" in resize_filter((1024, 1024), (1920, 1080), "1920", "1080",
                                                     "crop", "bicubic")
    assert "pad=642:482:0:0" in resize_filter((641, 481), (642, 482), "", "", "pad", "lanczos")
    assert "atempo" not in audio_filter(Fraction(1), Fraction(1))
    assert audio_filter(Fraction(16), Fraction(1)).count("atempo=2") == 4
    assert audio_filter(Fraction(1), Fraction(16)).count("atempo=0.5") == 4


if __name__ == "__main__":
    main_test(__file__)
