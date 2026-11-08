"""2.1-8 / 2.1-9 新增判据的反向验证（零 SKIP）。

三段式：① 断言注入真的发生了（文件字节变了）
        ② 断言「是哪条判据拦下的」（不能只看退出码非 0）
        ③ finally 还原全部文件，跑完再复跑一次自检，确认干净

★ 就地改文件，不复制 /tmp（Git Bash 下 /tmp 映射异常，
  曾出现 cp 失败但脚本继续跑、反向验证实际测的是原文件的事故）。
★ 覆盖两个文件：SingleVPN.x（代码闸门）与 devkit/mkdeb.sh（装包红线闸门）。
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
    def fn(s):
        assert s.count(old) == count, "%s 锚点命中 %d 次（期望 %d）" % (name, s.count(old), count)
        return s.replace(old, new, count)
    return (name, file, fn, expect_gate)


def replace_first(name, old, new, expect_gate, file="x"):
    """锚点在文件里出现多次时，只注入第一处。

    one_replace 的 count 同时约束「锚点出现次数」和「替换次数」，
    两个数据层 hook 写法完全相同时没法只改一处 → 用它。
    """
    def fn(s):
        assert old in s, "%s 锚点未命中" % name
        return s.replace(old, new, 1)
    return (name, file, fn, expect_gate)


CASES = [
    # ---- SingleVPN.x：启动闸门 / 诊断 / 条件块 ----
    one_replace("A8 诊断被误开", "#define SVPN_DIAG 0", "#define SVPN_DIAG 1",
                "SVPN_DIAG 为 0"),
    one_replace("A18a 启动窗口缩到 1 秒", "#define SVPN_READY_DELAY_SEC 5",
                "#define SVPN_READY_DELAY_SEC 1", "A18a 启动窗口"),
    one_replace("A18a 启动窗口常量被删", "#define SVPN_READY_DELAY_SEC 5",
                "// #define SVPN_READY_DELAY_SEC 5", "A18a 启动窗口"),
    one_replace("A18b 闸门变量丢 volatile", "static volatile int gSVPNReady = 0;",
                "static int gSVPNReady = 0;", "A18b 闸门变量"),
    # ★ 锚点必须带上一行 dispatch_after：2.1-10 的还原函数里也有
    #   dispatch_async(dispatch_get_main_queue(), ^{ ，裸锚点会命中 2 次。
    one_replace("A18c 闸门开在后台队列",
                "        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(SVPN_READY_DELAY_SEC * NSEC_PER_SEC)),\n"
                "        dispatch_get_main_queue(), ^{",
                "        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(SVPN_READY_DELAY_SEC * NSEC_PER_SEC)),\n"
                "        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_LOW, 0), ^{",
                "A18c ctor 里用 dispatch_after"),
    one_replace("A18c 闸门永远是关的", "gSVPNReady = 1;", "gSVPNReady = 0;",
                "A18c ctor 里用 dispatch_after"),
    one_replace("A18d %orig 落进闸门内（窗口内连系统更新都被跳过）",
                "    }\n    %orig;\n}\n\n- (void)_updateWithData:",
                "    %orig;\n    }\n}\n\n- (void)_updateWithData:",
                "A18d 两个数据层 hook"),
    one_replace("A18e 漏掉一处染色分支",
                "BOOL decision = gSVPNReady && (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled);",
                "BOOL decision = (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled);",
                "A18e iOS17 视图层", count=5),
    one_replace("A18f 5GA 主判断漏闸门",
                'if (!gSVPNReady || ![text hasPrefix:@"5G"] || !_isForce5GAEnabled) {',
                'if (![text hasPrefix:@"5G"] || !_isForce5GAEnabled) {',
                "A18f 5GA 路径"),
    one_replace("A18g iOS16 基线被污染",
                "%group SingleVPN_16\n",
                "%group SingleVPN_16\n// gSVPNReady\n",
                "A18g iOS16 分支"),
    one_replace("A19a 诊断块少一个 endif（条件块不配平）",
                "    SVPNLog(@\"%@\", s);\n}\n\n#endif\n// ============================================================================",
                "    SVPNLog(@\"%@\", s);\n}\n\n// #endif removed\n// ============================================================================",
                "A19a 条件块配平"),
    one_replace("A19b 诊断调用漏在 #if 块外（一关就编不过）",
                "    NSDictionary *settings = [prefs dictionaryRepresentation];",
                "    SVPNLog(@\"inject\");\n    NSDictionary *settings = [prefs dictionaryRepresentation];",
                "A19b 块外无残留诊断调用"),
    one_replace("A19c 关诊断后少一处 orig（功能骨架不完整）",
                "    }\n    %orig;\n}\n\n- (void)_updateWithData:",
                "    }\n    // orig removed\n}\n\n- (void)_updateWithData:",
                "A19c 关掉诊断后功能骨架完整"),

    # ---- SingleVPN.x：VPN 状态判定（2.1-9 修复断开不恢复）----
    replace_first("A22a 状态又退回上一轮 OR（断开 VPN 不恢复）",
                  "        if (vpnNow) { _isVPNEnabled = vpnNow.enabled; }",
                  "        _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;",
                  "A22a 两个数据层 hook"),
    replace_first("A22b 数据层多出一处状态写入",
                  "        if (vpnNow) { _isVPNEnabled = vpnNow.enabled; }",
                  "        if (vpnNow) { _isVPNEnabled = vpnNow.enabled; }\n"
                  "        _isVPNEnabled = YES;",
                  "A22b 数据层没有第三处状态写入"),
    replace_first("A22c 上一轮快照声明漏回诊断块",
                  "        if (vpnNow) { _isVPNEnabled = vpnNow.enabled; }",
                  "        STStatusBarData *currentData = [self currentData];\n"
                  "        if (vpnNow) { _isVPNEnabled = vpnNow.enabled; }",
                  "A22c 关诊断后数据层实代码里不含上一轮快照"),

    # ---- SingleVPN.x：2.1-10 视图层还原（断开 VPN 后桌面染色残留）----
    # 每条都对着 A23 里一条判据；断言「是哪条判据拦下的」而不是只看退出码。
    one_replace("A23a1 还原函数被塞进 iOS16 分组（污染上游基线）",
                "\n%end // SingleVPN_16",
                "\nstatic void SVPNRestorePaintedViewsNow(void) { }\n%end // SingleVPN_16",
                "A23a1 还原模块在 iOS17 分组之外"),
    one_replace("A23a2 还原表退回弱引用默认 personality（可能按 isEqual 合并视图）",
                "            NSPointerFunctionsWeakMemory | NSPointerFunctionsObjectPointerPersonality;",
                "            NSPointerFunctionsWeakMemory;",
                "A23a2 还原表 key 用 weak"),
    one_replace("A23a3 边遍历 NSMapTable 边还原（未定义行为）",
                "        for (id v in gSVPNOriginals) {\n            if (v) {",
                "        for (id v in gSVPNOriginals) {\n            [gSVPNOriginals removeObjectForKey:v];\n            if (v) {",
                "A23a3 收集循环体内不修改登记表"),
    one_replace("A23a4 还原前不判 respondsToSelector（unrecognized selector 崩宿主）",
                "            if (!sel || ![view respondsToSelector:sel]) {",
                "            if (!sel) {",
                "A23a4 每条 slot 还原前先 respondsToSelector"),
    one_replace("A23a5 视图登记表去掉了上限（无界增长）",
                "#define SVPN_RESTORE_MAX_VIEWS 32",
                "// #define SVPN_RESTORE_MAX_VIEWS 32",
                "A23a5 寄存器表上限有界"),
    one_replace("A23a6 还原计划混入没有视图 setter 的死槽位 dim",
                '        @[@"tint", @"setTintColor:"],\n    ];',
                '        @[@"tint", @"setTintColor:"],\n        @[@"dim", @"setImageDimmedTintColor:"],\n    ];',
                "A23a6 还原计划只含可回写的槽位"),
    one_replace("A23b1 去掉残留识别（会把绿色当系统色存下来 → 还原成绿色）",
                "        if (last && [last isEqual:color]) {\n            return;\n        }",
                "        if (NO) {\n            return;\n        }",
                "A23b1 识别残留"),
    replace_first("A23b2 一个数据层 hook 漏掉过渡检测（一半断开场景不还原）",
                  "        SVPNNoteDecisionTransition();\n",
                  "\n",
                  "A23b2 两个数据层 hook 都触发过渡检测"),
    one_replace("A23b3 过渡条件写成「变了就擦」（连上时也擦色）",
                "    if (prev == 1 && !now) {",
                "    if (prev != (now ? 1 : 0)) {",
                "A23b3 只在 1→0 时触发"),
    one_replace("A23b4 还原前不复核当前状态（断开又秒连会擦掉新染的色）",
                "    if (_isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled) {\n        return;\n    }\n    if (!gSVPNOriginals) {",
                "    if (!gSVPNOriginals) {",
                "A23b4 还原前重新确认当前不该染"),
    one_replace("A23b5 还原丢掉启动闸门判断（窗口内也会介入视图）",
                "    if (!gSVPNReady) {\n        return;\n    }\n    // ④ 延后执行期间可能已经重新连上",
                "    // ④ 延后执行期间可能已经重新连上",
                "A23b5 还原有独立闸门"),
    one_replace("A23c1 少记一个染色点的系统原色（该图标断开后还原不回来）",
                '        SVPNRememberOriginal(self, @"ac", activeColor);\n',
                "",
                "A23c1 五个染色点都记系统原色"),
    one_replace("A23c2 少记一个染色点染上的色（残留识别失效）",
                '        SVPNRememberPainted(self, @"ic", paint);\n',
                "",
                "A23c2 每个染色点同时记染上的色"),
    one_replace("A23c3 applyStyleAttributes 先 copy 后取原色（记下的是自己染的色）",
                '        UIColor *sysTextColor = styleAttrs.textColor;\n        styleAttrs = [styleAttrs copy];',
                '        styleAttrs = [styleAttrs copy];\n        UIColor *sysTextColor = styleAttrs.textColor;',
                "A23c3 StringView 侧先取原色"),
    one_replace("A23d 还原路径改成主动重绘宿主（最危险的一类改动）",
                "        @try {\n            [view setNeedsDisplay];",
                "        @try {\n            [(id)view _updateWithData:nil completionHandler:nil];\n"
                "            [view setNeedsDisplay];",
                "A23d 还原路径不主动重绘宿主"),

    # ---- devkit/mkdeb.sh：装包红线 ----
    one_replace("A20 postinst 混入 sbreload（会杀前台 Sileo → 半装）",
                "printf '\\n'\nexit 0\nEOF",
                "printf '\\n'\nif [ -x /usr/bin/sbreload ]; then /usr/bin/sbreload; fi\nexit 0\nEOF",
                "A20 postinst 不执行 sbreload", file="mk"),
    one_replace("A20 postinst 混入 killall SpringBoard",
                "printf '\\n'\nexit 0\nEOF",
                "printf '\\n'\nkillall SpringBoard || true\nexit 0\nEOF",
                "A20 postinst 不执行 killall", file="mk"),
    one_replace("A21a _tar 不再跳过 DEBIAN（data.tar 会解到只读根）",
                "dirs[:] = [d for d in dirs if d != 'DEBIAN']",
                "dirs[:] = [d for d in dirs]",
                "A21a _tar() 跳过 DEBIAN", file="mk"),
    one_replace("A21b 构建期解包回验闸门被删",
                "    raise SystemExit('data.tar 含 DEBIAN 条目，装包会炸: %s' % _bad[:5])",
                "    pass  # 闸门被删",
                "A21b 有构建期闸门解包回验", file="mk"),
    one_replace("A17 写模式 tar 丢了 format（会产出 PAX 头）",
                "with tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT) as tf:",
                "with tarfile.open(fileobj=buf, mode='w') as tf:",
                "mkdeb.sh 写模式的 tarfile.open 都指定了 format", file="mk"),
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
            # ① 确认注入真的落盘
            assert io.open(path, encoding="utf-8", newline="").read() == mutated, \
                "%s：注入没写进磁盘" % name
            # ② 断言是哪条判据拦下的
            rc, out = run_check()
            fail_block = out.split("失败：")[-1] if "失败：" in out else ""
            assert rc != 0, "%s：注入后自检竟然全绿 = 判据是摆设" % name
            assert expect in fail_block, (
                "%s：该报的是「%s」，实际失败列表：\n%s" % (name, expect, fail_block[:600]))
            # ③ 还原并确认干净
            restore_all()
            rc2, out2 = run_check()
            assert rc2 == 0, "%s：还原后自检仍挂：\n%s" % (name, out2[-800:])
            print("  ✓ %-44s 已拦下（%s）" % (name, expect))
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
