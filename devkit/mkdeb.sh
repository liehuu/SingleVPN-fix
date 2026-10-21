#!/bin/sh
# 打 deb 包（不依赖 Theos / dpkg-deb）
#
# 为什么自己写：整套 Theos 只为跑一次 dpkg-deb 不值得 —— clone 主仓库
# 加全部子模块要 11 分钟，而这里 1 秒就能做完同样的事。
# deb 的 ar 归档格式非常简单，标准库即可生成。
#
# 用法：sh devkit/mkdeb.sh <arch> <version>
#   arch    : iphoneos-arm | iphoneos-arm64 | iphoneos-arm64e
#   version : 2.1-6
set -eu

ARCH="${1:?用法: mkdeb.sh <arch> <version>}"
VER="${2:?用法: mkdeb.sh <arch> <version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/.build/stage"
OUT="$ROOT/.build/out"
PKG="com.82flex.singlevpn"
NAME="SingleVPN"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/Library/MobileSubstrate/DynamicLibraries" \
         "$STAGE/Library/PreferenceBundles/SingleVPNPrefs.bundle" \
         "$STAGE/Library/PreferenceLoader/Preferences/SingleVPNPrefs" \
         "$OUT"

# ---- 载荷 ----
cp "$ROOT/.build/$NAME.dylib" \
   "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.dylib"
cp "$ROOT/SingleVPN.plist" \
   "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.plist"

PB="$STAGE/Library/PreferenceBundles/SingleVPNPrefs.bundle"
cp "$ROOT/.build/SingleVPNPrefs" "$PB/SingleVPNPrefs"
cp "$ROOT/SingleVPNPrefs/Resources/Info.plist"  "$PB/Info.plist"
cp "$ROOT/SingleVPNPrefs/Resources/Root.plist"  "$PB/Root.plist"
cp "$ROOT/SingleVPNPrefs/Resources/icon@2x.png" "$PB/icon@2x.png"
cp "$ROOT/SingleVPNPrefs/Resources/icon@3x.png" "$PB/icon@3x.png"
[ -d "$ROOT/SingleVPNPrefs/Resources/en.lproj" ]      && cp -R "$ROOT/SingleVPNPrefs/Resources/en.lproj" "$PB/"
[ -d "$ROOT/SingleVPNPrefs/Resources/zh-Hans.lproj" ] && cp -R "$ROOT/SingleVPNPrefs/Resources/zh-Hans.lproj" "$PB/"
cp "$ROOT/SingleVPNPrefs/layout/Library/PreferenceLoader/Preferences/SingleVPNPrefs/Root.plist" \
   "$STAGE/Library/PreferenceLoader/Preferences/SingleVPNPrefs/Root.plist"

# 权限：目录 755，文件 644，dylib 与 bundle 主程序 755
find "$STAGE" -type d -exec chmod 755 {} \;
find "$STAGE" -type f -exec chmod 644 {} \;
chmod 755 "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.dylib"
chmod 755 "$PB/SingleVPNPrefs"

# ---- control ----
# 版本号必须由参数派生，不能硬编码（v2.8 踩过：包名 v2.7、内容 v2.8）
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Name: SingleVPN
Version: $VER
Architecture: $ARCH
Description: 把 VPN 图标染成系统主题色（StatusBar 染色）
Maintainer: liehuu
Author: liehuu
Section: Tweaks
Depends: mobilesubstrate
EOF
chmod 644 "$STAGE/DEBIAN/control"

# ---- postinst / postrm ----
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -d /Library/MobileSubstrate/DynamicLibraries ]; then :; fi
# RootHide 下由 dpkg 以 root 运行，可以安全重建图标缓存
if [ -x /usr/bin/sbreload ]; then /usr/bin/sbreload >/dev/null 2>&1 || true; fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

# ---- 打包 ----
DEB="$OUT/${PKG}_${VER}_${ARCH}.deb"
export STAGE OUT DEB
python3 - <<'PY'
import io, os, lzma, tarfile, hashlib, time

stage = os.environ['STAGE']
deb   = os.environ['DEB']


def _tar():
    """把 stage 目录打成 tar 字节流。

    ★ 不能遍历 os.walk 后对 dirs+files 逐个 tf.add(full, arcname=...)：
      tf.add 对一个目录会**递归**把整棵子树也加进去，而 os.walk 又会
      单独走到那些子目录 → 同一批文件被重复打包（实测出现
      './DEBIAN/control' 出现两次），deb 体积成倍膨胀。
    ★ 也不能只写 tf.add(stage, arcname='.')：那样文件权限取自本机文件系统，
      打包机上的 umask 会污染产物。
    正解：自己按 os.walk 的顺序逐个加入，并显式设置 mode。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            files.sort()
            for name in dirs:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                ti.mode = 0o755
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                tf.addfile(ti)
            for name in files:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                # ★ 不能读 os.stat().st_mode：脚本前面虽然 chmod 了，
                #   但 tarfile 在 Windows / 某些挂载上拿到的仍是 666。
                #   deb 里权限必须确定：dylib 与 bundle 主程序 755，其余 644。
                rel = os.path.relpath(full, stage).replace('\\', '/')
                ti.mode = 0o755 if _is_exec(rel) else 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                with open(full, 'rb') as fh:
                    tf.addfile(ti, fh)
    return buf.getvalue()


def _is_exec(rel):
    """哪些文件在 deb 里必须是可执行位。

    SpringBoard 注入的 dylib 与设置 bundle 的主程序如果丢了 +x，
    装上去表现是「插件完全没加载」，而 dpkg 不报任何错。
    """
    if rel.endswith('.dylib'):
        return True
    if rel.endswith('/SingleVPNPrefs') or rel == 'SingleVPNPrefs':
        return True
    if rel.startswith('DEBIAN/') and rel.split('/')[-1] in ('postinst', 'postrm', 'preinst', 'prerm'):
        return True
    return False


# data.tar 用 xz（与 Theos 产物一致）
data = lzma.compress(_tar(), format=lzma.FORMAT_XZ)

# control.tar 用 gz
import gzip
ctl = io.BytesIO()
with tarfile.open(fileobj=ctl, mode='w') as tf:
    for name in ('control', 'postinst'):
        p = os.path.join(stage, 'DEBIAN', name)
        if os.path.exists(p):
            ti = tf.gettarinfo(p, arcname='./' + name)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            with open(p, 'rb') as fh:
                tf.addfile(ti, fh)
control = gzip.compress(ctl.getvalue(), mtime=0)

members = [
    ('debian-binary', b'2.0\n'),
    ('control.tar.gz', control),
    ('data.tar.xz', data),
]

out = io.BytesIO()
out.write(b'!<arch>\n')
for name, blob in members:
    hdr = ('%-16s' % name).encode()
    hdr += ('%-12s' % str(int(time.time()))).encode()
    hdr += ('%-6s' % '0').encode()
    hdr += ('%-6s' % '0').encode()
    hdr += ('%-8s' % '100644').encode()
    hdr += ('%-10s' % str(len(blob))).encode()
    hdr += b'`\n'
    out.write(hdr)
    out.write(blob)
    if len(blob) % 2:
        out.write(b'\n')

blob = out.getvalue()
open(deb, 'wb').write(blob)
print('  %s  %d B  sha256=%s' % (os.path.basename(deb), len(blob),
                                 hashlib.sha256(blob).hexdigest()[:16]))
PY

echo "  -> $DEB"
