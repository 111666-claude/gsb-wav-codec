import io
import struct
import unittest
import wave

from wavcodec.wav import (
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


def samples_of(info):
    return [value for frame in iter_frames(info) for value in frame]


def simple(values, channels=1, rate=8000):
    return write({"channels": channels, "rate": rate, "bits": 16,
                  "samples": list(values)})


def riff(*parts):
    body = b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def packed(kind, payload):
    packed = kind + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        packed += b"\x00"
    return packed


FMT16 = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
FMT24 = struct.pack("<HHIIHH", 1, 1, 8000, 24000, 3, 24)
FMT24_ST = struct.pack("<HHIIHH", 1, 2, 8000, 48000, 6, 24)


class RoundTripTest(unittest.TestCase):
    def test_read_own_output(self):
        info = read(simple([1, -1, 2, -2]))
        self.assertEqual(info["channels"], 1)
        self.assertEqual(info["rate"], 8000)
        self.assertEqual(info["frames"], 4)
        self.assertEqual(samples_of(info), [1, -1, 2, -2])

    def test_stdlib_wave_can_open(self):
        data = simple([1, 2, 3, 4])
        with wave.open(io.BytesIO(data), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getnframes(), 4)
            self.assertEqual(handle.readframes(4), struct.pack("<4h", 1, 2, 3, 4))

    def test_raw_frames_byte_identical_to_wave_all_depths(self):
        for bits, values in (
            (8, [0, -128, 127, 5, -1]),
            (16, [1, -2, 3, -4000, 4000]),
            (24, [1000000, -1000000, 8388607, -8388608, 1]),
        ):
            data = write({"channels": 1, "rate": 8000, "bits": bits,
                          "samples": values})
            info = read(data)
            with wave.open(io.BytesIO(bytes(data)), "rb") as handle:
                wave_bytes = handle.readframes(handle.getnframes())
            self.assertEqual(bytes(raw_frames(info)), wave_bytes)
            self.assertEqual(samples_of(info), values)

    def test_8bit_odd_data_length_is_padded_and_still_valid(self):
        data = write({"channels": 1, "rate": 8000, "bits": 8,
                      "samples": [0, 1, 2]})  # 3 字节奇数 data
        info = read(data)
        self.assertEqual(info["frames"], 3)
        with wave.open(io.BytesIO(bytes(data)), "rb") as handle:
            wave_bytes = handle.readframes(handle.getnframes())
        self.assertEqual(bytes(raw_frames(info)), wave_bytes)

    def test_repeated_read_is_identical(self):
        data = simple([7, -8, 9])
        self.assertEqual(samples_of(read(data)), samples_of(read(data)))
        # 多次迭代同一 info 也一致（无状态）
        info = read(data)
        self.assertEqual(samples_of(info), samples_of(info))

    def test_bad_header(self):
        with self.assertRaises(WavError):
            read(b"NOPE" + b"\x00" * 40)


class ChunkParseTest(unittest.TestCase):
    def test_odd_chunk_padding_skipped(self):
        data = riff(packed(b"fmt ", FMT16) + packed(b"LIST", b"INFOx")
                    + packed(b"data", struct.pack("<2h", 100, -100)))
        kinds = [kind for kind, _, _ in parse_chunks(data)]
        self.assertEqual(kinds, [b"fmt ", b"LIST", b"data"])
        info = read(data)
        self.assertEqual((info["channels"], info["rate"], info["bits"],
                          info["frames"]), (1, 8000, 16, 2))
        self.assertEqual(samples_of(info), [100, -100])

    def test_chunk_order_arbitrary(self):
        # data 出现在 fmt 之前，且夹了奇数长度分块
        data = riff(packed(b"LIST", b"abc") + packed(b"data", struct.pack("<h", 5))
                    + packed(b"fmt ", FMT16))
        self.assertEqual(samples_of(read(data)), [5])

    def test_odd_chunk_at_eof_without_pad_byte_tolerated(self):
        data = riff(packed(b"fmt ", FMT16)
                    + packed(b"data", struct.pack("<h", 1))
                    + b"LIST" + struct.pack("<I", 3) + b"abc")
        kinds = [kind for kind, _, _ in parse_chunks(data)]
        self.assertEqual(kinds, [b"fmt ", b"data", b"LIST"])
        self.assertEqual(samples_of(read(data)), [1])

    def test_overflow_reports_chunk_offset(self):
        data = riff(b"LIST" + struct.pack("<I", 99) + b"abc")
        with self.assertRaises(WavError) as ctx:
            list(parse_chunks(data))
        self.assertEqual(ctx.exception.offset, 12)

    def test_riff_size_overflow(self):
        data = bytearray(simple([1, 2]))
        struct.pack_into("<I", data, 4, 999999)
        with self.assertRaises(WavError) as ctx:
            read(bytes(data))
        self.assertEqual(ctx.exception.offset, 4)

    def test_missing_fmt_or_data_report_offset(self):
        no_fmt = riff(packed(b"data", b""))
        with self.assertRaises(WavError) as ctx:
            read(no_fmt)
        self.assertEqual(ctx.exception.offset, 12)
        no_data = riff(packed(b"fmt ", FMT16))
        with self.assertRaises(WavError) as ctx:
            read(no_data)
        self.assertEqual(ctx.exception.offset, 12)


class DecodeTest(unittest.TestCase):
    def test_24bit_sign_extension(self):
        data = riff(packed(b"fmt ", FMT24)
                    + packed(b"data", b"\x40\x42\x0f"   # 1000000
                                    + b"\xc0\xbd\xf0"   # -1000000
                                    + b"\xff\xff\x7f"   # 8388607
                                    + b"\x00\x00\x80"))  # -8388608
        self.assertEqual(samples_of(read(data)),
                         [1000000, -1000000, 8388607, -8388608])

    def test_24bit_sign_extension_stereo(self):
        data = riff(packed(b"fmt ", FMT24_ST)
                    + packed(b"data",
                            b"\x01\x00\x00" + b"\xff\xff\xff"   # 1, -1
                            + b"\x00\x00\x80" + b"\xff\xff\x7f"))  # min, max
        info = read(data)
        self.assertEqual(list(iter_frames(info)),
                         [[1, -1], [-8388608, 8388607]])

    def test_8bit_unsigned_offset(self):
        data = riff(packed(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8))
                    + packed(b"data", bytes([128, 0, 255, 127])))
        self.assertEqual(samples_of(read(data)), [0, -128, 127, -1])

    def test_non_pcm_rejected(self):
        fmt3 = struct.pack("<HHIIHH", 3, 1, 8000, 16000, 2, 16)
        data = riff(packed(b"fmt ", fmt3) + packed(b"data", struct.pack("<h", 1)))
        with self.assertRaises(WavError) as ctx:
            read(data)
        self.assertIn("PCM", str(ctx.exception))

    def test_unsupported_bit_depth_rejected(self):
        fmt12 = struct.pack("<HHIIHH", 1, 1, 8000, 12000, 3, 12)
        data = riff(packed(b"fmt ", fmt12) + packed(b"data", b"\x00" * 6))
        with self.assertRaises(WavError) as ctx:
            read(data)
        self.assertIn("位深", str(ctx.exception))

    def test_data_not_multiple_of_frame_rejected(self):
        data = riff(packed(b"fmt ", FMT16) + packed(b"data", b"\x01\x02\x03")
                    + packed(b"LIST", b"zz"))
        with self.assertRaises(WavError) as ctx:
            read(data)
        # 报错偏移指向 data 分块头
        kinds = [(kind, off) for kind, _, off in parse_chunks(data)]
        data_offset = dict((k, o) for k, o in kinds)[b"data"]
        self.assertEqual(ctx.exception.offset, data_offset)

    def test_empty_data_frames_zero(self):
        data = riff(packed(b"fmt ", FMT16) + packed(b"data", b""))
        info = read(data)
        self.assertEqual(info["frames"], 0)
        self.assertEqual(list(iter_samples(info)), [])


class PeakTest(unittest.TestCase):
    def test_peak_of_chunks(self):
        info = read(simple([1, -5, 3, -2]))
        self.assertEqual(peak(info, 2), [5, 3])

    def test_per_channel_stereo(self):
        # 每声道 4 个样本：左声道最大 4000，右声道最大 4000
        info = read(simple([1000, -1000, 2000, -2000,
                            3000, -3000, 4000, -4000], channels=2))
        self.assertEqual(peak(info, 4), [4000, 4000])

    def test_per_channel_segments(self):
        # 5 个立体声帧，段长 2：
        # 段1 帧[0:2] -> 左 2 右 9；段2 帧[2:4] -> 左 4 右 4；尾段帧[4] -> 5,5
        info = read(simple([1, -9, 2, -8, 3, -1, 4, -4, 5, 5], channels=2))
        self.assertEqual(peak(info, 2), [2, 9, 4, 4, 5, 5])

    def test_tail_smaller_than_segment_counts(self):
        info = read(simple([1, -9, 5, -2, 3]))  # 5 帧，段长 4；尾帧为 3
        self.assertEqual(peak(info, 4), [9, 3])

    def test_empty(self):
        info = read(simple([]))
        self.assertEqual(peak(info, 4), [])


class ResampleTest(unittest.TestCase):
    def test_double_rate(self):
        info = read(simple([1, 2, 3, 4]))
        resampled = resample(info, 16000)
        self.assertEqual(resampled["rate"], 16000)
        self.assertEqual(resampled["frames"], 8)

    def test_linear_interpolation_values(self):
        info = read(simple([0, 10, 20, 30]))
        doubled = resample(info, 16000)
        self.assertEqual(samples_of(doubled), [0, 5, 10, 15, 20, 25, 30, 30])

    def test_non_integer_ratio(self):
        info = read(simple([0, 10, 20, 30]))
        up = resample(info, 12000)  # 1.5 倍
        self.assertEqual(up["frames"], 6)
        self.assertEqual(samples_of(up), [0, 7, 13, 20, 27, 30])
        down = resample(info, 4000)  # 0.5 倍
        self.assertEqual(down["frames"], 2)
        self.assertEqual(samples_of(down), [0, 20])

    def test_frame_count_rounding(self):
        # 3 帧 8000 -> 5000：round(3*5000/8000)=round(1.875)=2
        info = read(simple([0, 1, 2]))
        self.assertEqual(resample(info, 5000)["frames"], 2)
        # 10 帧 8000 -> 12000：15 帧
        info = read(simple(range(10)))
        self.assertEqual(resample(info, 12000)["frames"], 15)

    def test_resampled_round_trips_through_write(self):
        info = resample(read(simple([0, 10, 20, 30])), 12000)
        written = write(info)
        with wave.open(io.BytesIO(bytes(written)), "rb") as handle:
            self.assertEqual(handle.getframerate(), 12000)
            self.assertEqual(handle.getnframes(), 6)
        self.assertEqual(samples_of(read(written)), [0, 7, 13, 20, 27, 30])

    def test_resample_stereo(self):
        info = read(simple([0, 100, 10, 110], channels=2))
        up = resample(info, 16000)
        self.assertEqual(up["frames"], 4)
        self.assertEqual(list(iter_frames(up)),
                         [[0, 100], [5, 105], [10, 110], [10, 110]])

    def test_resample_empty(self):
        info = read(simple([]))
        up = resample(info, 16000)
        self.assertEqual(up["frames"], 0)
        self.assertEqual(samples_of(up), [])


if __name__ == "__main__":
    unittest.main()
