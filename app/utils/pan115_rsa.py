# Adapted from p115rsacipher 0.0.1 by ChenyangGao, MIT licensed.
# https://github.com/ChenyangGao/p115client/tree/main/modules/p115rsacipher
# Python 3.10 compatibility: Buffer backport and explicit byte order.
#
# MIT License
# Copyright (c) ChenyangGao
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__version__ = (0, 0, 1)
__all__ = ["decrypt", "encrypt"]

from base64 import b64decode, b64encode
from collections.abc import Iterator, Sized
from typing import Final

from typing_extensions import Buffer

G_kts: Final = b"\xf0\xe5i\xae\xbf\xdc\xbf\x8a\x1aE\xe8\xbe}\xa6s\xb8\xde\x8f\xe7\xc4E\xda\x86\xc4\x9bd\x8b\x14j\xb4\xf1\xaa8\x015\x9e&i,\x86\x00kO\xa564b\xa6*\x96h\x18\xf2J\xfd\xbdk\x97\x8fM\x8f\x89\x13\xb7l\x8e\x93\xed\x0e\rH>\xd7/\x88\xd8\xfe\xfe~\x86P\x95O\xd1\xeb\x83&4\xdbf{\x9c~\x9dz\x812\xea\xb63\xde:\xa9Y4f;\xaa\xba\x81`H\xb9\xd5\x81\x9c\xf8l\x84w\xffTx&_\xbe\xe8\x1e6\x9f4\x80\\E,\x9bv\xd5\x1b\x8f\xcc\xc3\xb8\xf5"
RSA_e: Final = 0x8686980C0F5A24C4B9D43020CD2C22703FF3F450756529058B1CF88F09B8602136477198A6E2683149659BD122C33592FDB5AD47944AD1EA4D36C6B172AAD6338C3BB6AC6227502D010993AC967D1AEF00F0C8E038DE2E4D3BC2EC368AF2E9F10A6F1EDA4F7262F136420C07C331B871BF139F74F3010E3C4FE57DF3AFB71683
RSA_n: Final = 0x10001


def to_bytes(value: int, length: int) -> bytes:
    return value.to_bytes(length, "big")


def from_bytes(value: Buffer) -> int:
    return int.from_bytes(value, "big")


def acc_step(start: int, stop: int, step: int = 1) -> Iterator[tuple[int, int, int]]:
    for i in range(start + step, stop, step):
        yield start, i, step
        start = i
    if start != stop:
        yield start, stop, stop - start


def bytes_xor(v1: Buffer, v2: Buffer, /) -> bytes:
    return to_bytes(
        from_bytes(v1) ^ from_bytes(v2),
        len(v1 if isinstance(v1, Sized) else memoryview(v1)),
    )


def gen_key(rand_key: Buffer, sk_len: int, /) -> bytearray:
    xor_key = bytearray(sk_len)
    length = sk_len * (sk_len - 1)
    index = 0
    if not isinstance(rand_key, (bytes, bytearray, memoryview)):
        rand_key = memoryview(rand_key)
    for i in range(sk_len):
        x = (rand_key[i] + G_kts[index]) & 0xFF
        xor_key[i] = G_kts[length] ^ x
        length -= sk_len
        index += sk_len
    return xor_key


def pad_pkcs1_v1_5(message: Buffer, /) -> int:
    length = len(message if isinstance(message, Sized) else memoryview(message))
    return from_bytes(b"\x00" + b"\x02" * (126 - length) + b"\x00" + message)


def xor(src: Buffer, key: Buffer, /) -> bytearray:
    src = memoryview(src)
    key = memoryview(key)
    secret = bytearray()
    i = len(src) & 0b11
    if i:
        secret += bytes_xor(src[:i], key[:i])
    offset = i
    for i, j, s in acc_step(offset, len(src), len(key)):
        secret += bytes_xor(src[i:j], key[:s])
    return secret


def encrypt(data: str | Buffer, /) -> bytes:
    if isinstance(data, str):
        data = bytes(data, "utf-8")
    xor_text = bytearray(16)
    tmp = memoryview(xor(data, b"\x8d\xa5\xa5\x8d"))[::-1]
    xor_text += xor(tmp, b"x\x06\xadL3\x86]\x18L\x01?F")
    cipher_data = bytearray()
    view = memoryview(xor_text)
    for left, right, _ in acc_step(0, len(view), 117):
        cipher_data += to_bytes(pow(pad_pkcs1_v1_5(view[left:right]), RSA_n, RSA_e), 128)
    return b64encode(cipher_data)


def decrypt(cipher_data: str | Buffer, /) -> bytearray:
    cipher_data = memoryview(b64decode(cipher_data))
    data = bytearray()
    for left, right, _ in acc_step(0, len(cipher_data), 128):
        p = pow(from_bytes(cipher_data[left:right]), RSA_n, RSA_e)
        b = to_bytes(p, (p.bit_length() + 0b111) >> 3)
        data += memoryview(b)[b.index(0) + 1 :]
    m = memoryview(data)
    key_l = gen_key(m[:16], 12)
    tmp = memoryview(xor(m[16:], key_l))[::-1]
    return xor(tmp, b"\x8d\xa5\xa5\x8d")
