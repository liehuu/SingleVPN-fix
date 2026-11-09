"""SingleVPN 源码闸门（2.2-1 重写版）。

设计原则（每条都是踩过的坑）：
1. **每条判据都必须能失败**。写完后先反问「它有没有可能失败」，答不出来就是装饰。
2. **判据要锚住被检查的那一段**：用 body_of() 按花括号配平取函数体，
   不能用「两个锚点之间」——中间夹的注释/别的函数会代答（真实踩过）。
3. **判「有没有某个 token」用 strip_code（连字符串一起抹）**，
   **判「某个 @"literal" 在不在」用 strip_comments_only（保留字符串）**。
   用错方向会让判据永真或永假（2.1-11 A23c4 就是永真）。
4. **负向断言必须在剥过注释的文本上做**，否则自己的说明文字会被判成违规。
5. 报错信息里带上实际值，失败时能直接看出「差在哪」。
"""
import io
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_REPO, "SingleVPN.x")
MK = os.path.join(_REPO, "devkit", "mkdeb.sh")
YML = os.path.join(_REPO, ".github", "workflows", "build.yml")

fails, oks = [], []


def chk(name, ok, detail=""):
    (oks if ok else fails).append((name, detail))
    print(("  OK   " if ok else "  FAIL ") + name + (("  -> " + detail) if detail else ""))


def strip_comments_only(s):
    """注释抹成空格，**保留字符串字面量内容**。"""
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
                    out.append(s[i:i + 2])
                    i += 2
                    continue
                out.append(s[i])
                if s[i] == q:
                    i += 1
                    break
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_code(s):
    """注释抹掉 + 字符串内容抹掉，只留代码结构。"""
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


def body_of(sig, txt):
    """按花括号配平取出某个函数的函数体（含外层 {}）。找不到返回空串。"""
    i = txt.find(sig)
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


def py_body_of(sig, txt):
    """按缩进取出 Python 函数体（mkdeb.sh 里内嵌的那段 Python）。

    ★ 不能用 body_of()：它按花括号配平，Python 函数没有花括号，
      必然返回空串 → 判据永远 FAIL（写完就被自己的闸门抓到）。
    """
    i = txt.find(sig)
    if i < 0:
        return ""
    lines = txt[i:].split("\n")
    head = lines[0]
    ind = len(head) - len(head.lstrip())
    out = [head]
    for ln in lines[1:]:
        if not ln.strip():
            out.append(ln)
            continue
        if (len(ln) - len(ln.lstrip())) <= ind:
            break
        out.append(ln)
    return "\n".join(out)


def slice_between(txt, start_marker, end_marker):
    i = txt.find(start_marker)
    if i < 0:
        return ""
    j = txt.find(end_marker, i + len(start_marker))
    return txt[i:j] if j > 0 else txt[i:]


src = io.open(SRC, encoding="utf-8").read()
nc = strip_code(src)              # 代码结构
ns = strip_comments_only(src)     # 保留字符串，去注释

print("文件: %s" % SRC)
print("总行数: %d" % len(src.split("\n")))

# 预处理行整行抹掉（含反斜杠续行），避免宏里的括号干扰配平检查
_lines, _cont = [], False
for _ln in nc.split("\n"):
    if _cont or _ln.lstrip().startswith("#"):
        _cont = _ln.rstrip().endswith("\\")
        _lines.append("")
    else:
        _lines.append(_ln)
nc_nopre = "\n".join(_lines)

# ============================================================================
print("\n[S1] 基本结构")
# ============================================================================
chk("S1a 括号配平（大/中/小括号）",
    nc_nopre.count("{") == nc_nopre.count("}")
    and nc_nopre.count("(") == nc_nopre.count(")")
    and nc_nopre.count("[") == nc_nopre.count("]"),
    "brace=%d paren=%d brack=%d" % (
        nc_nopre.count("{") - nc_nopre.count("}"),
        nc_nopre.count("(") - nc_nopre.count(")"),
        nc_nopre.count("[") - nc_nopre.count("]")))
chk("S1b 用 internal 后端（零 Theos 构建的前提）", "%config(generator=internal)" in src)
chk("S1c 没有 import Theos 私有头",
    "HBLog.h" not in nc and "<substrate.h>" not in nc)
chk("S1d iOS17 与 iOS16 两个 group 都在",
    "%group SingleVPN_17" in nc and "%group SingleVPN_16" in nc)
chk("S1e 两个 group 各自闭合",
    # 闭合标记写在 %end 之后的 // 注释里 → strip_code 会把注释抹掉，
    # 这两条必须在**原文**上判，否则永远为 0（判据写错方向）。
    src.count("%group SingleVPN_17") == 1 and src.count("%end // SingleVPN_17") == 1
    and src.count("%group SingleVPN_16") == 1 and src.count("%end // SingleVPN_16") == 1,
    "17: %d/%d  16: %d/%d" % (
        src.count("%group SingleVPN_17"), src.count("%end // SingleVPN_17"),
        src.count("%group SingleVPN_16"), src.count("%end // SingleVPN_16")))
chk("S1f 有 %ctor 且按 iOS 版本二选一",
    "%ctor" in nc and "%init(SingleVPN_17)" in nc and "%init(SingleVPN_16)" in nc)

# ============================================================================
print("\n[S2] 启动窗口闸门（2.1-8，实测通过，必须保留）")
# ============================================================================
chk("S2a 闸门变量 volatile（后台写、前台读）",
    re.search(r"static\s+volatile\s+int\s+gSVPNReady\s*=\s*0\s*;", nc) is not None)
chk("S2b 窗口时长有宏定义且为数值",
    re.search(r"^#define\s+SVPN_READY_DELAY_MS\s+\d+\s*$", src, re.M) is not None)
# ★ 判据必须锚「%ctor 里那一次 dispatch_after」本身。
#   裸判 "dispatch_get_main_queue(), ^{" 是假的：还原路径
#   dispatch_async(dispatch_get_main_queue(), ^{ ... }) 里也有一份，
#   把 ctor 那处换成后台队列后判据依然为真（反向验证 B07 抓到过）。
_ctor = body_of("%ctor {", nc)
chk("S2c 闸门开在主队列的 dispatch_after 里",
    "dispatch_after(" in _ctor
    and "dispatch_get_main_queue(), ^{" in _ctor
    and "gSVPNReady = 1;" in _ctor
    and "dispatch_get_global_queue" not in _ctor,
    "ctor 函数体 %d 字节" % len(_ctor))
chk("S2d 闸门只在一处被置 1（避免提前放行）",
    nc.count("gSVPNReady = 1;") == 1, "出现 %d 次" % nc.count("gSVPNReady = 1;"))
# 所有染色判定都必须经过 SVPNDecision（唯一入口），不允许自己拼条件
chk("S2e 判定统一走 SVPNDecision（不各自拼 gSVPNReady 条件）",
    nc.count("gSVPNReady && (") == 0 and nc.count("SVPNDecision()") >= 6,
    "SVPNDecision 调用 %d 次 / 裸拼 %d 次" % (
        nc.count("SVPNDecision()"), nc.count("gSVPNReady && (")))

# ============================================================================
print("\n[S3] VPN 状态判定（2.1-9，实测通过，必须保留）")
# ============================================================================
_g17 = nc[nc.index("%group SingleVPN_17"):]
chk("S3a 两个数据层 hook 都只认本轮 vpnEntry",
    _g17.count("STStatusBarDataEntry *vpnNow = data.vpnEntry;") == 2
    and _g17.count("if (vpnNow) {") == 2,
    "命中 %d / %d" % (_g17.count("STStatusBarDataEntry *vpnNow = data.vpnEntry;"),
                      _g17.count("if (vpnNow) {")))
# 反向：绝不许再出现「与上一轮快照做或运算」（那会锁存断开时的 YES）
chk("S3b 不与上一轮快照做或运算（否则断开不褪色）",
    "currentData.vpnEntry.enabled ||" not in nc,
    "发现 %d 处 OR 锁存" % nc.count("currentData.vpnEntry.enabled ||"))
chk("S3c 两个 hook 都触发过渡检测",
    _g17.count("SVPNNoteTransition();") == 2,
    "命中 %d 处（期望 2）" % _g17.count("SVPNNoteTransition();"))

# ============================================================================
print("\n[S4] entry 回填已彻底删除（塞残缺 entry 会让图标整块空白）")
# ============================================================================
chk("S4a 不再有回填函数",
    "svpnMergeMissingEntries" not in nc and "SVPN_DISABLE_ENTRY_MERGE" not in nc)
chk("S4b 数据层不改写系统 data（不调 dataByReplacingEntry）",
    "dataByReplacingEntry" not in nc,
    "出现 %d 次" % nc.count("dataByReplacingEntry"))
# 数据层 hook 里 data 只能被读，不能被重新赋值
_h1 = body_of("- (void)_updateWithAggregatedData:(STStatusBarData *)data", nc)
_h2 = body_of("- (void)_updateWithData:(STStatusBarData *)data completionHandler:", nc)
chk("S4c _updateWithAggregatedData 不重新赋值 data",
    bool(_h1) and not re.search(r"\bdata\s*=", _h1),
    "函数体 %d 字节" % len(_h1))
chk("S4d _updateWithData 不重新赋值 data",
    bool(_h2) and not re.search(r"\bdata\s*=", _h2),
    "函数体 %d 字节" % len(_h2))

# ============================================================================
print("\n[S5] 染色不得把图标弄到看不见（2.2-1 核心修复）")
# ============================================================================
# 这是本轮真正的根因：colorWithAlphaComponent: 是**替换** alpha 不是叠加。
# 系统给未激活格的 alpha 接近 1，被压到 0.2 后深底≈全黑、浅底≈全白，
# 两种背景下都「彻底空着」。用户明确说「原版也有」，所以这条是回归防线。
_bad = []
for _ln, _txt in enumerate(src.split("\n"), 1):
    if "colorWithAlphaComponent:" not in _txt:
        continue
    if _txt.lstrip().startswith("//"):
        continue
    _bad.append("%d:%s" % (_ln, _txt.strip()))
chk("S5a 代码里不用 colorWithAlphaComponent: 压 alpha", not _bad,
    ("残留 %d 处: " % len(_bad)) + " | ".join(_bad[:3]) if _bad else "0 处")
chk("S5b 存在保 alpha 的换色函数",
    "static UIColor *svpnReplaceKeepingAlpha(UIColor *original, UIColor *hue)" in nc)
_kp = body_of("static UIColor *svpnReplaceKeepingAlpha(UIColor *original, UIColor *hue)", nc)
chk("S5c 保 alpha 函数真的取了原色 alpha 并沿用它",
    "getRed:&sr green:&sg blue:&sb alpha:&sa" in _kp
    and "colorWithRed:r green:g blue:b alpha:sa" in _kp,
    "函数体 %d 字节" % len(_kp))
chk("S5d setInactiveColor 走保 alpha 路径",
    "svpnReplaceKeepingAlpha(inactiveColor," in _g17)
chk("S5e imageDimmedTintColor 走保 alpha 路径",
    "svpnReplaceKeepingAlpha(sysDimmed ?: sysTint, hue)" in _g17)
chk("S5f 不写死 alpha 常量",
    re.search(r"colorWithRed:[^\]]*alpha:0\.\d", nc) is None)

# ============================================================================
print("\n[S6] 还原：断开后写回系统原色")
# ============================================================================
_led = slice_between(src, "// 还原：断开 VPN 后", "// =====")
_led_nc = strip_code(_led)
chk("S6a 账本是「一视图一字典」的 NSMapTable（不是两张平行表）",
    nc.count("static NSMapTable *gSVPNLedger = nil;") == 1
    and "static NSMapTable *gSVPNOriginals" not in nc
    and "static NSMapTable *gSVPNPainted" not in nc,
    "gSVPNLedger=%d 旧表=%d" % (nc.count("gSVPNLedger = nil;"),
                                nc.count("gSVPNOriginals") + nc.count("gSVPNPainted")))
chk("S6b key 用 weak + 指针同一性",
    "NSPointerFunctionsWeakMemory | NSPointerFunctionsObjectPointerPersonality" in nc)
_rec = body_of("static void SVPNRecord(id view, NSString *slot", nc)
chk("S6c 残留识别：等于上次染的色就拒绝当系统色记账",
    "if (lastPainted && [lastPainted isEqual:systemColor])" in _rec,
    "记账函数体 %d 字节" % len(_rec))
chk("S6d 记账时把外观存进该视图自己的字典",
    '[d setObject:@(style) forKey:SVPN_STYLE_KEY];' in _rec)
_now = body_of("static void SVPNRestoreNow(void)", nc)
chk("S6e 还原前重新确认当前不该染（断开又秒连不擦色）",
    "if (SVPNDecision()) {" in _now and _now.find("if (SVPNDecision()) {") < _now.find("SVPNRestoreSlot"))
chk("S6f 还原有独立闸门（启动窗口内不介入视图）",
    "if (!gSVPNReady) {" in _now)
# ★ 只判「收集循环在还原循环之前」是不够的：往收集循环**体内**加一句
#   removeObjectForKey，顺序关系一点没变（反向验证 B28 抓到过）。
#   必须把收集循环的循环体取出来，断言里面根本不改表。
_collect = body_of("for (id v in gSVPNLedger) {", _now)
chk("S6g 先收集再处理（边遍历边改 NSMapTable 是未定义行为）",
    _now.find("for (id v in gSVPNLedger)") < _now.find("for (id view in views)")
    and _now.count("for (id v in gSVPNLedger)") == 1
    and bool(_collect)
    and "removeObjectForKey" not in _collect
    and "removeAllObjects" not in _collect
    and "setObject" not in _collect,
    "收集循环体 %d 字节；顺序 %d<%d" % (
        len(_collect), _now.find("for (id v in gSVPNLedger)"),
        _now.find("for (id view in views)")))
chk("S6h 逐视图外观比对，不符只跳过该视图（不清表）",
    "if (styleNow >= 0 && rec && [rec intValue] != styleNow) {" in _now
    and "removeAllObjects" not in _now)
chk("S6i 还原侧绕过缓存拿新鲜外观",
    "SVPNUserInterfaceStyle(YES)" in _now)
chk("S6j 处理完销账（账本不跨周期复用）",
    _now.count("[gSVPNLedger removeObjectForKey:view];") == 1)
# ★ 这条判的是三个 @"字面量" 在不在 → 必须用保留字符串的文本（ns）。
#   用 nc（strip_code 抹掉字符串内容）会永远 FAIL，那就是判据写错方向。
_now_ns = body_of("static void SVPNRestoreNow(void)", ns)
chk("S6k 只碰 tc/ac/ic 三个槽位（不含会传播的 tint）",
    '@[@"tc", @"setTextColor:", @"textColor"]' in _now_ns
    and '@[@"ac", @"setActiveColor:", @"activeColor"]' in _now_ns
    and '@[@"ic", @"setInactiveColor:", @"inactiveColor"]' in _now_ns
    and '@"tint"' not in _now_ns
    and "setTintColor:" not in _now,
    "还原体(ns) %d 字节 / plan 命中 %d 条" % (
        len(_now_ns),
        sum(1 for _s in ('@[@"tc"', '@[@"ac"', '@[@"ic"') if _s in _now_ns)))
chk("S6l 还原只写色不重绘",
    "setNeedsDisplay" not in _now and "setNeedsLayout" not in _now)
_slot = body_of("static void SVPNRestoreSlot(id view, NSString *setter", nc)
chk("S6m 写入口只写实色（非 RGB 不写）",
    "if (![color getRed:&r green:&g blue:&b alpha:&a]) {" in _slot)
chk("S6n 写入口不写透明色",
    "if (a <= 0.001) {" in _slot)
chk("S6o 写入口幂等（已相同不写）",
    "[cur isEqual:color]" in _slot)
chk("S6p IMP 取返回值用 __unsafe_unretained（否则 ARC 误 release）",
    "__unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);" in _slot)
chk("S6q 每条 slot 还原前判 respondsToSelector",
    "![view respondsToSelector:sel]" in _slot)
chk("S6r 只在 1→0 时触发还原（连上不擦色）",
    "if (prev == 1 && !now) {" in body_of("static void SVPNNoteTransition(void)", nc))

# ============================================================================
print("\n[S7] 崩溃防御：宿主进程绝不能被拖死")
# ============================================================================
# 逐个 hook 方法体检查：涉及私有 API 的都必须包 @try。
# ★ 用 list 不用 dict：applyStyleAttributes: 在 StringView 与 WifiSignalView
#   各出现一次，用 dict 会被同名 key 覆盖成一条（曾因此漏检）。
# ★ %new 方法是我们自己新增的，不该有 %orig → 必须排除，否则判据永远红。
_new_sigs = set()
for _m in re.finditer(r"%new\s*\n\s*- \(", nc):
    _seg = nc[_m.end():]
    _nm = re.match(r"[^)]*\)\s*(\w+)", _seg)
    if _nm:
        _new_sigs.add(_nm.group(1))
_hook_list = []
for m in re.finditer(r"^- \(([^)]*)\)(\w+(?:\s*:\s*\([^)]*\)\s*\w+)*)\s*\{", nc, re.M):
    sig = m.group(2)
    b = body_of(m.group(0).split("{")[0] + "{", nc)
    if b and sig not in _new_sigs:
        _hook_list.append((sig, b))
_hook_sigs = [s for s, _ in _hook_list]
chk("S7a 至少 hook 了 6 个方法（含同名不同类）",
    len(_hook_list) >= 6,
    "实际 " + str(len(_hook_list)) + " 个: " + ", ".join(_hook_sigs[:10])
    + "（%new 已排除: " + (", ".join(sorted(_new_sigs)) or "无") + "）")
# 每个 %orig 调用必须存在（漏了就等于把系统行为抹掉）
# ★ detail 里不能裸写 %orig：Python 会把它当 %o 格式符 → TypeError。
#   这一段全部用字符串拼接，不用 % 格式化。
_orig_bad = [s for s, b in _hook_list if "%orig" not in b]
chk("S7b 每个 hook 都有 %orig（漏了会抹掉系统行为）",
    not _orig_bad,
    ("缺 %orig 的: " + ", ".join(_orig_bad)) if _orig_bad
    else ("全部 " + str(len(_hook_list)) + " 个都有"))
_nc_try, _nc_catch = nc.count("@try"), nc.count("@catch")
chk("S7c @try/@catch 配对", _nc_try == _nc_catch,
    "try=" + str(_nc_try) + " catch=" + str(_nc_catch))
chk("S7d 染色路径至少 6 处 @try 包裹", _nc_try >= 6, "实际 " + str(_nc_try))
# 反向：不许出现裸的 objc_msgSend 强转（类型不安全，参数错就崩）
chk("S7e 不用裸 objc_msgSend 函数指针强转",
    "objc_msgSend" not in nc, "出现 " + str(nc.count("objc_msgSend")) + " 次")
# 反向：不许调系统的私有重绘入口（主动刷新宿主是风险最高的一类）
chk("S7f 不主动触发宿主状态栏重绘",
    "setNeedsStatusBarAppearanceUpdate" not in nc and "forceUpdateData" not in nc)
# 反向：不许 killall / sbreload 之类（装包红线，代码里也不该有）
# ★ 必须用 ns（剥注释、**保留字符串**）：这类词通常出现在字符串里
#   （system("killall SpringBoard")），用 nc 会把字符串内容抹掉 → 永远为真。
#   反向验证 B46 抓到过：注入 killall 后判据仍然绿。
chk("S7g 代码里没有 respring / killall",
    "sbreload" not in ns and "killall" not in ns.lower()
    and "respring" not in ns.lower(),
    "sbreload=%d killall=%d respring=%d" % (
        ns.count("sbreload"), ns.lower().count("killall"), ns.lower().count("respring")))

# ============================================================================
print("\n[S8] 颜色取值的边界")
# ============================================================================
chk("S8a 替换色有兜底（颜色为 nil 会把图标色清空）",
    "_lightReplacementColor = l ?:" in nc and "_darkReplacementColor = d ?:" in nc)
chk("S8b 取系统色失败时不返回 nil（按浅色处理而不是放弃）",
    "return dark ? _lightReplacementColor : _darkReplacementColor;" in nc)
chk("S8c 换色前判空入参",
    "if (!systemColor) {" in nc)

# ============================================================================
print("\n[S9] 版本号四处一致")
# ============================================================================
_ver_mk = re.search(r"PACKAGE_VERSION\s*:?=\s*([\w.\-]+)", io.open(os.path.join(_REPO, "Makefile"), encoding="utf-8").read())
_ver_yml = re.search(r"VERSION:\s*([\w.\-]+)", io.open(YML, encoding="utf-8").read())
_ver_sh = re.search(r"SingleVPN\s+([\w.\-]+)\s+已安装", io.open(MK, encoding="utf-8").read())
_vers = {
    "Makefile": _ver_mk.group(1) if _ver_mk else None,
    "build.yml": _ver_yml.group(1) if _ver_yml else None,
    "mkdeb.sh": _ver_sh.group(1) if _ver_sh else None,
}
chk("S9a 三处版本号一致且都能取到",
    len(set(_vers.values())) == 1 and None not in _vers.values(), str(_vers))

# ============================================================================
print("\n[S10] 装包红线（mkdeb.sh）")
# ============================================================================
_mk = io.open(MK, encoding="utf-8", newline="").read()
chk("S10a 没有 CRLF（Windows 换行会让 CI 的 set -e 报 invalid option）",
    b"\r\n" not in io.open(MK, "rb").read())
chk("S10b postinst 不执行 sbreload / killall / respring",
    "sbreload" not in _mk and "killall" not in _mk and "respring" not in _mk)
chk("S10c postinst 不调用 launchctl",
    "launchctl" not in _mk)
def strip_hash_comments(s):
    """抹掉 shell / Python 的 # 行注释（mkdeb.sh 是 sh + 内嵌 Python）。

    ★ 不剥的话，注释里写的「修法：tarfile.open(..., format=tarfile.GNU_FORMAT)」
      会被计进命中数 → 判据假绿（真实踩过：期望 2 实得 3）。
    """
    out = []
    for ln in s.split("\n"):
        i = ln.find("#")
        out.append(ln[:i] if i >= 0 else ln)
    return "\n".join(out)


_mk_code = strip_hash_comments(_mk)
chk("S10d data.tar 真的跳过 DEBIAN（rootless / 只读 → 整包装不上）",
    "dirs[:] = [d for d in dirs if d != 'DEBIAN']" in _mk_code,
    "命中 %d 次" % _mk_code.count("dirs[:] = [d for d in dirs if d != 'DEBIAN']"))
# ★ 构建期闸门：解包回验，确认没有 DEBIAN 条目。
#   光有「源码里写了 skip」不算证据，改实现的人可能把那行挪走。
chk("S10e 有构建期闸门解包回验 data.tar 不含 DEBIAN",
    "raise SystemExit('data.tar 含 DEBIAN 条目，装包会炸" in _mk_code)
chk("S10f tar 写模式显式指定 GNU_FORMAT（否则 PAX 头让 dpkg 装不上）",
    # ★ 必须锚「写模式那一行」本身。裸数文本里的 GNU_FORMAT 会命中注释
    #   与提示字符串（实测 3 处）→ 判据假绿。
    len([1 for ln in _mk_code.split("\n")
         if "tarfile.open(" in ln and "mode='w'" in ln
         and "format=tarfile.GNU_FORMAT" in ln]) == 2,
    "写模式带 GNU_FORMAT 的行数 %d（期望 2）" % len(
        [1 for ln in _mk_code.split("\n")
         if "tarfile.open(" in ln and "mode='w'" in ln
         and "format=tarfile.GNU_FORMAT" in ln]))
# ★ 不能只数「函数被定义 + 被调用」：把函数体内的 raise 删掉，
#   调用次数一点没变，判据依然绿（反向验证 B56 抓到过）。
#   必须取函数体，断言它在发现 PAX 头时真的抛错。
_pax = py_body_of("def _check_no_pax_headers(raw, label):", _mk_code)
chk("S10g PAX 扩展头运行期检查真的会抛错",
    bool(_pax) and "raise SystemExit" in _pax
    and "if bad:" in _pax
    and _mk_code.count("_check_no_pax_headers(") >= 3,
    "函数体 %d 字节；定义+调用 %d 处" % (
        len(_pax), _mk_code.count("_check_no_pax_headers(")))

print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")