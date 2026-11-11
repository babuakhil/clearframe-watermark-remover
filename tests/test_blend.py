import numpy as np

from watermark_remover.blend import match_boundary


def test_color_offset_is_removed_without_blurring_texture():
    y, x = np.mgrid[:46, :58]
    texture = ((x % 2)*10 + (y % 3)*7).astype(np.uint8)
    clean = np.stack([80+texture, 100+texture, 110+texture], axis=-1)
    predicted = (clean.astype(int)+np.array([20, -12, 8])).astype(np.uint8)
    reference = clean.copy()
    # A watermark in the reference interior must not get copied back in.
    reference[12:30, 16:40] = 240
    corrected = match_boundary(predicted, reference)
    assert np.array_equal(corrected, clean)
    assert np.array_equal(np.diff(corrected.astype(int), axis=1), np.diff(clean.astype(int), axis=1))


def test_boundary_matches_and_unobservable_frame_edge_is_not_copied():
    reference = np.full((24, 30, 3), 90, dtype=np.uint8)
    predicted = np.full_like(reference, 60)
    reference[0] = 255
    corrected = match_boundary(predicted, reference, (False, True, True, True))
    assert np.array_equal(corrected, np.full_like(reference, 90))
