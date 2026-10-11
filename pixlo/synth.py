"""Original background music, composed from scratch for each Reel (no third-party recordings or loops).

A small arranger picks a key, tempo and chord progression for the post's mood and writes the notes: pad chords,
an arpeggio, a bass line, a sparse melody and light drums. The same post id always gives the same track.

The notes are then played one of two ways:
- "sampled" (default): through real sampled instruments (guitar, piano, strings, kalimba, drum kit) from the
  MIT-licensed MuseScore General SoundFont, rendered offline with tinysoundfont. The tune is ours; only the
  single-note instrument samples come from the SoundFont.
- "synth": with the plain numpy synthesiser below. Used when the SoundFont can't be loaded.
"""
import hashlib
import wave
from pathlib import Path

import numpy as np

from .config import ROOT

SR = 44100
SOUNDFONT_URL = "https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/MuseScore_General.sf3"
SOUNDFONT = ROOT / "media" / "cache" / "soundfont" / "MuseScore_General.sf3"
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

# General MIDI programs for the sampled instruments, per mood
GM = {
    "calm":      {"pluck": 24, "pad": 89, "bass": 32, "bell": 11},   # nylon guitar, warm pad, acoustic bass, vibraphone
    "romantic":  {"pluck": 0,  "pad": 49, "bass": 32, "bell": 8},    # piano, slow strings, acoustic bass, celesta
    "adventure": {"pluck": 25, "pad": 48, "bass": 33, "bell": 12},   # steel guitar, strings, fingered bass, marimba
    "upbeat":    {"pluck": 24, "pad": 89, "bass": 33, "bell": 108},  # nylon guitar, warm pad, fingered bass, kalimba
    "epic":      {"pluck": 46, "pad": 48, "bass": 43, "bell": 14},   # harp, strings, contrabass, tubular bells
}
DRUM_NOTES = {"kick": 36, "hat": 42, "clap": 39, "tom_hi": 47, "tom_lo": 43}  # General MIDI percussion keys
# Sampled instruments differ a lot in loudness, so each part is rendered alone and set to this level
# (dB relative to the arpeggio, measured while the part is sounding) before mixing
LEVEL_DB = {"pluck": 0.0, "bass": -3.0, "drums": -3.0, "bell": -5.0, "pad": -8.0}


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


def arrange(p: dict, duration: float) -> list[dict]:
    """Write the notes: a list of {part, t (seconds), dur, note (MIDI), gain, pan}."""
    m = MOODS[p["mood"]]
    rng = np.random.default_rng(p["seed"])
    beat = 60 / p["bpm"]
    bar = 4 * beat
    scale, root = m["scale"], p["root"]
    penta = PENTA_MAJOR if scale is MAJOR else PENTA_MINOR
    events: list[dict] = []

    def add(part: str, t: float, dur: float, note: int, gain: float, pan: float = 0.0) -> None:
        events.append({"part": part, "t": t, "dur": dur, "note": int(note), "gain": gain, "pan": pan})

    def degree(d: int, octave: int = 0) -> int:
        return root + 12 * (octave + d // 7) + scale[d % 7]

    melody_note = root + 24 + penta[int(rng.integers(len(penta)))]
    for b in range(int(np.ceil((duration + 1) / bar))):
        t0 = b * bar
        d = p["prog"][b % len(p["prog"])]
        chord = [degree(d), degree(d + 2), degree(d + 4)] + ([degree(d + 6)] if m["sevenths"] else [])

        for note in chord:  # pad: the chord in the middle register
            add("pad", t0, bar + 0.3, note + 12, 0.07)

        for k in ([0, 2] if m["drums"] in ("none", "soft") else [0, 1.5, 2, 3]):
            add("bass", t0 + k * beat, beat * 1.5, degree(d) - 12, 0.32)

        # Arpeggio over two octaves
        tones = sorted(chord) + [x + 12 for x in sorted(chord)]
        steps = 8 if m["arp"] == "eighths" else 4
        pattern = [0, 2, 1, 3, 2, 4, 3, 5] if steps == 8 else [0, 2, 3, 4]
        for i in range(steps):
            add("pluck", t0 + i * bar / steps, 1.4, tones[pattern[i] % len(tones)] + 12,
                0.22 if i % 2 == 0 else 0.16, 0.35 if i % 2 else -0.35)

        # Sparse melody from bar 2: a step-wise walk on the pentatonic scale, landing on chord tones
        if b >= 1:
            for k in (0, 1.5, 2.5) if m["arp"] == "eighths" else (0, 2):
                if rng.random() < 0.75:
                    cands = [root + 24 + 12 * o + iv for o in (0, 1) for iv in penta]
                    cands = [c for c in cands if abs(c - melody_note) <= 4] or cands
                    chord_pcs = {x % 12 for x in chord}
                    if k in (0, 2) and any(c % 12 in chord_pcs for c in cands):
                        cands = [c for c in cands if c % 12 in chord_pcs]
                    melody_note = int(rng.choice(cands))
                    add("bell", t0 + k * beat, beat * 2, melody_note, 0.13, 0.1)

        # Drums (enter on bar 2 except pop, which needs energy from the first second)
        style = m["drums"]
        if style == "none" or (b == 0 and style != "pop"):
            continue
        for k in range(8):
            at = t0 + k * beat / 2
            if style in ("pop", "groove"):
                add("hat", at, 0.07, 0, 0.10 if k % 2 else 0.06, 0.4)
            if style == "soft" and k % 2 == 1:
                add("hat", at, 0.07, 0, 0.035, 0.3)
        for k in {"pop": [0, 2], "groove": [0, 1.5, 2], "epic": [0, 2.5], "soft": [0]}[style]:
            add("kick", t0 + k * beat, 0.4, 0, 0.55 if style != "soft" else 0.3)
        if style in ("pop", "groove"):
            for k in (1, 3):
                add("clap", t0 + k * beat, 0.25, 0, 0.22)
        if style == "epic":
            add("tom_hi", t0 + 3 * beat, 0.5, 0, 0.35, -0.3)
            add("tom_lo", t0 + 3.5 * beat, 0.5, 0, 0.35, 0.3)
            add("clap", t0 + 2 * beat, 0.25, 0, 0.25)
    return events


# ------------------------------------------------------------------ players
def _play_synth(events: list[dict], p: dict, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Plain numpy synthesiser. Returns (dry, wet_send) stereo tracks."""
    bright = MOODS[p["mood"]]["bright"]
    rng = np.random.default_rng(p["seed"] + 1)
    dry, wet = np.zeros((n, 2)), np.zeros((n, 2))
    for e in events:
        part, f = e["part"], _hz(e["note"])
        if part == "pad":  # two slightly detuned layers, left and right
            _place(wet, _pad(f, e["dur"], -5), e["t"], e["gain"], -0.5)
            _place(wet, _pad(f, e["dur"], +5), e["t"], e["gain"], 0.5)
        elif part == "pluck":
            _place(wet, _pluck(f, e["dur"], bright, rng), e["t"], e["gain"], e["pan"])
        elif part == "bell":
            _place(wet, _bell(f, e["dur"]), e["t"], e["gain"], e["pan"])
        elif part == "bass":
            _place(dry, _bass(f, e["dur"]), e["t"], e["gain"])
        else:
            sound = {"kick": _kick, "hat": lambda: _hat(rng), "clap": lambda: _clap(rng),
                     "tom_hi": lambda: _tom(110), "tom_lo": lambda: _tom(82)}[part]()
            _place(dry, sound, e["t"], e["gain"], e["pan"])
    return dry, wet


def soundfont_path() -> Path | None:
    """The SoundFont file, downloaded once into media/cache. None if it can't be fetched."""
    if not SOUNDFONT.exists():
        import requests
        try:
            resp = requests.get(SOUNDFONT_URL, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    ! SoundFont download failed ({type(exc).__name__}); using the built-in synth")
            return None
        SOUNDFONT.parent.mkdir(parents=True, exist_ok=True)
        SOUNDFONT.write_bytes(resp.content)
    return SOUNDFONT


def _part(event: dict) -> str:
    return "drums" if event["part"] in DRUM_NOTES else event["part"]


def _play_sampled(events: list[dict], p: dict, n: int, sf2: Path) -> tuple[np.ndarray, np.ndarray]:
    """Sampled instruments from the SoundFont. Returns (dry, wet_send) stereo tracks."""
    import tinysoundfont

    programs = GM[p["mood"]]
    rng = np.random.default_rng(p["seed"] + 1)
    synth = tinysoundfont.Synth(samplerate=SR)
    sfid = synth.sfload(str(sf2))
    # channel: (part, pan 0-127). The arpeggio alternates between a left and a right channel.
    channels = {0: ("pad", 64), 1: ("bass", 64), 2: ("pluck", 42), 3: ("pluck", 86), 4: ("bell", 70), 9: ("drums", 64)}
    for ch, (part, pan) in channels.items():
        if part == "drums":
            synth.program_select(ch, sfid, 128, 0)
        else:
            synth.program_select(ch, sfid, 0, programs[part])
        synth.control_change(ch, 10, pan)
    loudest: dict[str, float] = {}
    for e in events:
        loudest[_part(e)] = max(loudest.get(_part(e), 0.0), e["gain"])

    def play(parts: set[str]) -> np.ndarray:
        actions = []  # (sample, on?, channel, key, velocity); note-offs sort before note-ons at the same sample
        for e in events:
            part = _part(e)
            if part not in parts:
                continue
            ch = {"pad": 0, "bass": 1, "bell": 4, "drums": 9}.get(part, 2 if e["pan"] < 0 else 3)
            key = DRUM_NOTES[e["part"]] if part == "drums" else e["note"]
            # A human touch: a few milliseconds and a little loudness variation per note
            start = max(0, int((e["t"] + (0 if part in ("pad", "drums") else rng.normal(0, 0.006))) * SR))
            vel = int(np.clip(110 * (e["gain"] / loudest[part]) ** 0.6 * rng.uniform(0.9, 1.0), 20, 120))
            actions.append((start, 1, ch, key, vel))
            actions.append((start + int(max(e["dur"], 0.3) * SR), 0, ch, key, 0))
        out = np.zeros((n, 2), np.float32)
        pos = 0
        for at, on, ch, key, vel in sorted(a for a in actions if a[0] < n):
            if at > pos:
                out[pos:at] = np.frombuffer(synth.generate(at - pos), np.float32).reshape(-1, 2)
                pos = at
            if on:
                synth.noteon(ch, key, vel)
            else:
                synth.noteoff(ch, key)
        if pos < n:
            out[pos:] = np.frombuffer(synth.generate(n - pos), np.float32).reshape(-1, 2)
        synth.notes_off()
        synth.sounds_off()
        return out.astype(np.float64)

    dry, wet = np.zeros((n, 2)), np.zeros((n, 2))
    for part in loudest:
        stem = play({part})
        sounding = np.abs(stem).max(axis=1) > 1e-4
        if not sounding.any():
            continue
        stem *= 10 ** (LEVEL_DB[part] / 20) / np.sqrt((stem[sounding] ** 2).mean())
        (dry if part in ("bass", "drums") else wet)[:] += stem
    return dry, wet


def render(p: dict, duration: float, out_path: Path, instruments: str = "sampled") -> str:
    """Compose and render the track to a WAV. Returns how it was played: "sampled" or "synth"."""
    n = int((duration + 2.0) * SR)
    events = arrange(p, duration)
    sf2 = soundfont_path() if instruments == "sampled" else None
    played = "synth"
    if sf2:
        try:
            dry, wet = _play_sampled(events, p, n, sf2)
            played = "sampled"
        except Exception as exc:  # a broken SoundFont or missing library must not stop the Reel
            print(f"    ! sampled instruments failed ({exc}); using the built-in synth")
    if played == "synth":
        dry, wet = _play_synth(events, p, n)

    rng = np.random.default_rng(p["seed"])
    mix = dry + wet + (0.22 if played == "sampled" else 0.32) * _reverb(wet, rng)
    mix = _lowpass(mix, 14000 if played == "sampled" else 9000)
    mix = mix / (np.abs(mix).max() + 1e-9)
    mix = np.tanh(1.3 * mix) / np.tanh(1.3) * 0.9  # gentle saturation glues the mix
    mix = mix[: int((duration + 0.5) * SR)]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    return played
