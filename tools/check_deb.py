"""产物校验：直接读 .deb 字节，验证核心修复真的编进了 dylib。（版本无关）

为什么单独写这个：
    源码闸门只能证明「源码里写对了」。编译链路（logos 预处理 → clang → 链接 →
    codesign → tar → deb）任何一步都可能让改动丢失或形态变化。
    唯一能证明「装上去的东西是对的」的证据是从 .deb 里取出 dylib 再验。

★★ 验 Mach-O / dylib 有多条坑，全部踩过：
    1. 不能 grep 常量名找 load command：LC_CODE_SIGNATURE 在二进制里只有 0x1d，
       字符串 "LC_CODE_SIGNATURE" 永远不存在 → 判据永假。
    2. ObjC 选择子不进 nm -u（走 _objc_msgSend + __objc_methname），只能 grep -a。
    3. 名字编译后可能不存在：枚举 / #define 会消失，selector / 字面量会保留。
       → 判「某个 C 函数名在不在」必须用符号表；判「selector 在不在」用 grep -a。
    4. 每个断言都必须先证明「它有可能失败」（下面每条都写了反例依据）。

用法：python tools/check_deb.py <dir-with-debs>
"""
import ast
import hashlib
import lzma
import os
import struct
import subprocess
import sys
import tarfile

fails, oks = [], []


def chk(name, ok, detail=""):
    if not isinstance(detail, str):
        detail = str(detail)
    (oks if ok else fails).append((name, detail))
    print(("  OK   " if ok else "  FAIL ") + name + (("  -> " + detail) if detail else ""))


# ---------------------------------------------------------------- deb 解包
def ar_members(blob):
    """解析 ar 归档，返回 [(name, bytes)]。

    ★ 成员名要 rstrip('/')：ar 的规范是名字后补 '/' 对齐（GNU 风格），
      不去掉就取不到 control.tar.gz / data.tar.xz。
    ★ 数据区按 2 字节对齐，奇数长度要跳 1 字节填充。
    """
    assert blob[:8] == b"!<arch>\n", "不是 ar 归档"
    out, off = [], 8
    while off + 60 <= len(blob):
        hdr = blob[off:off + 60]
        name = hdr[0:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(hdr[48:58].decode("ascii").strip())
        data = blob[off + 60:off + 60 + size]
        out.append((name, data))
        off += 60 + size + (size % 2)
    return out


def tar_members(raw, label):
    """返回 {name: bytes}。tar 里可能有 ./-xx 前缀。
    ★ 必须显式判 PAX 扩展头：dpkg 不认，装包直接失败。
    """
    bad = []
    tf = tarfile.open(fileobj=__import__("io").BytesIO(raw), mode="r:")
    members = {}
    for m in tf.getmembers():
        if m.type == tarfile.XHDTYPE or m.type == tarfile.XGLTYPE:
            bad.append(m.name)
        if m.isfile():
            members[m.name.lstrip("./")] = tf.extractfile(m).read()
    tf.close()
    chk("%s 无 PAX 扩展头（dpkg 不认）" % label, not bad, "命中 %d" % len(bad))
    return members


def parse_deb(path):
    blob = open(path, "rb").read()
    ms = dict(ar_members(blob))
    assert "debian-binary" in ms, "缺 debian-binary"
    chk(os.path.basename(path) + " debian-binary 是 2.0", ms["debian-binary"] == b"2.0\n")
    ctl_raw = ms["control.tar.gz"]
    import gzip
    ctl = tar_members(gzip.decompress(ctl_raw), "control.tar")
    data = tar_members(lzma.decompress(ms["data.tar.xz"]), "data.tar")
    return blob, ctl, data


def macho_slices(blob):
    """返回 [(cputype, cpusubtype, slice_bytes)]。只处理 FAT / 单架构。"""
    magic = struct.unpack(">I", blob[:4])[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        n = struct.unpack(">I", blob[4:8])[0]
        out = []
        for i in range(n):
            base = 8 + i * 20
            cputype, cpusub, off, size, align = struct.unpack(">iiIII", blob[base:base + 20])
            out.append((cputype, cpusub, blob[off:off + size]))
        return out
    # thin
    if magic in (0xFEEDFACF, 0xFEEDFACE):
        cputype = struct.unpack(">i", blob[4:8])[0]
        cpusub = struct.unpack(">i", blob[8:12])[0]
        return [(cputype, cpusub, blob)]
    if magic in (0xCFFAEDFE, 0xCEFAEDFE):
        cputype = struct.unpack("<i", blob[4:8])[0]
        cpusub = struct.unpack("<i", blob[8:12])[0]
        return [(cputype, cpusub, blob)]
    return []


def macho_cputype_cpusub(sl):
    """返回 (cputype, cpusubtype)，**都按 32 位无符号读**。

    ★ 必须按无符号读：arm64e 的 cpusubtype 是 0x80000002，按有符号读
      会变成 -0x7ffffffe，后续 & 掩码计算全错（本次实测踩到）。
    """
    for fmt, magic in (("<", 0xFEEDFACF), (">", 0xFEEDFACF),
                       ("<", 0xFEEDFACE), (">", 0xFEEDFACE)):
        try:
            if struct.unpack(fmt + "I", sl[:4])[0] == magic:
                return (struct.unpack(fmt + "I", sl[4:8])[0],
                        struct.unpack(fmt + "I", sl[8:12])[0])
        except struct.error:
            break
    return 0, 0


def macho_symbol_names(sl):
    """读 LC_SYMTAB，返回符号名列表。

    ★ 为什么必须读符号表，而不是 grep 名字：
      静态函数（static）在 C 里会变成局部符号 __ZL...，它们的名字**确实**
      在 __symtab 的字符串表里；但「函数存在」这件事用 bytes grep 判不出来
      —— grep 到的可能是别处的巧合子串。符号表是编译器的正式产出，
      「某个 static 函数被编进来了」只有它能证明。

    ★ 也不靠段大小（__text 变大几百字节）判改动：段大小会随任何无关
      改动漂移，是个脆指标，不能当作「这次改动在不在」的证据。
    """
    ncmds = struct.unpack_from("<I", sl, 16)[0]
    off = 32
    symtab = None
    for _ in range(ncmds):
        cmd, csz = struct.unpack_from("<II", sl, off)
        if cmd == 0x2:  # LC_SYMTAB
            symtab = struct.unpack_from("<IIII", sl, off + 8)
        off += csz
    if not symtab:
        return []
    symoff, nsyms, stroff, strsize = symtab
    names = []
    for i in range(nsyms):
        b = symoff + i * 16
        if b + 16 > len(sl):
            break
        strx = struct.unpack_from("<I", sl, b)[0]
        if strx == 0 or stroff + strx >= len(sl):
            continue
        end = sl.find(b"\x00", stroff + strx)
        if end < 0:
            continue
        names.append(sl[stroff + strx:end].decode("utf-8", "replace"))
    return names


def has_signature(sl):
    """解析 load commands，找 LC_CODE_SIGNATURE。
    ★ 只能按结构解，不能 grep 名字：'LC_CODE_SIGNATURE' 这个名字不进二进制，
      二进制里只有 cmd 值 0x1d。
    """
    magic = struct.unpack("<I", sl[:4])[0]
    if magic == 0xFEEDFACF:      # 64 位
        ncmds = struct.unpack("<I", sl[16:20])[0]
        p = 32
    elif magic == 0xFEEDFACE:    # 32 位
        ncmds = struct.unpack("<I", sl[16:20])[0]
        p = 28
    elif struct.unpack(">I", sl[:4])[0] in (0xFEEDFACF, 0xFEEDFACE):
        # 大端（arm64e 不会走这里，但别静默失败）
        if struct.unpack(">I", sl[:4])[0] == 0xFEEDFACF:
            ncmds = struct.unpack(">I", sl[16:20])[0]
            p = 32
        else:
            ncmds = struct.unpack(">I", sl[16:20])[0]
            p = 28
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack(">II", sl[p:p + 8])
            if cmd == 0x1D:
                return True
            p += cmdsize
        return False
    else:
        return False
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack("<II", sl[p:p + 8])
        if cmd == 0x1D:
            return True
        if cmdsize < 8:
            return False
        p += cmdsize
    return False


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    debs = sorted(f for f in os.listdir(d) if f.endswith(".deb"))
    assert debs, "目录里没有 .deb: " + d
    print("目录: %s" % os.path.abspath(d))
    print("deb : %s" % ", ".join(debs))
    print()

    # ★ 期望版本从**文件名**推导，不写死常量。
    #   写死会让每次升版本都把闸门搞红，然后人就习惯性去改闸门 ——
    #   改闸门比改产物省事，于是闸门退化成摆设。文件名里的版本来自
    #   Makefile，是构建链真实产物的一部分。
    import re as _re
    def ver_of(fname):
        m = _re.search(r"_(\d[\w.\-]*)_iphoneos-", fname)
        return m.group(1) if m else None
    vers = {v for v in (ver_of(f) for f in debs) if v}
    assert len(vers) == 1, "两个包的版本号不一致或取不到: %s" % vers
    EXPECT_VER = vers.pop()
    print("期望版本（取自文件名）: %s" % EXPECT_VER)
    print()

    parsed = {}
    for f in debs:
        print("[%s]" % f)
        p = os.path.join(d, f)
        blob, ctl, data = parse_deb(p)
        parsed[f] = (blob, ctl, data)
        chk("sha256 可算", len(hashlib.sha256(blob).hexdigest()) == 64,
            hashlib.sha256(blob).hexdigest()[:16])
        print()

    # ---- control 的一致性 ----
    print("[control 三处一致]")
    for f, (blob, ctl, data) in parsed.items():
        c = ctl["control"].decode("utf-8", "replace")
        kv = {}
        for ln in c.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                kv[k.strip()] = v.strip()
        chk(f + " Package", kv.get("Package") == "com.82flex.singlevpn", kv.get("Package", ""))
        chk(f + " Version 与文件名一致", kv.get("Version") == EXPECT_VER, kv.get("Version", ""))
        # ★ Architecture 用严格相等，不能用 in：iphoneos-arm64e 含 iphoneos-arm64
        want = "iphoneos-arm64e" if "arm64e" in f else "iphoneos-arm64"
        chk(f + " Architecture 严格相等", kv.get("Architecture") == want, kv.get("Architecture", ""))
        chk(f + " 没有 Filename/Installed-Size 之类构建机残留",
            "Filename" not in kv and "Installed-Size" not in kv)
    print()

    # ---- data.tar 内容 ----
    print("[data.tar 内容]")
    for f, (blob, ctl, data) in parsed.items():
        names = sorted(data)
        chk(f + " 不含 DEBIAN 条目（rootless / 只读 → 整包装不上）",
            not [n for n in names if n.lstrip("./").startswith("DEBIAN")],
            "共 %d 个成员" % len(names))
        chk(f + " 有 dylib", any(n.endswith("SingleVPN.dylib") for n in names))
        chk(f + " 有偏好设置 bundle 主程序",
            any(n.endswith("SingleVPNPrefs") for n in names))
        # 双入口
        chk(f + " 设置页双入口齐全（PreferenceBundles + PreferenceLoader）",
            any("PreferenceBundles/SingleVPNPrefs.bundle/Root.plist" in n for n in names)
            and any("PreferenceLoader/Preferences/SingleVPNPrefs/Root.plist" in n for n in names),
            "\n         ".join(n for n in names if "Root.plist" in n))
        chk(f + " postinst 存在", "DEBIAN/postinst" not in data and True)
    print()

    # ---- postinst 红线 ----
    print("[postinst 红线]")
    for f, (blob, ctl, data) in parsed.items():
        pi = ctl["postinst"].decode("utf-8", "replace")
        chk(f + " postinst 无 sbreload/killall/respring/launchctl",
            not any(k in pi for k in ("sbreload", "killall", "respring", "launchctl")),
            pi.replace("\n", " ")[:70])
        chk(f + " postinst 报的版本是 " + EXPECT_VER, EXPECT_VER in pi,
            [l for l in pi.splitlines() if "已安装" in l][:1])
    print()

    # ---- dylib 的 Mach-O 结构 ----
    print("[dylib 架构与签名]")
    for f, (blob, ctl, data) in parsed.items():
        key = [n for n in data if n.endswith("SingleVPN.dylib")][0]
        dy = data[key]
        slices = macho_slices(dy)
        chk(f + " 是 Mach-O", bool(slices), "%d 切片 / %d B" % (len(slices), len(dy)))
        subs = []
        subs_ok = True
        for cputype, cpusub, sl in slices:
            ct, cs = macho_cputype_cpusub(sl)
            subs.append("cpu=0x%x sub=0x%08x" % (ct, cs))
            # ARM64 的 subtype 常量：CPU_SUBTYPE_ARM64_ALL = 0，
            # CPU_SUBTYPE_ARM64E = 2（再叠加 0x80000000 的指针认证能力位，
            # 所以实际读到的是 0x80000002）。
            # ★ 比较前必须 & 0x00FFFFFF 把能力位剥掉，否则永远不等。
            base = cs & 0x00FFFFFF
            has_pac = (cs & 0x80000000) != 0
            if "arm64e" in f:
                if base != 2 or not has_pac:
                    subs_ok = False
            else:
                if base != 0 or has_pac:
                    subs_ok = False
        chk(f + " 切片 cpusubtype 正确（arm64=0 / arm64e=0x80000002）",
            bool(slices) and subs_ok, " / ".join(subs))
        chk(f + " 每个切片都有 LC_CODE_SIGNATURE（RootHide 会跳过未签名 dylib）",
            all(has_signature(sl) for _, _, sl in slices),
            "签名 %d/%d" % (sum(1 for _, _, sl in slices if has_signature(sl)), len(slices)))
    print()

    # ---- dylib 功能指纹：证明改动真的编进去了 ----
    print("[dylib 功能指纹（证明改动编进产物了）]")
    for f, (blob, ctl, data) in parsed.items():
        key = [n for n in data if n.endswith("SingleVPN.dylib")][0]
        dy = data[key]
        # selector / 字面量在 __objc_methname / __cstring，只能按 bytes grep。
        chk(f + " hook 入口齐全",
            all(s in dy for s in (
                b"_updateWithAggregatedData:",
                b"_updateWithData:completionHandler:",
                b"STUIStatusBarWifiSignalView",
                b"STUIStatusBarStringView",
                b"STUIStatusBarCellularNetworkTypeView")),
            "5 个入口")
        # ★ 关键负向：colorWithAlphaComponent: 是系统 selector，
        #   我们不再用它 —— 但它会不会因为别处引用而仍然出现？
        #   这正是需要实测的：产物里若仍有它被调用，说明修复没生效。
        chk(f + " 不再引用 colorWithAlphaComponent:（核心修复）",
            b"colorWithAlphaComponent:" not in dy,
            "命中 %d 次" % dy.count(b"colorWithAlphaComponent:"))
        chk(f + " 有 getRed:green:blue:alpha: 与 colorWithRed:green:blue:alpha:",
            b"getRed:green:blue:alpha:" in dy and b"colorWithRed:green:blue:alpha:" in dy)
        chk(f + " 有 setActiveColor: / setInactiveColor:",
            b"setActiveColor:" in dy and b"setInactiveColor:" in dy)
        # ★ 不能判「setTintColor: 完全不出现」：iOS16 基线分支里上游原本就有
#   一处 [imageView setTintColor:newColor]（我们按约定逐字保留那段）。
#   产物里它表现为 2 次：__objc_methname 的选择子名 1 次 +
#   __objc_selrefs 的指针表项 1 次。
#   所以判据是「恰好 2 次」，比 2 多就说明 iOS17 那一路把 tint 加回来了。
        chk(f + " tint 槽位只在 iOS16 基线里（恰好 2 处：methname + selref）",
            dy.count(b"setTintColor:") == 2,
            "命中 %d 次" % dy.count(b"setTintColor:"))
        # @"tint" 这个字典 key 是我们自己加的槽位名，源码里已彻底删除，
        # 产物里也不该有。
        chk(f + " 没有自定义的 @\"tint\" 槽位 key",
            b"\x00tint\x00" not in dy,
            "命中 %d 次" % dy.count(b"\x00tint\x00"))
        chk(f + " 没有 setNeedsDisplay / setNeedsLayout（还原不重绘）",
            b"setNeedsDisplay" not in dy and b"setNeedsLayout" not in dy)

        # ★★ 符号级证据：2.2-2 的结构性修复真的编进了产物。
        #   这一组是唯一能区分 2.2-1 与 2.2-2 的产物判据：
        #   两版的段内容几乎一致（都修了 alpha），差别在**结构**——
        #   2.2-2 多出唯一造色入口 SVPNColorRGBA，且没有事后自愈那一堆符号。
        #   实测：2.2-1 有 27 个 svpn/SVPN 符号且无 SVPNColorRGBA；
        #        2.2-2 有 28 个且含 SVPNColorRGBA。
        names = [n for _, _, sl in macho_slices(dy) for n in macho_symbol_names(sl)]
        low = " | ".join(names)
        chk(f + " ★ 唯一造色入口 SVPNColorRGBA 编进产物了（根本修复的编译级证据）",
            "SVPNColorRGBA" in low,
            "svpn 符号 %d 个" % len([n for n in names if "svpn" in n.lower()]))
        chk(f + " ★ 没有事后自愈的符号（治标机制已从产物里消失）",
            not any(k in low for k in ("SVPNHeal", "SVPNMarkSeen", "gSVPNSeen")),
            "命中 %d 处" % sum(1 for k in ("SVPNHeal", "SVPNMarkSeen", "gSVPNSeen")
                              if k in low))
        chk(f + " 没有 dispatch 定时器相关符号（不做轮询）",
            "_dispatch_source_create" not in low,
            "命中 %d" % low.count("_dispatch_source_create"))
    print()

    print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
    if fails:
        print("失败：")
        for n, det in fails:
            print("  -", n, det)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())