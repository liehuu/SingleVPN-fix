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
    # ★ 造色已收进 SVPNColorRGBA，所以这里判「取原色 alpha」+「把 sa 原样交出去」。
    #   仍判死 'alpha:&sa'：这是唯一一次读 alpha，没有第二个来源。
    "getRed:&sr green:&sg blue:&sb alpha:&sa" in _kp
    and "SVPNColorRGBA(r, g, b, sa)" in _kp,
    "函数体 %d 字节" % len(_kp))
chk("S5d setInactiveColor 走保 alpha 路径",
    "svpnReplaceKeepingAlpha(inactiveColor," in _g17)
chk("S5e imageDimmedTintColor 走保 alpha 路径",
    "svpnReplaceKeepingAlpha(sysDimmed, hue)" in _g17)
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
# ★★ 2.2-4：S6d/S6h/S6i 原来是「记账时存外观 + 还原时比对外观」，
#    那条设计已删除（外观是间接代理指标，取错就整视图不还原）。
#    判据方向随之反转：现在必须**没有**外观记账与外观比对。
chk("S6d 不再按界面外观记账（撤销永远比保留染色安全）",
    "SVPN_STYLE_KEY" not in _rec and "SVPNUserInterfaceStyle" not in _rec,
    "记账函数体 %d 字节" % len(_rec))
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
# ★★ 2.2-4：原 S6h 要求「逐视图外观比对，不符只跳过该视图」。
#    那条正是「断开不褪色」的根因之一 —— 它把整个视图放过去了。
#    现在改成逐槽位判「当前值还是不是我染的」，方向反转。
chk("S6h ★ 不用外观比对当还原门槛（不符就跳过 = 留着绿色不撤）",
    "int styleNow" not in _now
    and "[rec intValue] != styleNow" not in _now
    and "SVPNUserInterfaceStyle" not in _now,
    "还原体 %d 字节" % len(_now))
# ★ 与 S6h 分开：S6h 只判「外观门槛已去掉」，
#   S6i 判「有没有换成直接判据」。两条判据各管一件事，
#   否则一条注入会同时点亮两条，反向用例的 expect 就没法精确定位。
chk("S6i ★ 逐槽位判「当前值是否仍是我染的」（直接判据，替代外观比对）",
    "UIColor *cur = SVPNReadColor(view, getter);" in _now
    and "[cur isEqual:painted]" in _now)
chk("S6j 处理完销账（账本不跨周期复用）",
    _now.count("[gSVPNLedger removeObjectForKey:view];") == 1)
# ★ 这条判的是 @"字面量" 在不在 → 必须用保留字符串的文本（ns）。
#   用 nc（strip_code 抹掉字符串内容）会永远 FAIL，那就是判据写错方向。
_now_ns = body_of("static void SVPNRestoreNow(void)", ns)
# ★★ 2.2-4：原来判「不含会传播的 tint」。实测否决了这条推断 ——
#    2.1-10 用 setTintColor: 还原 tint 且有用户实测背书；而删掉它
#    等于蜂窝/WiFi 图标永远没有还原入口（「断开不褪色」的第三处缺口）。
#    现在改为要求 tint **必须在** plan 里，且带 @NO（不做当前值校验）。
# ★ 2.2-4：条目多了第 4 个元素（是否做「当前值校验」的开关），
#   所以这里判「槽位 + setter」前缀，不判整行相等 —— 整行相等是脆指标，
#   加一个字段就红，红了人就去改判据，闸门慢慢退化成摆设。
chk("S6k ★ 还原覆盖 tc/ac/ic/tint 四路（tint 缺了 icon 就不褪色）",
    all(s in _now_ns for s in ('@[@"tc", @"setTextColor:"',
                               '@[@"ac", @"setActiveColor:"',
                               '@[@"ic", @"setInactiveColor:"',
                               '@[@"tint", @"setTintColor:"')),
    "还原体(ns) %d 字节 / plan 命中 %d 条" % (
        len(_now_ns),
        sum(1 for _s in ('@[@"tc"', '@[@"ac"', '@[@"ic"', '@[@"tint"') if _s in _now_ns)))
# ★★ 2.2-4：S6l 原来是「还原只写色不重绘」—— 这条判据本身是错的，
#    它正是「断开不褪色」这个 bug 没被拦下的原因。
#    setActiveColor: 是属性 setter，只存 ivar 不标脏；还原在系统链路
#    之外执行，后面没有系统重绘来接住它 → 值变了屏幕不变。
#    方向反转：还原写了色就**必须**标脏重绘。
#
# ★ 而且不能只判「函数体里出现过 setNeedsDisplay」——
#   往它外面套一个 `if (NO) { ... }` 照样命中，判据就成了摆设
#   （反向验证 B68 抓到过一次）。要判：
#     · 它必须挂在 didWrite 这个条件里（确实写了色才重绘）
#     · 还原路径里不许出现恒假守卫
_reflow_ok = (
    "if (didWrite) {" in _now
    and "setNeedsDisplay" in _now
    and not re.search(r"if \(NO\) \{\s*\n\s*@try", _now)
)
chk("S6l ★ 还原写了色必须标脏重绘（属性 setter 不触发绘制）",
    _reflow_ok,
    "还原体 %d 字节 / didWrite=%d" % (len(_now), _now.count("if (didWrite) {")))
_slot = body_of("static BOOL SVPNRestoreSlot(id view, NSString *setter", nc)
_read = body_of("static UIColor *SVPNReadColor(id view, NSString *getter)", nc)
chk("S6m 写入口只写实色（非 RGB 不写）",
    "if (![color getRed:&r green:&g blue:&b alpha:&a]) {" in _slot)
chk("S6n 写入口不写透明色",
    "if (a <= 0.001) {" in _slot)
# ★ 幂等判断 2.2-4 起挪到「读值」那一侧，锚点跟着走
chk("S6o 写入口幂等（已相同不写）",
    "if (cur && [cur isEqual:color]) {" in _slot and "return NO;" in _slot)
chk("S6p IMP 取返回值用 __unsafe_unretained（否则 ARC 误 release）",
    "__unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);" in _read,
    "读值函数体 %d 字节" % len(_read))
chk("S6q 每条 slot 还原前判 respondsToSelector",
    "![view respondsToSelector:sel]" in _slot)
chk("S6q2 读值前也判 respondsToSelector（否则 unrecognized selector）",
    "![view respondsToSelector:gsel]" in _read,
    "读值函数体 %d 字节" % len(_read))
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

# ============================================================================
print("\n[S11] 结构性不变量：坏色没有产生的机会（2.2-2 根本修复）")
# ============================================================================
# 为什么这一组比 S5 更重要：
#   S5 判的是「这一版那几行改对了」。下一版有人再写一处 [UIColor colorWithRed:...
#   alpha:0.05]，S5 全绿但图标又没了。所以要把「能不能造出看不见的色」变成
#   **结构问题**：
#     ① 全工程只有一处造色（SVPNColorRGBA）；
#     ② 它的 alpha 只有两个来源，一个是常量 1.0，一个是系统原色的 alpha；
#     ③ 所有染色调用点都必须经过 svpnReplaceKeepingAlpha（不许直接塞纯色相）。
#   这样一来「压缩 alpha」这件事在代码结构里根本没有位置。

# ① 唯一造色入口。判「UIColor colorWithRed:」在非注释代码里只出现一次。
_make = [ln for ln, t in enumerate(nc.split("\n"), 1) if "UIColor colorWithRed:" in t]
chk("S11a 全工程只有一处造色（SVPNColorRGBA）",
    len(_make) == 1,
    "命中 %d 处，行号 %s" % (len(_make), _make))
_rgba = body_of("static UIColor *SVPNColorRGBA(CGFloat r, CGFloat g, CGFloat b, CGFloat a)", nc)
# ★ 判「函数体不许对 a 重新赋值」，而不是判「有没有出现 alpha:1」。
#   后者太弱：写一句 `a = 1.0;` 就绕过去了（反向验证 B59 抓到过）。
_reassign_a = re.search(r"(?<![\w.>])a\s*=[^=]", _rgba) is not None
chk("S11b 造色入口不篡改 alpha 参数（收到什么就用什么）",
    bool(_rgba) and "alpha:a" in _rgba and not _reassign_a,
    "函数体 %d 字节 / 重新赋值 a: %s" % (len(_rgba), _reassign_a))

# ② alpha 的两个合法来源：常量 1.0（配置兜底）、系统原色 sa（换色）。
_calls = [ln.strip() for ln in nc.split("\n") if "SVPNColorRGBA(" in ln
          and "static UIColor *SVPNColorRGBA" not in ln]
_bad_alpha = [c for c in _calls if ("SVPNColorRGBA(r, g, b, sa)" not in c
                                    and ", 1.0)" not in c)]
chk("S11c alpha 只有两个来源：系统原色的 sa / 常量 1.0",
    bool(_calls) and not _bad_alpha,
    "调用 %d 处，可疑 %d 处: %s" % (len(_calls), len(_bad_alpha), _bad_alpha[:2]))

# ③ 所有染色点都保 alpha。判法不是「不许用 svpnReplacementFor」——
#    它本来就只负责挑色相，只有 3 个参数、没有 alpha 概念。
#    真正要判的是：**色相变量有没有被直接送进 UI 槽位**。
#    直接塞进去 = 用了色相自带的 alpha（=1），绕过系统的 alpha → 就是原来的 bug。
# ★ 组的结束标记 "%end // SingleVPN_17" 是**注释**，在 nc 里已被抹成空格，
#   所以边界要用原文 src 定位。两个 strip 函数都是逐字符等长替换，
#   索引可以通用。
_g17n = nc[src.index("%group SingleVPN_17"):src.index("%end // SingleVPN_17")]
_direct = []
for _m in re.finditer(r"UIColor \*(\w+)\s*=\s*svpnReplacementFor\(", _g17n):
    _var = _m.group(1)
    # 该变量在 17 组里的每一次使用，都必须在 svpnReplaceKeepingAlpha(x, var) 里
    for _u in re.finditer(r"\b" + re.escape(_var) + r"\b", _g17n):
        _ctx = _g17n[max(0, _u.start() - 80):_u.end() + 30]
        _is_def = "UIColor *" + _var in _ctx
        if _is_def:
            continue
        if not re.search(r"svpnReplaceKeepingAlpha\([^;]*\b" + re.escape(_var) + r"\b", _ctx):
            _direct.append(_var + " @ " + _ctx.replace("\n", " ").strip()[:70])
            break
chk("S11d 色相变量从不直接进 UI 槽位（必须先包保 alpha）",
    not _direct,
    "裸用 %d 处: %s" % (len(_direct), _direct[:2]))
chk("S11e 五个染色点全部经过保 alpha 包装",
    _g17n.count("svpnReplaceKeepingAlpha(") >= 5,
    "17 组内命中 %d 处" % _g17n.count("svpnReplaceKeepingAlpha("))

# ④ 不许有「事后补救」的机制。这类机制的存在本身就说明允许坏色先进来。
#    反面词用精确 selector/符号，不用泛词（避免误伤注释与正常代码）。
chk("S11f 没有事后扫描修复机制（坏色应从源头消灭）",
    "SVPNHeal" not in nc and "SVPNMarkSeen" not in nc and "gSVPNSeen" not in nc,
    "残留 %d 处" % (nc.count("SVPNHeal") + nc.count("SVPNMarkSeen") + nc.count("gSVPNSeen")))
chk("S11g 不做定时轮询（不长期和系统抢同一属性）",
    "DISPATCH_SOURCE_TYPE_TIMER" not in nc and "dispatch_source_create" not in nc)
# ★★ S11h 原来判「全工程不许出现 setNeedsDisplay / setNeedsLayout」——
#    这条判据本身是错的，而它正是「断开不褪色」这个 bug 没被拦下的原因：
#    它把「染色时不要和系统抢绘制节奏」和「还原后必须自己标脏重绘」
#    当成了同一件事，一刀切全禁。
#    正确的不变量是**按位置区分**：
#      · 染色函数体内（applyStyleAttributes: / setXxxColor: / setText:）
#        不许出现重绘 —— 那时系统正在重画，插队重绘就是抢节奏。
#      · 还原函数体（SVPNRestoreNow）**必须**出现重绘 —— 那里在系统
#        链路之外写属性，不标脏就永远不会生效。
_g17_body = nc[src.index("%group SingleVPN_17"):src.index("%end // SingleVPN_17")]
_stain_funcs = []
for _sig in ("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs",
             "- (void)setActiveColor:(UIColor *)activeColor",
             "- (void)setInactiveColor:(UIColor *)inactiveColor",
             "- (void)setText:(NSString *)text"):
    for _m in re.finditer(re.escape(_sig), _g17_body):
        _b = body_of(_sig, _g17_body[_m.start():])
        if _b:
            _stain_funcs.append(_b)
_stain_reflow = [b for b in _stain_funcs if "setNeedsDisplay" in b or "setNeedsLayout" in b]
chk("S11h 染色函数体内不主动重绘（只在系统自己的绘制链路内换色）",
    len(_stain_funcs) >= 4 and not _stain_reflow,
    "染色函数 %d 个，其中含重绘 %d 个" % (len(_stain_funcs), len(_stain_reflow)))

# ★ S11j 已并入 S6l（两条判同一件事会让一条注入点亮两条，
#   反向用例就没办法精确定位到「是哪一条拦下的」）。

# ⑤ ★★ 绝不凭空造值：系统没给的槽位就别设。
#    这一条是本轮最重要的一条自律，比「保 alpha」更上位：
#    原版两个 bug（回填空壳 entry、写死 alpha 0.2）本质都是
#    「在系统没要求的地方替它做决定」。
# ★ 定位必须按**类**切段：applyStyleAttributes: 在 StringView 和
#   WifiSignalView 各有一个，body_of() 会命中前面那个（StringView），
#   于是判的根本不是这段代码 —— 判据锚错地方等于没判。
_wifi_seg = _g17[_g17.index("%hook STUIStatusBarWifiSignalView"):]
_wifi_seg = _wifi_seg[:_wifi_seg.index("%end")]
_wifi_style = body_of("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs", _wifi_seg)
chk("S11i tint / dimmed 只在系统给了值时才设（不拿别的槽位顶替）",
    # 必须是 if (sysTint) / if (sysDimmed) 守卫，而不是 sysTint ?: sysText 这类兜底
    bool(_wifi_style)
    and "if (sysTint) {" in _wifi_style
    and "if (sysDimmed) {" in _wifi_style
    and "sysTint ?: sysText" not in _wifi_style
    and "sysDimmed ?: tintAnchor" not in _wifi_style
    and "sysDimmed ?: sysTint" not in _wifi_style,
    "wifi style 体 %d 字节" % len(_wifi_style))

# ⑥ ★★ 褪色链路：断开后必须真的能把四类槽位都撤回去（2.2-4）。
#    这是「断开不褪色」之后的回归防线。三个必要条件，缺一条就不褪色：
#      (a) 还原路径必须覆盖 tint —— 蜂窝/WiFi 图标吃 imageTintColor，
#          不记账就没有还原入口。
#      (b) 染色时必须记 tint 这一笔，否则账本里根本没有它。
#      (c) 还原后必须标脏重绘（已由 S11j 判）。
#
# ★★ 这里判的是 @"字面量" 在不在 → **必须**用保留字符串的 ns，
#    用 nc（strip_code 会把字符串内容抹成空格）会永远 FAIL。
#    本轮就踩了这个坑：_restore_body 取自 nc，'"tint"' 根本不存在，
#    于是 S11k/S11l/S11m 三条一起假红。判据方向没错，取错了文本。
_restore_ns = body_of("static void SVPNRestoreNow(void)", ns)
_plan_block = ""
if "NSArray *plan = @[" in _restore_ns:
    _plan_block = _restore_ns[_restore_ns.index("NSArray *plan = @["):]
    if "];" in _plan_block:
        _plan_block = _plan_block[:_plan_block.index("];")]
chk("S11k 还原路径覆盖 tint 槽位（icon 吃 imageTintColor，没有它就不褪色）",
    '@"tint"' in _plan_block and "setTintColor:" in _plan_block,
    "plan 条目 %d 个 / plan 块 %d 字节" % (_plan_block.count('@["'), len(_plan_block)))

# ★ 同理：这条要判 @"tint" 这个字面量，也得用 ns。
# ★★ 段边界必须用**原文 src** 取下标：`%end // SingleVPN_17` 是注释，
#    在 ns 里已被抹成空格，直接 index 会 ValueError。
#    strip_code / strip_comments_only 都是逐字符**等长**替换，
#    所以 src 上的下标可以直接用来切 ns（这是它们的隐含契约）。
_a, _b = src.index("%group SingleVPN_17"), src.index("%end // SingleVPN_17")
_wifi_seg_ns = ns[_a:_b]
_wifi_seg_ns = _wifi_seg_ns[_wifi_seg_ns.index("%hook STUIStatusBarWifiSignalView"):]
_wifi_seg_ns = _wifi_seg_ns[:_wifi_seg_ns.index("%end")]
_wifi_style_ns = body_of("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs", _wifi_seg_ns)
chk("S11l 染色时确实记了 tint 这一笔（否则账本里没有它）",
    'SVPNRecord(self, @"tint"' in _wifi_style_ns,
    "wifi style 体(ns) %d 字节" % len(_wifi_style_ns))

# ★ tint 槽位不能做「当前值是否仍是我染的」校验：
#   视图 tintColor 多是继承值，永远不等于 painted → 会恒跳过、永远不还原。
_tint_pair = [ln for ln in _plan_block.split("\n") if '@"tint"' in ln]
chk("S11m tint 槽位不做「当前值比对」校验（继承值永不匹配 → 会恒跳过）",
    bool(_tint_pair) and "@NO" in _tint_pair[0],
    (_tint_pair[0].strip() if _tint_pair else "找不到 tint 条目"))

# ⑦ ★ 不允许再用「界面外观」当作「要不要还原」的门槛。
#   这是间接代理指标：桌面状态栏 trait 与 UIApplication 未必一致，
#   取错了就把整个视图放过去 → 不褪色。撤销永远比保留染色更安全。
chk("S11n 不用界面外观当还原门槛（间接代理指标，取错就整视图不还原）",
    "SVPNUserInterfaceStyle" not in nc and "SVPN_STYLE_KEY" not in nc,
    "残留 %d 处" % (nc.count("SVPNUserInterfaceStyle") + nc.count("SVPN_STYLE_KEY")))

# ============================================================================
print("\n[S12] 诊断层（2.2-5：只在异常时留证据，不改染色行为）")
# ============================================================================
# ★★ 为什么这一组必须存在：
#    2.2-5 新增约 200 行诊断代码，而 [S11] 那 74 项一条都没碰到它 ——
#    全绿但新代码零覆盖，就是「装饰性通过」（本项目的头号自欺形态）。
#    这一组按两个问题来写：
#      (a) 诊断会不会自己变成新 bug？→ 绝不能崩宿主、绝不能拖慢热路径
#      (b) 诊断能不能真的回答问题？  → 记录点必须在判断之前、在闸门外
#    这两类里任何一条不成立，这版诊断包就白跑了。

# ① 环形缓冲的结构必须成立，且尺寸是「够看完一次复现」的量级。
#    太小会在复现前就被覆盖掉（这就是最典型的白跑）。
def _define_int(name):
    _m = re.search(r"#define\s+" + re.escape(name) + r"\s+(\d+)", src)
    return int(_m.group(1)) if _m else None

_slots = _define_int("SVPN_DIAG_SLOTS")
chk("S12a 环形缓冲有静态数组 + 固定槽位数（够覆盖一次复现）",
    "static SVPNEvent gSVPNRing[SVPN_DIAG_SLOTS];" in nc
    and _slots is not None and 64 <= _slots <= 4096,
    "SVPN_DIAG_SLOTS=%s" % _slots)

# ② 序号与计数必须真的在推进，否则导出的会是「永远是第 0 条」。
_note_body = body_of("static inline void SVPNNote(", nc)
chk("S12b SVPNNote 推进序号与计数（不推进则导出恒为旧数据）",
    bool(_note_body)
    and "gSVPNSeq = e->seq;" in _note_body
    and "gSVPNRingCount = gSVPNRingCount + 1;" in _note_body,
    "体 %d 字节" % len(_note_body))

# ③ ★★ 热路径红线：SVPNNote 内不许出现任何分配 / IO / 锁。
#    这里判的是**函数体**，不是文件里有没有这些词 —— 导出函数里必然有
#    open/write/snprintf，用全文判会把这函数判红（判据锚错地方）。
_ring_forbidden = [t for t in ("alloc", "NSString", "NSLog", "printf", "open(",
                               "write(", "dispatch_", "pthread_mutex", "@synchronized",
                               "os_unfair", "NSFileManager")
                   if t in _note_body]
chk("S12c SVPNNote 体内无分配 / 无 IO / 无锁（热路径必须极便宜）",
    bool(_note_body) and not _ring_forbidden,
    "命中 %s" % _ring_forbidden)

# ④ ★★ 诊断层的最高红线：绝不崩宿主。
#    落盘要真做 IO，必须被 @try 包住 —— 但**不能**因此把整条热路径包进 @try
#    （每条钩子都建 setjmp 的成本不可忽略）。所以要判两件事：
#      先判标志（便宜路径直接 return） → 再 @try 包住真正的 IO。
_maybe_body = body_of("static inline void SVPNMaybeExport(", nc)
_maybe_head_guard = re.search(r"if\s*\(\s*!\s*gSVPNExportRequest\s*\)\s*\{?\s*return", _maybe_body) is not None
_maybe_tries_export = re.search(r"@try\s*\{\s*SVPNExportDiag\(\);", _maybe_body) is not None
chk("S12d 落盘被 @try 包住，且标志判断在 @try 之前（便宜路径不建 setjmp）",
    bool(_maybe_body) and _maybe_head_guard and _maybe_tries_export,
    "体 %d 字节 / 前置判标志=%s / @try包住落盘=%s"
    % (len(_maybe_body), _maybe_head_guard, _maybe_tries_export))

# ⑤ 导出只在「被显式请求」时发生 —— 不许自动落盘、不许定时落盘。
#    自动落盘会在崩溃/卡顿时留下半截文件，而且时机不可控。
_export_callers = [m.start() for m in re.finditer(r"SVPNExportDiag\(\)", nc)]
# 扣除它自己的定义处
_def_at = nc.find("static void SVPNExportDiag(void) {")
_callers = [i for i in _export_callers if i > _def_at + 10]
_export_ok = True
for _i in _callers:
    # 每个调用点必须落在 SVPNMaybeExport 体内
    _in_maybe = _maybe_body and (_i >= nc.find("static inline void SVPNMaybeExport(") and
                                 _i <= nc.find("static inline void SVPNMaybeExport(") + len(_maybe_body))
    if not _in_maybe:
        _export_ok = False
chk("S12e 落盘只由 SVPNMaybeExport 触发（无第二条写入路径）",
    _export_ok and len(_callers) == 1,
    "调用点数 %d（应为 1）" % len(_callers))

# ★ 这里原本写的「S12f 不做定时落盘」已删除：它和 S11g 用同一个条件
#   （dispatch_source_create 在不在）判同一件事，一次注入会同时点亮两条，
#   反向用例就没办法断言「是哪一条拦下的」。定时器这一条已由 S11g 覆盖。
#   换成另一个角度：导出路径必须是 /var/mobile 下的绝对路径 ——
#   否则用户在 Filza / 终端里找不到（rootless 下 / 只有只读挂载）。
_diag_path_m = re.search(r'#define\s+SVPN_DIAG_EXPORT_PATH\s+"([^"]+)"', src)
_diag_path = _diag_path_m.group(1) if _diag_path_m else ""
chk("S12f 导出路径在 /var/mobile 下且是绝对路径（用户能在 Filza 里取到）",
    _diag_path.startswith("/var/mobile/") and ".." not in _diag_path,
    "path=%s" % _diag_path)

# ★ 诊断层用到的每个 POSIX 接口，其头文件都必须**显式**导入。
#   这是典型的「本地看着对、CI 才报错」：UIKit 会传递包含一部分，
#   靠传递包含活着，SDK 一变就 unknown type / implicit declaration。
#   本机没有 Xcode，编译错只能等 CI，一次失败一次往返 —— 所以在这里拦。
_need_headers = {
    "snprintf": "stdio.h",
    "open(": "fcntl.h",
    "write(": "unistd.h",
    "close(": "unistd.h",
    "clock_gettime": "time.h",
    "dlopen": "dlfcn.h",
}
_hdr_missing = []
for _sym, _hdr in _need_headers.items():
    if _sym in nc and ("#import <%s>" % _hdr) not in nc and ("#include <%s>" % _hdr) not in nc:
        _hdr_missing.append("%s 需要 %s" % (_sym, _hdr))
chk("S12f2 诊断层用到的 POSIX 接口都有显式头文件（不靠传递包含）",
    not _hdr_missing, "缺失 %s" % _hdr_missing)
chk("S12g 导出请求标志只由通知回调置 1（不在热路径里置 1 → 否则每帧落盘）",
    # ★ 原来这条写的是「不许 dispatch_after 里调落盘」，但它和 S12e
    #   （唯一调用点）判的是同一件事：任何新增调用点都会先点亮 S12e。
    #   两条同时红就没法断言「是哪一条拦下的」。改成另一个真正独立的角度：
    #   标志位是谁置的。置 1 不等于调用，与 S12e 不重合。
    nc.count("gSVPNExportRequest = 1;") == 1
    and len(re.findall(r"gSVPNExportRequest\s*=\s*1;", nc)) == 1
    and body_of("static void SVPNRequestExport(", nc).count("gSVPNExportRequest = 1;") == 1,
    "置 1 处数 %d" % nc.count("gSVPNExportRequest = 1;"))

# ⑥ ★★ 诊断能不能回答问题，全看记录点在哪一行。
#    SVPNNote 必须在**染色判断之前** —— 只有在那之前，
#    「日志里没这条」才能解释成「方法没被调用」（视图没建）。
#    写在 if (SVPNDecision()) 里面，就只能在「我们决定染色」时才记，
#    而那时视图显然存在 —— 恰好把最想观察的那一半挖掉。
# ★ 这条不许只判「先后顺序」（在循环体内插一句非法调用顺序也没变）。
#   必须把方法体取出来，单独断言 SVPNNote 落在第一个 if 之前。
_view_hooks = {
    "StringView.applyStyleAttributes:": ("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs", "%hook STUIStatusBarStringView"),
    "WifiSignal.applyStyleAttributes:": ("- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs", "%hook STUIStatusBarWifiSignalView"),
    "WifiSignal.setActiveColor:": ("- (void)setActiveColor:(UIColor *)activeColor", "%hook STUIStatusBarWifiSignalView"),
    "WifiSignal.setInactiveColor:": ("- (void)setInactiveColor:(UIColor *)inactiveColor", "%hook STUIStatusBarWifiSignalView"),
    "StringView.setText:": ("- (void)setText:(NSString *)text", "%hook STUIStatusBarStringView"),
    "CellNetType.setText:prefix:...": ("- (void)setText:(NSString *)text prefixLength:(NSInteger)prefixLength", "%hook STUIStatusBarCellularNetworkTypeView"),
}
_note_before_decision_fails = []
for _lbl, (_sig, _hook) in _view_hooks.items():
    # 按类切段：同一个 selector 在多个类里都有，不切段会锚错（踩过）
    _seg = nc[nc.find(_hook):] if nc.find(_hook) >= 0 else ""
    if _seg:
        _nxt = _seg.find("%hook", 1)
        if _nxt > 0:
            _seg = _seg[:_nxt]
    _b = body_of(_sig, _seg)
    if not _b:
        _note_before_decision_fails.append(_lbl + "(取不到函数体)")
        continue
    _p_note = _b.find("SVPNNote(")
    _p_dec = _b.find("SVPNDecision()")
    _p_ready = _b.find("gSVPNReady")
    _first_guard = min([p for p in (_p_dec, _p_ready) if p >= 0], default=-1)
    if _p_note < 0:
        _note_before_decision_fails.append(_lbl + "(没有记录点)")
    elif _first_guard >= 0 and _p_note > _first_guard:
        _note_before_decision_fails.append(_lbl + "(记录点在第 %d 字符，晚于判断 %d)" % (_p_note, _first_guard))
chk("S12h 六个视图钩子的记录点全部在染色判断之前（否则看不到「没被调用」）",
    not _note_before_decision_fails,
    "问题项 %s" % _note_before_decision_fails)

# ⑦ ★★ 数据层记录必须在闸门**外面**。
#    gSVPNReady 的启动窗口正是最可疑的一段时间（respring 后图标空白），
#    把记录放进 if (gSVPNReady) 里，等于把要观察的那段挖掉。
_data_ok_fails = []
for _sig in ("- (void)_updateWithAggregatedData:(STStatusBarData *)data",
             "- (void)_updateWithData:(STStatusBarData *)data completionHandler:(id)handler"):
    _b = body_of(_sig, nc)
    if not _b:
        _data_ok_fails.append(_sig[:40] + "(取不到函数体)")
        continue
    _p_note = _b.find("SVPNNote(")
    _p_ready = _b.find("if (gSVPNReady)")
    if _p_note < 0:
        _data_ok_fails.append(_sig[:40] + "(没有记录点)")
    elif _p_ready >= 0 and _p_note > _p_ready:
        _data_ok_fails.append(_sig[:40] + "(记录点落在闸门内)")
chk("S12i 数据层记录点在 gSVPNReady 闸门之外（启动窗口才看得见）",
    not _data_ok_fails, "问题项 %s" % _data_ok_fails)

# ⑧ 事件枚举必须**每个都被真的用到**，且每个都有名字。
#    定义了不用 = 装饰性代码：日志里永远看不到那一类事件，
#    而人会以为「没出现 = 没发生」（本项目明确禁止的推论）。
_ev_names = re.findall(r"\b(SVPNEv\w+)\s*=", src)
_ev_unused = [n for n in _ev_names if nc.count(n + ",") + nc.count(n + ")") == 0]
_ev_noname = [n for n in _ev_names if ('case %s:' % n) not in nc]
chk("S12j 每个事件枚举都被真的记录、且都有可读名字",
    bool(_ev_names) and not _ev_unused and not _ev_noname,
    "枚举 %d 个 / 未使用 %s / 无名字 %s" % (len(_ev_names), _ev_unused, _ev_noname))

# ⑨ 版本串必须与三处版本号一致，否则导出的日志对不上「哪一版跑出来的」。
_ver_src = re.search(r'#define\s+SVPN_VERSION_STR\s+"([\w.\-]+)"', src)
chk("S12k 诊断版本串与 S9a 的版本号一致（日志要能对上产物）",
    _ver_src is not None and _ver_src.group(1) == _vers.get("Makefile"),
    "src=%s / Makefile=%s" % (_ver_src.group(1) if _ver_src else None, _vers.get("Makefile")))

# ⑩ ★★ Darwin 通知回调必须是五参数签名。
#    原来直接把 ReloadPrefs（无参函数）当回调传 —— arm64 上多余参数落寄存器
#    被忽略所以「看起来能用」，但那是 ABI 巧合。
#    判据：两个回调都不能是裸的 ReloadPrefs，且都必须有五个形参。
_cb_fails = []
for _cb in ("SVPNRequestExport", "SVPNPrefsChanged"):
    _m = re.search(r"static void " + _cb + r"\s*\(([^)]*)\)", nc)
    if not _m:
        _cb_fails.append(_cb + "(没有定义)")
        continue
    _params = [p for p in _m.group(1).split(",") if p.strip()]
    if len(_params) != 5:
        _cb_fails.append("%s(%d 个形参)" % (_cb, len(_params)))
chk("S12l 两个 Darwin 通知回调都是五参数签名（不再拿无参函数当回调）",
    not _cb_fails, "问题项 %s" % _cb_fails)
chk("S12m 不再把 ReloadPrefs 直接注册成通知回调",
    "CFNotificationCallback)ReloadPrefs" not in nc
    and "(CFNotificationCallback)SVPNPrefsChanged" in nc
    and "(CFNotificationCallback)SVPNRequestExport" in nc)

# ⑪ ★★ 设置页那一侧：action 名、通知名、文件路径三处必须与插件侧严格一致。
#    这三处任一处写岔，用户点了按钮却什么都没发生 —— 而且看起来像「插件没生效」，
#    会被误判成染色逻辑的问题，白跑一轮。
_prefs_m = os.path.join(_REPO, "SingleVPNPrefs", "SingleVPNRootListController.m")
_prefs_plist = os.path.join(_REPO, "SingleVPNPrefs", "Resources", "Root.plist")
_pm = io.open(_prefs_m, encoding="utf-8").read() if os.path.exists(_prefs_m) else ""
_pp = io.open(_prefs_plist, encoding="utf-8").read() if os.path.exists(_prefs_plist) else ""
_pm_nc = strip_code(_pm)

chk("S12n 设置页有导出 action，且 Root.plist 里的 action 名与之相符",
    "- (void)exportDiagnostics" in _pm_nc
    and "<string>exportDiagnostics</string>" in _pp,
    "m 定义=%s / plist 引用=%s"
    % ("- (void)exportDiagnostics" in _pm_nc, "<string>exportDiagnostics</string>" in _pp))

# 通知名两侧必须字面量完全相同（跨进程唯一契约）
_notif_plugin = re.search(r'CFSTR\("([^"]*exportdiag)"\)', src)
_notif_prefs = re.search(r'CFSTR\("([^"]*exportdiag)"\)', _pm)
chk("S12o 导出通知名在插件侧与设置页侧完全一致",
    _notif_plugin is not None and _notif_prefs is not None
    and _notif_plugin.group(1) == _notif_prefs.group(1),
    "插件侧=%s / 设置页=%s"
    % (_notif_plugin.group(1) if _notif_plugin else None,
       _notif_prefs.group(1) if _notif_prefs else None))

# 文件路径两侧必须一致 —— 用户按提示去找，找不到就会以为插件没生效。
# 所以要求两侧各有一个**完整的**路径字面量（不是拆成两段拼接）。
_path_plugin = re.search(r'#define\s+SVPN_DIAG_EXPORT_PATH\s+"([^"]+)"', src)
_path_prefs_lits = re.findall(r'@?"(/var/mobile/[^"]+)"', _pm)
chk("S12p 导出文件路径两侧是同一个完整字面量（不拆段拼接）",
    _path_plugin is not None
    and _path_plugin.group(1) in _path_prefs_lits,
    "插件侧=%s / 设置页侧=%s"
    % (_path_plugin.group(1) if _path_plugin else None, _path_prefs_lits[:3]))

# ⑫ ★ 设置页不能再用段落序号当判据（会随布局漂移）。
chk("S12q 设置页不用 section 序号当判据（插一段就漂移）",
    "indexPath.section ==" not in _pm_nc and "indexPath.section==" not in _pm_nc,
    "残留 %d 处" % (_pm_nc.count("indexPath.section")))

# ============================================================================
print("\n[S13] CI shell 判据与 Python 闸门的一致性")
# ============================================================================
# ★★ 为什么要有这一组（2.2-5 真实踩到）：
#    build.yml 里有一整套 shell 判据（Verify patch presence），它跑在
#    Python 闸门**之前**。本轮新增诊断层用了 SVPN_DIAG_SLOTS /
#    SVPN_DIAG_EXPORT_PATH，而那个黑名单里有一条裸子串判据 "SVPN_DIAG"
#    （历史遗留的旧诊断脚手架是个裸宏开关）—— 子串命中，CI 直接红，
#    报错信息还写着「推的不是 2.2-5」，方向完全误导。
#
#    这正是本项目反复记录的「禁项闸门不能用泛词」：判据锚的是子串，
#    不锚标识符边界，任何以它为前缀的新名字都会被误伤。
#    而这条判据在 Python 侧没有任何对应物，所以本地全绿、CI 才红。
#
#    这一组的职责：把「CI 会误伤新代码」这件事变成**本地就能发现**的。
_yml = io.open(YML, encoding="utf-8").read()

# ① CI 的黑名单条目不许用「可能成为别的标识符前缀」的裸子串。
#    判法：对每个黑名单条目，检查源码里所有以它开头的**更长标识符**。
#    （例外：条目本身已经足够长且唯一，如 gSVPNOriginals —— 用长度阈值区分。）
_blacklist = []
_m = re.search(r"for old in ([^;]+); do", _yml)
if _m:
    _blacklist = [t for t in _m.group(1).replace("\\", " ").split() if t and not t.startswith("$")]
_blacklist = [t.strip('"\'') for t in _blacklist]
_ambig = []
for _bad in _blacklist:
    if len(_bad) < 10:      # 短名字最容易成为别人的前缀
        _longer = sorted({t for t in re.findall(r"\b%s[A-Za-z0-9_]+\b" % re.escape(_bad), src)})
        if _longer:
            _ambig.append("%s → %s" % (_bad, _longer[:3]))
chk("S13a CI 旧结构黑名单里没有会误伤新标识符的短子串",
    not _ambig, "歧义 %s" % _ambig)

# ② 裸 SVPN_DIAG（旧脚手架）必须按完整标识符匹配，不能子串匹配。
#    旧写法是 `#define SVPN_DIAG 0` + `#if SVPN_DIAG`；新常量是 SVPN_DIAG_xxx。
#    判据：yml 里必须出现单词边界正则，且不能把 SVPN_DIAG 留在 for old 列表里。
_has_word_boundary = "SVPN_DIAG([^A-Za-z0-9_]" in _yml or "SVPN_DIAG\\b" in _yml
chk("S13b CI 对裸 SVPN_DIAG 用单词边界匹配（否则 SVPN_DIAG_SLOTS 会被误伤）",
    _has_word_boundary and "SVPN_DIAG " not in _m.group(1) if _m else False,
    "边界正则=%s / 仍在 for old 列表=%s"
    % (_has_word_boundary,
       ("SVPN_DIAG " in _m.group(1)) if _m else None))

# ③ ★★ 反向：源码里确实存在 SVPN_DIAG_xxx，而裸 SVPN_DIAG 不存在。
#    这条防的是「判据虽然改了，但改的方向不对」——比如把正则写成
#    只认带下划线的，那真出现旧脚手架时反而抓不到。
_diag_prefixed = len(re.findall(r"\bSVPN_DIAG_[A-Z_]+\b", src))
_bare_diag = len(re.findall(r"(^|[^A-Za-z0-9_])SVPN_DIAG([^A-Za-z0-9_]|$)", nc, re.M))
chk("S13c 源码有 SVPN_DIAG_ 前缀常量但没有裸 SVPN_DIAG（判据两侧都成立）",
    _diag_prefixed >= 2 and _bare_diag == 0,
    "带前缀 %d 个 / 裸的 %d 个" % (_diag_prefixed, _bare_diag))

# ④ ★★ 静态函数「先声明/定义、后使用」——C 的硬要求，违反就是编译错误。
#    这条是 2.2-5 真实踩到的：SVPNPrefsChanged 在 405 行调用 ReloadPrefs，
#    而 ReloadPrefs 定义在 428 行 → CI 报
#      error: use of undeclared identifier 'ReloadPrefs'
#    本机没有 Xcode，这类错误只能等 CI，一次失败一次往返。
#    所以在这里按**通用规则**判，不只判这一个符号：
#    对每个 `static ... name(` 形式的函数，若它的首次「调用」早于
#    「定义或前置声明」，就报出来。
_static_defs = {}
for _m in re.finditer(r"^static\s+(?:inline\s+)?[\w\s\*]+?\b([A-Za-z_]\w*)\s*\(", nc, re.M):
    _fn = _m.group(1)
    _static_defs.setdefault(_fn, _m.start())
# 前置声明形如 `static void Foo(void);`（花括号前就结束）
_forwards = {}
for _m in re.finditer(r"^static\s+(?:inline\s+)?[\w\s\*]+?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*;",
                      nc, re.M):
    _forwards.setdefault(_m.group(1), _m.start())
_order_viol = []
for _fn, _def_at in _static_defs.items():
    _avail = min([p for p in (_def_at, _forwards.get(_fn)) if p is not None])
    # 找第一次「调用」：函数名后面跟 ( 但该位置不是定义/声明处
    for _c in re.finditer(r"\b%s\s*\(" % re.escape(_fn), nc):
        if _c.start() == _def_at or _c.start() == _forwards.get(_fn):
            continue
        if _c.start() < _avail:
            # 排除它自己的定义/声明参数表内部的匹配
            _order_viol.append("%s 在 %d 处先用、%d 处才可见" % (_fn, _c.start(), _avail))
            break
chk("S13d 静态函数都是先声明/定义后使用（否则 CI 报 undeclared identifier）",
    not _order_viol, "违规 %s" % _order_viol)

print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")