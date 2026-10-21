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
  A9~A12 桥接/全局变量/日志通道等细节
  A13 脚本行尾必须是 LF（CRLF 在 runner 上 command not found）
  A14 脚本在 git 索引里必须是 100755
  A15 CI 不得再安装整套 Theos（零 Theos 构建）
  A16 shell 里 $VAR 后不得紧跟中文/全角字符（会被算进变量名）
  A17 打包脚本必须用 GNU tar 格式（PAX 扩展头会让 dpkg 拒绝安装）

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

print("\n[A9] 多变量声明漏指针星号")
# 真实踩过：NSString *str = nil, cross = nil;
# → clang: error: interface type cannot be statically allocated
# 这类错一眼能看出，但导致 CI 白跑 12 分钟，必须静态拦住。
# 判定：一行形如「类型 *a = ..., b = ...」中，b 之前没有 * 且类型是指针型/对象型。
_PTR_TYPES = {
    "NSString", "NSMutableString", "NSArray", "NSMutableArray", "NSDictionary",
    "NSMutableDictionary", "NSSet", "NSNumber", "NSData", "NSDate",
    "UIColor", "UIFont", "UIView", "UIImage", "UIImageView", "UIWindow",
    "NSUserDefaults", "NSException", "NSMutableString",
    "STStatusBarData", "STStatusBarDataEntry", "STUIStatusBar",
    "STUIStatusBarStyleAttributes", "STUIStatusBarStringView",
    "UITraitCollection", "NSLayoutConstraint",
}
_OBJ_PREFIXES = ("NS", "UI", "ST", "CA")

star_bad = []
for ln, raw in enumerate(lines, 1):
    l = raw.strip()
    if not l or l.startswith("//") or l.startswith("#") or l.startswith("*"):
        continue
    # 去掉行尾注释
    l = re.sub(r"//.*$", "", l)
    # 粗略去掉方法声明行（含 ) - 开头在下一行）
    m = re.match(r"^(?:static\s+|const\s+|__block\s+|__weak\s+|__strong\s+)*"
                 r"([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*(?:=[^;]*)?,\s*(.+)$", l)
    if not m:
        continue
    typ, _, rest = m.group(1), m.group(2), m.group(3)
    is_obj = typ in _PTR_TYPES or typ.startswith(_OBJ_PREFIXES) or typ == "id"
    if not is_obj:
        continue
    # rest 里按逗号切分，逐项检查是否带 *
    for part in rest.split(","):
        p = part.strip()
        if not p:
            continue
        pm = re.match(r"^\**\s*([A-Za-z_]\w*)\s*=", p)
        if pm and not p.startswith("*"):
            star_bad.append("第 %d 行: %s 声明了 %s *%s 但 %s 缺少 * | %s" %
                            (ln, typ, typ, m.group(2), pm.group(1), l[:80]))
chk("对象指针多变量声明均带 *", not star_bad,
    ("\n           ".join(star_bad)) if star_bad else "")

print("\n[A10] ARC 桥接转换")
# 真实踩过两次：
#   ① NSData 用 NSString 的 writeToFile:...（类型用错）
#   ② (const void *)data —— clang: requires a bridged cast
# 本插件 Makefile 带 -fobjc-arc，ObjC 指针转 C 指针必须显式 __bridge，
# 否则直接编译失败。这里查「(void *)<objc 变量>」这类裸转换。
_ARC_BAD = []
# 这些 API 的第一个参数要的是「函数/符号地址」，函数指针转 C 指针不受 ARC 约束，
# 写成 __bridge 反而是错的（ARC 只管 ObjC 对象指针）。这里整行豁免。
_ADDR_API = ("dladdr", "dlsym", "dlopen", "dlerror", "pthread_create",
             "imp_implementationWithBlock", "method_setImplementation")
for ln, raw in enumerate(lines, 1):
    l = re.sub(r"//.*$", "", raw)
    if "__bridge" in l or "__bridge_transfer" in l or "CFBridging" in l:
        continue
    if any(api + "(" in l for api in _ADDR_API):
        continue
    for m in re.finditer(r"\(\s*(?:const\s+)?void\s*\*\s*\)\s*([A-Za-z_]\w*)", l):
        name = m.group(1)
        if name in ("NULL", "nil", "0"):
            continue
        # 标识符紧跟 '(' → 是函数名，不是 ObjC 对象变量
        tail = l[m.end():m.end() + 1]
        if tail == "(":
            continue
        # 项目内所有 ObjC 变量都用驼峰小写开头；C 标量/常量全大写或全小写单词
        _ARC_BAD.append("第 %d 行: 裸转 C 指针需 __bridge | %s" % (ln, l.strip()[:90]))
chk("ObjC→C 指针转换均带 __bridge", not _ARC_BAD,
    ("\n           ".join(_ARC_BAD)) if _ARC_BAD else "")

print("\n[A11] 全局变量先定义后使用")
# 加诊断代码时很容易「用了新计数器忘了定义」（gStyleDiagCount 就是这样差点漏掉）。
# 这里扫描所有 g 开头的全局量：首次出现必须是 static 定义行。
# ★ 必须跳过注释行：否则在注释里提到变量名（比如「总上限 gHeartbeatMax 条」）
#   会被当成首次出现而误报，逼着人不敢在注释里写变量名，本末倒置。
_g_undef = []
_g_first = {}
for ln, raw in enumerate(lines, 1):
    stripped = raw.lstrip()
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        continue
    for m in re.finditer(r"\b(g[A-Z]\w*)\b", raw):
        name = m.group(1)
        if name not in _g_first:
            _g_first[name] = (ln, raw.strip())
for name, (ln, l) in sorted(_g_first.items()):
    if not re.match(r"\s*static\s", l):
        _g_undef.append("%s 首次出现在第 %d 行但不是 static 定义 | %s" % (name, ln, l[:80]))
chk("g* 全局量均先定义", not _g_undef,
    ("\n           ".join(_g_undef)) if _g_undef else
    "共 %d 个：%s" % (len(_g_first), ",".join(sorted(_g_first))))

print("\n[A12] 日志通道依赖")
# 诊断版靠 dladdr 反推 jbroot 落盘。缺 dlfcn.h 会报 Dl_info 未定义；
# 缺 dladdr 则整个文件通道静默失效（编译能过，运行期永远 nil）——最难查的一种。
_LOGDEP = [
    ("<dlfcn.h> 已导入",        lambda s: "#import <dlfcn.h>" in s or "#include <dlfcn.h>" in s),
    ("dladdr 调用存在",         lambda s: "dladdr(" in s),
    ("SVPNLogFilePath 定义",    lambda s: "static NSString *SVPNLogFilePath(void)" in s),
    ("SVPNAppendToFile 定义",   lambda s: "static void SVPNAppendToFile(NSString *line)" in s),
    ("SVPNLog 宏定义",          lambda s: "#define SVPNLog(" in s),
    # 第二通道：文件写不进去时的兜底，靠 cfprefsd
    ("CoreFoundation 已导入",   lambda s: "CoreFoundation/CoreFoundation.h" in s),
    ("CFPreferencesSetAppValue", lambda s: "CFPreferencesSetAppValue(" in s),
    ("CFPreferencesAppSynchronize", lambda s: "CFPreferencesAppSynchronize(" in s),
    ("SVPNPushToDefaults 定义", lambda s: "static void SVPNPushToDefaults(" in s),
]
_src_all = "\n".join(lines)
_missing = [n for n, f in _LOGDEP if not f(_src_all)]
chk("日志通道依赖齐全", not _missing,
    ("缺失：".join(_missing)) if _missing else "%d/%d 齐全" % (len(_LOGDEP), len(_LOGDEP)))

# 落盘函数的定义必须在宏之前：C 宏展开时函数要先有声明过
_idx_append = _src_all.find("static void SVPNAppendToFile")
_idx_macro = _src_all.find("#define SVPNLog(")
chk("SVPNAppendToFile 定义在 SVPNLog 之前",
    _idx_append >= 0 and _idx_macro > _idx_append,
    "" if (_idx_append >= 0 and _idx_macro > _idx_append)
    else "append=%d macro=%d（宏内调用会得到隐式声明）" % (_idx_append, _idx_macro))

print("\n[A13] 脚本行尾（CRLF 会让 shell 直接报 command not found）")
# 真实踩过：devkit/env.sh 是 CRLF，在 macOS runner 上执行报
#   "devkit/env.sh: line 2: : command not found" + exit 127
# 根因是 \r 被当成命令的一部分。git 通道提交会按 .gitattributes 规范化，
# 但用 REST API 直推 blob 时不会 —— 所以必须在自检里卡住。
_bad_eol = []
for _root, _dirs, _fs in os.walk(_REPO):
    if '.git' in _root or '.theos' in _root or 'packages' in _root:
        continue
    for _f in _fs:
        if not _f.endswith(('.sh', '.pl', '.pm')):
            continue
        _p = os.path.join(_root, _f)
        _b = open(_p, 'rb').read()
        if b'\r\n' in _b:
            _bad_eol.append(os.path.relpath(_p, _REPO))
chk("所有 .sh/.pl/.pm 均为 LF 行尾", not _bad_eol,
    ("CRLF: " + ", ".join(_bad_eol)) if _bad_eol else "全部 LF")

print("\n[A14] 脚本可执行位（644 在 runner 上 permission denied）")
# 真实踩过：一次 git add -A 把 devkit/env.sh / roothide.sh / rootless.sh
# 从 100755 降成 100644。Windows 侧 ls 看不出差别（一律显示 -rwxr-xr-x），
# 只有 git 索引里的 mode 才作数；到 Linux runner 上就 permission denied。
# 与 A13 属同一类：本地完全正常，跨平台才炸。
_git_dir = os.path.join(_REPO, '.git')
_mode_bad = []
if os.path.isdir(_git_dir):
    import subprocess
    _r = subprocess.run(['git', 'ls-files', '-s', '*.sh'],
                        cwd=_REPO, capture_output=True, text=True)
    for _line in (_r.stdout or '').splitlines():
        # 形如：100755 <sha> 0\tdevkit/env.sh
        _parts = _line.split('\t')
        if len(_parts) != 2:
            continue
        _mode = _parts[0].split()[0]
        _path = _parts[1]
        if _mode != '100755':
            _mode_bad.append("%s=%s" % (_path, _mode))
chk("索引中所有 .sh 均为 100755", not _mode_bad,
    ("权限不对: " + ", ".join(_mode_bad)) if _mode_bad
    else "全部 100755（修法：git update-index --chmod=+x <file>）")

print("\n[A15] CI 不得再安装整套 Theos（用户明确质疑过）")
# 2026-10-21 用户原话：「为什么要Theos 安装，我之前编译了那么多deb，
# 也没安装这个吧」。实测 Setup Theos 要 680 秒（clone 主仓库+全部子模块
# ~200MB），而三个架构真正编译合计只要 42 秒 —— 90% 时间花在无谓的 clone 上。
# 现在改为：单文件 logos.pl 预处理 + xcrun clang 直编 + devkit/mkdeb.sh 拼 deb。
_wf = os.path.join(_REPO, '.github', 'workflows', 'build.yml')
_wf_ok = os.path.isfile(_wf)
_wf_txt = open(_wf, encoding='utf-8').read() if _wf_ok else ''
chk("workflow 文件存在", _wf_ok, _wf)
if _wf_ok:
    # 反向断言：工作流里不应再出现 install-theos / 整目录缓存 theos
    chk("未再调用 install-theos", 'install-theos' not in _wf_txt,
        "" if 'install-theos' not in _wf_txt else "仍在装整套 Theos")
    # 必须以模块方式使用 logos.pl 与 build.sh
    chk("logos.pl 已 vendor 进仓库（不靠运行时下载）",
        os.path.isfile(os.path.join(_REPO, 'vendor', 'logos', 'bin', 'logos.pl')))
    # raw.githubusercontent.com 在国内网络常不可达，构建不得依赖它
    chk("构建不依赖 raw.githubusercontent 下载",
        'raw.githubusercontent.com' not in _wf_txt,
        "" if 'raw.githubusercontent.com' not in _wf_txt else "仍在运行时下载")
    chk("使用 devkit/build.sh 构建", 'devkit/build.sh' in _wf_txt)
    chk("打包走 devkit/mkdeb.sh（不依赖 dpkg-deb）",
        os.path.isfile(os.path.join(_REPO, 'devkit', 'mkdeb.sh')))
    # 两个架构都要出包
    chk("覆盖 arm64e 与 arm64", 'arm64e' in _wf_txt and 'arm64' in _wf_txt)

print("\n[A16] shell 里 $VAR 不得紧跟中文/全角字符")
# 真实踩过（CI 实测 devkit/build.sh line 125）：
#   echo "✓ 架构校验通过（$CPU）"
#   报 `CPU）: unbound variable`，set -eu 直接退出，整步失败。
# 根因：bash 的变量名边界只认 ASCII 字母/数字/下划线，但它并不把多字节
#   UTF-8 字符当「非标识符」，而是把这些字节一并算进名字里 → 变量名变成
#   `CPU）`。本项目注释与提示语大量用中文，这类位置极易随手写出。
# 修法：一律写 ${CPU}。此检查专抓「$name 后紧跟非 ASCII 字节」。
import re as _re
_var_bad = []
for _root, _dirs, _fs in os.walk(os.path.join(_REPO, 'devkit')):
    for _f in _fs:
        if not _f.endswith('.sh'):
            continue
        _p = os.path.join(_root, _f)
        for _ln, _line in enumerate(open(_p, encoding='utf-8', errors='replace'), 1):
            if _line.lstrip().startswith('#'):
                continue          # 纯注释行不执行，放过（与 A11/A15 同类处理）
            # $NAME 后面紧跟一个非 ASCII 字节
            for _m in _re.finditer(r'\$([A-Za-z_][A-Za-z0-9_]*)([^\x00-\x7f])', _line):
                _var_bad.append("%s:%d $%s 后紧跟 %r"
                                % (os.path.relpath(_p, _REPO), _ln, _m.group(1), _m.group(2)))
chk("devkit/*.sh 无 $VAR 紧邻全角字符", not _var_bad,
    ("; ".join(_var_bad)) if _var_bad else "全部使用 ${VAR} 或 ASCII 边界")

print("\n[A17] 打包脚本必须用 GNU tar 格式（PAX 会让 dpkg 装不上）")
# 真实事故（2026-10-21 用户实机安装失败，截图报错）：
#   corrupted filesystem tarfile in package archive:
#   unsupported PAX tar header type 'x'
#   W: Unknown TAR header type 120
#   E: Sub-process /usr/bin/dpkg returned an error code (1)
# 根因：Python 3.8+ 的 tarfile.open(mode='w') 默认 PAX_FORMAT，遇到
#   mtime 带小数秒 / 路径非 ASCII 时，给**每个**成员插一个 typeflag='x' 的
#   ././@PaxHeader。实测那个 deb 的 data.tar 里有 26~27 个。
# dpkg 的 tar 实现只认 ustar/GNU，不认 PAX 扩展头 → 装包直接失败。
# 修法：所有 tarfile.open(...) 都必须显式 format=tarfile.GNU_FORMAT。
_mk = os.path.join(_REPO, 'devkit', 'mkdeb.sh')


def _code_hits(txt, needle):
    """返回 txt 里所有「引号闭合状态下」出现的 needle 的 (起始下标, 上下文)。

    ★ 这个坑在本项目已经犯过四次（A11 / A15 / A16 / A17）：
      检查器把「解释为什么不能用某个写法」的说明文字当成了「真的用了」。
      前三次靠「剔除以 # 开头的行」能糊过去，这次不行 —— 命中的两处都不是
      bash 注释，而是嵌在 `python3 - <<'PY'` heredoc 里的：
        ① 函数 docstring 举例：`Python 3.8+ 的 tarfile.open(mode='w') 默认…`
        ② print 的字符串参数：`print('   修法：tarfile.open(..., format=…)')`
      按「行首 #」剔不掉，按 strip_code 逐行处理也剔不干净（三引号跨行）。

    可靠判据是**扫描到该位置时引号是否闭合**：
      真代码 `with tarfile.open(fileobj=buf, mode='w', …)` → 引号为 None；
      docstring / print 字符串里的 → 前面必有一个未闭合的引号。
    shell 与 python 都适用（mkdeb.sh 里没有跨行转义引号这种写法）。
    """
    hits = []
    quote = None
    i, n = 0, len(txt)
    while i < n:
        if quote is None:
            for q in ('"""', "'''"):
                if txt.startswith(q, i):
                    quote = q
                    i += 3
                    break
            else:
                c = txt[i]
                if c in ('"', "'"):
                    quote = c
                    i += 1
                    continue
                if txt.startswith(needle, i):
                    hits.append(txt[max(0, i - 40):i + len(needle) + 40])
                    i += len(needle)
                    continue
                if c == '\\':
                    i += 2
                    continue
                i += 1
            continue
        # 在引号内：找闭合
        if len(quote) == 3:
            if txt.startswith(quote, i):
                quote = None
                i += 3
                continue
            if txt[i] == '\\':
                i += 2
                continue
            i += 1
        else:
            if txt[i] == '\\':
                i += 2
                continue
            if txt[i] == quote:
                quote = None
            elif txt[i] == '\n' and quote == "'" and False:
                quote = None
            i += 1
    return hits


def _code_hits(txt, needle):
    """返回 txt 里所有「引号闭合状态下」出现的 needle 的 (起始下标, 上下文)。

    逐字符扫，维护 quote 状态：未闭合 / 单引号 / 双引号 / 三引号。
    遇到 needle 时若 quote 为「未闭合」才算真代码命中 —— 文档串里的举例不算。
    shell 与 python 都适用：本文件用到的范围内两者引号规则一致
    （mkdeb.sh 里没有跨行转义引号这种写法）。
    """
    hits = []
    quote = None
    i, n = 0, len(txt)
    while i < n:
        if quote is None:
            if txt.startswith('"""', i) or txt.startswith("'''", i):
                quote = txt[i:i + 3]
                i += 3
                continue
            c = txt[i]
            if c == '\\':
                i += 2
                continue
            if c in ('"', "'"):
                quote = c
                i += 1
                continue
            if txt.startswith(needle, i):
                hits.append((i, txt[max(0, i - 40):i + len(needle) + 40]))
                i += len(needle)
                continue
            i += 1
        else:
            if len(quote) == 3:
                if txt.startswith(quote, i):
                    quote = None
                    i += 3
                    continue
                if txt[i] == '\\':
                    i += 2
                    continue
                i += 1
            else:
                if txt[i] == '\\':
                    i += 2
                    continue
                if txt[i] == quote:
                    quote = None
                i += 1
    return hits


def _call_args(txt, start):
    """从 `tarfile.open(` 的 start 位置起，取到配对的右括号为止（含）。"""
    i = txt.index('(', start)
    depth = 0
    while i < len(txt):
        if txt[i] == '(':
            depth += 1
        elif txt[i] == ')':
            depth -= 1
            if depth == 0:
                return txt[start:i + 1]
        i += 1
    return txt[start:start + 200]


if os.path.isfile(_mk):
    _mk_txt = open(_mk, encoding='utf-8').read()
    # 只在「引号外」的实代码里找 tarfile.open，注释与字符串里的举例不算
    _open_pos = _code_hits(_mk_txt, 'tarfile.open(')
    _calls = [_call_args(_mk_txt, p) for p, _ in _open_pos]
    _no_fmt = [c for c in _calls if 'format=' not in c]
    chk("mkdeb.sh 的 tarfile.open 都指定了 format",
        bool(_calls) and not _no_fmt,
        ("%d 处未指定格式: %s" % (len(_no_fmt), _no_fmt[0][:60])) if _no_fmt
        else "%d 处全部显式指定" % len(_calls))
    _has_gnu = bool(_code_hits(_mk_txt, 'tarfile.GNU_FORMAT'))
    chk("mkdeb.sh 使用 tarfile.GNU_FORMAT（不用 PAX）",
        _has_gnu,
        "" if _has_gnu else "缺 GNU_FORMAT，会产出 PAX 头")
    # 反向断言：实代码里不该显式选 PAX（注释里提到 PAX_FORMAT 是为了说明原因，不算）
    _pax_hits = _code_hits(_mk_txt, 'PAX_FORMAT')
    chk("mkdeb.sh 代码里未显式选 PAX_FORMAT",
        not _pax_hits,
        "" if not _pax_hits else "显式用了 PAX，dpkg 会拒绝: %s" % _pax_hits[0][1])
    chk("mkdeb.sh 里有 PAX 扩展头自检闸门",
        '_check_no_pax_headers' in _mk_txt)

print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")
