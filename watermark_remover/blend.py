"""Match patch boundary colors with a harmonic correction; no spatial blur."""
import numpy as np
from scipy.fft import dstn, idstn


def match_boundary(predicted, reference, clean_edges=(True, True, True, True)):
    """Preserve model gradients while matching the clean surrounding boundary.

    The correction solves Laplace's equation with Dirichlet boundary values.
    It corrects illumination/color drift, rather than low-pass filtering detail.
    Only the supplied patch is returned; outside pixels are never modified.
    """
    if predicted.shape[0] < 4 or predicted.shape[1] < 4:
        return predicted
    height, width = predicted.shape[:2]
    delta = reference.astype(np.float64)-predicted.astype(np.float64)
    top, bottom, left, right = clean_edges
    if not top:
        delta[0] = delta[-1] if bottom else 0
    if not bottom:
        delta[-1] = delta[0] if top else 0
    if not left:
        delta[:, 0] = delta[:, -1] if right else 0
    if not right:
        delta[:, -1] = delta[:, 0] if left else 0
    correction = np.zeros_like(delta)
    correction[0], correction[-1] = delta[0], delta[-1]
    correction[:, 0], correction[:, -1] = delta[:, 0], delta[:, -1]
    forcing = np.zeros_like(delta[1:-1, 1:-1])
    forcing[0] -= delta[0, 1:-1]
    forcing[-1] -= delta[-1, 1:-1]
    forcing[:, 0] -= delta[1:-1, 0]
    forcing[:, -1] -= delta[1:-1, -1]
    eigenvalues = (2*np.cos(np.pi*np.arange(1, height-1)/(height-1))[:, None]
                   + 2*np.cos(np.pi*np.arange(1, width-1)/(width-1))[None, :] - 4)[:, :, None]
    correction[1:-1, 1:-1] = idstn(dstn(forcing, type=1, norm='ortho', axes=(0, 1))/eigenvalues,
                                  type=1, norm='ortho', axes=(0, 1))
    return np.clip(np.rint(predicted.astype(np.float64)+correction), 0, 255).astype(np.uint8)
