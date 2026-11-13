#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 SingleVPN 2.2-5 导出的诊断文件，判定「图标整块不见」坏在哪一层。

用法：
    python tools/read_diag.py <diag.txt>
    python tools/read_diag.py --selftest      # 自测：合成三份日志，验证分层判定能分开

★ 为什么要有这个脚本：
   诊断层的价值全在「读出来的东西能不能区分世界」。靠肉眼扫 256 行日志，
   很容易把「视图没建」和「系统没给色」看成同一件事 —— 那是这版诊断白做。
   把判定写死成代码，条件是显式的、可复算的、被别人复核时不用信我的眼。

★ 语义前提（读这份日志时最容易搞错的地方，必须记住）：
   color 列记的是 **hook 收到的入参颜色**，不是我们写出去的颜色。
   记录点故意放在染色判断**之前**，所以：
     · color=nil/CLEAR  → 系统本来就没给可用的色（不是我们弄没的）
     · color=opaque     → 系统给的是正常色，问题在我们处理之后
   如果哪天记录点被挪到判断之后，这个脚本的全部结论都会反过来。
   闸门 S12h 判的就是这条位置，改动会先在那里红。

★ 另一个坑（CI 上真实踩过，本地看不出来）：
   这个脚本会被 tools/check_source.py 的 S14 组当模块加载、又被当子进程跑。
   两条路都会经过 Python 的字节码缓存（__pycache__），而缓存的有效性判据是
   **(mtime, size)**，不是内容哈希 —— 「改字符不改长度」的改动在 size 上
   完全看不出差别，同秒内写入连 mtime 也一样，于是**读到旧字节码**。
   结果就是：注入缺陷以后闸门仍然绿。
   所以闸门里一律用 exec(compile()) 与 `python -B` + PYTHONPYCACHEPREFIX，
   彻底绕开缓存。这不是洁癖，是判据本身不能再被缓存骗一次。
"""

import os
import re
import sys
from collections import Counter, OrderedDict

# ---------------------------------------------------------------------------
# 事件名 → 层。名字必须与 SingleVPN.x 的 SVPNEventName() 逐字一致。
# ---------------------------------------------------------------------------
LAYER_VIEW = "view"    # 视图是否被创建 / 属性是否被下发
LAYER_DATA = "data"    # 系统给的数据（颜色）本身
LAYER_CTRL = "ctrl"    # 全局数据流 / 还原 / 闸门

EVENT_LAYER = {
    "StringView.applyStyleAttributes:": LAYER_VIEW,
    "StringView.setText:": LAYER_VIEW,
    "WifiSignal.applyStyleAttributes:": LAYER_VIEW,
    "WifiSignal.setActiveColor:": LAYER_VIEW,
    "WifiSignal.setInactiveColor:": LAYER_VIEW,
    "CellNetType.setText:prefix:...": LAYER_VIEW,
    "StatusBar._updateWithAggregatedData:": LAYER_CTRL,
    "StatusBar._updateWithData:completion:": LAYER_CTRL,
    "SVPNRestoreNow": LAYER_CTRL,
    "gate open": LAYER_CTRL,
}

# 用户实际报缺的是两类：「双卡信号条」和「4G / WiFi 文字」。
#
# ★ 为什么按「组」判而不是按「单项」判（第一版写错在这）：
#   单项为 0 是正常的 —— 网络类型文字没变化时 CellNetType.setText: 本来就不会
#   被调用；信号格数没变时 setActiveColor: 也可能不重发。拿单项判「视图没下发」
#   会把「这次本来就没变」误报成「视图没建」，方向完全带偏。
#   只有**一整组方法全部为 0** 才说明这条链路根本没走。
GROUP_WIFI = [
    "WifiSignal.setActiveColor:",
    "WifiSignal.setInactiveColor:",
    "WifiSignal.applyStyleAttributes:",
]
GROUP_TEXT = [
    "StringView.applyStyleAttributes:",
    "StringView.setText:",
    "CellNetType.setText:prefix:...",
]
CRITICAL = GROUP_WIFI + GROUP_TEXT

GOOD = "opaque"
BAD = ("CLEAR", "faint", "nil", "notRGB")


def parse(path):
    """返回 (header, rows)。row 是 OrderedDict。格式不符直接抛，不猜。"""
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # 日志全是 ASCII，出现非 UTF-8 说明不是这个文件（比如二进制）
        raise SystemExit("不是文本文件，或编码不对（长度 %d 字节）" % len(raw))

    lines = text.splitlines()
    if not lines:
        raise SystemExit("文件是空的 —— 导出没成功，或点按钮时插件没接住通知")

    m = re.match(
        r"^#\s*SingleVPN diag v([\w.\-]+)\s+total=(\d+)\s+kept=(\d+)\s+slots=(\d+)\s*$",
        lines[0],
    )
    if not m:
        raise SystemExit(
            "首行不是诊断文件头，收到的第一行是：\n  %r\n"
            "可能是：① 取错文件 ② 文件被别的程序覆盖 ③ 导出的还是旧版本" % lines[0]
        )
    header = {
        "version": m.group(1),
        "total": int(m.group(2)),
        "kept": int(m.group(3)),
        "slots": int(m.group(4)),
    }

    rows = []
    for ln in lines[1:]:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        # seq  ms  ready  dec  color  extra  event（event 可能含空格，取剩余全部）
        p = re.match(
            r"^(\d+)\s+(\d+)\s+([01])\s+([01])\s+(\S+)\s+(\d+)\s+(.+?)\s*$", s
        )
        if not p:
            raise SystemExit("有一行解析不了：\n  %r\n（格式变了？先对一下 SVPNExportDiag）" % s)
        rows.append(
            OrderedDict(
                seq=int(p.group(1)),
                ms=int(p.group(2)),
                ready=int(p.group(3)),
                dec=int(p.group(4)),
                color=p.group(5),
                extra=int(p.group(6)),
                ev=p.group(7),
            )
        )
    if not rows:
        raise SystemExit("文件头正常但一条事件都没有（total=%d）" % header["total"])
    return header, rows


def classify(header, rows):
    """把日志映射成「哪一层坏了」。返回 (verdict_key, 结论文本块)。"""
    by_ev = Counter(r["ev"] for r in rows)
    colors = {ev: Counter(r["color"] for r in rows if r["ev"] == ev) for ev in by_ev}

    unknown = [e for e in by_ev if e not in EVENT_LAYER]
    view_total = sum(by_ev.get(e, 0) for e in CRITICAL)
    ctrl_total = sum(v for e, v in by_ev.items() if EVENT_LAYER.get(e) == LAYER_CTRL)

    lines = []
    add = lines.append

    add("文件版本 %s  total=%d kept=%d slots=%d"
        % (header["version"], header["total"], header["kept"], header["slots"]))
    if header["total"] > header["kept"]:
        add("  ⚠ 缓冲已经绕圈了：最旧的 %d 条被覆盖。"
            "如果复现时刻早于日志起点，可能看不到当时那几条。"
            % (header["total"] - header["kept"]))
    if unknown:
        add("  ⚠ 有不认识的事件名 %s —— 日志不是这版插件写的，或 SVPNEventName 改过" % unknown)
    add("")

    add("各事件条数：")
    for ev in sorted(by_ev, key=lambda e: (-by_ev[e], e)):
        cc = colors[ev]
        add("  %-42s %4d   %s" % (ev, by_ev[ev], dict(cc)))
    add("")

    # ---- 判定 ----
    if ctrl_total == 0 and view_total == 0:
        return "none", lines + [
            "结论：一条视图层 / 数据层的记录都没有。",
            "  → 钩子没被调用。这不是颜色问题，是「插件根本没跑到状态栏这条路上」。",
            "  先确认：设置里 Diagnostics 段在不在（在=装上了）；",
            "  再看 ready 列是不是一直 0（=闸门没打开，可能没读到偏好）。",
        ]

    if view_total == 0 and ctrl_total > 0:
        return "view_absent", lines + [
            "结论：数据流事件有 %d 条，但视图层事件 0 条。" % ctrl_total,
            "  → 【视图层】状态栏在更新，但那些视图的方法一次都没被调用。",
            "  最可能：视图没被创建出来，或系统走了另一条不下发样式的路径。",
            "  下一步不要改染色（颜色再对也没东西可染），要查视图创建。",
        ]

    dead_groups = [
        (name, grp) for name, grp in (("WiFi 图标组", GROUP_WIFI), ("文字组", GROUP_TEXT))
        if all(by_ev.get(e, 0) == 0 for e in grp)
    ]
    if dead_groups:
        names = "、".join(n for n, _ in dead_groups)
        return "view_partial", lines + [
            "结论：视图层整组缺失 —— %s 的方法一条都没被调用。" % names,
            "  → 【视图层，局部】这一组视图在观察窗口内完全没被下发。",
            "  若缺失的恰好是用户报「不见了」的那几个，就是问题所在；",
            "  没被调用的方法不可能由染色引起空白。",
            "  ⚠ 也可能是复现时刻不在缓冲窗口内（看上面 kept/total 有没有绕圈）。",
        ]

    # 到这里：所有关键视图都有调用。看系统给的色。
    all_c = Counter()
    for e in CRITICAL:
        all_c.update(colors.get(e, Counter()))
    total_c = sum(all_c.values())
    bad_n = sum(all_c.get(k, 0) for k in BAD)
    good_n = all_c.get(GOOD, 0)

    add("关键视图入参颜色分布：%s（共 %d 条）" % (dict(all_c), total_c))
    add("")

    if total_c and bad_n * 2 >= total_c:
        return "data_bad", lines + [
            "结论：视图都被调用了，但系统给的色多半是 %s（%d/%d）。"
            % ([k for k in BAD if all_c.get(k)], bad_n, total_c),
            "  → 【数据层】系统本来就没给可用的色，我们只是把它传下去。",
            "  改染色代码没用（我们没造这个值）。要查的是：为什么这个时刻",
            "  系统给的是透明/接近透明 —— 例如该状态栏实例正处于隐藏/推送中状态。",
        ]

    if good_n:
        return "color_ours", lines + [
            "结论：视图都被调用了，系统给的也多半是正常不透明色（%d/%d）。"
            % (good_n, total_c),
            "  → 【颜色层】输入是好的，问题在我们处理之后写出去的值。",
            "  这一层才是「改染色代码」能解决的。重点看 svpnReplaceKeepingAlpha",
            "  的返回路径与 _lightReplacementColor / _darkReplacementColor 的取值。",
        ]

    return "unclear", lines + [
        "结论：无法归到三层中的任何一层（入参颜色分布 %s）。" % dict(all_c),
        "  把原始文件发我，别先改代码。",
    ]


VERDICT_TITLE = {
    "none": "钩子没跑到（不是颜色问题）",
    "view_absent": "视图层 —— 视图级方法一次都没被调用",
    "view_partial": "视图层（局部）—— 部分视图没被下发",
    "data_bad": "数据层 —— 系统给的色本来就是透明",
    "color_ours": "颜色层 —— 输入正常，我们写出去的不对",
    "unclear": "未定",
}


def report(path):
    header, rows = parse(path)
    key, block = classify(header, rows)
    print("=" * 74)
    print("判定：%s   [%s]" % (VERDICT_TITLE[key], key))
    print("=" * 74)
    for ln in block:
        print(ln)
    print()
    print("★ 别忘语义前提：color 列是 **hook 收到的入参**，记录点在染色判断之前。")
    print("  记录点若被挪到判断之后，上面所有结论都会反过来（闸门 S12h 会先红）。")
    return key


# ---------------------------------------------------------------------------
# 自测：合成四份日志，断言分层判定真的分得开。
# 这一步不是形式 —— 判据分不开世界，等于诊断层白做。
# ---------------------------------------------------------------------------
def _synth(total, rows, ver="2.2-5", slots=256):
    out = ["# SingleVPN diag v%s  total=%d kept=%d slots=%d" % (ver, total, len(rows), slots),
           "# seq  ms      ready dec color     extra  event"]
    for i, (color, ev, dec, extra) in enumerate(rows, 1):
        out.append("%-5d %-7d 1     %d   %-8s  %-5d  %s" % (i, i * 3, dec, color, extra, ev))
    return "\n".join(out) + "\n"


def selftest():
    import tempfile

    cases = []

    # ① 视图层：只有数据流事件
    cases.append((
        "view_absent",
        _synth(3, [("nil", "gate open", 0, 0),
                   ("nil", "StatusBar._updateWithAggregatedData:", 1, 1),
                   ("nil", "StatusBar._updateWithData:completion:", 1, 1)]),
    ))
    # ② 视图层局部：整组 WiFi 一条都没有（文字组正常）
    cases.append((
        "view_partial",
        _synth(3, [("opaque", "StringView.applyStyleAttributes:", 1, 0),
                   ("opaque", "StringView.setText:", 1, 3),
                   ("opaque", "CellNetType.setText:prefix:...", 1, 0)]),
    ))
    # ③ 数据层：全部被调用，但系统给的近乎透明
    cases.append((
        "data_bad",
        _synth(5, [("CLEAR", "WifiSignal.setActiveColor:", 1, 0),
                   ("CLEAR", "WifiSignal.setInactiveColor:", 1, 0),
                   ("CLEAR", "WifiSignal.applyStyleAttributes:", 1, 0),
                   ("faint", "StringView.applyStyleAttributes:", 1, 0),
                   ("opaque", "CellNetType.setText:prefix:...", 1, 0)]),
    ))
    # ④ 颜色层：全部正常不透明
    cases.append((
        "color_ours",
        _synth(4, [("opaque", "WifiSignal.setActiveColor:", 1, 0),
                   ("opaque", "WifiSignal.setInactiveColor:", 1, 0),
                   ("opaque", "WifiSignal.applyStyleAttributes:", 1, 0),
                   ("opaque", "StringView.applyStyleAttributes:", 1, 0)]),
    ))
    # ⑤ ★ 反向：组内只缺一项（网络类型文字没变）不能判成视图缺失。
    #    这正是第一版判据误报的情形 —— 单项为 0 是常态，不是故障。
    cases.append((
        "color_ours",
        _synth(3, [("opaque", "WifiSignal.setActiveColor:", 1, 0),
                   ("opaque", "WifiSignal.setInactiveColor:", 1, 0),
                   ("opaque", "StringView.applyStyleAttributes:", 1, 0)]),
    ))

    ok = 0
    fail = []
    for want, text in cases:
        fd, p = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            header, rows = parse(p)
            got, _ = classify(header, rows)
        finally:
            os.remove(p)
        if got == want:
            ok += 1
            print("  OK   %-14s ← 合成日志被判成 %s" % (want, got))
        else:
            fail.append((want, got))
            print("  FAIL %-14s ← 期望 %s，实际 %s" % (want, want, got))

    # ⑤ 空/坏文件必须报错退出，不能返回一个「看起来正常」的结论
    for name, text in (("空文件", ""),
                       ("不是诊断文件", "hello world\n"),
                       ("头正常但无事件", "# SingleVPN diag v2.2-5  total=0 kept=0 slots=256\n")):
        fd, p = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        try:
            parse(p)
        except SystemExit:
            ok += 1
            print("  OK   %-14s ← 正确拒绝（抛 SystemExit）" % name)
        except Exception as e:
            fail.append((name, repr(e)))
            print("  FAIL %-14s ← 抛了 %r，不是 SystemExit" % (name, e))
        else:
            fail.append((name, "没报错"))
            print("  FAIL %-14s ← 竟然解析成功了" % name)
        finally:
            os.remove(p)

    print()
    if fail:
        print("✗ 自测失败 %d 例：%s" % (len(fail), fail))
        return 1
    print("✓ 自测全过（%d 例）：四种世界都能分开，坏文件都会被拒绝" % ok)
    return 0


def main(argv):
    if len(argv) == 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) != 2:
        print(__doc__)
        return 2
    if not os.path.exists(argv[1]):
        raise SystemExit("文件不存在：%s" % argv[1])
    report(argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))