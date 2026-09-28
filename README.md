# wav-codec

WAV / RIFF 读写（8/16/24 位 PCM），只用 Python 标准库。

```
python3 -m wavcodec.cli --sample odd --out /tmp/odd.wav
python3 -m wavcodec.cli --info /tmp/odd.wav
python3 -m wavcodec.cli --sample s24 --out /tmp/s24.wav --peak 1
python3 -m unittest discover -s tests -v
```

## 口径（README 为准）

- **分块要按 RIFF 对齐**：分块长度是奇数时，后面要多跳 1 个填充字节。`--sample odd` 的样本里
  `LIST` 分块长度是 5，现在解析器不跳填充字节，`--info` 报「分块长度超出文件」；
  按口径要给出 `channels=1 rate=8000 bits=16 frames=8`。
- **24 位要符号扩展**：`--sample s24 --info --peak 1` 现在给 `peak=1000000,15777216,8388607,8388608`
  （-1000000 被当成无符号读），按口径应该是 `peak=1000000,1000000,8388607,8388608`。
- **峰值按声道**：`--sample stereo --peak 4`（每个声道 4 个样本）现在把所有样本混在一起给 `peak=2000,4000`，
  按口径要按声道分别求峰值，给 `peak=4000,4000`。
- 采样处理：8 位按无符号（减 128）、16/24 位按有符号小端；`--resample` 用线性插值，
  输出帧数 = round(原帧数 × 新采样率 ÷ 原采样率)（现在只支持整数倍）。
- 不变量：`write` 写出的文件必须能被标准库 `wave` 打开并逐帧读回同样的样本；
  自己的 `read` 与 `wave` 模块读出的帧数据逐字节一致；同一文件重复读取结果相同。
- 规模：100 MB 的 WAV 要单遍处理，内存 O(一帧 + 一个分块)；不许把整个 data 分块复制成多份。

## 实现要点

- `parse_chunks` 校验 RIFF/WAVE 头，奇数长度的分块多跳 1 个填充字节，分块顺序随意；
  载荷长度与文件实际数据不一致、缺 fmt 或 data 都会报错并给出字节偏移。
- `_decode_samples` 的 24 位分支做符号扩展；8 位减 128，16 位按有符号小端；只支持 PCM（tag 1）。
- `peak` 每 N 帧一段、按声道分别求峰值，不足一段的尾巴也算。
- `resample` 用线性插值，输出帧数 = round(原帧数 × 新采样率 ÷ 原采样率)。

## 计划新增的能力

```
--sample 名称    odd / s24 / stereo
--out 文件       写出样本
--info 文件      打印声道、采样率、位深、帧数
--peak N         每 N 个样本取一个峰值
--resample 采样率 重采样
```

## 输出

```
channels=1 rate=8000 bits=16 frames=8
peak=1000000
```

## 目录

```
wavcodec/wav.py   解析、峰值与重采样
wavcodec/cli.py   命令行入口 + 样本构造
tests/            unittest 用例
```
