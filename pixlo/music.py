"""Beat and segment analysis for music tracks you add yourself in assets/music/.

(Reels normally use original music from synth.py; no third-party audio is fetched automatically.)
"""
import subprocess
from pathlib import Path

import numpy as np


# ------------------------------------------------------------------ analysis
def _decode(path: Path, sr: int = 22050) -> np.ndarray:
    import imageio_ffmpeg
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(path),
                          "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def analyze(path: Path, sr: int = 22050, hop: int = 512) -> dict:
    """Tempo (BPM) from the onset envelope's autocorrelation, and loudness per second for segment picking."""
    y = _decode(path, sr)
    n_fft = 1024
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop] * np.hanning(n_fft)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
    onset = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)       # spectral flux
    onset = onset - onset.mean()
    fps = sr / hop
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 60) + 1)  # 180..60 BPM
    ac = np.array([np.dot(onset[:-lag], onset[lag:]) for lag in lags])
    bpms = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 110) / 0.9) ** 2)        # gentle preference for ~110 BPM
    bpm = float(bpms[np.argmax(ac * prior)])
    per_sec = y[: len(y) // sr * sr].reshape(-1, sr)
    energy = np.sqrt((per_sec ** 2).mean(axis=1))
    return {"bpm": round(bpm, 1), "energy": energy, "seconds": len(y) / sr}


def best_start(analysis: dict, duration: float) -> float:
    """Earliest window of `duration` seconds that is nearly as energetic as the loudest one (skips quiet intros)."""
    energy, win = analysis["energy"], max(1, int(round(duration)))
    if len(energy) <= win:
        return 0.0
    means = np.convolve(energy, np.ones(win) / win, mode="valid")
    limit = max(1, int(len(means) * 0.7))  # don't start in the outro
    good = np.nonzero(means[:limit] >= 0.85 * means[:limit].max())[0]
    return float(good[0]) if len(good) else 0.0
