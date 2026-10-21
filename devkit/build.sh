#!/bin/sh
# 不用 Theos 的完整构建流程
#
# 为什么这样：装整套 Theos 只为跑 make package，代价是 clone 主仓库 +
# 全部子模块约 11 分钟，而真正的编译只要几十秒。这里只用：
#   ① theos 的**单文件** logos.pl 做 %hook 预处理（几百 KB，秒级下载）
#   ② xcrun clang 直接编译 dylib / PreferenceBundle
#   ③ devkit/mkdeb.sh 自己拼 deb
# 私有 framework 用仓库内自备的 .tbd（SingleVPNPrefs/Frameworks/Preferences.tbd）。
#
# 用法：sh devkit/build.sh <arm64|arm64e> <arch-name> <version>
#   例：sh devkit/build.sh arm64e iphoneos-arm64e 2.1-6
set -eu

CPU="${1:?用法: build.sh <arm64|arm64e> <arch-name> <version>}"
ARCH_NAME="${2:?}"
VER="${3:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/.build"
mkdir -p "$BUILD"

# iOS 部署目标：arm64e 从 14.0 起（arm64e 的 ABI 从 iOS 12 才有稳定支持），
# arm64 从 13.0 起（与原有 Theos TARGET iphone:clang:latest:14.0 保持兼容语义）
case "$CPU" in
  arm64)  TRIPLE="arm64-apple-ios13.0"  ;;
  arm64e) TRIPLE="arm64e-apple-ios14.0" ;;
  *) echo "未知架构: $CPU"; exit 1 ;;
esac

SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
echo "SDK   : $SDK"
echo "TRIPLE: $TRIPLE"

# ---------- 1) Logos 预处理 ----------
# logos.pl 是 theos 的**单文件 perl 预处理器**（含 lib/ 共 270KB，已 vendor 进仓库），
# 不需要 theos 的 makefile 体系。
#
# ★ 接口：logos.pl <file.x>，**结果输出到 stdout**，没有 -o / -b 参数
#   （第一版误按 theos 的 theos/bin/logos.pl 用法写了 -b/-o，会直接失败）。
#   生成的文件是 .mm（含 ObjC++ 内容），扩展名必须对，否则 clang 按 C 编译报错。
LOGOS="$ROOT/vendor/logos/bin/logos.pl"
[ -f "$LOGOS" ] || { echo "缺少 $LOGOS"; exit 1; }

# ★ 产物必须放在**仓库根目录**，不能放 .build/：
#   SingleVPN.x 里有 #import "Common.h" / "UIColor+.h"，这是相对路径，
#   clang 的引号 include 只查「本文件所在目录」+ -I 列表。
#   放 .build/ 会报 fatal error: 'Common.h' file not found（已踩过一次）。
#   同时 -I "$ROOT" 双保险。
GEN="$ROOT/_SingleVPN_gen.mm"
perl "$LOGOS" "$ROOT/SingleVPN.x" > "$GEN"
[ -s "$GEN" ] || { echo "✗ Logos 预处理输出为空"; exit 1; }
echo "Logos 预处理完成: $(wc -l < "$GEN") 行"

# 预处理产物里不应再有未展开的 %hook / %orig（有就是不认这个语法，会编译失败）
if grep -qE '^\s*%hook|%orig' "$GEN"; then
  echo "✗ 预处理后仍有 %hook/%orig，Logos 未生效"
  grep -nE '^\s*%hook|%orig' "$GEN" | head -5
  exit 1
fi

# ---------- 2) 编译 dylib ----------
# -undefined dynamic_lookup：MSHookMessageEx / STStatusBarData* 等私有符号
# 留到运行时由 SpringBoard 解析，链接期不要求存在。
# -fobjc-arc：源码使用 ARC。
# ★ 必须加 -I "$ROOT"：
#   预处理产物放在 .build/ 下，而源码里的 #import "Common.h" / "UIColor+.h"
#   是相对源码目录的，不在 .build/ 里。clang 的引号 include 只查
#   「包含本文件的目录」+ -I 列表，产物挪了目录就找不到。
xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -I "$ROOT" \
  -dynamiclib -fobjc-arc -undefined dynamic_lookup -Wall \
  -framework Foundation -framework UIKit -framework CoreFoundation \
  "$GEN" "$ROOT/UIColor+.m" \
  -o "$BUILD/SingleVPN.dylib" \
  -Xlinker -install_name -Xlinker \
    "@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/SingleVPN.dylib"

# ---------- 3) 编译 PreferenceBundle ----------
# 私有 Preferences.framework：
#   · 头文件用仓库自带的 vendor/Preferences-shim（Theos 那套头不可用了）
#   · 链接用仓库自带的 SingleVPNPrefs/Frameworks/Preferences.tbd
#   · 符号本身走 -undefined dynamic_lookup，运行时由设置 App 解析
xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -dynamiclib -fobjc-arc -undefined dynamic_lookup -Wall \
  -I "$ROOT/vendor/Preferences-shim" \
  -framework Foundation -framework UIKit -framework CoreFoundation \
  -F "$ROOT/SingleVPNPrefs/Frameworks" -framework Preferences \
  "$ROOT/SingleVPNPrefs/SingleVPNRootListController.m" \
  "$ROOT/SingleVPNPrefs/SingleVPNColorCell.m" \
  "$ROOT/UIColor+.m" \
  -o "$BUILD/SingleVPNPrefs" \
  -Xlinker -install_name -Xlinker "@rpath/SingleVPNPrefs"

# ---------- 4) 签名 ----------
# RootHide / ellekit 的加载器会跳过没有 LC_CODE_SIGNATURE 的 dylib。
codesign --force --sign - "$BUILD/SingleVPN.dylib"
codesign --force --sign - "$BUILD/SingleVPNPrefs"

# ---------- 5) 架构硬闸门 ----------
echo "--- dylib ---";  file "$BUILD/SingleVPN.dylib";  xcrun lipo -info "$BUILD/SingleVPN.dylib"
echo "--- prefs ---";  file "$BUILD/SingleVPNPrefs";  xcrun lipo -info "$BUILD/SingleVPNPrefs"

xcrun lipo -info "$BUILD/SingleVPN.dylib" | grep -q "$CPU" \
  || { echo "✗ dylib 缺 $CPU 切片"; exit 1; }
xcrun lipo -info "$BUILD/SingleVPNPrefs" | grep -q "$CPU" \
  || { echo "✗ Prefs 缺 $CPU 切片"; exit 1; }
echo "✓ 架构校验通过（$CPU）"

# ---------- 6) 功能指纹闸门 ----------
# 这些字符串/选择子任何一条丢了，产物表面看不出来，但装到机器上就是功能缺失。
# ★ ObjC 选择子不进 nm -u（走 _objc_msgSend + __objc_methname），只能 grep -a。
for sym in _updateWithAggregatedData: _updateWithData:completionHandler: \
           STUIStatusBarCellularNetworkTypeView STUIStatusBarWifiSignalView; do
  grep -aq "$sym" "$BUILD/SingleVPN.dylib" \
    || { echo "✗ dylib 缺 $sym"; exit 1; }
done
echo "✓ dylib hook 入口齐全"

for sym in loadSpecifiersFromPlistName _specifiers; do
  grep -aq "$sym" "$BUILD/SingleVPNPrefs" \
    || { echo "✗ Prefs 缺 $sym（设置界面会空白/重载崩溃）"; exit 1; }
done
echo "✓ Prefs specifiers 加载路径齐全"

# ---------- 7) 打包 ----------
sh "$ROOT/devkit/mkdeb.sh" "$ARCH_NAME" "$VER"
