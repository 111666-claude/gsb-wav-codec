"""命令行入口：看 WAV 信息、抽峰值、重采样、写样本。"""

import argparse
import struct
import sys

from .wav import WavError, peak, read, resample, write


def _sample_payload(bits, values):
    payload = bytearray()
    for value in values:
        if bits == 8:
            payload.append((value + 128) & 0xFF)
        elif bits == 16:
            payload.extend(struct.pack("<h", value))
        else:
            payload.extend(struct.pack("<i", value)[:3])
    return bytes(payload)


def _wav(channels, rate, bits, values, extra=None):
    header = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8,
                         channels * bits // 8, bits)
    body = b"fmt " + struct.pack("<I", len(header)) + header
    if extra is not None:
        body += extra[0] + struct.pack("<I", len(extra[1])) + extra[1]
        if len(extra[1]) % 2:
            body += b"\x00"  # 奇数长度分块后面的填充字节
    payload = _sample_payload(bits, values)
    body += b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def sample(name):
    """构造样本：参考实现按规范打包。"""
    if name == "odd":
        # 奇数长度的 LIST 分块（5 字节）后面跟 data，data 之前要多一个填充字节
        return _wav(1, 8000, 16, [100, -100, 200, -200, 300, -300, 400, -400], extra=(b"LIST", b"INFOx"))
    if name == "s24":
        return _wav(1, 8000, 24, [1000000, -1000000, 8388607, -8388608])
    if name == "stereo":
        return _wav(2, 8000, 16, [1000, -1000, 2000, -2000, 3000, -3000, 4000, -4000])
    raise WavError("不认识的样本：%s" % name)


def build_parser():
    parser = argparse.ArgumentParser(prog="wav-codec", description="WAV 读写")
    parser.add_argument("--sample", help="odd / s24 / stereo")
    parser.add_argument("--out", help="写出的文件")
    parser.add_argument("--info", help="查看文件信息")
    parser.add_argument("--peak", type=int, help="每 N 个样本取一个峰值")
    parser.add_argument("--resample", type=int, help="重采样到该采样率")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.sample:
            data = sample(args.sample)
            with open(args.out, "wb") as handle:
                handle.write(data)
            print("wrote=%s bytes=%d" % (args.out, len(data)))
            return 0
        if not args.info:
            print("错误：需要 --sample 或 --info", file=sys.stderr)
            return 2
        with open(args.info, "rb") as handle:
            data = handle.read()
        info = read(data)
        print("channels=%d rate=%d bits=%d frames=%d" % (info["channels"], info["rate"],
                                                        info["bits"], info["frames"]))
        if args.peak:
            print("peak=%s" % ",".join(str(value) for value in peak(info, args.peak)))
        if args.resample:
            resampled = resample(info, args.resample)
            print("resampled=%d frames=%d" % (resampled["rate"], resampled["frames"]))
    except (WavError, OSError, ValueError, struct.error) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
