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
import re
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(_REPO, "tools", "check_source.py")
PY = sys.executable

FILES = {
    "x": os.path.join(_REPO, "SingleVPN.x"),
    "mk": os.path.join(_REPO, "devkit", "mkdeb.sh"),
    # ★ 2.2-5 起设置页也进了闸门（S12n/S12o/S12p/S12q 判的就是它）。
    #   判据覆盖了文件却不在还原清单里 = 注入后永远改不回来，
    #   下一轮跑闸门会看到一堆莫名其妙的失败。
    "m": os.path.join(_REPO, "SingleVPNPrefs", "SingleVPNRootListController.m"),
    "pl": os.path.join(_REPO, "SingleVPNPrefs", "Resources", "Root.plist"),
    # ★ 2.2-5：CI workflow 也进了还原清单。S13 组判的就是它的 shell 判据，
    #   注入后必须能改回来，否则下一轮 verify 步骤会莫名其妙地假红。
    "yml": os.path.join(_REPO, ".github", "workflows", "build.yml"),
    # ★ 2.2-5 收尾：诊断解析器也进了闸门（S14 组判的是它与源码的格式契约）。
    #   同一个规则：判据覆盖了哪个文件，就必须在还原清单里。
    "dp": os.path.join(_REPO, "tools", "read_diag.py"),
    # ★★ S14f 判的是**闸门脚本自己**有没有用 importlib —— 所以闸门脚本本身
    #    也必须在还原清单里。第一版漏了这个键，B108 用了默认 file="x"，
    #    结果往 SingleVPN.x 里找 exec(compile(...)) → 命中 0 次 → 整轮中断。
    #    这正是我自己写下的规则：「判据覆盖了哪个文件，就必须进反向验证的
    #    还原清单（FILES）；否则注入后文件不回滚。」
    "cs": os.path.join(_REPO, "tools", "check_source.py"),
}
_orig = {k: io.open(p, encoding="utf-8", newline="").read() for k, p in FILES.items()}


def run_check():
    r = subprocess.run([PY, CHECK], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ★★ 提取失败列表必须锚「结构」，不能锚「文字」。
#
# 第一版是 `out.split("失败：")[-1]` —— 拿最后一个「失败：」当切分点。
# 这在 B104 上炸了：新加的 S14g detail 里写了「模拟/解析失败：%r」，
# 于是最后一个「失败：」跑到了 detail 内部，切出来的块从 S14g 中途开始，
# S14d 那行被切在块外 → 断言「该报的是 S14d」失败 → 整轮中断。
#
# 这不是用例写错，是**判据的取数方式锚在了自由文本上**：detail 是给人看的
# 任意字符串，谁以后写一句含「失败：」的说明都会再踩一次。
# 所以改成认 check_source.py 的**固定输出结构**：
#   「失败：」单独占一行 + 紧随其后的若干「  - <判据名> <detail>」行。
# 只认「行首两个空格 + '- '」的行，且必须在「通过 N 项，失败 M 项」之后。
_FAIL_LINE = re.compile(r"^  - (\S.*)$")
_SUMMARY = re.compile(r"^通过 \d+ 项，失败 \d+ 项\s*$", re.M)


def _fail_block_of(out):
    """从闸门输出里取出失败判据名的拼接串（只认结构性失败行）。

    返回的是所有失败判据的「名字 + detail」行拼起来，供 `expect in` 断言。
    取不到任何失败行时返回空串 —— 那样断言必然失败并打印原始输出，
    比返回整段输出更好（后者可能让 expect 蒙对，掩盖取数失效）。
    """
    # 从「通过 N 项，失败 M 项」这一行之后开始找，避开前面各判据的正文。
    # 认不出汇总行时退化为全文扫描（宁可多找，也不能少找 —— 少了就是假 FAIL）。
    idx = 0
    for m in _SUMMARY.finditer(out):
        idx = m.end()
    body = out[idx:]
    names = []
    for ln in body.splitlines():
        m = _FAIL_LINE.match(ln)
        if m:
            names.append(m.group(1))
    return "\n".join(names)


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


def _bump_mk_version(s):
    """把 mkdeb.sh 里 printf 的版本号改掉一位（不依赖具体数字）。

    ★ 为什么不用 one_replace 写死 "2.2-4"：版本号一升版，写死的锚点
      就命中 0 次，整轮反向验证在断言处中断 —— 那是用例过期，
      不是判据失效，但看起来一模一样，会把人带偏。
    """
    import re as _re
    m = _re.search(r"(SingleVPN\s+)([\w.\-]+)(\s+已安装)", s)
    assert m, "mkdeb.sh 里找不到已安装提示"
    old_ver = m.group(2)
    bump = old_ver[:-1] + "9" if old_ver[:-1] != old_ver[-1] else old_ver + "9"
    if bump == old_ver:
        bump = old_ver + "9"
    return s[:m.start(2)] + bump + s[m.end(2):]


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
                "            gSVPNReady = 1;\n            // 记下闸门打开的时刻",
                "            gSVPNReady = 1;\n            gSVPNReady = 1;\n            // 记下闸门打开的时刻",
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
                "    // ★ 诊断记录放在闸门",
                "- (void)_updateWithAggregatedData:(STStatusBarData *)data {\n"
                "    data = data;\n"
                "    // ★ 诊断记录放在闸门",
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
                "                    UIColor *dimmed = svpnReplaceKeepingAlpha(sysDimmed, hue);",
                "                    UIColor *dimmed = hue;",
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
    # ★★ 2.2-4：B25/B29/B30/B31 原来测的是「按外观记账 + 比对外观」，
    #    那套机制已删除（它是「断开不褪色」的根因）。用例整体改写。
    one_replace("B25 ★ 把「按界面外观记账」加回来（间接代理指标，取错就整视图不还原）",
                "        [d setObject:systemColor forKey:slot];",
                "        [d setObject:@(0) forKey:SVPN_STYLE_KEY];\n"
                "        [d setObject:systemColor forKey:slot];",
                "S6d 不再按界面外观记账"),
    one_replace("B26 还原前不复核当前状态（断开又秒连会擦掉新染的色）",
                "    // 延后执行期间可能已经重新连上，此时擦色就是新 bug\n"
                "    if (SVPNDecision()) {\n        return;\n    }\n",
                "",
                "S6e 还原前重新确认当前不该染"),
    one_replace("B27 还原丢掉启动闸门（respring 窗口内就介入视图）",
                "    SVPNMaybeExport();\n\n    if (!gSVPNReady) {\n        return;\n    }\n"
                "    // 延后执行期间可能已经重新连上，此时擦色就是新 bug",
                "    SVPNMaybeExport();\n\n"
                "    // 延后执行期间可能已经重新连上，此时擦色就是新 bug",
                "S6f 还原有独立闸门"),
    one_replace("B28 边遍历 NSMapTable 边改它（未定义行为）",
                "        for (id v in gSVPNLedger) {\n            if (v) {",
                "        for (id v in gSVPNLedger) {\n"
                "            [gSVPNLedger removeObjectForKey:v];\n            if (v) {",
                "S6g 先收集再处理"),
    # ★★ B29/B30 直接对应「断开不褪色」这个 bug 的两个成因。
    one_replace("B29 ★★ 还原改回「按外观不符就整视图跳过」（= 断开不褪色的原 bug）",
                "        BOOL didWrite = NO;",
                "        int styleNow = SVPNUserInterfaceStyle(YES);\n"
                "        NSNumber *rec = [d objectForKey:SVPN_STYLE_KEY];\n"
                "        if (styleNow >= 0 && rec && [rec intValue] != styleNow) {\n"
                "            continue;\n"
                "        }\n"
                "        BOOL didWrite = NO;",
                "S6h ★ 不用外观比对当还原门槛"),
    one_replace("B30 ★ 去掉「当前值是否仍是我染的」判断（会把系统已刷新的也重写）",
                "            if (checkCurrent && painted) {\n"
                "                UIColor *cur = SVPNReadColor(view, getter);\n"
                "                if (cur && ![cur isEqual:painted]) {\n"
                "                    continue;\n"
                "                }\n"
                "            }",
                "            (void)checkCurrent;",
                "S6i ★ 逐槽位判「当前值是否仍是我染的」"),
    one_replace("B32 处理完不销账（旧账跨周期复用 → 不定期写错色）",
                "            [gSVPNLedger removeObjectForKey:view];\n",
                "",
                "S6j 处理完销账"),
    # ★★ B33 方向反转：原来是「把 tint 加回来 = 违规」，
    #    现在是「把 tint 去掉 = 违规」（去掉就不褪色）。
    one_replace("B33 ★★ 还原计划删掉 tint（蜂窝/WiFi 图标就永远不褪色）",
                '        @[@"tint", @"setTintColor:", @"tintColor", @NO],\n',
                "",
                "S6k ★ 还原覆盖 tc/ac/ic/tint 四路"),
    one_replace("B34 少一个安全槽位（该图标断开后还原不回来）",
                '        @[@"ac", @"setActiveColor:", @"activeColor", @YES],\n',
                "",
                "S6k ★ 还原覆盖 tc/ac/ic/tint 四路"),
    # ★★ B35 方向反转：原来是「加回强制重绘 = 违规」，
    #    现在是「去掉还原重绘 = 违规」（属性 setter 不触发绘制 → 不褪色）。
    one_replace("B35 ★★ 去掉还原后的标脏重绘（= 断开不褪色的直接原因）",
                "        if (didWrite) {\n            @try {\n                [view setNeedsDisplay];",
                "        if (NO) {\n            @try {\n                [view setNeedsDisplay];",
                "S6l ★ 还原写了色必须标脏重绘"),
    one_replace("B36 去掉实色检查（动态色会按当前 trait 重解析成错色）",
                "        if (![color getRed:&r green:&g blue:&b alpha:&a]) {\n            return NO;",
                "        r = g = b = a = 1;\n        if (NO) {\n            return NO;",
                "S6m 写入口只写实色"),
    one_replace("B37 去掉透明色检查（写透明 = 图标空白）",
                "        if (a <= 0.001) {\n            return NO;",
                "        if (NO) {\n            return NO;",
                "S6n 写入口不写透明色"),
    one_replace("B38 去掉幂等检查（在系统更新流程之外反复改私有属性）",
                "        if (getter) {\n"
                "            UIColor *cur = SVPNReadColor(view, getter);\n"
                "            if (cur && [cur isEqual:color]) {\n"
                "                return NO;\n"
                "            }\n"
                "        }",
                "        (void)getter;",
                "S6o 写入口幂等"),
    one_replace("B39 IMP 取返回值不用 __unsafe_unretained（ARC 误 release → 崩宿主）",
                "__unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);",
                "id cur = ((id (*)(id, SEL))gimp)(view, gsel);",
                "S6p IMP 取返回值用 __unsafe_unretained"),
    one_replace("B40 写入口不判 respondsToSelector（unrecognized selector 崩宿主）",
                "        if (!sel || ![view respondsToSelector:sel]) {\n            return NO;\n        }",
                "        if (!sel) {\n            return NO;\n        }",
                "S6q 每条 slot 还原前判 respondsToSelector"),
    one_replace("B40b 读值不判 respondsToSelector（unrecognized selector 崩宿主）",
                "    if (!gsel || ![view respondsToSelector:gsel]) {\n        return nil;\n    }",
                "    if (!gsel) {\n        return nil;\n    }",
                "S6q2 读值前也判 respondsToSelector"),
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
                "    // 先收集再处理：遍历 NSMapTable 的同时改它属于未定义行为",
                "    [[UIApplication sharedApplication] setNeedsStatusBarAppearanceUpdate];\n"
                "    // 先收集再处理：遍历 NSMapTable 的同时改它属于未定义行为",
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
    one_replace("B66 ★ 系统没给 dimmed 时拿 tint 顶替（凭空造值）",
                "                if (sysDimmed) {",
                "                if (YES) {\n"
                "                    UIColor *dimmedAnchor = sysDimmed ?: sysTint;",
                "S11i tint / dimmed 只在系统给了值时才设"),
    one_replace("B67 dimmed 分支改用 ?: 兜底（等价于凭空造值）",
                "                    UIColor *dimmed = svpnReplaceKeepingAlpha(sysDimmed, hue);",
                "                    UIColor *dimmed = svpnReplaceKeepingAlpha(sysDimmed ?: sysTint, hue);",
                "S11i tint / dimmed 只在系统给了值时才设"),
    one_replace("B65 染色路径里加一次强制重绘（和系统绘制节奏打架）",
                "            if (paint) {\n"
                "                SVPNRecord(self, @\"ac\", activeColor, paint);\n"
                "                activeColor = paint;\n            }",
                "            if (paint) {\n"
                "                [self setNeedsDisplay];\n"
                "                SVPNRecord(self, @\"ac\", activeColor, paint);\n"
                "                activeColor = paint;\n            }",
                "S11h 染色函数体内不主动重绘"),

    # ---------------------------------------------------------------
    # S11j~S11n 褪色链路（2.2-4）
    # ★★ 这一组的每一例都直接对应「断开不褪色」的一个真实成因。
    #    它们必须能失败 —— 否则下次同样的回归还是拦不住。
    # ---------------------------------------------------------------
    # ★ 两种注入都要拦：① 整块删掉；② 套进 if (NO) ——
    #   只判「出现过 setNeedsDisplay」的话，② 会漏（反向验证抓到过）。
    one_replace("B68 ★★ 去掉还原后的标脏重绘（= 断开不褪色的直接原因）",
                "        if (didWrite) {\n"
                "            @try {\n"
                "                [view setNeedsDisplay];",
                "        if (NO) {\n"
                "            @try {\n"
                "                [view setNeedsDisplay];",
                "S6l ★ 还原写了色必须标脏重绘"),
    one_replace("B68b ★ 把还原重绘整块删掉（连 if (NO) 都不留）",
                "        if (didWrite) {\n"
                "            @try {\n"
                "                [view setNeedsDisplay];\n"
                "            } @catch (NSException *e) {\n"
                "            }\n"
                "        }",
                "        (void)didWrite;",
                "S6l ★ 还原写了色必须标脏重绘"),
    one_replace("B69 ★★ 还原计划漏掉 tint（icon 永远不褪色）",
                '        @[@"tint", @"setTintColor:", @"tintColor", @NO],\n',
                "",
                "S11k 还原路径覆盖 tint 槽位"),
    one_replace("B70 ★★ 染色时不记 tint（账本里没它 → 无从还原）",
                "                    if (sysTint && tint) {\n"
                "                        SVPNRecord(self, @\"tint\", sysTint, tint);\n"
                "                    }",
                "                    (void)tint;",
                "S11l 染色时确实记了 tint 这一笔"),
    one_replace("B71 ★ tint 槽位改成做「当前值校验」（继承值永不匹配 → 恒跳过）",
                '@[@"tint", @"setTintColor:", @"tintColor", @NO],',
                '@[@"tint", @"setTintColor:", @"tintColor", @YES],',
                "S11m tint 槽位不做「当前值比对」校验"),
    one_replace("B72 ★★ 把「按界面外观决定要不要还原」加回来（= 断开不褪色的原 bug）",
                "    // 先收集再处理：遍历 NSMapTable 的同时改它属于未定义行为",
                "    int styleNow = SVPNUserInterfaceStyle(YES);\n"
                "    (void)styleNow;\n"
                "    // 先收集再处理：遍历 NSMapTable 的同时改它属于未定义行为",
                "S11n 不用界面外观当还原门槛"),

    # ---------------------------------------------------------------
    # S12 诊断层（2.2-5）
    # ---------------------------------------------------------------
    # ★ 这一组要回答的问题不是「诊断代码写没写」，而是
    #   「这版诊断包放出去会不会白跑一轮」——那才是真代价。
    #   每一例都对应一种「白跑」：崩宿主 / 拖慢 / 记不到 / 路径对不上。
    one_replace("B80 环形缓冲改成 0 槽（导出永远是空的，白跑一轮）",
                "#define SVPN_DIAG_SLOTS 256",
                "#define SVPN_DIAG_SLOTS 1",
                "S12a 环形缓冲有静态数组"),

    one_replace("B81 SVPNNote 忘了推进序号（导出恒为第 0 条）",
                "    gSVPNSeq = e->seq;\n",
                "",
                "S12b SVPNNote 推进序号与计数"),

    one_replace("B82 热路径里加了 NSLog（每条钩子都打日志，状态栏卡顿）",
                "    e->extra = extra;\n",
                "    e->extra = extra;\n    NSLog(@\"note %u\", ev);\n",
                "S12c SVPNNote 体内无分配"),

    one_replace("B83 落盘不包 @try（IO 抛异常直接崩 SpringBoard）",
                "    @try {\n        SVPNExportDiag();\n    } @catch (NSException *e) {\n    }",
                "    SVPNExportDiag();",
                "S12d 落盘被 @try 包住"),

    one_replace("B84 标志判断挪进 @try（每条钩子都建 setjmp）",
                "    if (!gSVPNExportRequest) {\n        return;\n    }\n"
                "    gSVPNExportRequest = 0;\n    @try {",
                "    @try {\n    if (!gSVPNExportRequest) {\n        return;\n    }\n"
                "    gSVPNExportRequest = 0;",
                "S12d 落盘被 @try 包住"),

    one_replace("B85 闸门打开时顺手落盘一次（多一条写入路径）",
                "            gSVPNReady = 1;\n            // 记下闸门打开的时刻",
                "            gSVPNReady = 1;\n            SVPNExportDiag();\n            // 记下闸门打开的时刻",
                "S12e 落盘只由 SVPNMaybeExport 触发"),

    one_replace("B86 导出路径写到 /tmp（rootless 下用户取不到）",
                '#define SVPN_DIAG_EXPORT_PATH "/var/mobile/Library/Preferences/com.82flex.singlevpn-diag.txt"',
                '#define SVPN_DIAG_EXPORT_PATH "/tmp/com.82flex.singlevpn-diag.txt"',
                "S12f 导出路径在 /var/mobile 下"),

    one_replace("B86b 去掉 stdio.h 靠传递包含（本地过、CI 报 implicit declaration）",
                "#import <stdio.h>   // snprintf —— 不能靠 UIKit 的传递包含，那是实现细节\n",
                "",
                "S12f2 诊断层用到的 POSIX 接口都有显式头文件"),

    one_replace("B87 热路径里也把导出标志置 1（每帧落盘）",
                "    e->extra = extra;\n",
                "    e->extra = extra;\n    gSVPNExportRequest = 1;\n",
                "S12g 导出请求标志只由通知回调置 1"),

    # ★★ 下面三条是本轮的核心：诊断能不能回答问题，全看记录点在哪一行。
    replace_first("B88 视图记录点挪到染色判断之后（看不到「没被调用」）",
                  "    @try {\n        SVPNNote(SVPNEvWifiInactive, inactiveColor, 0);\n    } @catch (NSException *e) {\n    }\n"
                  "    SVPNMaybeExport();\n\n    if (SVPNDecision() && inactiveColor) {",
                  "    if (SVPNDecision() && inactiveColor) {\n"
                  "        @try {\n            SVPNNote(SVPNEvWifiInactive, inactiveColor, 0);\n        } @catch (NSException *e) {\n        }\n"
                  "        SVPNMaybeExport();",
                  "S12h 六个视图钩子的记录点"),

    one_replace("B89 数据层记录点塞进 gSVPNReady 里（启动窗口被挖掉）",
                "    @try {\n        STStatusBarDataEntry *vpnProbe = data ? data.vpnEntry : nil;\n"
                "        SVPNNote(SVPNEvDataAggregated, nil, vpnProbe ? (vpnProbe.enabled ? 1u : 2u) : 0u);\n"
                "    } @catch (NSException *e) {\n    }\n    SVPNMaybeExport();\n\n"
                "    if (gSVPNReady) {\n        @try {\n"
                "            // 只认本轮 data 携带的条目",
                "    if (gSVPNReady) {\n        @try {\n"
                "            STStatusBarDataEntry *vpnProbe = data ? data.vpnEntry : nil;\n"
                "            SVPNNote(SVPNEvDataAggregated, nil, vpnProbe ? (vpnProbe.enabled ? 1u : 2u) : 0u);\n"
                "        } @catch (NSException *e) {\n        }\n        SVPNMaybeExport();\n"
                "        @try {\n"
                "            // 只认本轮 data 携带的条目",
                "S12i 数据层记录点在 gSVPNReady 闸门之外"),

    one_replace("B90 定义了事件枚举却从不记录（日志里永远看不到那类事件）",
                "    SVPNEvGateOpen         = 10,  // 闸门打开\n};",
                "    SVPNEvGateOpen         = 10,  // 闸门打开\n    SVPNEvUnused           = 99,\n};",
                "S12j 每个事件枚举都被真的记录"),

    one_replace("B91 诊断版本串没跟着升版（日志对不上产物）",
                '#define SVPN_VERSION_STR "2.2-5"',
                '#define SVPN_VERSION_STR "2.2-4"',
                "S12k 诊断版本串"),

    one_replace("B92 通知回调少写形参（签名对不上，靠 ABI 巧合运行）",
                "static void SVPNPrefsChanged(CFNotificationCenterRef center,\n"
                "                             void *observer,\n"
                "                             CFStringRef name,\n"
                "                             const void *object,\n"
                "                             CFDictionaryRef userInfo) {",
                "static void SVPNPrefsChanged(void) {",
                "S12l 两个 Darwin 通知回调都是五参数签名"),

    one_replace("B93 设置页 plist 的 action 名写岔（点了按钮什么都不发生）",
                "<string>exportDiagnostics</string>",
                "<string>exportDiag</string>",
                "S12n 设置页有导出 action", file="pl"),

    one_replace("B94 两侧通知名不一致（设置页发的通知插件收不到）",
                'CFSTR("com.82flex.singlevpnprefs/exportdiag")',
                'CFSTR("com.82flex.singlevpnprefs/exportdiag2")',
                "S12o 导出通知名在插件侧与设置页侧完全一致", file="m"),

    one_replace("B95 两侧导出路径不一致（用户按提示找不到文件）",
                '@"/var/mobile/Library/Preferences/com.82flex.singlevpn-diag.txt"',
                '@"/var/mobile/Library/Preferences/com.82flex.singlevpn-diag.log"',
                "S12p 导出文件路径两侧是同一个完整字面量", file="m"),

    one_replace("B96 设置页又用 section 序号当判据（插一段就漂移）",
                "    PSSpecifier *specifier = [self specifierAtIndexPath:indexPath];",
                "    if (indexPath.section == 2) { return [super tableView:tableView cellForRowAtIndexPath:indexPath]; }\n"
                "    PSSpecifier *specifier = [self specifierAtIndexPath:indexPath];",
                "S12q 设置页不用 section 序号当判据", file="m"),

    # ---------------------------------------------------------------
    # S13 CI shell 判据一致性（2.2-5 真实踩到的 CI 假红）
    # ---------------------------------------------------------------
    # ★ 这一组防的是「本地全绿、CI 红」——本轮真的发生了：
    #   CI 的旧结构黑名单用裸子串 "SVPN_DIAG"，而新诊断常量叫
    #   SVPN_DIAG_SLOTS，子串命中 → CI 红，报错还写着「推的不是 2.2-5」。
    one_replace("B97 黑名单里加回短子串 SVPN_DIAG（会误伤 SVPN_DIAG_SLOTS）",
                "for old in gSVPNOriginals SVPNRememberOriginal SVPNRestorePaintedViewsNow \\\n"
                "                     gSVPNPainted kSVPNStyleKey SVPN_DISABLE_ENTRY_MERGE; do",
                "for old in gSVPNOriginals SVPNRememberOriginal SVPNRestorePaintedViewsNow \\\n"
                "                     gSVPNPainted kSVPNStyleKey SVPN_DIAG SVPN_DISABLE_ENTRY_MERGE; do",
                "S13a CI 旧结构黑名单", file="yml"),

    one_replace("B98 把 SVPN_DIAG 的单词边界判定改回子串 grep（判据方向反了）",
                "if printf '%s' \"$CODE\" | grep -Eq '(^|[^A-Za-z0-9_])SVPN_DIAG([^A-Za-z0-9_]|$)'; then",
                "if printf '%s' \"$CODE\" | grep -q 'SVPN_DIAG'; then",
                "S13b CI 对裸 SVPN_DIAG 用单词边界匹配", file="yml"),

    one_replace("B99 源码里混进旧脚手架裸 SVPN_DIAG（该报的必须报）",
                "#define SVPN_DIAG_SLOTS 256",
                "#define SVPN_DIAG 0\n#define SVPN_DIAG_SLOTS 256",
                "S13c 源码有 SVPN_DIAG_ 前缀常量但没有裸 SVPN_DIAG"),

    # ★★ 2.2-5 真实踩到的编译错（CI 第 2 次假红，这次是真编不过）：
    #    SVPNPrefsChanged 在 405 行调用 ReloadPrefs，而它定义在 428 行
    #    → error: use of undeclared identifier 'ReloadPrefs'
    #    本机没有 Xcode，这类错只能等 CI，一次失败一次往返。
    one_replace("B100 去掉 ReloadPrefs 前置声明（CI 报 undeclared identifier）",
                "static void ReloadPrefs(void);\n",
                "",
                "S13d 静态函数都是先声明/定义后使用"),

    # ---------------------------------------------------------------
    # S14 诊断解析器与源码格式契约（2.2-5 收尾新增）
    #
    # ★ 这一组测的是「读诊断数据的工具自己会不会读错」。它比一般判据更需要
    #   反向验证：一份读错字段的解析器会输出**看起来很有道理的错误结论**，
    #   然后我照着它去改染色代码 —— 那比没有工具更糟。
    # ---------------------------------------------------------------
    # 事件名漂移：源码改了名字而脚本没跟（最常见的真实情形）
    one_replace("B101 源码改了事件名，解析器没跟着改（两侧对不上）",
                'return "WifiSignal.setActiveColor:";',
                'return "WifiSignal.setActiveColorX:";',
                "S14b 事件名两侧完全一致"),

    # 脚本多认一个源码里没有的事件名 —— 同样要红（方向相反的漂移）
    one_replace("B102 解析器多认了一个源码没有的事件名（反向漂移）",
                '    "gate open": LAYER_CTRL,',
                '    "gate open": LAYER_CTRL,\n    "phantom.event:": LAYER_CTRL,',
                "S14b 事件名两侧完全一致", file="dp"),

    # 颜色名漂移：源码把 CLEAR 改名，脚本仍按旧名分类 → 透明色被当未知
    one_replace("B103 源码改了颜色名（CLEAR → TRANSPARENT），脚本不认",
                'case SVPNColorClear:  return "CLEAR";',
                'case SVPNColorClear:  return "TRANSPARENT";',
                "S14c 颜色名两侧一致"),

    # 解析器字段顺序错位：这是最隐蔽的一种 —— 照样能解析出 7 个字段，
    # 但 ready/dec 或 color/extra 互换，结论就会指向错误的层。
    one_replace("B104 解析器把 color 与 extra 两列读反（能解析但字段错位）",
                r'r"^(\d+)\s+(\d+)\s+([01])\s+([01])\s+(\S+)\s+(\d+)\s+(.+?)\s*$", s',
                r'r"^(\d+)\s+(\d+)\s+([01])\s+([01])\s+(\d+)\s+(\S+)\s+(.+?)\s*$", s',
                "S14d 导出行的字段顺序与解析器一致", file="dp"),

    # 自测被架空：把「按组判」改回「按单项判」，自测必须失败（那条反向用例
    # 就是为了咬住这个错法才写的 —— 第五例「组内只缺一项」）。
    one_replace("B105 分层判定退回按单项判（会把『这次没变』误报成视图缺失）",
                "if all(by_ev.get(e, 0) == 0 for e in grp)",
                "if any(by_ev.get(e, 0) == 0 for e in grp)",
                "S14e read_diag.py --selftest 实跑通过", file="dp"),

    # 解析器整个不见了：诊断数据就没人能读。
    # ★ 期望 S14a2 而不是 S14a：实测发现删空文件后 S14a（`is not None`）**仍然通过**
    #   —— 空 .py 也能 import 成功，拿到的是空 module 而不是 None。
    #   真正咬住「读不了」的是 S14a2（关键成员齐全 + 有实质内容）。
    #   这条用例的名字保留，是因为它测的场景是「文件没了」；
    #   但判据必须挂在真正能红的那一条上。
    ("B106 解析器文件被清空（诊断数据从此没人能读）",
     "dp", lambda s: "", "S14a2 解析器不是空壳"),

    # ★ 同一个场景的另一种形态：文件还在、也还能 import，但内容被删到只剩
    #   一个无关函数 —— S14a 和「文件存在」都过，只有 S14a2 的成员断言能拦住。
    one_replace("B107 解析器被掏空成只剩一个空壳（存在但不可用）",
                "def parse(path):",
                "def parse_disabled(path):",
                "S14a2 解析器不是空壳", file="dp"),

    # ★★ S14f 的反向用例（CI 上真实换来的教训）。
    #    把 importlib 加载方式注回闸门 → 必须红。
    #    这个缺陷的实际后果是**假绿**：字节码缓存按 (mtime, size) 判有效，
    #    「改字符不改长度」的注入 size 不变、同秒写入 mtime 也可能不变 →
    #    闸门读到注入前的旧字节码 → 判据说自己过了。
    # ★ 锚点只用**一行**，不要跨行。跨行锚点第一版漏了行首 8 个空格，
    #   命中 0 次 —— 而 verify 脚本在这种情况下会断言中断整轮，
    #   看起来像判据失效，实际是用例自己写错了锚点。
    #   单行锚点还有个好处：注入后语法仍然成立，闸门才能真的跑到 S14f。
    one_replace("B108 闸门改回用 importlib 加载解析器（会读字节码缓存 → 假绿）",
                '        exec(compile(_diag_src, _DIAG_PY, "exec"), _ns)',
                '        import importlib.util as _il\n'
                '        _sp2 = _il.spec_from_file_location("rd", _DIAG_PY)\n'
                '        _m2 = _il.module_from_spec(_sp2)\n'
                '        _sp2.loader.exec_module(_m2)',
                "S14f 闸门不用 importlib 加载被测脚本", file="cs"),

    # ---------------------------------------------------------------
    # S14g / S14h / S14i：C 端格式串 ↔ 解析器（本轮补的漏）
    #
    # 这三条的注入都必须落在 **SingleVPN.x 的 snprintf 上**，不能落到
    # read_diag.py —— 因为要证明的正是「判据锚在 C 那头」。落到 Python 侧
    # 就只能证明解析器自己变了，证明不了「C 改了闸门会不会红」。
    #
    # ★★ expect 用 **list** 表示「失败列表必须恰好是这几条」（严格断言），
    #    用 str 则只断言「包含」（老用例的形态）。
    #    为什么新用例必须严格：第一版 B109 我一次注入同时改了格式串列序
    #    和实参顺序，实测点亮 S14g + S14h 两条 —— 那就无法断言「是哪一条
    #    拦下的」，S14h 是否真的独立有效也就无从证明。
    #    拆成 B109（只改格式串）/ B110（只改实参）后各自只点亮一条，干净。
    #    这跟「判据不许重复」是同一条规则的两种表现：**要么判据不重复，
    #    要么用例自己把两件事分开注入**。
    # ---------------------------------------------------------------
    # B109：只把数据行的格式串列序改掉（color 与 extra 两个转换符换位），
    #   实参表达式一个字不动。这是最现实的一种改动（有人觉得 extra 该靠前）。
    #   后果：格式串拿 %-5u 去接一个 NSString* → 模拟器严格拒绝并抛错，
    #   解析器就算能读也是错位的。**只点 S14g**（S14h 判实参，没动）。
    one_replace("B109 C 端数据行格式串把 color 与 extra 两个转换符换位",
                '"%-5u %-7u %d     %d   %-8s  %-5u  %s\\n",\n'
                '                     e->seq, e->ms, (int)e->ready, (int)e->dec,\n'
                '                     SVPNColorName(e->colorKind), e->extra, SVPNEventName(e->ev));',
                '"%-5u %-7u %d     %d   %-5u  %-8s  %s\\n",\n'
                '                     e->seq, e->ms, (int)e->ready, (int)e->dec,\n'
                '                     SVPNColorName(e->colorKind), e->extra, SVPNEventName(e->ev));',
                ["S14g C 端数据行格式串渲染出的真行能被解析器正确读出"]),
    # B110：只把 e->seq 与 e->ms 两个同类型 %u 互换，格式串一个字不动。
    #   ★ 这条是 S14h 存在的唯一理由：转换符序列完全不变（都是 %u %u），
    #     渲染出来的行按位置填值也不变 → **S14g 全程绿**。
    #     只有钉住实参表达式才能发现。没有这条注入，S14h 就是个摆设。
    one_replace("B110 C 端数据行把 seq 与 ms 两个 %u 互换（转换符序列不变，S14g 拦不住）",
                "                     e->seq, e->ms, (int)e->ready, (int)e->dec,",
                "                     e->ms, e->seq, (int)e->ready, (int)e->dec,",
                ["S14h C 端数据行实参的语义顺序"]),
    # B111：首行关键字 total= 改成 cnt=（解析器按 total= 匹配 → 找不到首行）
    one_replace("B111 C 端首行把 total= 改成 cnt=（解析器的头正则匹配不上）",
                '"# SingleVPN diag v" SVPN_VERSION_STR "  total=%u kept=%u slots=%d\\n"',
                '"# SingleVPN diag v" SVPN_VERSION_STR "  cnt=%u kept=%u slots=%d\\n"',
                ["S14i C 端首行格式串"]),
    # B112：首行左侧不写版本号（v 后面直接跟 total）→ 解析出的 version 变空，
    #   与源码 SVPN_VERSION_STR 不符。这正是 S14i 断言 version 的用处。
    one_replace("B112 C 端首行漏掉版本号（解析出的 version 与源码对不上）",
                '"# SingleVPN diag v" SVPN_VERSION_STR "  total=%u kept=%u slots=%d\\n"',
                '"# SingleVPN diag v" "  total=%u kept=%u slots=%d\\n"',
                ["S14i C 端首行格式串"]),

    # S14 组之外的收尾用例：B57（版本号）。
    # ★ B57 一直放在列表最后，但它属于 S9 版本号一致性，不属于 S14。
    #   分组注释必须和编号对得上，否则复盘时按编号找判据会找不到。

    # ---------------------------------------------------------------
    # S9 版本号一致性
    # ---------------------------------------------------------------
    # ★ 不写死版本号的字面量：写死 "2.2-4" 的话，每次升版这条用例
    #   就在锚点断言处直接报「命中 0 次」而整轮中断 —— 那是用例自己过期，
    #   不是判据失效。改成「把任意版本号改成另一个」。
    ("B57 版本号三处不一致（mkdeb 报的版本与源码对不上）",
     "mk", _bump_mk_version, "S9a 三处版本号一致"),
]


def restore_all():
    for k, p in FILES.items():
        io.open(p, "w", encoding="utf-8", newline="").write(_orig[k])


# ---------------------------------------------------------------------------
# 取数函数自身必须先被验一遍。
#
# 为什么放在 main() 最前面：`_fail_block_of` 是全部用例断言的**唯一**依据。
# 它一旦取错，整轮的「已拦下」都可能是假的（假 FAIL 会让人去改判据，
# 假 PASS 更坏 —— 会让人相信一个坏判据是好的）。这跟「闸门自己也要进闸门」
# 是同一件事，只不过这次的工具是几行字符串处理，最容易被当成「显然正确」。
#
# ★ 用例必须是**真实形态**的样本，不能是我随手编的好看输入：
#   第 ② 例就是 B104 实际炸出来的那种输出（detail 里含「失败：」）。
# ---------------------------------------------------------------------------
def _selfcheck_fail_block():
    cases = [
        # (名字, 输入, 必须含, 必须不含)
        ("正常：两行都能取到",
         "通过 106 项，失败 2 项\n失败：\n"
         "  - S14g C 端数据行格式串 模拟/解析失败：ValueError(...)\n"
         "  - S14i C 端首行格式串 解析抛了 SystemExit()\n",
         ["S14g", "S14i"], ["S1a"]),
        # ★ B104 回归：detail 里含「失败：」不能把前面的失败行切掉
        ("detail 含「失败：」仍能取全",
         "通过 106 项，失败 2 项\n失败：\n"
         "  - S14d 导出行的字段顺序与解析器一致（行为级） 解析抛了 SystemExit('...')\n"
         "  - S14g C 端数据行格式串 模拟/解析失败：ValueError(\"...\")\n",
         ["S14d", "S14g"], []),
        ("全绿时返回空（防 expect 蒙对）",
         "  OK   S14d 导出行的字段顺序 解析结果 {...}\n通过 106 项，失败 0 项\n全部通过\n",
         [], ["S14d", "通过"]),
        ("汇总行之前的 '- ' 行不被当失败",
         "  OK   S12a 环形缓冲  -> 细节:\n  - 这不是失败行\n"
         "通过 106 项，失败 1 项\n失败：\n  - S12d 落盘被 @try 包住 没包\n",
         ["S12d"], ["这不是失败行"]),
        ("崩在汇总行之前（语法错）→ 不崩，返回空",
         'Traceback (most recent call last):\nSyntaxError: invalid syntax\n',
         [], []),
    ]
    bad = []
    for name, src, must, mustnot in cases:
        got = _fail_block_of(src)
        if not all(s in got for s in must) or any(s in got for s in mustnot):
            bad.append("%s → %r" % (name, got[:160]))
    return bad


# ---------------------------------------------------------------------------
# 严格模式的判定函数也自己验一遍。
#
# 严格模式做的事是「失败判据集合必须恰好等于期望集合」。它有两个可能坏的
# 方向，两个都必须实测：
#   ① 太松（退化成包含判断）→ 一次注入点亮两条也照样通过，严格就白加了；
#   ② 太紧（任何输入都判不等）→ 新用例整轮中断，看起来像判据失效。
# 所以下面左右各给一例。
# ---------------------------------------------------------------------------
def _names_of(block):
    return [ln.split()[0] for ln in block.splitlines() if ln.strip()]


def _selfcheck_strict_match():
    """返回坏掉的用例描述列表（空 = 全过）。"""
    bad = []

    def expect_fail(desc, got, want):
        # 期望「不相等」（严格模式必须拒绝）
        if got == want:
            bad.append("%s：本该拒绝却判等（太松）%s" % (desc, got))

    def expect_pass(desc, got, want):
        if got != want:
            bad.append("%s：本该判等却拒绝（太紧）%s vs %s" % (desc, got, want))

    a = _names_of("S14g xxx\n")
    b = _names_of("S14g xxx\nS14h yyy\n")
    c = _names_of("")

    expect_fail("多条 vs 单条", b, a)          # 点两条、期望一条 → 必须拒
    expect_fail("单条 vs 两条", a, b)          # 点一条、期望两条 → 必须拒
    expect_fail("有失败 vs 期望空", a, c)      # 有失败、期望空 → 必须拒
    expect_pass("完全一致", b, _names_of("S14g xxx\nS14h yyy\n"))
    expect_pass("都空", c, [])
    return bad


def main():
    _sc = _selfcheck_fail_block()
    assert not _sc, (
        "★ 取数函数 _fail_block_of 自己坏了 —— 下面所有用例的断言都不可信，先修它：\n  "
        + "\n  ".join(_sc))
    _sm = _selfcheck_strict_match()
    assert not _sm, (
        "★ 严格模式判定自己坏了（太松或太紧），先修它：\n  " + "\n  ".join(_sm))
    print("前置：取数函数自检 %d 例 + 严格模式自检 %d 例全过 ✓" % (5, 5))

    rc, out = run_check()
    assert rc == 0, "干净态自检就挂了，先修：\n" + out[-1500:]
    print("前置：干净态自检全绿 ✓\n")

    passed = 0
    n_strict = 0
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
            fail_block = _fail_block_of(out)
            if isinstance(expect, (list, tuple)):
                # ★ 严格模式：失败判据集合必须**恰好**是列出的这些。
                #   多一条少一条都算失败 —— 多一条说明这次注入同时触碰了两件
                #   事（无法断言是哪条拦下的），少一条说明列出的判据没生效。
                got_names = [ln.split()[0] for ln in fail_block.splitlines() if ln.strip()]
                want_names = [e.split()[0] for e in expect]
                assert got_names == want_names, (
                    "%s：期望失败集合恰好 %s，实际 %s\n%s"
                    % (name, want_names, got_names, fail_block[:700]))
                n_strict += 1
                shown = "、".join(want_names)
            else:
                assert expect in fail_block, (
                    "%s：该报的是「%s」，实际失败列表：\n%s" % (name, expect, fail_block[:700]))
                shown = expect
            # ③ 还原并确认干净
            restore_all()
            rc2, out2 = run_check()
            assert rc2 == 0, "%s：还原后自检仍挂：\n%s" % (name, out2[-800:])
            mark = "  ✓ %-50s 已拦下（%s）" % (name, shown)
            if isinstance(expect, (list, tuple)):
                mark += "  [严格]"
            print(mark)
            passed += 1
    finally:
        restore_all()

    rc, out = run_check()
    assert rc == 0, "最终复跑不干净：\n" + out[-1200:]
    for k, p in FILES.items():
        assert io.open(p, encoding="utf-8", newline="").read() == _orig[k], "文件没还原: " + k
    print("\n✓ 反向验证全过（%d 例，零 SKIP，其中 %d 例严格断言失败集合恰好相等），"
          "%d 个文件均已还原且复跑干净" % (passed, n_strict, len(FILES)))


if __name__ == "__main__":
    main()