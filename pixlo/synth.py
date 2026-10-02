"""Original background music, synthesised from scratch for each Reel (no samples, no third-party audio).

A small arranger picks a key, tempo and chord progression for the post's mood, then renders soft pads,
plucked-string arpeggios, a bass line, a sparse melody and light drums with plain numpy, adds a convolution
reverb and writes a WAV. The same post id always gives the same track; different posts get different ones.
"""
import hashlib
import wave
from pathlib import Path

import numpy as np

SR = 44100
MAJOR, MINOR = [0, 2, 4, 5, 7, 9, 11], [0, 2, 3, 5, 7, 8, 10]
PENTA_MAJOR, PENTA_MINOR = [0, 2, 4, 7, 9], [0, 3, 5, 7, 10]

# Scale degrees (0 = tonic) of the chord for each bar
MOODS = {
    "calm":      {"bpm": (68, 78),   "scale": MAJOR, "prog": [[0, 3], [0, 5, 3, 4], [5, 3, 0, 4]],
                  "arp": "quarters", "drums": "none",  "sevenths": True,  "bright": 0.55},
    "romantic":  {"bpm": (72, 80),   "scale": MAJOR, "prog": [[0, 5, 3, 4], [0, 2, 5, 3]],
                  "arp": "quarters", "drums": "soft",  "sevenths": True,  "bright": 0.6},
    "adventure": {"bpm": (92, 102),  "scale": MAJOR, "prog": [[5, 3, 0, 4], [0, 4, 5, 3]],
                  "arp": "eighths",  "drums": "groove", "sevenths": False, "bright": 0.7},
    "upbeat":    {"bpm": (108, 118), "scale": MAJOR, "prog": [[0, 4, 5, 3], [0, 3, 4, 3]],
                  "arp": "eighths",  "drums": "pop",   "sevenths": False, "bright": 0.75},
    "epic":      {"bpm": (84, 92),   "scale": MINOR, "prog": [[0, 5, 2, 6], [0, 6, 5, 6]],
                  "arp": "eighths",  "drums": "epic",  "sevenths": False, "bright": 0.65},
}


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


# ------------------------------------------------------------------ instruments
def _pluck(freq: float, dur: float, bright: float, rng) -> np.ndarray:
    """Plucked string: harmonics that decay faster the higher they are, plus a tiny attack."""
    t = _t(dur)
    out = np.zeros_like(t)
    for h in range(1, 10):
        f = freq * h * (1 + 0.0006 * h * h)  # slight inharmonicity like a real string
        if f > SR / 2.2:
            break
        out += (bright ** (h - 1)) / h * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * np.exp(-(1.3 + 1.1 * h) * t)
    return out * (1 - np.exp(-t / 0.003))


def _pad(freq: float, dur: float, detune_cents: float) -> np.ndarray:
    t = _t(dur)
    f = freq * 2 ** (detune_cents / 1200)
    out = sum(np.sin(2 * np.pi * f * h * t) / h ** 1.7 for h in range(1, 7))
    attack, release = min(0.5, dur / 3), min(0.7, dur / 3)
    env = np.minimum(1, t / attack) * np.minimum(1, (dur - t) / release)
    return out * env


def _bass(freq: float, dur: float) -> np.ndarray:
    t = _t(dur)
    out = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)
    return out * np.exp(-2.2 * t) * (1 - np.exp(-t / 0.006)) * np.minimum(1, (dur - t) / 0.05)


def _bell(freq: float, dur: float) -> np.ndarray:
    t = _t(dur)
    out = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2.01 * t) * np.exp(-3 * t)
    return out * np.exp(-1.8 * t) * (1 - np.exp(-t / 0.004))


def _kick() -> np.ndarray:
    t = _t(0.4)
    f = 45 + 80 * np.exp(-t * 32)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)


def _hat(rng) -> np.ndarray:
    t = _t(0.07)
    n = np.diff(np.diff(rng.standard_normal(len(t) + 2)))  # crude high-pass
    return n / 4 * np.exp(-t * 70)


def _clap(rng) -> np.ndarray:
    t = _t(0.25)
    n = np.diff(rng.standard_normal(len(t) + 1)) / 2
    return n * np.exp(-t * 22) + 0.5 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)


def _tom(freq: float) -> np.ndarray:
    t = _t(0.5)
    f = freq * (1 + 0.5 * np.exp(-t * 20))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6)


# ------------------------------------------------------------------ mixing helpers
def _place(track: np.ndarray, sound: np.ndarray, at: float, gain: float, pan: float = 0.0) -> None:
    """Add a mono sound into a stereo track at `at` seconds, with constant-power pan (-1 left .. 1 right)."""
    start = int(at * SR)
    if start >= len(track):
        return
    sound = sound[: len(track) - start]
    left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    track[start:start + len(sound), 0] += sound * gain * left
    track[start:start + len(sound), 1] += sound * gain * right


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(len(x), 1 / SR)[:, None]
    return np.fft.irfft(spec / np.sqrt(1 + (f / cutoff) ** 4), n=len(x), axis=0)


def _reverb(x: np.ndarray, rng, seconds: float = 1.6, decay: float = 0.45) -> np.ndarray:
    """Convolution reverb with a decaying-noise impulse response (different per channel for width)."""
    t = _t(seconds)
    n = 1 << int(np.ceil(np.log2(len(x) + len(t))))
    out = np.zeros_like(x)
    for ch in range(2):
        ir = rng.standard_normal(len(t)) * np.exp(-t / decay)
        ir = _lowpass(np.stack([ir, ir], 1), 5000)[:, 0]
        ir /= np.sqrt((ir ** 2).sum())
        out[:, ch] = np.fft.irfft(np.fft.rfft(x[:, ch], n) * np.fft.rfft(ir, n), n)[: len(x)]
    return out


# ------------------------------------------------------------------ arranger
def plan(mood: str, seed_text: str) -> dict:
    """Pick tempo, key and progression deterministically from the post id."""
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    m = MOODS.get(mood, MOODS["calm"])
    return {
        "mood": mood if mood in MOODS else "calm",
        "seed": seed,
        "bpm": float(rng.integers(m["bpm"][0], m["bpm"][1] + 1)),
        "root": int(rng.choice([45, 47, 48, 50, 52, 53, 55])),  # A2..G3
        "prog": [int(d) for d in m["prog"][int(rng.integers(len(m["prog"])))]],
    }


def render(p: dict, duration: float, out_path: Path) -> Path:
    m = MOODS[p["mood"]]
    rng = np.random.default_rng(p["seed"])
    beat = 60 / p["bpm"]
    bar = 4 * beat
    scale, root = m["scale"], p["root"]
    penta = PENTA_MAJOR if scale is MAJOR else PENTA_MINOR
    n = int((duration + 2.0) * SR)
    dry, wet_send = np.zeros((n, 2)), np.zeros((n, 2))

    def degree(d: int, octave: int = 0) -> int:
        return root + 12 * (octave + d // 7) + scale[d % 7]

    melody_note = root + 24 + penta[int(rng.integers(len(penta)))]
    bars = int(np.ceil((duration + 1) / bar))
    for b in range(bars):
        t0 = b * bar
        d = p["prog"][b % len(p["prog"])]
        chord = [degree(d), degree(d + 2), degree(d + 4)] + ([degree(d + 6)] if m["sevenths"] else [])

        # Pad: chord in the middle register, slightly detuned left/right
        for note in chord:
            f = _hz(note + 12)
            _place(wet_send, _pad(f, bar + 0.3, -5), t0, 0.07, -0.5)
            _place(wet_send, _pad(f, bar + 0.3, +5), t0, 0.07, 0.5)

        # Bass
        bass_hits = [0, 2] if m["drums"] in ("none", "soft") else [0, 1.5, 2, 3]
        for k in bass_hits:
            _place(dry, _bass(_hz(degree(d) - 12), beat * 1.5), t0 + k * beat, 0.32)

        # Plucked arpeggio over two octaves
        tones = sorted(chord) + [x + 12 for x in sorted(chord)]
        steps = 8 if m["arp"] == "eighths" else 4
        pattern = [0, 2, 1, 3, 2, 4, 3, 5] if steps == 8 else [0, 2, 3, 4]
        for s in range(steps):
            note = tones[pattern[s] % len(tones)] + 12
            _place(wet_send, _pluck(_hz(note), 1.4, m["bright"], rng), t0 + s * bar / steps,
                   0.22 if s % 2 == 0 else 0.16, pan=0.35 if s % 2 else -0.35)

        # Sparse melody from bar 2: a step-wise walk on the pentatonic scale, landing on chord tones
        if b >= 1:
            for k in (0, 1.5, 2.5) if m["arp"] == "eighths" else (0, 2):
                if rng.random() < 0.75:
                    cands = [root + 24 + 12 * o + iv for o in (0, 1) for iv in penta]
                    cands = [c for c in cands if abs(c - melody_note) <= 4] or cands
                    on_beat = k in (0, 2)
                    chord_pcs = {x % 12 for x in chord}
                    if on_beat and any(c % 12 in chord_pcs for c in cands):
                        cands = [c for c in cands if c % 12 in chord_pcs]
                    melody_note = int(rng.choice(cands))
                    _place(wet_send, _bell(_hz(melody_note), beat * 2), t0 + k * beat, 0.13, 0.1)

        # Drums (enter on bar 2 except pop, which needs energy from the first second)
        style = m["drums"]
        if style == "none" or (b == 0 and style != "pop"):
            continue
        for k in range(8):
            at = t0 + k * beat / 2
            if style in ("pop", "groove"):
                _place(dry, _hat(rng), at, 0.10 if k % 2 else 0.06, 0.4)
            if style == "soft" and k % 2 == 1:
                _place(dry, _hat(rng), at, 0.035, 0.3)
        kicks = {"pop": [0, 2], "groove": [0, 1.5, 2], "epic": [0, 2.5], "soft": [0]}[style]
        for k in kicks:
            _place(dry, _kick(), t0 + k * beat, 0.55 if style != "soft" else 0.3)
        if style in ("pop", "groove"):
            for k in (1, 3):
                _place(dry, _clap(rng), t0 + k * beat, 0.22)
        if style == "epic":
            _place(dry, _tom(110), t0 + 3 * beat, 0.35, -0.3)
            _place(dry, _tom(82), t0 + 3.5 * beat, 0.35, 0.3)
            _place(dry, _clap(rng), t0 + 2 * beat, 0.25)

    mix = dry + wet_send + 0.32 * _reverb(wet_send, rng)
    mix = _lowpass(mix, 9000)
    mix = np.tanh(1.3 * mix) / np.tanh(1.3)      # gentle saturation glues the mix
    mix *= 0.9 / (np.abs(mix).max() + 1e-9)
    mix = mix[: int((duration + 0.5) * SR)]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    return out_path
