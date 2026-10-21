#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SingleVPN .x 源码自检（推 CI 前必跑）。

检查目标（只查「编不过 / 逻辑接错」，不猜运行时行为）：
  A1 注释/字符串剔除后的括号配对
  A2 预处理条件块配对（#if / #else / #elif / #endif）
  A3 所有 NSLog 的格式符个数与实参个数一致（跨行解析，不是单行）
  A4 关键静态函数「先定义后使用」
  A5 iOS 16 分支与上游 v2.1-2 逐字节一致
  A6 %hook 数量与目标
  A7 修复点仍在（回填守卫 / A-B 宏）
  A8 诊断开关与使用一致

参数：可传 --no-cross 跳过跨行 NSLog 检查（调试用）
"""
import io
import os
import re
import sys

# 仓库内运行时，以脚本自身位置为基准（CI 里没有开发机路径）
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_REPO, "SingleVPN.x")
# 上游原件（可选）：优先用仓库里的基线副本，没有就跳过 A5
ORIG = os.path.join(_REPO, "tools", "baseline_SingleVPN_v2.1-2.x")

fails, oks = [], []


def chk(name, ok, detail=""):
    (oks if ok else fails).append((name, detail))
    print(("  OK   " if ok else "  FAIL ") + name + (("  -> " + detail) if detail else ""))


def strip_code(s):
    """把注释替换成空格、字符串内容替换成空格，保留所有分隔符本身。

    ★ 关键：结尾的引号必须保留，否则 @"a" "b" 会被粘成 @"a  b ，
      导致相邻的两个 @"" 空串被误判成一个吞掉一个括号（上一版就栽在这）。
      空的 @"\0" 也不该被当成字符串开始——这里统一按「引号成对」处理即可。
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        nx = s[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            while i < n and s[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and nx == "*":
            out.append("  ")
            i += 2
            while i < n - 1 and not (s[i] == "*" and s[i + 1] == "/"):
                out.append("\n" if s[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        if c in ('"', "'"):
            q = c
            out.append(q)
            i += 1
            while i < n:
                if s[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if s[i] == q:
                    out.append(q)
                    i += 1
                    break
                out.append("\n" if s[i] == "\n" else " ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


src = io.open(SRC, encoding="utf-8").read()
lines = src.split("\n")
print("文件: %s" % SRC)
print("总行数: %d" % len(lines))

code = strip_code(src)
# 预处理行整行抹掉，避免 `#define ... ( \` 的续行把括号算进去。
# ★ 必须连反斜杠续行的后续行一起抹掉，否则宏首行的 `(` 被删、续行里的
#   括号却留着，造成「少一个左括号」的假失败（上一版就栽在这）。
_np = []
_skip = False
for l in code.split("\n"):
    if _skip:
        _np.append("")
        _skip = l.rstrip().endswith("\\")
        continue
    if l.strip().startswith("#"):
        _np.append("")
        _skip = l.rstrip().endswith("\\")
        continue
    _np.append(l)
code_np = "\n".join(_np)

print("\n[A1] 括号配对")
for o, c, name in [("{", "}", "花括号"), ("(", ")", "圆括号"), ("[", "]", "方括号")]:
    a, b = code_np.count(o), code_np.count(c)
    chk("%s %d/%d" % (name, a, b), a == b, "差 %+d" % (a - b))

print("\n[A2] 预处理条件块")
stack = []
bad = []
for i, l in enumerate(lines, 1):
    t = l.strip()
    if re.match(r"^#\s*if", t):
        stack.append(i)
    elif re.match(r"^#\s*endif", t):
        if not stack:
            bad.append("第 %d 行多余 #endif" % i)
        else:
            stack.pop()
if stack:
    bad.append("未闭合 #if 行号 %s" % stack)
chk("条件块配对", not bad, "; ".join(bad))

print("\n[A3] NSLog 格式符 / 实参（跨行）")


def split_args(s, start):
    """从 start（指向 '(' 之后）起，返回 (顶层逗号数, 结束位置)。"""
    depth = 0
    ncomma = 0
    i = start
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return ncomma, i
            depth -= 1
        elif ch == "," and depth == 0:
            ncomma += 1
        i += 1
    return ncomma, n


nslog_bad = []
# ★ 必须用原文定位：strip_code 把字符串内容抹成空格了，
#   在抹过的文本上搜 @"..." 会一路滑到下一个 NSLog，出现整片错位。
for m in re.finditer(r"\bNSLog\s*\(", src):
    open_pos = m.end() - 1
    # 在原文对应位置附近找第一个字符串字面量
    seg = src[open_pos:open_pos + 4000]
    fm = re.search(r'@"((?:[^"\\]|\\.)*)"', seg)
    if not fm:
        continue
    fmt = fm.group(1)
    nspec = len(re.findall(r"%(?:%|[-+ 0#]*\d*(?:\.\d+)?(?:hh|h|ll|l|q|z|t|j)?[@diouxXeEfgGcsSpn])", fmt))
    # 实参个数：从 open_pos 起按原文算顶层逗号（栈深度要跳过字符串内部）
    depth, ncomma, i, n = 0, 0, open_pos + 1, len(src)
    in_str = False
    while i < n:
        ch = src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            ncomma += 1
        i += 1
    # 从 '(' 之后开始数顶层逗号。第 1 个逗号之后的才是「格式符对应的实参」，
    # 所以：实参个数 == 顶层逗号个数。（格式串本身不算实参）
    nreal = ncomma
    if nspec != nreal:
        nslog_bad.append("第 %d 行: 格式符 %d / 实参 %d | %s" %
                         (line_of(src, m.start()), nspec, nreal,
                          src[m.start():m.start() + 90].replace("\n", " ")))
chk("所有 NSLog 参数个数一致", not nslog_bad,
    ("\n           ".join(nslog_bad)) if nslog_bad else "")

print("\n[A4] 符号定义顺序")


def def_line(name):
    for i, l in enumerate(lines, 1):
        if re.match(r"\s*static\s+[A-Za-z_][\w\s\*]*\b%s\s*\(" % re.escape(name), l):
            return i
    return None


for name in ["SVPNColorDesc", "SVPNEntryDesc", "SVPNLogData",
             "SVPNDataLooksSuspicious", "svpnMergeMissingEntries"]:
    d = def_line(name)
    use = None
    for i, l in enumerate(lines, 1):
        if re.search(r"\b%s\s*\(" % name, l) and not re.match(r"\s*static\s", l):
            use = i
            break
    ok = d is not None and (use is None or use > (d or 0) or use == d)
    chk("%s 定义(%s) 先于使用(%s)" % (name, d, use), ok)

print("\n[A5] iOS 16 分支未改")


def group16(s):
    a = s.find("%group SingleVPN_16")
    b = s.find("%end", a)
    return s[a:b]


if os.path.exists(ORIG):
    o = io.open(ORIG, encoding="utf-8").read()
    chk("SingleVPN_16 与 v2.1-2 逐字节一致", group16(src) == group16(o),
        "%d vs %d 字节" % (len(group16(o)), len(group16(src))))
else:
    chk("找到对照原件", False, ORIG)

print("\n[A6] hook 清单")
hooks = re.findall(r"^%hook\s+(\w+)", code, re.M)
chk("%hook 共 7 个", len(hooks) == 7, ",".join(hooks))
for want in ["STUIStatusBar", "STUIStatusBarWifiSignalView",
             "STUIStatusBarCellularNetworkTypeView"]:
    chk("含 %s" % want, want in hooks)

print("\n[A7] 修复点仍在")
n_cur = len(re.findall(r"cur\.(?:cellularEntry|secondaryCellularEntry|wifiEntry)\.enabled", code_np))
chk("回填三处均加 enabled 守卫", n_cur == 3, "找到 %d 处" % n_cur)
chk("A/B 宏 SVPN_DISABLE_ENTRY_MERGE 存在", "SVPN_DISABLE_ENTRY_MERGE" in src)

print("\n[A8] 诊断开关")
chk("SVPN_DIAG 为 1（本版为诊断版）",
    re.search(r"^#define\s+SVPN_DIAG\s+1\b", src, re.M) is not None)

print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")
