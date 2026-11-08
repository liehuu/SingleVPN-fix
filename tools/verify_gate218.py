"""2.1-8 新增判据的反向验证（零 SKIP）。

三段式：① 断言注入真的发生了（文件字节变了）
        ② 断言「是哪条判据拦下的」（不能只看退出码非 0）
        ③ finally 还原，全部跑完再复跑一次自检，确认干净

★ 就地改 SingleVPN.x，不复制 /tmp（Git Bash 下 /tmp 映射异常，
  曾出现 cp 失败但脚本继续跑、反向验证实际测的是原文件的事故）。
"""
import io
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_REPO, "SingleVPN.x")
CHECK = os.path.join(_REPO, "tools", "check_source.py")
PY = sys.executable

_orig = io.open(SRC, encoding="utf-8", newline="").read()


def run_check():
    r = subprocess.run([PY, CHECK], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def mutate(m):
    """返回 (变异后文本, 注入标记)；标记用于确认注入真的发生。"""
    return m(_orig)


def one_replace(name, old, new, expect_gate, count=1):
    def fn(s):
        assert s.count(old) == count, "%s 锚点命中 %d 次（期望 %d）" % (name, s.count(old), count)
        return s.replace(old, new, count)
    return (name, fn, expect_gate)


CASES = [
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
    # 把第一个数据层 hook 的「闸门闭合」与 %orig 交换 → %orig 落进闸门内
    # （A1 括号仍配平，只有 A18d 该报）
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
]


def main():
    # 前置：干净态必须全绿，否则反向验证没有意义
    rc, out = run_check()
    assert rc == 0, "干净态自检就挂了，先修：\n" + out[-1500:]
    print("前置：干净态自检全绿 ✓\n")

    passed = 0
    try:
        for name, fn, expect in CASES:
            mutated = fn(_orig)
            assert mutated != _orig, "%s：注入没改变文件（反向验证会失真）" % name
            io.open(SRC, "w", encoding="utf-8", newline="").write(mutated)
            # ① 确认注入真的落盘
            on_disk = io.open(SRC, encoding="utf-8", newline="").read()
            assert on_disk == mutated, "%s：注入没写进磁盘" % name
            # ② 断言是哪条判据拦下的
            rc, out = run_check()
            fail_block = out.split("失败：")[-1] if "失败：" in out else ""
            assert rc != 0, "%s：注入后自检竟然全绿 = 判据是摆设" % name
            assert expect in fail_block, (
                "%s：该报的是「%s」，实际失败列表：\n%s" % (name, expect, fail_block[:600]))
            # ③ 还原并确认干净
            io.open(SRC, "w", encoding="utf-8", newline="").write(_orig)
            rc2, out2 = run_check()
            assert rc2 == 0, "%s：还原后自检仍挂：\n%s" % (name, out2[-800:])
            print("  ✓ %-38s 已拦下（%s）" % (name, expect))
            passed += 1
    finally:
        # 无论中途哪一步炸了，都必须还原源文件
        io.open(SRC, "w", encoding="utf-8", newline="").write(_orig)

    rc, out = run_check()
    assert rc == 0, "最终复跑不干净：\n" + out[-1200:]
    assert io.open(SRC, encoding="utf-8", newline="").read() == _orig, "源文件没还原"
    print("\n✓ 反向验证全过（%d 例，零 SKIP），源文件已还原且复跑干净" % passed)


if __name__ == "__main__":
    main()
