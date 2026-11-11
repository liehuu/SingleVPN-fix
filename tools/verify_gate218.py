"""2.2-1 重写版的反向验证（零 SKIP）。

为什么要有这个文件：
    正向判据「代码里有 X」永远可以因为「判据写错方向」而假绿。
    唯一能证明判据有效的方法是**故意把缺陷注回去，看它是否被拦下，
    并且断言是「哪一条」判据拦下的**（只看退出码非 0 不够 —— 可能是
    括号不配平之类的连带失败，那这条判据本身仍是摆设）。

三段式：
    ① 断言注入真的发生了（内存值与落盘值都变了）
    ② 断言「是哪条判据拦下的」（在失败列表里点名）
    ③ finally 还原全部文件，跑完再复跑一次自检，确认干净

★ 就地改文件，不复制到 /tmp：Git Bash 下 /tmp 映射异常，
  曾出现 cp 失败但脚本继续跑、反向验证实际测的还是原文件的事故。
★ 覆盖两个文件：SingleVPN.x（代码闸门）与 devkit/mkdeb.sh（装包红线闸门）。
★ 每条用例的 expect 必须是 check_source.py 里真实存在的判据名子串，
  且必须唯一匹配 —— 名字打错就等于测了空气。
"""
import io
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(_REPO, "tools", "check_source.py")
PY = sys.executable

FILES = {
    "x": os.path.join(_REPO, "SingleVPN.x"),
    "mk": os.path.join(_REPO, "devkit", "mkdeb.sh"),
}
_orig = {k: io.open(p, encoding="utf-8", newline="").read() for k, p in FILES.items()}


def run_check():
    r = subprocess.run([PY, CHECK], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def one_replace(name, old, new, expect_gate, count=1, file="x"):
    """锚点必须恰好出现 count 次，全部替换。

    count 同时约束「锚点出现次数」和「替换次数」——
    写错一个空格就会在这里报出来，而不是让反向验证悄悄测错地方。
    """
    def fn(s):
        assert s.count(old) == count, "%s：锚点命中 %d 次（期望 %d）" % (
            name, s.count(old), count)
        return s.replace(old, new, count)
    return (name, file, fn, expect_gate)


def replace_first(name, old, new, expect_gate, file="x"):
    """锚点在文件里出现多次时，只注入第一处。

    两个数据层 hook 写法完全相同时没法只改一处 → 用它。
    「只改一处」正是要测的场景（漏一半）。
    """
    def fn(s):
        assert old in s, "%s：锚点未命中" % name
        return s.replace(old, new, 1)
    return (name, file, fn, expect_gate)


CASES = [
    # ---------------------------------------------------------------
    # S1 基本结构
    # ---------------------------------------------------------------
    one_replace("B01 后端退回 MobileSubstrate（零 Theos 编不过）",
                "%config(generator=internal)",
                "%config(generator=MobileSubstrate)",
                "S1b 用 internal 后端"),
    one_replace("B02 引入 HBLog.h（零 Theos 编不到）",
                "#import <dlfcn.h>",
                "#import <dlfcn.h>\n#import <HBLog.h>",
                "S1c 没有 import Theos 私有头"),
    one_replace("B03 iOS17 group 被改名（%init 对不上）",
                "%init(SingleVPN_17)",
                "%init(SingleVPN_17x)",
                "S1f 有 %ctor"),
    one_replace("B04 group 少了闭合标记",
                "%end // SingleVPN_16",
                "// SingleVPN_16 闭合被删",
                "S1e 两个 group 各自闭合"),

    # ---------------------------------------------------------------
    # S2 启动窗口闸门（2.1-8）
    # ---------------------------------------------------------------
    one_replace("B05 闸门变量丢 volatile",
                "static volatile int gSVPNReady = 0;",
                "static int gSVPNReady = 0;",
                "S2a 闸门变量 volatile"),
    one_replace("B06 启动窗口时长被注释掉",
                "#define SVPN_READY_DELAY_MS 500",
                "// #define SVPN_READY_DELAY_MS 500",
                "S2b 窗口时长有宏定义"),
    one_replace("B07 闸门开到后台队列（首帧仍会被介入）",
                "        dispatch_get_main_queue(), ^{\n            gSVPNReady = 1;",
                "        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_LOW, 0), ^{\n"
                "            gSVPNReady = 1;",
                "S2c 闸门开在主队列"),
    one_replace("B08 闸门被两处置 1（提前放行）",
                "            gSVPNReady = 1;\n        }\n    );",
                "            gSVPNReady = 1;\n            gSVPNReady = 1;\n        }\n    );",
                "S2d 闸门只在一处被置 1"),
    replace_first("B09 染色判定绕过统一入口（自己拼 Ready 条件）",
                  "    if (SVPNDecision() && inactiveColor) {",
                  "    if (gSVPNReady && (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled)"
                  " && inactiveColor) {",
                  "S2e 判定统一走 SVPNDecision"),

    # ---------------------------------------------------------------
    # S3 VPN 状态判定（2.1-9）
    # ---------------------------------------------------------------
    replace_first("B10 两个数据层 hook 里只改一处 vpnNow（漏一半）",
                  "            STStatusBarDataEntry *vpnNow = data.vpnEntry;",
                  "            STStatusBarDataEntry *vpnNow = nil;",
                  "S3a 两个数据层 hook 都只认本轮 vpnEntry"),
    replace_first("B11 退回与上一轮快照做或运算（断开不褪色）",
                  "            STStatusBarDataEntry *vpnNow = data.vpnEntry;",
                  "            STStatusBarDataEntry *vpnNow = data.vpnEntry;\n"
                  "            _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;",
                  "S3b 不与上一轮快照做或运算"),
    replace_first("B12 一个数据层 hook 漏掉过渡检测（一半断开场景不还原）",
                  "            SVPNNoteTransition();",
                  "            (void)0;",
                  "S3c 两个 hook 都触发过渡检测"),

    # ---------------------------------------------------------------
    # S4 entry 回填已删
    # ---------------------------------------------------------------
    one_replace("B13 回填函数被加回来",
                "static void SVPNNoteTransition(void) {",
                "static void svpnMergeMissingEntries(id d) { }\n\n"
                "static void SVPNNoteTransition(void) {",
                "S4a 不再有回填函数"),
    replace_first("B14 数据层调用 dataByReplacingEntry（塞残缺 entry → 图标空白）",
                  "            STStatusBarDataEntry *vpnNow = data.vpnEntry;",
                  "            data = [data dataByReplacingEntry:nil];\n"
                  "            STStatusBarDataEntry *vpnNow = data.vpnEntry;",
                  "S4b 数据层不改写系统 data"),
    one_replace("B15 _updateWithAggregatedData 重新赋值 data",
                "- (void)_updateWithAggregatedData:(STStatusBarData *)data {\n"
                "    if (gSVPNReady) {",
                "- (void)_updateWithAggregatedData:(STStatusBarData *)data {\n"
                "    data = data;\n"
                "    if (gSVPNReady) {",
                "S4c _updateWithAggregatedData 不重新赋值 data"),

    # ---------------------------------------------------------------
    # S5 ★ 核心修复：保 alpha（本版最重要的一组）
    # ---------------------------------------------------------------
    one_replace("B16 ★ 退回 colorWithAlphaComponent:0.2（图标在深浅背景下都看不见）",
                "UIColor *paint = svpnReplaceKeepingAlpha(inactiveColor,"
                " svpnReplacementFor(inactiveColor));",
                "UIColor *paint = [svpnReplacementFor(inactiveColor)"
                " colorWithAlphaComponent:0.2];",
                "S5a 代码里不用 colorWithAlphaComponent"),
    one_replace("B17 保 alpha 函数被删",
                "static UIColor *svpnReplaceKeepingAlpha(UIColor *original, UIColor *hue) {",
                "static UIColor *svpnReplaceKeepingAlphaXX(UIColor *original, UIColor *hue) {",
                "S5b 存在保 alpha 的换色函数"),
    one_replace("B18 保 alpha 函数改用 hue 自己的 alpha（等于没保）",
                "    return SVPNColorRGBA(r, g, b, sa);",
                "    return SVPNColorRGBA(r, g, b, a);",
                "S5c 保 alpha 函数真的取了原色 alpha"),
    one_replace("B19 setInactiveColor 绕过保 alpha",
                "UIColor *paint = svpnReplaceKeepingAlpha(inactiveColor,"
                " svpnReplacementFor(inactiveColor));",
                "UIColor *paint = svpnReplacementFor(inactiveColor);",
                "S5d setInactiveColor 走保 alpha"),
    one_replace("B20 dimmed 色绕过保 alpha",
                "                UIColor *dimmed = svpnReplaceKeepingAlpha(dimmedAnchor, hue);",
                "                UIColor *dimmed = hue;",
                "S5e imageDimmedTintColor 走保 alpha"),
    one_replace("B21 兜底色写死一个小 alpha（同样是看不见）",
                "    _lightReplacementColor = l ?: SVPNColorRGBA(0.196, 0.780, 0.349, 1.0);",
                "    _lightReplacementColor = l ?: SVPNColorRGBA(0.196, 0.780, 0.349, 0.2);",
                # ★ 2.2-2 起 S11c（alpha 只有两个来源）会先拦住它 ——
                #   这比 S5f 更根本：S5f 只看「有没有写死小数」，S11c 直接
                #   限定 alpha 只能从系统原色或常量 1.0 来。断言最根本的那条。
                "S11c alpha 只有两个来源"),

    # ---------------------------------------------------------------
    # S6 还原账本
    # ---------------------------------------------------------------
    one_replace("B22 退回两张平行表（漏销一张就串味）",
                "static NSMapTable *gSVPNLedger = nil;",
                "static NSMapTable *gSVPNLedger = nil;\nstatic NSMapTable *gSVPNOriginals = nil;",
                "S6a 账本是「一视图一字典」"),
    one_replace("B23 key 退回默认 personality（按 isEqual 合并视图）",
                "            NSPointerFunctionsWeakMemory | NSPointerFunctionsObjectPointerPersonality;",
                "            NSPointerFunctionsWeakMemory;",
                "S6b key 用 weak + 指针同一性"),
    one_replace("B24 去掉残留识别（会把绿色当系统色存下 → 还原成绿色）",
                "        if (lastPainted && [lastPainted isEqual:systemColor]) {",
                "        if (NO) {",
                "S6c 残留识别"),
    one_replace("B25 记账不存外观（跨外观写回 = 错色）",
                "            [d setObject:@(style) forKey:SVPN_STYLE_KEY];",
                "            (void)style;",
                "S6d 记账时把外观存进该视图自己的字典"),
    one_replace("B26 还原前不复核当前状态（断开又秒连会擦掉新染的色）",
                "    // 延后执行期间可能已经重新连上，此时擦色就是新 bug\n"
                "    if (SVPNDecision()) {\n        return;\n    }\n",
                "",
                "S6e 还原前重新确认当前不该染"),
    one_replace("B27 还原丢掉启动闸门（respring 窗口内就介入视图）",
                "static void SVPNRestoreNow(void) {\n    if (!gSVPNReady) {\n        return;\n    }\n",
                "static void SVPNRestoreNow(void) {\n",
                "S6f 还原有独立闸门"),
    one_replace("B28 边遍历 NSMapTable 边改它（未定义行为）",
                "        for (id v in gSVPNLedger) {\n            if (v) {",
                "        for (id v in gSVPNLedger) {\n"
                "            [gSVPNLedger removeObjectForKey:v];\n            if (v) {",
                "S6g 先收集再处理"),
    one_replace("B29 去掉逐视图外观比对（跨外观写回 = 图标空白）",
                "            if (styleNow >= 0 && rec && [rec intValue] != styleNow) {",
                "            if (NO) {",
                "S6h 逐视图外观比对"),
    one_replace("B30 外观不符时清空整表（外观没变的记录一起丢）",
                "            if (styleNow >= 0 && rec && [rec intValue] != styleNow) {\n"
                "                usable = NO;\n            }",
                "            if (styleNow >= 0 && rec && [rec intValue] != styleNow) {\n"
                "                [gSVPNLedger removeAllObjects];\n                usable = NO;\n            }",
                "S6h 逐视图外观比对"),
    one_replace("B31 还原侧用缓存外观（刚切外观就断开会判错）",
                "    int styleNow = SVPNUserInterfaceStyle(YES);",
                "    int styleNow = SVPNUserInterfaceStyle(NO);",
                "S6i 还原侧绕过缓存拿新鲜外观"),
    one_replace("B32 处理完不销账（旧账跨周期复用 → 不定期写错色）",
                "            [gSVPNLedger removeObjectForKey:view];\n",
                "",
                "S6j 处理完销账"),
    one_replace("B33 还原计划把 tint 加回来（setTintColor: 会传播到子视图）",
                '        @[@"ic", @"setInactiveColor:", @"inactiveColor"],\n    ];',
                '        @[@"ic", @"setInactiveColor:", @"inactiveColor"],\n'
                '        @[@"tint", @"setTintColor:", @"tintColor"],\n    ];',
                "S6k 只碰 tc/ac/ic 三个槽位"),
    one_replace("B34 少一个安全槽位（该图标断开后还原不回来）",
                '        @[@"ac", @"setActiveColor:", @"activeColor"],\n',
                "",
                "S6k 只碰 tc/ac/ic 三个槽位"),
    one_replace("B35 还原路径加回强制重绘（白重绘，且会碰宿主观测）",
                "        for (NSArray *pair in plan) {",
                "        [view setNeedsDisplay];\n        for (NSArray *pair in plan) {",
                "S6l 还原只写色不重绘"),
    one_replace("B36 去掉实色检查（动态色会按当前 trait 重解析成错色）",
                "        if (![color getRed:&r green:&g blue:&b alpha:&a]) {\n            return;",
                "        r = g = b = a = 1;\n        if (NO) {\n            return;",
                "S6m 写入口只写实色"),
    one_replace("B37 去掉透明色检查（写透明 = 图标空白）",
                "        if (a <= 0.001) {",
                "        if (NO) {",
                "S6n 写入口不写透明色"),
    one_replace("B38 去掉幂等检查（在系统更新流程之外反复改私有属性）",
                "                        if ([cur isKindOfClass:[UIColor class]] && [cur isEqual:color]) {\n"
                "                            return;\n                        }",
                "                        (void)cur;",
                "S6o 写入口幂等"),
    one_replace("B39 IMP 取返回值不用 __unsafe_unretained（ARC 误 release → 崩宿主）",
                "__unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);",
                "id cur = ((id (*)(id, SEL))gimp)(view, gsel);",
                "S6p IMP 取返回值用 __unsafe_unretained"),
    one_replace("B40 写入口不判 respondsToSelector（unrecognized selector 崩宿主）",
                "        if (!sel || ![view respondsToSelector:sel]) {",
                "        if (!sel) {",
                "S6q 每条 slot 还原前判 respondsToSelector"),
    one_replace("B41 过渡条件写成「变了就擦」（连上时也擦色）",
                "    if (prev == 1 && !now) {",
                "    if (prev != (now ? 1 : 0)) {",
                "S6r 只在 1→0 时触发还原"),

    # ---------------------------------------------------------------
    # S7 崩溃防御
    # ---------------------------------------------------------------
    one_replace("B42 某 hook 漏了 %orig（把系统行为整块抹掉）",
                "    }\n    %orig;\n}\n\n- (void)_updateWithData:",
                "    }\n}\n\n- (void)_updateWithData:",
                "S7b 每个 hook 都有"),
    one_replace("B43 @try 与 @catch 不配对（编不过/异常逃逸）",
                "    } @catch (NSException *e) {\n        }\n    }\n    %orig;\n}\n\n- (void)applyStyleAttributes:",
                "    }\n    }\n    %orig;\n}\n\n- (void)applyStyleAttributes:",
                "S7c @try/@catch 配对"),
    one_replace("B44 用裸 objc_msgSend 强转（参数错就崩宿主）",
                "__unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);",
                "__unsafe_unretained id cur = ((id (*)(id, SEL))objc_msgSend)(view, gsel);",
                "S7e 不用裸 objc_msgSend"),
    one_replace("B45 主动触发宿主状态栏重绘（最危险的一类改动）",
                "    int styleNow = SVPNUserInterfaceStyle(YES);",
                "    int styleNow = SVPNUserInterfaceStyle(YES);\n"
                "    [[UIApplication sharedApplication] setNeedsStatusBarAppearanceUpdate];",
                "S7f 不主动触发宿主状态栏重绘"),
    one_replace("B46 代码里混入 killall（装包/运行红线）",
                "static void SVPNRestoreAsync(void) {",
                "static void svpnKillallSpringBoard(void) { system(\"killall SpringBoard\"); }\n\n"
                "static void SVPNRestoreAsync(void) {",
                "S7g 代码里没有 respring"),

    # ---------------------------------------------------------------
    # S8 颜色边界
    # ---------------------------------------------------------------
    one_replace("B47 替换色去掉兜底（nil 会把图标色清空）",
                "    _lightReplacementColor = l ?: SVPNColorRGBA(0.196, 0.780, 0.349, 1.0);",
                "    _lightReplacementColor = l;",
                "S8a 替换色有兜底"),
    one_replace("B48 取系统色失败时返回 nil（放弃染色）",
                "    return dark ? _lightReplacementColor : _darkReplacementColor;",
                "    return nil;",
                "S8b 取系统色失败时不返回 nil"),
    one_replace("B49 换色前不判空入参",
                "static UIColor *svpnReplacementFor(UIColor *systemColor) {\n    if (!systemColor) {",
                "static UIColor *svpnReplacementFor(UIColor *systemColor) {\n    if (NO) {",
                "S8c 换色前判空入参"),

    # ---------------------------------------------------------------
    # S10 装包红线（mkdeb.sh）
    # ---------------------------------------------------------------
    one_replace("B50 postinst 混入 sbreload（杀前台 Sileo → 半装）",
                "printf '\\n'\nexit 0\nEOF",
                "printf '\\n'\nif [ -x /usr/bin/sbreload ]; then /usr/bin/sbreload; fi\nexit 0\nEOF",
                "S10b postinst 不执行 sbreload", file="mk"),
    one_replace("B51 postinst 混入 killall SpringBoard",
                "printf '\\n'\nexit 0\nEOF",
                "printf '\\n'\nkillall SpringBoard || true\nexit 0\nEOF",
                "S10b postinst 不执行 sbreload", file="mk"),
    one_replace("B52 postinst 调 launchctl",
                "printf '\\n'\nexit 0\nEOF",
                "printf '\\n'\nlaunchctl kickstart -k system/com.apple.SpringBoard\nexit 0\nEOF",
                "S10c postinst 不调用 launchctl", file="mk"),
    one_replace("B53 data.tar 不再跳过 DEBIAN（rootless / 只读 → 整包装不上）",
                "dirs[:] = [d for d in dirs if d != 'DEBIAN']",
                "dirs[:] = [d for d in dirs]",
                "S10d data.tar 真的跳过 DEBIAN", file="mk"),
    one_replace("B54 构建期解包回验闸门被删",
                "    raise SystemExit('data.tar 含 DEBIAN 条目，装包会炸: %s' % _bad[:5])",
                "    pass  # 闸门被删",
                "S10e 有构建期闸门", file="mk"),
    replace_first("B55 data.tar 写模式丢了 format（PaxHeader 让 dpkg 装不上）",
                  "with tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT) as tf:",
                  "with tarfile.open(fileobj=buf, mode='w') as tf:",
                  "S10f tar 写模式显式指定 GNU_FORMAT", file="mk"),
    one_replace("B56 PAX 运行期检查被删（解压前就写盘了）",
                "    raise SystemExit(1)",
                "    pass",
                "S10g PAX 扩展头运行期检查真的会抛错", file="mk"),

    # ---------------------------------------------------------------
    # S11 ★★ 结构性不变量（2.2-2 根本修复）
    #
    # 这一组的用例和 B16-B21 的区别：B16-B21 测「某一行改对了」，
    # 这一组测「坏色根本造不出来」。所以注入的是**结构性的退化**：
    # 重新允许在别处造色、把 alpha 来源放开、把事后补救机制加回来。
    # ---------------------------------------------------------------
    one_replace("B58 ★ 在别处新增一个造色点（绕开唯一入口）",
                "static void ReloadPrefs(void) {",
                "static UIColor *svpnAdHocColor(void) {\n"
                "    return [UIColor colorWithRed:1 green:0 blue:0 alpha:0.05];\n"
                "}\n\n"
                "static void ReloadPrefs(void) {",
                "S11a 全工程只有一处造色"),
    one_replace("B59 造色入口自己写死 alpha（调用方再也压不下去也没意义）",
                "        return [UIColor colorWithRed:r green:g blue:b alpha:a];",
                "        a = 1.0;\n        return [UIColor colorWithRed:r green:g blue:b alpha:a];",
                # 注入后 SVPNColorRGBA 体内出现 alpha:1.0 之外的写死 —— 由 S11b 拦
                "S11b 造色入口不篡改 alpha 参数"),
    one_replace("B60 配置色改成半透明（alpha 不再恒为 1）",
                "    _darkReplacementColor = d ?: SVPNColorRGBA(0.173, 0.816, 0.341, 1.0);",
                "    _darkReplacementColor = d ?: SVPNColorRGBA(0.173, 0.816, 0.341, 0.35);",
                "S11c alpha 只有两个来源"),
    one_replace("B61 色相变量直接塞进 UI 槽位（绕过系统 alpha）",
                "            UIColor *paint = svpnReplaceKeepingAlpha(sysTextColor, svpnReplacementFor(sysTextColor));",
                "            UIColor *paint = svpnReplacementFor(sysTextColor);",
                "S11d 色相变量从不直接进 UI 槽位"),
    one_replace("B62 setActiveColor 退回直接塞纯色相（激活格不再跟随系统淡出）",
                "UIColor *paint = svpnReplaceKeepingAlpha(activeColor, svpnReplacementFor(activeColor));",
                "UIColor *paint = svpnReplacementFor(activeColor);",
                "S11d 色相变量从不直接进 UI 槽位"),
    one_replace("B63 ★ 把「事后扫描修复」加回来（治标：允许坏色先进来）",
                "static void SVPNRestoreAsync(void) {",
                "static void SVPNHealNow(const char *r) { (void)r; }\n\n"
                "static void SVPNRestoreAsync(void) {",
                "S11f 没有事后扫描修复机制"),
    one_replace("B64 加回定时轮询（长期和系统抢同一个属性）",
                "static void SVPNRestoreAsync(void) {",
                "static void svpnStartPoll(void) {\n"
                "    dispatch_source_t t = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,\n"
                "                                                 dispatch_get_main_queue());\n"
                "    (void)t;\n"
                "}\n\n"
                "static void SVPNRestoreAsync(void) {",
                "S11g 不做定时轮询"),
    one_replace("B65 染色路径里加一次强制重绘（和系统绘制节奏打架）",
                "            if (paint) {\n"
                "                SVPNRecord(self, @\"ac\", activeColor, paint);\n"
                "                activeColor = paint;\n            }",
                "            if (paint) {\n"
                "                [self setNeedsDisplay];\n"
                "                SVPNRecord(self, @\"ac\", activeColor, paint);\n"
                "                activeColor = paint;\n            }",
                "S11h 染色路径里不主动重绘"),

    # ---------------------------------------------------------------
    # S9 版本号一致性
    # ---------------------------------------------------------------
    one_replace("B57 版本号三处不一致（装着 2.2-2，报 2.1-11）",
                "SingleVPN 2.2-2 已安装",
                "SingleVPN 2.1-11 已安装",
                "S9a 三处版本号一致", file="mk"),
]


def restore_all():
    for k, p in FILES.items():
        io.open(p, "w", encoding="utf-8", newline="").write(_orig[k])


def main():
    rc, out = run_check()
    assert rc == 0, "干净态自检就挂了，先修：\n" + out[-1500:]
    print("前置：干净态自检全绿 ✓\n")

    passed = 0
    try:
        for name, fk, fn, expect in CASES:
            path = FILES[fk]
            mutated = fn(_orig[fk])
            assert mutated != _orig[fk], "%s：注入没改变文件（反向验证会失真）" % name
            io.open(path, "w", encoding="utf-8", newline="").write(mutated)
            # ① 确认注入真的落盘（不是只改了内存）
            assert io.open(path, encoding="utf-8", newline="").read() == mutated, \
                "%s：注入没写进磁盘" % name
            # ② 断言是哪条判据拦下的
            rc, out = run_check()
            assert rc != 0, "%s：注入后自检竟然全绿 = 判据是摆设" % name
            fail_block = out.split("失败：")[-1] if "失败：" in out else ""
            assert expect in fail_block, (
                "%s：该报的是「%s」，实际失败列表：\n%s" % (name, expect, fail_block[:600]))
            # ③ 还原并确认干净
            restore_all()
            rc2, out2 = run_check()
            assert rc2 == 0, "%s：还原后自检仍挂：\n%s" % (name, out2[-800:])
            print("  ✓ %-50s 已拦下（%s）" % (name, expect))
            passed += 1
    finally:
        restore_all()

    rc, out = run_check()
    assert rc == 0, "最终复跑不干净：\n" + out[-1200:]
    for k, p in FILES.items():
        assert io.open(p, encoding="utf-8", newline="").read() == _orig[k], "文件没还原: " + k
    print("\n✓ 反向验证全过（%d 例，零 SKIP），两个文件均已还原且复跑干净" % passed)


if __name__ == "__main__":
    main()