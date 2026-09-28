import io
import struct
import unittest
import wave

from wavcodec.cli import sample
from wavcodec.wav import WavError, parse_chunks, peak, read, resample, write


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

    def test_linear_interpolation(self):
        info = read(simple([0, 10]))
        resampled = resample(info, 16000)
        self.assertEqual(resampled["samples"], [0, 5, 10, 10])

    def test_non_integer_ratio_frame_count(self):
        info = read(simple([1, 2, 3, 4]))
        resampled = resample(info, 11025)
        self.assertEqual(resampled["frames"], 6)  # round(4 * 11025 / 8000)
        self.assertEqual(len(resampled["samples"]), 6)

    def test_resample_roundtrip_wave_readable(self):
        info = read(simple([0, 100, -100, 50]))
        data = write(resample(info, 16000))
        with wave.open(io.BytesIO(data), "rb") as handle:
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getnframes(), 8)


class OddChunkTest(unittest.TestCase):
    def test_odd_list_chunk_before_data(self):
        info = read(sample("odd"))
        self.assertEqual((info["channels"], info["rate"], info["bits"], info["frames"]),
                         (1, 8000, 16, 8))
        self.assertEqual(info["samples"], [100, -100, 200, -200, 300, -300, 400, -400])

    def test_chunk_offsets_skip_pad_byte(self):
        chunks = parse_chunks(sample("odd"))
        kinds = [kind for kind, _, _ in chunks]
        self.assertEqual(kinds, [b"fmt ", b"LIST", b"data"])
        start, size = chunks[1][1], chunks[1][2]
        self.assertEqual(size, 5)
        self.assertEqual(chunks[2][1] - 8 - (start + size), 1)  # 分块头前的填充字节

    def test_wave_module_agrees_on_odd_chunk_file(self):
        data = sample("odd")
        with wave.open(io.BytesIO(data), "rb") as handle:
            raw = handle.readframes(handle.getnframes())
        payload = next(bytes(memoryview(data)[start:start + size])
                       for kind, start, size in parse_chunks(data) if kind == b"data")
        self.assertEqual(payload, raw)


class Signed24Test(unittest.TestCase):
    def test_sign_extension(self):
        info = read(sample("s24"))
        self.assertEqual(info["bits"], 24)
        self.assertEqual(info["samples"], [1000000, -1000000, 8388607, -8388608])

    def test_peak_24bit(self):
        info = read(sample("s24"))
        self.assertEqual(peak(info, 1), [1000000, 1000000, 8388607, 8388608])

    def test_write_24bit_roundtrip(self):
        data = write({"channels": 1, "rate": 8000, "bits": 24,
                      "samples": [1000000, -1000000, 8388607, -8388608]})
        self.assertEqual(read(data)["samples"], [1000000, -1000000, 8388607, -8388608])
        with wave.open(io.BytesIO(data), "rb") as handle:
            self.assertEqual(handle.getsampwidth(), 3)
            self.assertEqual(handle.readframes(4),
                             b"\x40\x42\x0f\xc0\xbd\xf0\xff\xff\x7f\x00\x00\x80")


class PerChannelPeakTest(unittest.TestCase):
    def test_stereo_peak_per_channel(self):
        info = read(sample("stereo"))
        self.assertEqual(peak(info, 4), [4000, 4000])

    def test_tail_block_counts(self):
        info = read(sample("stereo"))  # 4 帧，size=3 时尾巴 1 帧
        self.assertEqual(peak(info, 3), [3000, 3000, 4000, 4000])


class EdgeCaseTest(unittest.TestCase):
    def test_empty_data_frames_zero(self):
        info = read(simple([]))
        self.assertEqual(info["frames"], 0)
        self.assertEqual(info["samples"], [])

    def test_data_not_frame_aligned(self):
        data = bytearray(simple([1, 2]))
        struct.pack_into("<I", data, 40, 3)  # data 长度改成 3，不是帧长 2 的整数倍
        with self.assertRaises(WavError):
            read(bytes(data))

    def test_unsupported_bits(self):
        data = bytearray(simple([1, 2]))
        struct.pack_into("<H", data, 34, 32)  # bits 改成 32
        with self.assertRaises(WavError):
            read(bytes(data))

    def test_non_pcm_tag(self):
        data = bytearray(simple([1, 2]))
        struct.pack_into("<H", data, 20, 3)  # format tag 改成 3
        with self.assertRaises(WavError):
            read(bytes(data))

    def test_missing_fmt_and_data_report_offset(self):
        with self.assertRaises(WavError) as caught:
            read(b"RIFF" + struct.pack("<I", 4) + b"WAVE")
        self.assertIsNotNone(caught.exception.offset)

    def test_truncated_chunk(self):
        data = simple([1, 2])[:-4]
        with self.assertRaises(WavError):
            read(data)

    def test_repeated_read_is_stable(self):
        data = sample("s24")
        self.assertEqual(read(data), read(data))


if __name__ == "__main__":
    unittest.main()
