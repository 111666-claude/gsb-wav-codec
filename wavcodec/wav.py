"""WAV / RIFF 读写（8/16/24 位 PCM），只用标准库。

解析按 RIFF 口径：奇数长度的分块后多跳 1 个填充字节，分块顺序不限。
采样按流式逐帧产出（内存 O(一帧 + 一个分块)），data 载荷用 memoryview
零切片，不把整个 data 分块复制成多份。
"""

import math
import struct


class WavError(ValueError):
    """WAV 不合法；``offset`` 为问题分块在文件中的字节偏移（若有）。"""

    def __init__(self, message, offset=None):
        self.offset = offset
        text = message if offset is None else "%s（偏移 %d）" % (message, offset)
        super().__init__(text)
        self.message = text


def parse_chunks(data):
    """逐块拆 RIFF，惰性产出 ``(kind, payload, offset)``。

    ``payload`` 是指向原缓冲的 memoryview（零拷贝）；``offset`` 是分块头
    （4 字节 id）的起始偏移。奇数长度分块后自动跳过 1 个填充字节。
    """
    size_total = len(data)
    if size_total < 12 or bytes(data[0:4]) != b"RIFF" or bytes(data[8:12]) != b"WAVE":
        raise WavError("不是 WAVE 文件（RIFF 头不合法）", 0)
    riff_size = struct.unpack("<I", data[4:8])[0]
    if riff_size + 8 > size_total:
        raise WavError("RIFF 声明长度超出实际文件", 4)
    view = memoryview(data)
    offset = 12
    while offset + 8 <= size_total:
        kind = bytes(view[offset:offset + 4])
        chunk_size = struct.unpack("<I", view[offset + 4:offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > size_total:
            raise WavError("分块载荷长度超出文件", offset)
        yield kind, view[payload_start:payload_end], offset
        offset = payload_end
        if chunk_size & 1:
            offset += 1  # 偶数对齐用的填充字节（文件末尾可缺省）


def _to_signed(value, bits):
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


def iter_frames(info):
    """逐帧产出，每帧是长度为声道数的整数列表（有符号 PCM）。

    既支持 read 返回的原始字节支撑的 info，也支持 resample 返回的工厂 info。
    """
    if "_data" not in info:
        for frame in info["_frames"]():
            yield frame
        return
    channels = info["channels"]
    width = info["bits"] // 8
    view = memoryview(info["_data"])
    payload = view[info["_start"]:info["_start"] + info["_len"]]
    position = 0
    for _ in range(info["frames"]):
        frame = [0] * channels
        for channel in range(channels):
            if width == 1:  # 8 位无符号
                frame[channel] = payload[position] - 128
                position += 1
            elif width == 2:  # 16 位有符号小端
                value = payload[position] | (payload[position + 1] << 8)
                frame[channel] = _to_signed(value, 16)
                position += 2
            else:  # 24 位有符号小端，需符号扩展
                value = (payload[position]
                         | (payload[position + 1] << 8)
                         | (payload[position + 2] << 16))
                frame[channel] = _to_signed(value, 24)
                position += 3
        yield frame


def iter_samples(info):
    """按声道交错顺序逐样本产出去交错后的整数（扁平流）。"""
    for frame in iter_frames(info):
        for value in frame:
            yield value


def raw_frames(info):
    """data 分块原始 PCM 字节（零拷贝 memoryview），供与 wave 模块逐字节对照。"""
    view = memoryview(info["_data"])
    return view[info["_start"]:info["_start"] + info["_len"]]


def read(data):
    """读 WAV，返回流式信息（不物化样本列表）。"""
    fmt = None
    fmt_offset = None
    data_start = data_len = data_offset = None
    for kind, payload, offset in parse_chunks(data):
        if kind == b"fmt " and fmt is None:
            fmt = payload
            fmt_offset = offset
        elif kind == b"data" and data_start is None:
            data_start = offset + 8
            data_len = len(payload)
            data_offset = offset
    if fmt is None:
        raise WavError("缺少 fmt 分块", 12)
    if data_start is None:
        raise WavError("缺少 data 分块", 12)
    if len(fmt) < 16:
        raise WavError("fmt 分块过短（不足 16 字节）", fmt_offset)
    tag, channels, rate, _byte_rate, _block_align, bits = struct.unpack(
        "<HHIIHH", fmt[:16])
    if tag != 1:
        raise WavError("只支持 PCM（format tag 1），收到 format tag %d" % tag,
                       fmt_offset)
    if bits not in (8, 16, 24):
        raise WavError("不支持的位深 %d（只支持 8/16/24）" % bits, fmt_offset)
    if channels < 1:
        raise WavError("声道数必须 >= 1", fmt_offset)
    if rate < 1:
        raise WavError("采样率必须 >= 1", fmt_offset)
    block = channels * (bits // 8)
    if data_len % block != 0:
        raise WavError("data 长度 %d 不是帧长 %d 的整数倍" % (data_len, block),
                       data_offset)
    info = {
        "channels": channels,
        "rate": rate,
        "bits": bits,
        "frames": data_len // block,
        "_data": data,
        "_start": data_start,
        "_len": data_len,
    }
    info["_frames"] = lambda: iter_frames(info)
    return info


def peak(info, size):
    """每 ``size`` 帧、按声道分别取绝对值峰值。

    返回扁平列表：段顺序优先，每段内按声道顺序。不足 ``size`` 的尾段照常计入；
    空 data 返回 ``[]``。
    """
    if size < 1:
        raise WavError("peak 段长必须为正整数")
    channels = info["channels"]
    out = []
    current = [0] * channels
    used = 0
    for frame in iter_frames(info):
        if used == size:
            out.extend(current)
            current = [0] * channels
            used = 0
        for channel in range(channels):
            value = frame[channel]
            magnitude = -value if value < 0 else value
            if magnitude > current[channel]:
                current[channel] = magnitude
        used += 1
    if used:
        out.extend(current)
    return out


def _round_half_away(value):
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def resample(info, rate):
    """线性插值重采样，输出帧数 = round(原帧数 * 新采样率 / 原采样率)。"""
    source_rate = info["rate"]
    channels = info["channels"]
    source_frames = info["frames"]
    if rate < 1:
        raise WavError("目标采样率必须 >= 1")
    # 整数实现 round-half-up，避免大数浮点误差（帧数恒非负）。
    out_frames = (2 * source_frames * rate + source_rate) // (2 * source_rate)

    def factory():
        frames = iter_frames(info)
        try:
            first = next(frames)
        except StopIteration:
            return
        second = next(frames, None)
        index = 0  # first 对应的源帧下标
        for output_index in range(out_frames):
            position = output_index * source_rate / rate
            low = int(math.floor(position))
            if low > source_frames - 1:
                low = source_frames - 1
            fraction = position - low
            while index < low:
                first = second
                index += 1
                second = next(frames, None)
            if second is None or fraction == 0:
                yield [int(value) for value in first]
            else:
                yield [
                    _round_half_away(
                        first[c] + (second[c] - first[c]) * fraction)
                    for c in range(channels)
                ]

    return {
        "channels": channels,
        "rate": rate,
        "bits": info["bits"],
        "frames": out_frames,
        "_frames": factory,
    }


def _flat_frames(samples, channels):
    frame = []
    for value in samples:
        frame.append(value)
        if len(frame) == channels:
            yield frame
            frame = []
    if frame:
        raise WavError("样本数 %d 不是声道数 %d 的整数倍"
                       % (len(samples), channels))


def _encode_sample(out, value, bits):
    if bits == 8:
        if value < -128:
            value = -128
        elif value > 127:
            value = 127
        out.append((int(value) + 128) & 0xFF)
    elif bits == 16:
        if value < -32768:
            value = -32768
        elif value > 32767:
            value = 32767
        out += struct.pack("<h", int(value))
    else:
        if value < -8388608:
            value = -8388608
        elif value > 8388607:
            value = 8388607
        unsigned = int(value) & 0xFFFFFF
        out.append(unsigned & 0xFF)
        out.append((unsigned >> 8) & 0xFF)
        out.append((unsigned >> 16) & 0xFF)


def write(info):
    """写出 PCM WAV。

    入参可以是 read/resample 返回的流式信息（含 ``_frames``），也可以是带
    ``samples``（扁平整数列表）的字典。整段在单个 bytearray 中组装，不复制
    data 载荷。
    """
    channels = info["channels"]
    rate = info["rate"]
    bits = info.get("bits", 16)
    if bits not in (8, 16, 24):
        raise WavError("不支持的位深 %d（只支持 8/16/24）" % bits)
    if channels < 1 or rate < 1:
        raise WavError("声道数与采样率必须为正")
    if "_frames" in info:
        frames = info["_frames"]()
    else:
        frames = _flat_frames(info["samples"], channels)

    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 0) + b"WAVE"
    block = channels * (bits // 8)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    out += b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_size_at = len(out)
    out += b"data" + struct.pack("<I", 0)
    payload_start = len(out)
    for frame in frames:
        for value in frame:
            _encode_sample(out, value, bits)
    payload_len = len(out) - payload_start
    out[data_size_at + 4:data_size_at + 8] = struct.pack("<I", payload_len)
    if payload_len & 1:
        out.append(0)  # data 为奇数长度时的填充字节
    out[4:8] = struct.pack("<I", len(out) - 8)
    return out
