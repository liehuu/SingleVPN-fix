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
    one_replace("A18c 闸门开在后台队列", "dispatch_get_main_queue(), ^{",
                "dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_LOW, 0), ^{",
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
