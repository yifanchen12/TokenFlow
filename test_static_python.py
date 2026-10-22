"""Checks the supported semantics and conservative fallback of static evaluation."""
from static_python import static_python_answer


def self_check():
    query = "total 的返回值是多少？只回答数字。"
    code = "BASE = 31\nRESERVED = 7\ndef available():\n    n = BASE - RESERVED\n    return n\ndef total():\n    return available() * 2 + -3\n"
    result = static_python_answer(query, code)
    assert result["answer"] == "45" and result["method"] == "static_python"
    assert static_python_answer(query, "```python\n" + code + "```")["answer"] == "45"
    for unsafe in (
        "import os\ndef total():\n    return 42\n",
        "def total():\n    return __import__('os').system('echo no')\n",
        "def total():\n    return total()\n",
        "def total():\n    while True: pass\n",
        "def total():\n    return 2 ** 999999999\n",
        "def total():\n    return 1 / 2\n",
        "@decorator\ndef total():\n    return 1\n",
        "def total(x=side_effect()):\n    return 1\n",
        "x=5\ndef total():\n    y=x\n    x=2\n    return y\n",
        "x=y\ny=5\ndef total():\n    return x\n",
        "def total():\n    return 1\ntotal=2\n",
        "def total():\n    return True\n",
        "def total():\n    return 1//0\n",
        "def total():\n    return missing()\n",
        "def total():\n    return 42\n    yield 1\n",
        "x=5\ndef total():\n    return x\n    if False: x=2\n",
        "def helper():\n    return 1\ndef total():\n    helper=2\n    return helper()\n",
        "x=5\ndef total():\n    return x\n    (x:=2)\n",
    ):
        assert static_python_answer(query, unsafe) is None, unsafe
    assert static_python_answer("解释 total 的返回值", code) is None
    assert static_python_answer("total 和 available 的返回值？", code) is None
    assert static_python_answer('total 的返回值是多少？代码有多少行？', code) is None
    assert static_python_answer('total 的返回值是多少？列出全部依赖。', code) is None
    assert static_python_answer('What does total() return? Answer number only.', code)['answer'] == '45'
    assert static_python_answer(query, code.replace("BASE = 31", "BASE = " + str(1 << 300))) is None
    # Unused ordinary helper parameters do not change the zero-argument target.
    assert static_python_answer(query, code + "def other(x):\n    return x + 1\n")["answer"] == "45"
    print("Static Python self-check: PASS")


if __name__ == "__main__":
    self_check()
