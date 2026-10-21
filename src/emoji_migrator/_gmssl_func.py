# ============================================================================
# 【vendor 文件】来源与改动留痕（EM-005 D-17，RUN-20261020-004；删除本块必须红：
# tests/test_abogus.py 的 provenance 守护断言依赖本块的存在）
# ----------------------------------------------------------------------------
# 来源项目 : gmssl（https://github.com/duanhongyi/gmssl，PyPI 3.2.2）
# 来源文件 : gmssl/func.py（wheel gmssl-3.2.2-py3-none-any.whl 原样解出）
# 取得方式 : 2026-10-20 pip download gmssl==3.2.2 --no-deps
# 原文锚点 : md5 4b92f60fd691bc8a570cc3227962f1d0
# 许可证   : BSD（上游 PyPI METADATA 声明；上游未随包分发全文，见
#            docs/licenses/gmssl-BSD.txt 的登记说明）
# 本仓改动 : ①文末追加 ``__all__``（模块登记守护要求）②本文件整体豁免 ruff；
#            代码逐字未动
# 用途     : SM3 国密哈希的辅助工具函数（abogus.py 的签名算法依赖）
# ============================================================================

from random import choice


xor = lambda a, b:list(map(lambda x, y: x ^ y, a, b))

rotl = lambda x, n:((x << n) & 0xffffffff) | ((x >> (32 - n)) & 0xffffffff)

get_uint32_be = lambda key_data:((key_data[0] << 24) | (key_data[1] << 16) | (key_data[2] << 8) | (key_data[3]))

put_uint32_be = lambda n:[((n>>24)&0xff), ((n>>16)&0xff), ((n>>8)&0xff), ((n)&0xff)]

pkcs7_padding = lambda data, block=16: data + [(16 - len(data) % block)for _ in range(16 - len(data) % block)]

zero_padding = lambda data, block=16: data + [0 for _ in range(16 - len(data) % block)]

pkcs7_unpadding = lambda data: data[:-data[-1]]

zero_unpadding = lambda data,i =1:data[:-i] if data[-i] == 0 else i+1

list_to_bytes = lambda data: b''.join([bytes((i,)) for i in data])

bytes_to_list = lambda data: [i for i in data]

random_hex = lambda x: ''.join([choice('0123456789abcdef') for _ in range(x)])

__all__ = ['xor', 'rotl', 'get_uint32_be', 'put_uint32_be', 'pkcs7_padding', 'zero_padding', 'pkcs7_unpadding', 'zero_unpadding', 'list_to_bytes', 'bytes_to_list', 'random_hex']
