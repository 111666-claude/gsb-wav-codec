"""WAV / RIFF 读写。"""

from .wav import WavError, peak, read, resample, write

__all__ = ["WavError", "peak", "read", "resample", "write"]
