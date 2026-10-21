"""Deterministic exact-frame temporal mapping and parsing checks."""

from fractions import Fraction
import pytest

from frame_mapping import FrameMapping, parse_fps, parse_frames
from test_support import main_test


@pytest.mark.parametrize("source,target", [(90, 97), (107, 97), (141, 134), (192, 186), (73, 81),
                                          (22, 5), (5, 22), (1, 24), (17, 17), (1, 1)])
def test_mapping(source: int, target: int) -> None:
    """Keep exact count, monotone indices and both available endpoints."""
    mapping = FrameMapping(list(range(source)), target)
    assert len(mapping) == target
    assert mapping[0] == 0 and mapping[-1] == source - 1
    assert list(mapping) == sorted(mapping)
    for i in range(target):
        expected = 0 if target == 1 else (2 * i * (source - 1) + target - 1) // (2 * (target - 1))
        assert mapping[i] == expected
    with pytest.raises(IndexError):
        mapping[target]


@pytest.mark.parametrize("value", ["", "0", "default", "AUTO", "none", None, "  auto  "])
def test_unspecified(value: str | None) -> None:
    """Treat all specified sentinel spellings as no retime."""
    assert parse_frames(value, 90) == 90


@pytest.mark.parametrize("value", ["-1", "bad", "1.2", "1", "00"])
def test_bad_count(value: str) -> None:
    """Reject malformed and endpoint-breaking counts."""
    with pytest.raises(ValueError):
        parse_frames(value, 90)


def test_fps_and_rounding() -> None:
    """Preserve rational rates and round exact half ties upward."""
    assert parse_fps("24000/1001") == Fraction(24000, 1001)
    assert parse_fps("24") == 24
    assert list(FrameMapping([0, 1], 3)) == [0, 1, 1]
    for value in ("0", "-1", "1/0", "nan", "1001"):
        with pytest.raises(ValueError):
            parse_fps(value)


if __name__ == "__main__":
    main_test(__file__)
