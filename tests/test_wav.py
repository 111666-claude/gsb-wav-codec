import io
import struct
import unittest
import wave

from wavcodec.wav import WavError, peak, read, resample, write


def simple(values, channels=1, rate=8000):
    return write({"channels": channels, "rate": rate, "bits": 16, "samples": list(values)})


class RoundTripTest(unittest.TestCase):
    def test_read_own_output(self):
        info = read(simple([1, -1, 2, -2]))
        self.assertEqual(info["channels"], 1)
        self.assertEqual(info["rate"], 8000)
        self.assertEqual(info["frames"], 4)
        self.assertEqual(info["samples"], [1, -1, 2, -2])

    def test_stdlib_wave_can_open(self):
        with wave.open(io.BytesIO(simple([1, 2, 3, 4])), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getnframes(), 4)
            self.assertEqual(handle.readframes(4), struct.pack("<4h", 1, 2, 3, 4))

    def test_bad_header(self):
        with self.assertRaises(WavError):
            read(b"NOPE" + b"\x00" * 40)


class PeakTest(unittest.TestCase):
    def test_peak_of_chunks(self):
        info = read(simple([1, -5, 3, -2]))
        self.assertEqual(peak(info, 2), [5, 3])

    def test_empty(self):
        info = read(simple([]))
        self.assertEqual(peak(info, 4), [])


class ResampleTest(unittest.TestCase):
    def test_double_rate(self):
        info = read(simple([1, 2, 3, 4]))
        resampled = resample(info, 16000)
        self.assertEqual(resampled["rate"], 16000)
        self.assertEqual(resampled["frames"], 8)


if __name__ == "__main__":
    unittest.main()
