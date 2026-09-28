"""WAV / RIFF 读写。"""

from .wav import (
    WavError,
    iter_frames,
    iter_samples,
    parse_chunks,
    peak,
    raw_frames,
    read,
    resample,
    write,
)

__all__ = [
    "WavError",
    "iter_frames",
    "iter_samples",
    "parse_chunks",
    "peak",
    "raw_frames",
    "read",
    "resample",
    "write",
]
