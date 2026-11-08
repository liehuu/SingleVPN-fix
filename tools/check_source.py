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
# ★ 2.1-8 方向反转：交付版必须关诊断。
#   SVPNLog = NSLog + 主线程同步写文件双写；respring 首帧数据必然残缺，
#   会当场命中 SUSPECT 分支写文件，正好卡在状态栏建实例的时刻。
#   诊断代码留在源码里备用，但不参与编译。
chk("SVPN_DIAG 为 0（交付版不编译诊断，热路径无同步 I/O）",
    re.search(r"^#define\s+SVPN_DIAG\s+0\b", src, re.M) is not None)

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
    # ★ 只约束「写」：PAX 扩展头是写 tar 时才生成的。
    #   读回验用 tarfile.open(..., mode='r:') 不需要 format，
    #   把它算进来会假红（A21 加读回验闸门时踩过）。
    _writes = [c for c in _calls if re.search(r"mode\s*=\s*['\"]w", c)]
    _no_fmt = [c for c in _writes if 'format=' not in c]
    chk("mkdeb.sh 写模式的 tarfile.open 都指定了 format",
        bool(_writes) and not _no_fmt,
        ("%d 处未指定格式: %s" % (len(_no_fmt), _no_fmt[0][:60])) if _no_fmt
        else "%d 处全部显式指定" % len(_writes))
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

print("\n[A18] 启动窗口透传闸门（2.1-8 核心修复）")
# 判据全部锚到「被检查的那一段」，不做全文件泛扫：
#   _bar / _view / _g16 分别限定数据层 hook 块、iOS17 视图层、iOS16 分支。
# ★ 每条都必须能失败。写完逐条做反向注入验证（见 tools/verify_gate218.py）。
_bar_start = src.index("%hook STUIStatusBar")
_bar_end = src.index("%hook STUIStatusBarCellularNetworkTypeView")
_bar = src[_bar_start:_bar_end]
_g17_start = src.index("%group SingleVPN_17")
# ★ iOS16 分支的范围必须收在它自己的 %end 上，不能用「到 %group SingleVPN_17
#   为止」—— 这两者之间还夹着诊断块和 2.1-10 的还原模块，把整段当成 iOS16
#   分支会让 A18g 假红（2.1-10 插入还原模块时真实踩到）。
_g16_start = src.index("%group SingleVPN_16")
_g16_end = src.index("%end // SingleVPN_16")
_g16 = src[_g16_start:_g16_end]
_g17 = src[_g17_start:]

_delay = re.search(r"^#define\s+SVPN_READY_DELAY_SEC\s+(\d+)\b", src, re.M)
chk("A18a 启动窗口 >= 3 秒（首帧与数据上报都要在这段时间内完成）",
    _delay is not None and int(_delay.group(1)) >= 3,
    "" if _delay else "缺 SVPN_READY_DELAY_SEC 定义")
chk("A18b 闸门变量是 static volatile int",
    re.search(r"^static\s+volatile\s+int\s+gSVPNReady\s*=\s*0\s*;", src, re.M) is not None)
chk("A18c ctor 里用 dispatch_after + 主队列把闸门打开",
    "dispatch_after(" in _g17
    and "dispatch_get_main_queue()" in _g17
    and re.search(r"gSVPNReady\s*=\s*1\s*;", _g17) is not None)
_bl = _bar.split("\n")
_gate_open = [i for i, l in enumerate(_bl) if l.strip() == "if (gSVPNReady) {"]
_gate_close = [i for i, l in enumerate(_bl) if l == "    }"]   # 精确 4 空格，不误吃 8 空格的嵌套 }
_orig_lines = [i for i, l in enumerate(_bl) if l.strip() == "%orig;"]
chk("A18d 两个数据层 hook 都包在闸门内，且 %orig 在闸门之外（单一调用点）",
    len(_gate_open) == 2 and len(_orig_lines) == 2 and len(_gate_close) >= 2
    # 开启 < 闭合 < %orig：顺序错了就等于窗口内连系统更新都被跳过
    and all(o < c < x for o, c, x in
            zip(_gate_open, _gate_close[:2], _orig_lines)),
    "open=%s close=%s orig=%s" % (_gate_open, _gate_close[:2], _orig_lines))
chk("A18e iOS17 视图层 5 处染色分支全部受闸门控制",
    _g17.count("BOOL decision = gSVPNReady && (") == 5
    and _g17.count("BOOL decision = _isEnabledReversed") == 0,
    "命中 %d / 漏 %d" % (_g17.count("BOOL decision = gSVPNReady && ("),
                        _g17.count("BOOL decision = _isEnabledReversed")))
chk("A18f 5GA 路径三处开关也都受闸门控制",
    "if (!gSVPNReady || ![text hasPrefix:@\"5G\"]" in _g17
    and "if (gSVPNReady && _isForce5GAEnabled &&" in _g17
    and "if (!gSVPNReady || !_isForce5GAEnabled)" in _g17)
chk("A18g iOS16 分支不受影响（上游基线，用户设备是 iOS 17）",
    "gSVPNReady" not in _g16)

print("\n[A19] 关诊断后仍可编译（模拟 SVPN_DIAG=0 预处理）")
# 10-21 真实踩过：#endif 边界错位 → 诊断辅助函数落在 #if 块外，
# 一关 SVPN_DIAG 就变成「未定义标识符」直接编不过。
# A2 只数 #if/#endif 配对，配对正确也可能把函数切在块外 —— 必须单独验。
_DIAG_FNS = ("SVPNLog(", "SVPNLogData(", "SVPNEntryDesc(", "SVPNColorDesc(",
             "SVPNDataLooksSuspicious(", "SVPNHeartbeatDue(",
             "SVPNLogFilePath(", "SVPNAppendToFile(", "gDiagCount")
_kept_lines = []
_dep = 0
for _l in src.split("\n"):
    _t = _l.strip()
    if re.match(r"^#if\s+SVPN_DIAG\b", _t):
        _dep += 1
        _kept_lines.append("")
        continue
    if _dep:
        if re.match(r"^#if", _t):
            _dep += 1
        elif re.match(r"^#endif", _t):
            _dep -= 1
        _kept_lines.append("")
        continue
    _kept_lines.append(_l)
_kept = "\n".join(_kept_lines)
# ★ 数 %orig 前必须剥注释：头注释里写了「15 处 %orig」、闸门注释里也提到
#   「只做 %orig 透传」，不剥会把 15 数成 17，判据变成永真/永假的装饰。
_kept_nc = "\n".join("" if l.lstrip().startswith("//") else l
                     for l in _kept_lines)
chk("A19a 条件块配平", _dep == 0, "剩余嵌套 %d" % _dep)
_leftover = [f for f in _DIAG_FNS if f in _kept]
chk("A19b 块外无残留诊断调用（关掉后不会 unrecognized / 未声明）",
    not _leftover, "残留: %s" % ",".join(_leftover))
chk("A19c 关掉诊断后功能骨架完整（7 hook / 15 orig / 染色 / 闸门）",
    len(re.findall(r"^%hook\s", _kept_nc, re.M)) == 7
    and len(re.findall(r"%orig\b", _kept_nc)) == 15
    and "svpnColorWithTextColor" in _kept_nc
    and "gSVPNReady" in _kept_nc
    and "dispatch_after(" in _kept_nc,
    "hook=%d orig=%d" % (len(re.findall(r"^%hook\s", _kept_nc, re.M)),
                         len(re.findall(r"%orig\b", _kept_nc))))

print("\n[A20] postinst 不得炸装包环境")
# ★ 这是本项目最高优先级红线：postinst 跑在 dpkg 事务最后一步，
#   此刻前台 Sileo 还活着；任何重启桌面的动作都会把包管理器一起杀掉
#   → status 没写、锁没释放 → 半装，之后连别的包都装不上。
#   `|| true` 只兜得住返回码，兜不住进程被杀。
# ★ 判据必须锚 here-doc 正文，且剔除 printf 行（提示文案里提到某个词
#   不等于真会执行）—— 10-21 在别的项目踩过「注释/文案把禁项判成命中」。
_mk20 = os.path.join(_REPO, 'devkit', 'mkdeb.sh')
_raw20 = io.open(_mk20, encoding='utf-8').read() if os.path.isfile(_mk20) else ""
_po = re.search(r"cat\s+>\s+\"?\$STAGE/DEBIAN/postinst\"?\s+<<\s*'EOF'\s*\n(.*?)\nEOF",
                _raw20, re.S)
_body20 = _po.group(1) if _po else ""
_cmd20 = "\n".join(l for l in _body20.split("\n")
                   if l.strip() and not l.strip().startswith(("printf", "echo", "#")))
chk("A20a 能提取到 postinst 正文", bool(_po) and "exit 0" in _body20)
for _w in ("sbreload", "killall", "respring", "launchctl"):
    chk("A20 postinst 不执行 %s" % _w,
        re.search(r"\b%s\b" % _w, _cmd20) is None)
chk("A20 postinst 不碰 SpringBoard 进程",
    "SpringBoard" not in _cmd20)

print("\n[A21] data.tar 不得含 DEBIAN/")
# dpkg 只从 control.tar.gz 读控制文件；data.tar 里那份会解到根 /DEBIAN/，
# rootless / RootHide 上根只读 → 整个包装不上。
chk("A21a _tar() 跳过 DEBIAN 顶层目录",
    re.search(r"dirs\[:\]\s*=\s*\[[^\]]*!=\s*['\"]DEBIAN['\"]", _raw20) is not None)
chk("A21b 有构建期闸门解包回验（不只靠源码里写了 skip）",
    "data_raw" in _raw20
    and "data.tar 含 DEBIAN 条目" in _raw20
    and "_bad" in _raw20)

print("\n[A22] VPN 状态只认本轮数据（2.1-9 修复「断开不恢复」）")
# 上游 6e068f7 为配合「回填旧 entry」，把上一轮快照与 data 做或运算；
# 2.1-8 关掉回填后这个 OR 失去前提，却把断开时的旧 YES 永久锁存。
# ★ 判据锚「数据层 hook 段」并剥注释，防止被头注释里的说明文字代答。
# ★ 三条必须各自能独立失败，见 tools/verify_gate218.py 的 A22 反向用例。
_bar_nc = strip_code(_bar)
_vpn_upd = [l.strip() for l in _bar_nc.split("\n")
            if re.fullmatch(r"if\s*\(\s*vpnNow\s*\)\s*\{\s*_isVPNEnabled\s*=\s*vpnNow\.enabled\s*;\s*\}",
                            l.strip())]
chk("A22a 两个数据层 hook 都是「本轮有条目才更新」",
    len(_vpn_upd) == 2, "命中 %d 处（期望 2）" % len(_vpn_upd))
_all_writes = re.findall(r"_isVPNEnabled\s*=", _bar_nc)
chk("A22b 数据层没有第三处状态写入（防止别处把状态改回去）",
    len(_all_writes) == 2, "共 %d 处" % len(_all_writes))
_ks = _kept_nc.find("%hook STUIStatusBar")
_ke = _kept_nc.find("%hook STUIStatusBarCellularNetworkTypeView", _ks)
_bar_kept = _kept_nc[_ks:_ke] if _ks >= 0 and _ke > _ks else ""
chk("A22c 关诊断后数据层实代码里不含上一轮快照 currentData",
    bool(_bar_kept) and "currentData" not in _bar_kept,
    "" if "currentData" not in _bar_kept else "残留 currentData")

print("\n[A23] 断开 VPN 后视图层还原（2.1-10 修复桌面染色残留）")
# 用户的实测把范围钉死了：App 内颜色已正常退回（数据层 2.1-9 成立），
# 只有桌面残留 → 桌面那套常驻状态栏视图实例不会被系统重新下发样式，
# 上一次染上去的颜色就留在视图上。修法是「染色时记下系统原色，断开时回写」。
# ★ 三条最容易写错、且错了会静默失效的点各一条判据：
#   a 记的是系统原色而不是自己染的色（把绿色当系统色存 = 还原成绿色 = 没修）
#   b 过渡触发挂在两个数据层 hook 上（漏一个就有一半断开场景不还原）
#   c 还原前必须重新确认「现在确实不该染」（断开又秒连时擦色 = 制造新 bug）
#
# ★ 区间必须精确，两种错法都会让判据变成摆设：
#   ① 用「%end // SingleVPN_16 到 %group SingleVPN_17」= 把诊断块、回填函数
#      一起算进来（2.1-10 首次加判据时真实踩到：诊断块里的 @try/@finally
#      让 A23e 假红，注释里的方法名让 A23d 假红）
#   ② 染色点在 %hook 内部，属于 _g17，不在还原模块里 —— 用还原模块数它们
#      永远是 0 处
_MOD10 = "// ===== 2.1-10：断开 VPN 后把染色还原"
_r_mod = src[src.index(_MOD10):_g17_start]     # 还原模块（函数 + 注释）
_r_nc = strip_code(_r_mod)
_g17_nc = strip_code(_g17)                     # iOS17 分组（含五个染色点）


def body_of(name, txt):
    """按花括号配平取出某个函数的函数体（含外层 {}）。

    ★ 判据必须锚「被检查的那一段」：`if (!gSVPNReady)` 这种闸门出现在本模块
      两个函数里，用整个模块去找会被另一个函数代答（A23b5 注入时真实踩到）。
    """
    i = txt.find(name)
    if i < 0:
        return ""
    b = txt.find("{", i)
    if b < 0:
        return ""
    depth = 0
    for j in range(b, len(txt)):
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
            depth -= 1
            if depth == 0:
                return txt[b:j + 1]
    return ""


_restore_body = body_of("static void SVPNRestorePaintedViewsNow(void)", _r_nc)

chk("A23a1 还原模块在 iOS17 分组之外（iOS16 分支保持上游基线）",
    "SVPNRestorePaintedViewsNow" in _r_mod and "SVPNRestorePaintedViewsNow" not in _g16)
chk("A23a2 还原表 key 用 weak + 指针同一性（视图销毁自动出表，且不按 isEqual 合并）",
    "NSPointerFunctionsWeakMemory | NSPointerFunctionsObjectPointerPersonality" in _r_mod
    and "mapTableWithKeyOptions:kOpt" in _r_mod)
# ★ 真正的危险是「遍历集合的同时改它」，不是两段循环的先后 —— 首版判据
#   写成「收集循环在还原循环之前」，注入 removeObjectForKey 后顺序没变 → 拦不住。
#   改成直接把收集循环的循环体抓出来，断言里面没有对登记表的写操作。
_c_for = _r_nc.find("for (id v in gSVPNOriginals) {")
_c_body = ""
if _c_for >= 0:
    _c_end = _r_nc.find("\n        }", _c_for)
    _c_body = _r_nc[_c_for:_c_end if _c_end > 0 else _c_for + 400]
chk("A23a3 收集循环体内不修改登记表（边遍历边改 NSMapTable 是未定义行为）",
    bool(_c_body) and "removeObjectForKey" not in _c_body and "setObject:" not in _c_body,
    "循环体 %d 字节" % len(_c_body) if _c_body else "循环体没抓到")
chk("A23a3b 收集完成后才逐个还原（不边遍历边改）",
    0 <= _c_for < _r_nc.find("for (id view in views)"),
    "collect=%d restore=%d" % (_c_for, _r_nc.find("for (id view in views)")))
chk("A23a4 每条 slot 还原前先 respondsToSelector（不存在的选择子直接跳过）",
    "respondsToSelector:sel" in _r_mod and "methodForSelector:sel" in _r_mod)
# ★ 必须锚行首（re.M + ^）：否则 `// #define SVPN_RESTORE_MAX_VIEWS 32`
#   这种「被注释掉的宏」照样能匹配到，判据变成永真（反向注入 A23a5 时真实踩到）。
chk("A23a5 寄存器表上限有界（宏未被注释/删除）",
    re.search(r"^#define\s+SVPN_RESTORE_MAX_VIEWS\s+\d+\s*$", _r_mod, re.M) is not None)
# 表里只放四个真正能回写的槽位；dim 没有对应视图 setter，放进来就是死条目
chk("A23a6 还原计划只含可回写的槽位（tc/ac/ic/tint，不含 dim）",
    '@[@"tc", @"setTextColor:"]' in _r_mod
    and '@[@"ac", @"setActiveColor:"]' in _r_mod
    and '@[@"ic", @"setInactiveColor:"]' in _r_mod
    and '@[@"tint", @"setTintColor:"]' in _r_mod
    and '@[@"dim"' not in _r_mod)
# ★ a：残留识别。setText: 路径读的是 self.textColor，可能是上一轮染的绿，
#   必须有「等于上次染的色就拒绝记账」这道判断，否则还原会还原成绿色。
chk("A23b1 识别残留：等于上次染的色就不当系统色记账",
    "if (last && [last isEqual:color])" in _r_mod)
# ★ b：过渡触发。两个数据层 hook 各一处，少一处就有一半场景不还原。
chk("A23b2 两个数据层 hook 都触发过渡检测", _bar_nc.count("SVPNNoteDecisionTransition();") == 2,
    "命中 %d 处（期望 2）" % _bar_nc.count("SVPNNoteDecisionTransition();"))
chk("A23b3 只在 1→0 时触发（连上不擦色）",
    "if (prev == 1 && !now)" in _r_mod)
# ★ c：执行期复核。异步一拍之后状态可能已经变了。
chk("A23b4 还原前重新确认当前不该染", "if (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled) {" in _restore_body
    and _restore_body.find("if (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled) {")
    < _restore_body.find("NSMutableArray *views = [NSMutableArray array];"))
chk("A23b5 还原有独立闸门，不受启动窗口影响（启动期间不该有染色也就无需还原）",
    "if (!gSVPNReady) {\n        return;\n    }" in _restore_body,
    "还原函数体 %d 字节" % len(_restore_body))
# 染色侧：五个染色点都在 iOS17 分组里，必须同时记原色 + 记染上的色。
# 与 A18e 的「5 处染色分支」是同一批（tc×2 / ac / ic / tint）。
_n_orig = _g17_nc.count("SVPNRememberOriginal(self,")
chk("A23c1 五个染色点都记系统原色", _n_orig == 5, "命中 %d 处（期望 5）" % _n_orig)
_n_paint = _g17_nc.count("SVPNRememberPainted(self,")
chk("A23c2 每个染色点同时记染上的色", _n_paint == 5, "命中 %d 处（期望 5）" % _n_paint)
# ★ 顺序：applyStyleAttributes: 里必须「先读原值再 copy 后改」，反过来读到的是改后的值
_as = _g17.find("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs {")
_as_body = _g17[_as:_as + 1400] if _as >= 0 else ""
_i_sys = _as_body.find("UIColor *sysTextColor = styleAttrs.textColor;")
chk("A23c3 StringView 侧先取原色再 copy 再染色（顺序反了会记下自己的色）",
    0 <= _i_sys < _as_body.find("styleAttrs = [styleAttrs copy];")
    < _as_body.find('SVPNRememberOriginal(self, @"tc", sysTextColor);'),
    "sys=%d copy=%d rec=%d" % (_i_sys, _as_body.find("styleAttrs = [styleAttrs copy];"),
                               _as_body.find('SVPNRememberOriginal(self, @"tc", sysTextColor);')))
# 反向断言：不得去调系统的私有重绘 API（那是「主动刷新宿主」，风险最高的一类）
# ★ 必须先剥注释：还原模块的注释里就写着「不主动重绘」并提到了这些方法名，
#   直接数原文会把自己的说明文字当成调用（本项目已犯过五次同类错）。
for _bad_api in ("_updateWithAggregatedData", "setNeedsStatusBarAppearanceUpdate",
                 "_updateWithData", "layoutIfNeeded", "layoutSubviews"):
    chk("A23d 还原路径不主动重绘宿主（不调用 %s）" % _bad_api,
        _r_nc.count(_bad_api) == 0,
        "出现 %d 次" % _r_nc.count(_bad_api))
_t = _r_nc.count("@try")
_c = _r_nc.count("@catch")
chk("A23e 还原全程 @try 包裹（异常绝不出宿主进程）",
    _t >= 6 and _c == _t, "try=%d catch=%d" % (_t, _c))

print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")
