"""WAV / RIFF 读写（8/16/24 位 PCM），只用标准库。"""

import struct


class WavError(ValueError):
    """WAV 不合法，尽量带上字节偏移。"""

    def __init__(self, message, offset=None):
        self.offset = offset
        if offset is not None:
            message = "偏移 %d：%s" % (offset, message)
        super().__init__(message)
        self.message = message


def parse_chunks(data):
    """拆 RIFF 分块，返回 (类型, 载荷偏移, 载荷长度)，不复制载荷。

    奇数长度的分块后面多跳 1 个填充字节；分块顺序随意。
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("不是 WAVE 文件", 0)
    chunks = []
    offset = 12
    end = len(data)
    while offset + 8 <= end:
        kind = bytes(data[offset:offset + 4])
        size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        if start + size > end:
            raise WavError("分块 %r 声明长度 %d 超出文件（剩余 %d 字节）"
                           % (kind, size, end - start), offset)
        chunks.append((kind, start, size))
        offset = start + size + (size & 1)  # 奇数长度多跳 1 个填充字节
    return chunks


def _decode_samples(payload, bits, channels):
    """把 PCM 字节解成整数列表：8 位无符号减 128，16/24 位有符号小端。"""
    if bits == 8:
        return [value - 128 for value in payload]
    if bits == 16:
        count = len(payload) // 2
        return list(struct.unpack("<%dh" % count, payload[:count * 2]))
    if bits == 24:
        samples = []
        for at in range(0, len(payload) - 2, 3):
            value = payload[at] | (payload[at + 1] << 8) | (payload[at + 2] << 16)
            if value & 0x800000:  # 24 位符号扩展
                value -= 0x1000000
            samples.append(value)
        return samples
    raise WavError("只支持 8/16/24 位 PCM，实际 %d 位" % bits)


def read(data):
    """读 WAV，返回 channels/rate/bits/frames/samples。"""
    fmt = None
    data_chunk = None
    last_end = 12
    for kind, start, size in parse_chunks(data):
        last_end = start + size
        if kind == b"fmt ":
            if size < 16:
                raise WavError("fmt 分块太短（%d 字节）" % size, start)
            fmt = struct.unpack_from("<HHIIHH", data, start)
        elif kind == b"data" and data_chunk is None:
            data_chunk = (start, size)
    if fmt is None:
        raise WavError("缺少 fmt 分块", last_end)
    if data_chunk is None:
        raise WavError("缺少 data 分块", last_end)
    tag, channels, rate, _, _, bits = fmt
    if tag != 1:
        raise WavError("只支持 PCM（format tag 1），实际 tag=%d" % tag)
    if bits not in (8, 16, 24):
        raise WavError("只支持 8/16/24 位 PCM，实际 %d 位" % bits)
    if channels < 1:
        raise WavError("声道数不合法：%d" % channels)
    frame_size = channels * (bits // 8)
    start, size = data_chunk
    if size % frame_size:
        raise WavError("data 长度 %d 不是帧长 %d 的整数倍" % (size, frame_size), start)
    frames = size // frame_size
    view = memoryview(data)
    samples = _decode_samples(view[start:start + size], bits, channels)
    return {
        "channels": channels,
        "rate": rate,
        "bits": bits,
        "frames": frames,
        "samples": samples,
    }


def peak(info, size):
    """每 size 帧为一段，按声道分别取绝对值峰值；不足一段的尾巴也算。"""
    samples = info["samples"]
    channels = info["channels"]
    if not samples or size <= 0:
        return []
    out = []
    for start in range(0, info["frames"], size):
        block = samples[start * channels:(start + size) * channels]
        for channel in range(channels):
            out.append(max(abs(value) for value in block[channel::channels]))
    return out


def resample(info, rate):
    """线性插值重采样，输出帧数 = round(原帧数 × 新采样率 ÷ 原采样率)。"""
    if rate <= 0:
        raise WavError("采样率不合法：%d" % rate)
    src_rate = info["rate"]
    channels = info["channels"]
    frames = info["frames"]
    samples = info["samples"]
    out_frames = int(frames * rate / src_rate + 0.5)
    out = []
    for index in range(out_frames):
        pos = index * src_rate / rate
        base = int(pos)
        frac = pos - base
        nxt = min(base + 1, frames - 1)
        for channel in range(channels):
            first = samples[base * channels + channel]
            second = samples[nxt * channels + channel]
            out.append(int(round(first + (second - first) * frac)))
    info = dict(info)
    info["rate"] = rate
    info["samples"] = out
    info["frames"] = out_frames
    return info


def write(info):
    """按 info 的位深写出 PCM WAV（8/16/24 位），奇数长度 data 补填充字节。"""
    channels = info["channels"]
    bits = info.get("bits", 16)
    rate = info["rate"]
    payload = bytearray()
    if bits == 8:
        for value in info["samples"]:
            payload.append(max(0, min(255, int(value) + 128)))
    elif bits == 16:
        for value in info["samples"]:
            payload.extend(struct.pack("<h", max(-32768, min(32767, int(value)))))
    elif bits == 24:
        for value in info["samples"]:
            value = max(-8388608, min(8388607, int(value)))
            payload.extend(value.to_bytes(3, "little", signed=True))
    else:
        raise WavError("只支持 8/16/24 位 PCM，实际 %d 位" % bits)
    block = channels * (bits // 8)
    header = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = b"fmt " + struct.pack("<I", len(header)) + header
    pad = b"\x00" if len(payload) % 2 else b""
    body += b"data" + struct.pack("<I", len(payload)) + bytes(payload) + pad
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body
