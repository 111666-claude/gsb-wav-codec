"""WAV / RIFF 读写（8/16/24 位 PCM），只用标准库。

缺陷：24 位样本没做符号扩展、偶数对齐的 padding 不跳过（后面的 chunk 全错位）、
峰值不分声道、重采样只在整数倍时才对。
"""

import struct


class WavError(ValueError):
    """WAV 不合法。缺陷：不带字节偏移。"""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def parse_chunks(data):
    """拆 RIFF 分块。缺陷：奇数长度的分块不跳过那一个填充字节。"""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("不是 WAVE 文件")
    offset = 12
    chunks = []
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        size = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        payload = data[offset + 8:offset + 8 + size]
        if len(payload) != size:
            raise WavError("分块长度超出文件")
        chunks.append((kind, payload))
        offset += 8 + size  # 缺陷：size 是奇数时要再多跳 1 个填充字节
    return chunks


def _decode_samples(payload, bits, channels):
    """把 PCM 字节解成整数列表。缺陷：24 位当无符号读。"""
    samples = []
    if bits == 8:
        for value in payload:
            samples.append(value - 128)
    elif bits == 16:
        for at in range(0, len(payload) - 1, 2):
            samples.append(struct.unpack("<h", payload[at:at + 2])[0])
    elif bits == 24:
        for at in range(0, len(payload) - 2, 3):
            value = payload[at] | (payload[at + 1] << 8) | (payload[at + 2] << 16)
            samples.append(value)  # 缺陷：没有做 24 位符号扩展
    else:
        raise WavError("只支持 8/16/24 位 PCM")
    return samples


def read(data):
    """读 WAV。缺陷：分块错位、24 位符号错。"""
    fmt = None
    payload = None
    for kind, chunk in parse_chunks(data):
        if kind == b"fmt ":
            fmt = struct.unpack("<HHIIHH", chunk[:16])
        elif kind == b"data":
            payload = chunk
    if fmt is None or payload is None:
        raise WavError("缺少 fmt 或 data 分块")
    tag, channels, rate, _, _, bits = fmt
    if tag != 1:
        raise WavError("只支持 PCM（format tag 1）")
    samples = _decode_samples(payload, bits, channels)
    return {
        "channels": channels,
        "rate": rate,
        "bits": bits,
        "frames": len(samples) // channels,
        "samples": samples,
    }


def peak(info, size):
    """缺陷：只给一个全局最大值，不分声道。"""
    samples = info["samples"]
    if not samples:
        return []
    out = []
    for start in range(0, len(samples), size):
        out.append(max(abs(value) for value in samples[start:start + size]))
    return out


def resample(info, rate):
    """缺陷：只在整数倍时按最近点取，其它情况直接出错。"""
    if rate % info["rate"] != 0:
        raise WavError("只支持整数倍重采样")
    factor = rate // info["rate"]
    out = []
    for value in info["samples"]:
        out.extend([value] * factor)
    info = dict(info)
    info["rate"] = rate
    info["samples"] = out
    info["frames"] = len(out) // info["channels"]
    return info


def write(info):
    """写出 16 位 PCM WAV。"""
    channels = info["channels"]
    payload = bytearray()
    for value in info["samples"]:
        payload.extend(struct.pack("<h", max(-32768, min(32767, int(value)))))
    header = struct.pack("<HHIIHH", 1, channels, info["rate"], info["rate"] * channels * 2,
                         channels * 2, 16)
    body = b"fmt " + struct.pack("<I", len(header)) + header
    body += b"data" + struct.pack("<I", len(payload)) + bytes(payload)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body
