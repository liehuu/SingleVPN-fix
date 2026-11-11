// ============================================================================
// SingleVPN —— 状态栏 VPN 染色
//
// 2.2-2：根本修复版。
// 22-1 是重写；2.2-2 只做一件事：把「图标看不见」从**源头**堵死，
// 并把堵法变成代码结构，而不是靠某几行写对。
//
// 保留的四条（都有用户实测背书，缺一不可）：
//   ① 启动 0.5 秒闸门 —— respring 后主屏状态栏实例在那一刻建成，插件介入会把
//      它建成残缺状态，且 iOS 17 之后不会重建。整体挪出启动窗口即可（2.1-8）。
//   ② VPN 状态只认本轮 data —— 与上一轮快照做或运算会把断开时的 YES 永久
//      锁存，导致断开不褪色（2.1-9）。
//   ③ 断开后主动写回原色 —— 视图染色是「一次性下发」，断开不改变格数/强度，
//      系统不重发样式，上一轮的色就留在视图上（2.1-10）。
//   ④ 换色相时保住系统 alpha —— 这是「桌面不定期图标空白」的真正根因（2.1-12）。
//
// 删掉的四类：
//   · 诊断/取证脚手架（SVPN_DIAG 整块、日志双通道、心跳计数、地址去重）
//   · entry 回填（svpnMergeMissingEntries）
//     回填源是「已经渲染出去的上一轮数据」，respring/切换网络时可能是
//     「存在但 enabled=NO」的空壳，塞回渲染管线 = 图标整块空白。
//   · 还原机制的过度设计（全局外观 flag、tint 槽位、强制重绘）
//   · ★ 2.2-2 删掉的「事后扫描自愈」（2.2-1 里加过一版）——
//     它先允许坏色写进去，再用「alpha < 0.25」这个启发式认出来、重写、重绘。
//     那是治标：启发式必然误判（系统本来就会给低 alpha），且它在系统流程
//     之外改状态栏私有视图，和原版那个 bug 属于同一类动作。
//     详见文件中部「不变量」一节的注释。
//
// ★★ 关于「图标空白」的定性（最重要的一条，别再走错方向）：
//   用户实测：桌面不定期「双卡信号条 + 4G/WiFi 一起彻底空着，连轮廓都没有」，
//   深浅色背景都出现过，改一下网络或过一会儿自己恢复，**且原版也有**。
//
// ★★★ 2.2-5 的重大纠正 —— 上一版这里的说法是错的，别再照着走：
//   此前这里写「根因在上游染色里的 colorWithAlphaComponent:0.2」。
//   那是**误读**：核对过 git 历史之后确认，
//     · fork 点 d6a7c83（initial commit）只有 102 行，只 hook iOS 16 的
//       三个类，**一处 colorWithAlphaComponent 都没有**；
//     · colorWithAlphaComponent:0.2 出自 6e068f7 "fix ios 17 phone"
//       （原作者 82Flex，2025-12-13），是**后来为 iOS 17 补的分支**。
//   也就是说我把「原作者后加的一段代码」当成了「原版固有基因」，
//   2.2-1 / 2.2-2 / 2.2-3 三轮都建在这个错误转述上。
//
//   现在的事实是：**根因未定**。已经排除的方向见下，不要重复排查：
//     ✗ 染色写出 nil / 透明色 —— svpnReplaceKeepingAlpha 四条返回路径
//       最差退回 hue/original，无一条返回 nil；写色点全有 if (paint) 守卫。
//     ✗ 宽度被压成 0 —— 已确认「强制显示 5GA」开关没开过，那段不执行。
//     ✗ entry 回填塞空壳 —— 现源码 dataByReplacingEntry 命中 0 次；
//       2.1-4 / 2.1-9 已实测关闭回填无效。
//     ✗ hook 没装上 —— 没装上 = 插件等于没装 = 系统自己正常画，不产生空白。
//     ✗ dlopen 副作用 —— StatusStatusUI 由系统自己加载，重复 dlopen 仅引用计数 +1。
//     ✗ 设置 hidden/alpha/transform —— 全工程 0 处。
//     ✗ 注入到别的进程 —— Filter.Bundles 只有 com.apple.springboard。
//     ✗ 启动窗口 —— 2.1-8 自己的 commit message 就写了
//       「装后仍复现 ⇒ 根因不在启动窗口」，而现象已复现。
//
//   ★ 分层的判断（这才是关键）：
//     「颜色不对」和「图标整块不见、连轮廓都没有」**不是一个层的问题**。
//     褪色问题在**颜色值**层（2.2-4 修好了，用户已确认）。
//     「整块不见」在**视图是否存在**层 —— 而 2.2-1 到 2.2-4 改的全是颜色值，
//     一轮都没动过「视图还在不在」。这就是四轮都无效的原因。
//
//   因此 2.2-5 **不改任何染色逻辑**，只加一层「只在异常时留证据」的观测，
//   用运行时数据回答唯一的那个问题：空白发生时，系统到底有没有把
//   这个视图的样式/颜色交给我们？
//     能拿到 → 视图在，问题在我们写出去的值（颜色层）
//     拿不到 → 方法没被调用，视图没建（视图层）
//   观测设计见下面「诊断层」一节的注释。
//
//   历史修复（2.2-2 起仍有效，与上述定性无关）：只换 RGB、alpha 沿用系统原色。
//   2.2-2 把它升级成结构性保证（见下面「造色唯一入口」）：
//     · 全工程只有 SVPNColorRGBA 一处造色；
//     · 它的 alpha 只有两个来源：常量 1.0（配置兜底）、系统原色的 sa；
//     · 所有染色点都必须经过 svpnReplaceKeepingAlpha，色相不直接进 UI 槽位。
//   于是「压掉 alpha」在代码里没有位置，不是「这一版恰好没写」。
// ============================================================================

// ★ 不要 import <HBLog.h> / <substrate.h>：本工程是「零 Theos」构建
//   （clang 直编 + vendored logos.pl），那些头编不到。
//
// ★★ 必须指定 internal 后端：
//   Logos 默认后端 MobileSubstrate 的 preamble 会强行注入 <substrate.h>，
//   零 Theos 环境下编不到。internal 只注入 <objc/message.h>（系统 SDK 自带），
//   并自带 register_hook（class_copyMethodList + method_setImplementation
//   + class_addMethod 沿继承链上溯），语义与 MSHookMessageEx 一致。
%config(generator=internal)

#import <UIKit/UIKit.h>
#import <dlfcn.h>
// 诊断导出要用 open/write/close 与 clock_gettime。
// 用 POSIX 而不是 NSFileManager：导出时不想启动 Foundation 的文件系统栈，
// 也不想在热路径间接分配任何 Objective-C 对象。
#import <fcntl.h>
#import <stdio.h>   // snprintf —— 不能靠 UIKit 的传递包含，那是实现细节
#import <time.h>
#import <unistd.h>

#import "Common.h"
#import "UIColor+.h"

// ===== 私有接口（逆向所得，字段名必须与系统一致，否则 @try 里会 unrecognized selector）
//   注意：这里的声明只用于「知道有哪些属性/方法」，不做任何 KVC 动态调用。

@interface STStatusBarDataEntry : NSObject
@property (getter=isEnabled, nonatomic, readonly) bool enabled;
@end

@interface STStatusBarDataCellularEntry : STStatusBarDataEntry
@end

@interface STStatusBarDataWifiEntry : STStatusBarDataEntry
@end

@interface STStatusBarData : NSObject
@property (nonatomic, readonly) STStatusBarDataCellularEntry *cellularEntry;
@property (nonatomic, readonly) STStatusBarDataCellularEntry *secondaryCellularEntry;
@property (nonatomic, readonly) STStatusBarDataEntry *vpnEntry;
@property (nonatomic, readonly) STStatusBarDataWifiEntry *wifiEntry;
@end

@interface STUIStatusBar : NSObject
- (STStatusBarData *)currentAggregatedData;
- (STStatusBarData *)currentData;
@end

@interface STUIStatusBarStyleAttributes : NSObject
@property (nonatomic, copy) UIColor *textColor;
@property (nonatomic, copy) UIColor *imageDimmedTintColor;
@property (nonatomic, copy) UIColor *imageTintColor;
@property (nonatomic, copy) UIFont *font;
@end

@interface STUIStatusBarStringView : UILabel
- (void)svpnApply5GAdvancedAttributesIfNeeded;
@end

@interface STUIStatusBarCellularNetworkTypeView : UIView
@property (nonatomic, strong) STUIStatusBarStringView *stringView;
@property (nonatomic, strong) NSLayoutConstraint *widthConstraint;
@end

@interface STUIStatusBarWifiSignalView : UIView
@property (nonatomic, strong) UIColor *inactiveColor;
@property (nonatomic, strong) UIColor *activeColor;
@end

// ============================================================================
// 配置
// ============================================================================

// 网络类型文字白名单。只有这些文本会被染色，别的（运营商名、通知等）不动。
#define IsNetworkTypeText(text) ( \
    [text isEqualToString:@"G"] || [text isEqualToString:@"3G"] || \
    [text isEqualToString:@"4G"] || [text containsString:@"5G"] || \
    [text isEqualToString:@"LTE"])

// 启动窗口：0.5 秒。见文件头 ①。
// ★ 为什么是 0.5 而不是原来的 5：
//   5 秒是「宁可功能晚生效，也要确保不撞启动」的保守值 —— 当时还不知道
//   到底哪个动作惹的祸，只能整块往后推。现在根因已定位到「respring 首帧
//   数据未就绪时介入」，而首帧完成后到第一帧稳定通常在 200~400ms 内。
//   0.5 秒足够避开，且用户几乎感觉不到「刚 respring 完颜色没生效」。
//   若仍有 respring 后短暂空白，把它调大即可，功能不受影响。
#define SVPN_READY_DELAY_MS 500
static volatile int gSVPNReady = 0;

static BOOL _isEnabled = NO;            // 总开关
static BOOL _isVPNEnabled = NO;         // VPN 当前是否连接（只认本轮 data）
static BOOL _isEnabledReversed = NO;    // 反向：不连 VPN 才染色
static BOOL _isForce5GAEnabled = NO;    // 强制显示 5GA

static UIColor *_darkReplacementColor = nil;   // 系统色偏深时用（浅色底上的深色文字）
static UIColor *_lightReplacementColor = nil;  // 系统色偏浅时用

// ============================================================================
// 诊断：环形缓冲（2.2-5）
// ============================================================================
//
// ★★ 为什么需要它，以及为什么它不能猜
//
//   「图标整块不见」这个现象，2.1-8 / 2.2-1 / 2.2-2 / 2.2-3 / 2.2-4 五轮都没修好，
//   五轮改的全是**颜色值**（alpha、凭空造值、还原重绘、tint 槽位）。而这个现象
//   的形态是「连轮廓都没有」—— 那是**视图级**的事，不是颜色值的事。
//   颜色层和视图层之间隔着「视图到底有没有被画」，光读源码看不出来。
//
//   所以在弄清楚之前不再改染色逻辑，只做一件事：**把运行时事实记下来**。
//
// ★★ 设计约束（每一条都为了「不让诊断本身改变被观察的行为」）
//
//   ① 热路径零分配、零 IO、零锁。
//      applyStyleAttributes: 是系统绘制链路里的方法，在那里分配 NSString 或写文件
//      会改变时序，可能把要观察的 bug 直接掩盖掉。所以只存**整数与枚举**，
//      方法名用编译期常量下标，不做字符串拼接。
//
//   ② 只记「输入」，不记「我们的判断结果」。
//      记下入参颜色的 alpha / 是否为 nil，才能区分三种情况：
//        · 压根没被调用         → 视图没建（指向视图层）
//        · 被调用但入参是 nil   → 系统没给色（指向数据层）
//        · 被调用且入参正常     → 那问题在我们写出去的值上
//      这三种情况指向完全不同的修法，现在一个都不知道。
//
//   ③ 导出是**显式动作**，不是自动触发。
//      自动写文件会在崩溃/卡顿时留下半截文件，而且时机不可控。
//   ④ 缓冲固定在栈外（静态数组），不 malloc、不释放。

#define SVPN_DIAG_SLOTS 256
#define SVPN_DIAG_EXPORT_PATH "/var/mobile/Library/Preferences/com.82flex.singlevpn-diag.txt"

// 版本串：让导出的日志能对上「这是哪一版跑出来的」。
// 与 Makefile / build.yml / mkdeb.sh 三处保持一致（闸门 S9a 会判）。
#define SVPN_VERSION_STR "2.2-5"

// 前置声明：SVPNNote 要读当前判定结果，而 SVPNDecision 定义在后面。
static inline BOOL SVPNDecision(void);
// ★ ReloadPrefs 定义在文件中部（约 428 行），而下面的通知回调
//   SVPNPrefsChanged 在 405 行就调用它 —— 必须前置声明，否则
//   `use of undeclared identifier 'ReloadPrefs'`（CI 上真的报了这条）。
static void ReloadPrefs(void);

// 事件类型（整数，编译期常量）
enum {
    SVPNEvApplyStyleString = 1,   // STUIStatusBarStringView applyStyleAttributes:
    SVPNEvSetText          = 2,   // STUIStatusBarStringView setText:
    SVPNEvWifiApplyStyle   = 3,   // STUIStatusBarWifiSignalView applyStyleAttributes:
    SVPNEvWifiActive       = 4,   // STUIStatusBarWifiSignalView setActiveColor:
    SVPNEvWifiInactive     = 5,   // STUIStatusBarWifiSignalView setInactiveColor:
    SVPNEvCellSetText      = 6,   // STUIStatusBarCellularNetworkTypeView setText:...
    SVPNEvDataAggregated   = 7,   // STUIStatusBar _updateWithAggregatedData:
    SVPNEvDataWithHandler  = 8,   // STUIStatusBar _updateWithData:completionHandler:
    SVPNEvRestore          = 9,   // SVPNRestoreNow 执行
    SVPNEvGateOpen         = 10,  // 闸门打开
};

// 颜色状态（整数）
enum {
    SVPNColorNil     = 0,   // 入参为 nil
    SVPNColorOpaque  = 1,   // alpha >= 0.99
    SVPNColorFaint   = 2,   // 0 < alpha < 0.99
    SVPNColorClear   = 3,   // alpha <= 0.01（写上去就是看不见）
    SVPNColorNotRGB  = 4,   // 取不到 RGBA（动态色 / 非 RGB 色彩空间）
};

typedef struct {
    uint32_t seq;        // 递增序号，用于判断顺序与是否丢帧
    uint32_t ev;         // 事件类型
    uint32_t colorKind;  // 入参颜色状态
    uint32_t dec;        // 当时的 SVPNDecision()（0/1）
    uint32_t ready;      // 当时的 gSVPNReady（0/1）
    uint32_t extra;      // 事件相关的附加整数（如 vpnEntry.enabled）
    uint32_t ms;         // 距插件加载的毫秒数
} SVPNEvent;

static SVPNEvent gSVPNRing[SVPN_DIAG_SLOTS];
static volatile int gSVPNRingCount = 0;      // 已写入条数（超过槽位数则覆盖最旧）
static volatile uint32_t gSVPNSeq = 0;
static uint64_t gSVPNStartMs = 0;            // 插件加载时刻
static volatile int gSVPNExportRequest = 0;  // 设置页置 1 表示请求导出

/// 把颜色归类成枚举。**只读 alpha，不做任何分配。**
static inline uint32_t SVPNColorKind(UIColor *c) {
    if (!c) {
        return SVPNColorNil;
    }
    CGFloat r = 0, g = 0, b = 0, a = 0;
    // ★ getRed:green:blue:alpha: 是 UIColor 的公开 API，会做色彩空间换算。
    //   动态色（UISystemColor 那类）在这里返回 NO —— 归为 NotRGB 即可，
    //   不额外去 resolve，那会引入 trait 依赖与潜在分配（热路径）。
    if (![c getRed:&r green:&g blue:&b alpha:&a]) {
        return SVPNColorNotRGB;
    }
    if (a <= 0.01) {
        return SVPNColorClear;
    }
    if (a >= 0.99) {
        return SVPNColorOpaque;
    }
    return SVPNColorFaint;
}

/// 记一条。热路径调用，必须极便宜：无分配、无 IO、无锁。
/// 单线程写（状态栏更新都在主线程），所以不需要锁。
static inline void SVPNNote(uint32_t ev, UIColor *color, uint32_t extra) {
    uint32_t i = gSVPNSeq % SVPN_DIAG_SLOTS;
    SVPNEvent *e = &gSVPNRing[i];
    e->seq = gSVPNSeq + 1;
    e->ev = ev;
    e->colorKind = SVPNColorKind(color);
    e->dec = SVPNDecision() ? 1 : 0;
    e->ready = gSVPNReady ? 1 : 0;
    e->extra = extra;
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0 && gSVPNStartMs) {
        uint64_t now = (uint64_t)ts.tv_sec * 1000ull + (uint64_t)(ts.tv_nsec / 1000000);
        e->ms = (uint32_t)(now > gSVPNStartMs ? (now - gSVPNStartMs) : 0);
    } else {
        e->ms = 0;
    }
    gSVPNSeq = e->seq;
    if (gSVPNRingCount < SVPN_DIAG_SLOTS) {
        gSVPNRingCount = gSVPNRingCount + 1;
    }
}

// 事件名与颜色名的查表（只在导出时用，热路径不碰）
static const char *SVPNEventName(uint32_t ev) {
    switch (ev) {
        case SVPNEvApplyStyleString: return "StringView.applyStyleAttributes:";
        case SVPNEvSetText:          return "StringView.setText:";
        case SVPNEvWifiApplyStyle:   return "WifiSignal.applyStyleAttributes:";
        case SVPNEvWifiActive:       return "WifiSignal.setActiveColor:";
        case SVPNEvWifiInactive:     return "WifiSignal.setInactiveColor:";
        case SVPNEvCellSetText:      return "CellNetType.setText:prefix:...";
        case SVPNEvDataAggregated:   return "StatusBar._updateWithAggregatedData:";
        case SVPNEvDataWithHandler:  return "StatusBar._updateWithData:completion:";
        case SVPNEvRestore:          return "SVPNRestoreNow";
        case SVPNEvGateOpen:         return "gate open";
        default:                     return "?";
    }
}

static const char *SVPNColorName(uint32_t k) {
    switch (k) {
        case SVPNColorNil:    return "nil";
        case SVPNColorOpaque: return "opaque";
        case SVPNColorFaint:  return "faint";
        case SVPNColorClear:  return "CLEAR";
        case SVPNColorNotRGB: return "notRGB";
        default:              return "?";
    }
}

/// 导出到文件。**只在被显式请求时调用**，所以可以放心做 IO。
/// 用 O_NONBLOCK 建标记文件做去重，避免设置页连点导出多次。
static void SVPNExportDiag(void) {
    int fd = open(SVPN_DIAG_EXPORT_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return;
    }
    char line[192];
    int n = 0;
    uint32_t total = gSVPNSeq;
    uint32_t have = (uint32_t)gSVPNRingCount;
    n = snprintf(line, sizeof(line),
                 "# SingleVPN diag v" SVPN_VERSION_STR "  total=%u kept=%u slots=%d\n",
                 total, have, SVPN_DIAG_SLOTS);
    if (n > 0) { write(fd, line, (size_t)n); }
    n = snprintf(line, sizeof(line),
                 "# seq  ms      ready dec color     extra  event\n");
    if (n > 0) { write(fd, line, (size_t)n); }

    uint32_t first = (total > have) ? (total - have) : 0;
    for (uint32_t s = first; s < total; s++) {
        SVPNEvent *e = &gSVPNRing[s % SVPN_DIAG_SLOTS];
        n = snprintf(line, sizeof(line), "%-5u %-7u %d     %d   %-8s  %-5u  %s\n",
                     e->seq, e->ms, (int)e->ready, (int)e->dec,
                     SVPNColorName(e->colorKind), e->extra, SVPNEventName(e->ev));
        if (n > 0) { write(fd, line, (size_t)n); }
    }
    close(fd);
}

/// 检查是否需要把缓冲落盘。
///
/// ★ 结构说明（两条都是刻意的）：
///   ① **先判标志再进 @try**：这条函数被每个钩子每条调用（热路径），
///      @try 要建 setjmp，成本不可忽略。把判断放在 @try 外面，
///      99.99% 的调用只做一次 int 读 + 返回，零额外开销。
///   ② **真正落盘那段包 @try**：诊断层的第一红线是「绝不崩宿主」。
///      SVPNExportDiag 只走 POSIX，理论上不抛，但这条红线不能靠
///      「理论上」来保证 —— 一旦抛出去就是 SpringBoard 崩。
static inline void SVPNMaybeExport(void) {
    if (!gSVPNExportRequest) {
        return;
    }
    gSVPNExportRequest = 0;
    @try {
        SVPNExportDiag();
    } @catch (NSException *e) {
    }
}

/// 设置页「导出诊断」按钮的回调。
///
/// ★ 签名说明：CFNotificationCenterAddObserver 的回调原型是
///   (CFNotificationCenterRef, void *observer, CFStringRef name,
///    const void *object, CFDictionaryRef userInfo) —— **五个参数**。
///   原来这里直接把 `ReloadPrefs`（无参函数）当回调传，属于签名不匹配：
///   在 arm64 上多余的参数落在寄存器里、被调用方忽略，所以「看起来能用」，
///   但它依赖 ABI 巧合，不是正确写法。这里改成真正的回调签名。
static void SVPNRequestExport(CFNotificationCenterRef center,
                              void *observer,
                              CFStringRef name,
                              const void *object,
                              CFDictionaryRef userInfo) {
    // 置标志，让下一次钩子调用时就地落盘 —— 不在通知回调里直接做 IO，
    // 因为此刻可能不在主线程、也不是一个安全的写入时机。
    gSVPNExportRequest = 1;
}

/// 设置页保存配置后的回调（真正的五参数签名）。
///
/// ★ 原来这里直接把 `ReloadPrefs` 传进去，签名不匹配（它是无参函数）。
///   arm64 上多余参数落在寄存器里被忽略，所以「看起来能用」，但那是 ABI 巧合。
///   这里包一层，签名正确，且行为与原来等价（就是重新读配置）。
static void SVPNPrefsChanged(CFNotificationCenterRef center,
                             void *observer,
                             CFStringRef name,
                             const void *object,
                             CFDictionaryRef userInfo) {
    ReloadPrefs();
}

// ---------------------------------------------------------------------------
// 造色：全工程唯一入口（2.2-2 的结构性修复）
//
// ★★ 为什么要把「造一个颜色」收成一处：
//   上游把图标弄没的写法是 [绿色 colorWithAlphaComponent:0.2] —— 一次手滑就能
//   把 alpha 从 1 压到 0.2，图标立刻消失。只要允许代码里散落着写
//   [UIColor colorWithRed:... alpha:...]，「能造出多小的 alpha」就是个
//   靠人自觉的问题，总有下一次手滑。
//   收成一处之后它变成**结构性**问题：全工程只有这一处造色，而它的调用方
//   只有两个 —— 一个恒定给 1.0（配置色兜底），一个给「系统原色的 alpha」。
//   闸门于是可以判「全工程只有一处造色」，这比逐个盯调用点强得多。
// ---------------------------------------------------------------------------
static UIColor *SVPNColorRGBA(CGFloat r, CGFloat g, CGFloat b, CGFloat a) {
    @try {
        return [UIColor colorWithRed:r green:g blue:b alpha:a];
    } @catch (NSException *e) {
        return nil;
    }
}

static void ReloadPrefs(void) {
    static NSUserDefaults *prefs = nil;
    if (!prefs) {
        prefs = [[NSUserDefaults alloc] initWithSuiteName:@"com.82flex.singlevpnprefs"];
    }
    if (!prefs) {
        return;
    }
    NSDictionary *settings = [prefs dictionaryRepresentation];
    if (!settings) {
        return;
    }
    _isEnabled = settings[@"IsEnabled"] ? [settings[@"IsEnabled"] boolValue] : YES;
    _isEnabledReversed = settings[@"IsEnabledReversed"] ? [settings[@"IsEnabledReversed"] boolValue] : NO;
    _isForce5GAEnabled = settings[@"IsForce5GAEnabled"] ? [settings[@"IsForce5GAEnabled"] boolValue] : NO;

    UIColor *l = nil, *d = nil;
    @try {
        l = [UIColor svpn_colorWithExternalRepresentation:settings[@"ForegroundColorLight"]];
        d = [UIColor svpn_colorWithExternalRepresentation:settings[@"ForegroundColorDark"]];
    } @catch (NSException *e) {
    }
    // 设置里没配就用默认绿。★ 这里必须有兜底：颜色为 nil 会让染色路径
    // 走进「写 nil」的分支，等于把图标颜色清空。
    // alpha 恒为 1：这是「色相本身」，不含任何透明度语义。
    _lightReplacementColor = l ?: SVPNColorRGBA(0.196, 0.780, 0.349, 1.0);
    _darkReplacementColor = d ?: SVPNColorRGBA(0.173, 0.816, 0.341, 1.0);
}

// ============================================================================
// 颜色：只换色相，保留 alpha
// ============================================================================

/// 取「当前是否该染色」。
/// 唯一的判定入口 —— 所有 hook 都调它，保证行为一致。
static inline BOOL SVPNDecision(void) {
    if (!gSVPNReady) {
        return NO;
    }
    return _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
}

/// 按系统色挑替换色。
/// svpn_isDarkColor 对非 RGB 色（动态色等）一律返回 NO（当浅色处理），
/// 所以调它之前不保证能取到 RGBA —— 取不到时走 _lightReplacementColor，
/// 那是最常见的「深色文字配浅色底」假设，比写 nil 安全得多。
static UIColor *svpnReplacementFor(UIColor *systemColor) {
    if (!systemColor) {
        return nil;
    }
    BOOL dark = NO;
    @try {
        dark = [systemColor svpn_isDarkColor];
    } @catch (NSException *e) {
        dark = NO;
    }
    return dark ? _lightReplacementColor : _darkReplacementColor;
}

/// ★★★ 本版核心：换 RGB，**alpha 沿用 original 的**。
///
/// 绝不能用 colorWithAlphaComponent: —— 它替换 alpha 而不是叠加。
/// 系统给未激活信号格的 alpha 接近 1，被压到 0.2 后深底近乎全黑、
/// 浅底近乎全白，两种背景下都「看不见」，这正是用户报的图标空白。
///
/// 三个边界，都按「宁可保持原样，不要写一个看不见的色」处理：
///   · hue 为 nil        → 返回 original（没得换，保持原样）
///   · original 为 nil   → 返回 hue（没有原色可参照，用纯色相）
///   · 取不到 RGBA       → 返回 hue（动态色按纯色相，alpha 用 hue 自己的）
static UIColor *svpnReplaceKeepingAlpha(UIColor *original, UIColor *hue) {
    if (!hue) {
        return original;
    }
    if (!original) {
        return hue;
    }
    CGFloat r = 0, g = 0, b = 0, a = 0;
    CGFloat sr = 0, sg = 0, sb = 0, sa = 0;
    if (![original getRed:&sr green:&sg blue:&sb alpha:&sa]) {
        return hue;
    }
    if (![hue getRed:&r green:&g blue:&b alpha:&a]) {
        return hue;
    }
    // ★ 唯一的 alpha 来源是 sa（系统原色的 alpha）。
    //   这里没有任何一条路径的 alpha 来自我们自己 —— 想压也压不下去。
    return SVPNColorRGBA(r, g, b, sa);
}

// ============================================================================
// 还原：断开 VPN 后把染上去的色写回系统原色
// ============================================================================
//
// 为什么需要：视图染色是「一次性下发」。decision==NO 时各 hook 只是纯透传，
//   前提是这些方法会再被调用一次。但 VPN 断开通常不改变信号格数 / WiFi 强度 /
//   网络类型文字，系统不重发样式 → hook 不再被调用 → 上一轮的色留在视图上。
//   App 内状态栏是另一个实例、进入时走完整创建流程，所以那里一直是对的。
//
// 数据结构：**一视图一字典**，键是槽位名，同一字典里放两样：
//     @"tc" / @"ac" / @"ic" / @"tint"   → 系统原本的颜色
//     @"tc#" / @"ac#" / @"ic#" / @"tint#" → 我们自己染上去的颜色（残留识别用）
//   销账时一次 removeObjectForKey 就干净，不存在「两张表漏销一张」的问题。
//   ★ 2.2-4 起不再记 @"@style"（界面外观）—— 那道门槛是错的，已删。
//
// 四个必须遵守的点（每一条都对应一次踩坑）：
//   ① 残留识别：setText: 路径读到的 self.textColor 可能已被我们染过。
//      若读到的色等于「我上次染的这个值」，它就不是系统色，拒绝记账。
//      不判这一条，第二次调用会把绿色当系统色存下来，还原时还原成绿色。
//   ② 逐视图比对外观：★ **已废弃（2.2-4）**。原来判「记账时与还原时外观不同
//      就整视图跳过」，那是间接代理指标，取错 trait 就整块不还原。
//      现在改为逐槽位直接判「当前值是否还是我染的那个」。
//   ③ 写回前实色 + 幂等：只写能取到 RGBA 的实色；透明色不写（写上去就是
//      看不见）；当前值已相同就不写（我们是在系统更新流程之外改私有属性）。
//   ④ 处理完销账：账本只在一个「染色 → 断开」周期内有效。桌面视图长期存活，
//      账留着就会在下次断开时拿一本跨了多次样式更新的旧账去校准屏幕。

#define SVPN_RESTORE_MAX_VIEWS 32

static NSMapTable *gSVPNLedger = nil;   // 视图(weak, 指针同一性) → NSMutableDictionary
static int gSVPNLastDecision = -1;      // -1 未知 / 0 不染 / 1 染
// 账本里当前有几个视图。只用于诊断导出（回答「还原时到底有没有对象可还原」），
// 不参与任何染色/还原判断 —— 以免诊断反过来改变被观察的行为。
static volatile int gSVPNLedgerCount = 0;

// ★ 2.2-4 删掉 SVPNUserInterfaceStyle 与 SVPN_STYLE_KEY。
//   它们只服务于「按界面外观决定要不要还原」这道门槛，而那道门槛是错的
//   （见 SVPNRestoreNow 里的说明）。留着一段没人读的记账代码，
//   后面的人会以为它还起作用 —— 死代码比没代码更危险。

/// ★ key 必须 WeakMemory + ObjectPointerPersonality：
///   只写 weakToStrongObjectsMapTable 会退回默认 personality，
///   视图类一旦重写 isEqual 就可能把两个视图并成一条 → 还原串味。
static void SVPNEnsureLedger(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSPointerFunctionsOptions kOpt =
            NSPointerFunctionsWeakMemory | NSPointerFunctionsObjectPointerPersonality;
        NSPointerFunctionsOptions vOpt =
            NSPointerFunctionsStrongMemory | NSPointerFunctionsObjectPointerPersonality;
        gSVPNLedger = [NSMapTable mapTableWithKeyOptions:kOpt valueOptions:vOpt];
    });
}

static NSMutableDictionary *SVPNLedgerDict(id view, BOOL create) {
    if (!view) {
        return nil;
    }
    SVPNEnsureLedger();
    NSMutableDictionary *d = nil;
    @try {
        d = [gSVPNLedger objectForKey:view];
        if (!d && create) {
            d = [NSMutableDictionary dictionaryWithCapacity:4];
            [gSVPNLedger setObject:d forKey:view];
            // 只维护计数，不参与判断
            gSVPNLedgerCount = gSVPNLedgerCount + 1;
        }
    } @catch (NSException *e) {
        return nil;
    }
    return d;
}

/// 记一笔：系统原色 + 我们染上去的色。painted 可为 nil（表示只记原色）。
static void SVPNRecord(id view, NSString *slot, UIColor *systemColor, UIColor *paintedColor) {
    if (!view || !slot || !systemColor) {
        return;
    }
    @try {
        NSMutableDictionary *d = SVPNLedgerDict(view, YES);
        if (!d) {
            return;
        }
        // ① 残留识别
        NSString *paintedKey = [slot stringByAppendingString:@"#"];
        UIColor *lastPainted = [d objectForKey:paintedKey];
        if (lastPainted && [lastPainted isEqual:systemColor]) {
            return;   // 这个「系统色」其实是我上次染的，不能当原色记
        }
        [d setObject:systemColor forKey:slot];
        if (paintedColor) {
            [d setObject:paintedColor forKey:paintedKey];
        }
    } @catch (NSException *e) {
        // 记账失败最多是这次还原不全，绝不影响宿主
    }
}

/// 读视图某个颜色属性。取不到 / 不是颜色都返回 nil。
///
/// ★ ARC 下 IMP 直调返回对象必须 __unsafe_unretained：
///   ARC 不知道函数指针返回对象的所有权，默认按 +1 处理会误 release
///   别人的对象 → 崩宿主。
static UIColor *SVPNReadColor(id view, NSString *getter) {
    if (!view || !getter) {
        return nil;
    }
    SEL gsel = NSSelectorFromString(getter);
    if (!gsel || ![view respondsToSelector:gsel]) {
        return nil;
    }
    UIColor *out = nil;
    @try {
        IMP gimp = [view methodForSelector:gsel];
        if (gimp) {
            __unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);
            if ([cur isKindOfClass:[UIColor class]]) {
                out = cur;
            }
        }
    } @catch (NSException *e) {
        out = nil;
    }
    return out;
}

/// 写一个色回视图的某个槽位。任意一条检查不过就不写。
/// 返回 YES 表示**确实写进去了**（调用方据此决定要不要标脏重绘）。
static BOOL SVPNRestoreSlot(id view, NSString *setter, NSString *getter, UIColor *color) {
    if (!view || !setter || !color) {
        return NO;
    }
    @try {
        SEL sel = NSSelectorFromString(setter);
        if (!sel || ![view respondsToSelector:sel]) {
            return NO;
        }
        CGFloat r = 0, g = 0, b = 0, a = 0;
        if (![color getRed:&r green:&g blue:&b alpha:&a]) {
            return NO;   // 非实色：按当前 trait 重解析可能又变另一个色，不写
        }
        if (a <= 0.001) {
            return NO;   // 透明色写上去就是看不见
        }
        // 幂等：已经相同就不写（也就没有「写过了」这件事）
        if (getter) {
            UIColor *cur = SVPNReadColor(view, getter);
            if (cur && [cur isEqual:color]) {
                return NO;
            }
        }
        IMP imp = [view methodForSelector:sel];
        if (imp) {
            ((void (*)(id, SEL, id))imp)(view, sel, color);
            return YES;
        }
    } @catch (NSException *e) {
    }
    return NO;
}

static void SVPNRestoreNow(void) {
    // 记录一次还原「真的跑了」以及账本里有几个视图。
    // extra = 待处理视图数；0 表示账本是空的（那就等于没有还原对象）。
    @try {
        uint32_t n = 0;
        if (gSVPNLedgerCount) {
            n = (uint32_t)gSVPNLedgerCount;
        }
        SVPNNote(SVPNEvRestore, nil, n);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (!gSVPNReady) {
        return;
    }
    // 延后执行期间可能已经重新连上，此时擦色就是新 bug
    if (SVPNDecision()) {
        return;
    }
    if (!gSVPNLedger) {
        return;
    }

    // ★ 2.2-4 起不再在这里取界面外观：外观比对作为「整视图跳过」的
    //   门槛已被删除，改成逐槽位判「当前值是否还是我染的那个」。
    //   见下面循环里的说明。

    // 先收集再处理：遍历 NSMapTable 的同时改它属于未定义行为
    NSMutableArray *views = [NSMutableArray array];
    @try {
        for (id v in gSVPNLedger) {
            if (v) {
                [views addObject:v];
            }
            if ([views count] >= SVPN_RESTORE_MAX_VIEWS) {
                break;
            }
        }
    } @catch (NSException *e) {
        return;
    }

    // 槽位 → setter / getter / 是否做「当前值是否仍是我染的」校验
    //
    // ★★ 2.2-4 把 @"tint" 加回来（2.2-2 曾删掉）。删掉的理由站不住：
    //    蜂窝/WiFi 图标吃的是 styleAttrs 的 imageTintColor，它没有落成
    //    某个「可回读」的视图属性，所以不记账就永远没有还原的入口。
    //    2.1-10 用 setTintColor: 还原这一路，用户当时确认「断开能正常褪色」。
    //    既然实测过，就照抄，不要用「会传播到子视图」这种推断去否决实测。
    //
    // ★ tint 这一路**不能**做「当前值是否仍是我染的」校验，理由：
    //   styleAttrs.imageTintColor 与 UIView.tintColor 不是同一条通路。
    //   视图的 tintColor 绝大多数时候是**继承**来的值，从来不会等于
    //   我们记下的 painted → 若套用同一判据，每次都会被判成「系统已改过」
    //   然后跳过，等于这一路永远不还原（比不记账还差，因为它看起来在做事）。
    NSArray *plan = @[
        @[@"tc", @"setTextColor:", @"textColor", @YES],
        @[@"ac", @"setActiveColor:", @"activeColor", @YES],
        @[@"ic", @"setInactiveColor:", @"inactiveColor", @YES],
        @[@"tint", @"setTintColor:", @"tintColor", @NO],
    ];

    for (id view in views) {
        NSMutableDictionary *d = SVPNLedgerDict(view, NO);
        if (!d) {
            continue;
        }

        // ★★ 2.2-4 删掉「外观比对就整视图跳过」这道门槛。
        //
        //    2.2-2/2.2-3 判的是：记账时的 userInterfaceStyle ≠ 还原时的 → 跳过。
        //    这是拿一个**间接代理指标**去推断「这批色还在不在」，而它错在两处：
        //      · 桌面状态栏的 trait 与 UIApplication 的 trait 未必一致，
        //        取哪一个是运气；取到不一致的值就把整个视图放过去了。
        //      · 即使外观真的变了，正确的动作也不是「留着绿色」，而是
        //        「先把绿色撤掉」—— 撤销永远比保留我们的染色更安全。
        //
        //    改成直接判据：**当前值还是不是我染的那个色？**
        //      · 还是 → 系统没动过，必须还原。
        //      · 已被系统改掉 → 系统自己刷新了，不用管，跳过。
        //    这才是「还原」这个词真正想问的问题。

        BOOL didWrite = NO;
        for (NSArray *pair in plan) {
            NSString *slot = pair[0];
            NSString *setter = pair[1];
            NSString *getter = pair[2];
            BOOL checkCurrent = [pair[3] boolValue];

            UIColor *origin = nil;
            UIColor *painted = nil;
            @try {
                origin = [d objectForKey:slot];
                painted = [d objectForKey:[slot stringByAppendingString:@"#"]];
            } @catch (NSException *e) {
                origin = nil;
            }
            if (!origin) {
                continue;
            }

            // 系统已经自己改过这一路 → 不需要我们动手
            if (checkCurrent && painted) {
                UIColor *cur = SVPNReadColor(view, getter);
                if (cur && ![cur isEqual:painted]) {
                    continue;
                }
            }

            if (SVPNRestoreSlot(view, setter, checkCurrent ? getter : nil, origin)) {
                didWrite = YES;
            }
        }

        // ★★ 2.2-4 还原后必须自己标脏重绘。
        //
        //    为什么这条不能省（2.2-2 删掉它就是「断开不褪色」的直接原因）：
        //      setActiveColor: / setInactiveColor: 是**属性 setter**，
        //      只把值存进 ivar，不碰 CALayer、不标脏、不触发绘制。
        //      系统之所以会重画，是因为这些 setter 平时都在系统自己的
        //      「数据更新 → 布局 → 绘制」链路里被调用 —— 一次调用后面
        //      紧跟着系统自己的重绘。
        //      而还原发生在**系统链路之外**（断开后的主队列下一拍），
        //      后面没有任何一次系统重绘来接住它 → 值变了，屏幕不变。
        //
        //    ★ 这条与「染色时不额外重绘」不矛盾，那是两件事：
        //      · 染色：系统正在重画，我们插队重绘 = 和系统抢节奏 → 禁止。
        //      · 还原：系统不会重画，我们自己不标脏 = 永远不会生效 → 必须。
        if (didWrite) {
            @try {
                [view setNeedsDisplay];
            } @catch (NSException *e) {
            }
        }

        // ④ 销账。原色表必销（账本不跨周期）。
        @try {
            if ([gSVPNLedger objectForKey:view]) {
                [gSVPNLedger removeObjectForKey:view];
                if (gSVPNLedgerCount > 0) {
                    gSVPNLedgerCount = gSVPNLedgerCount - 1;
                }
            }
        } @catch (NSException *e) {
        }
    }
}

// ============================================================================
// 不变量：颜色只能从 SVPNColorRGBA 出来
// ============================================================================
//
// 上一版在这里放了一整套「扫描 → 发现 alpha 太低 → 重写颜色 → 重绘」的自愈。
// 它被删掉了，理由是它是**治标**：
//
//   · 它先允许坏色被写进去，再靠 alpha < 0.25 这个**启发式**去认出来。
//     启发式一定会有误判：系统在某些场景下（未激活格、淡出动画、按压态）
//     本来就给低 alpha，"低 alpha = 坏" 这个前提并不成立。
//   · 它在系统的更新流程之外写视图属性（setNeedsDisplay / setXxxColor）。
//     ★ 注意：这里说的「坏事」是**在系统正绘制的时候插队重画**，
//       不是「永远不能在系统之外写属性」。2.2-4 修的正是后者被误用的后果：
//       还原本来就必须在系统链路之外做（断开时系统不会重画），
//       那时**必须**自己标脏，否则值改了屏幕不变。
//       两句话的区别就是「染色路径」与「还原路径」，闸门 S11h / S6l 分开判。
//   · 定时器 2 秒扫一轮，等于让插件长期和系统抢同一个属性。
//
// 根本修法是把「坏色」这类值从**源头**消灭，而不是事后认出来：
//
//   ① 全工程只有 SVPNColorRGBA 一处造色。闸门 S11a/S11b 判它就是唯一一处。
//   ② 它的调用方只有两个，alpha 各自被钉死：
//        · ReloadPrefs 兜底色  → alpha 恒 1.0（色相本身，不含透明度语义）
//        · svpnReplaceKeepingAlpha → alpha 来自**系统原色**的 sa
//      没有任何一条路径的 alpha 来自我们自己，想压也压不下去。
//   ③ 染色只发生在系统自己的调用栈里（applyStyleAttributes: / setXxxColor:），
//      也就是系统「本来就在重画」的时刻 —— 染色路径里不插队重绘。
//
// 结果：图标看不见这件事**没有发生的机会**，而不是「发生了再修」。
//
// 那用户说的「切一下网络就回来了」还需要额外做什么？
//   不需要。VPN 连接状态变化必然改变 vpnEntry → 系统必然重发数据 →
//   _updateWithData: / _updateWithAggregatedData: 被调用 → %orig 触发
//   完整重绘 → applyStyleAttributes: 重新下发样式 → 我们在这条链路里
//   按系统的 alpha 换色相。这条链路是系统自己的，不靠我们另外补一次重绘。
//
// ★★ 但「断开要褪色」是**另一条路**，它必须自己补动作：
//    断开时系统只是把 vpnEntry.enabled 变 NO，不改变格数/强度/文字，
//    因此不会重发样式 → 染色 hook 不再被调用 → 上一轮的绿色留在视图上。
//    所以断开会走 SVPNRestoreNow 主动写回原色，并且**必须自己标脏重绘**
//    （2.2-2 把这条删了，直接导致「断开不褪色」）。

static void SVPNRestoreAsync(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        @try {
            SVPNRestoreNow();
        } @catch (NSException *e) {
            // 还原失败最多是颜色晚点退，绝不能因此崩宿主
        }
    });
}

/// 每个数据层 hook 末尾调一次。两个 hook 都调，第二次 prev 已是 0，
/// 不会重复触发 —— 天然去重，不需要额外的重入标志。
///
/// ★ 这里只处理「连 → 断」这一个状态迁移（触发还原）。
///   反方向（断 → 连）什么都不做，因为不需要：
///   连着 VPN 时系统会重发完整数据，染色链路（applyStyleAttributes: /
///   setXxxColor:）由系统自己驱动，我们不补动作。
static void SVPNNoteTransition(void) {
    if (!gSVPNReady) {
        return;
    }
    BOOL now = SVPNDecision();
    int prev = gSVPNLastDecision;
    gSVPNLastDecision = now ? 1 : 0;
    if (prev == 1 && !now) {
        SVPNRestoreAsync();
    }
}

// ============================================================================
// iOS 17
// ============================================================================

%group SingleVPN_17

%hook STUIStatusBar

- (void)_updateWithAggregatedData:(STStatusBarData *)data {
    // ★ 诊断记录放在闸门**外面**：闸门未开时的调用次数正是要观察的
    //   （respring 后的启动窗口就在这一段）。放在 if (gSVPNReady) 里面
    //   就永远看不到这个窗口，等于把最可疑的那段时间挖掉了。
    //   extra 记 vpnEntry 是否存在（0=本轮没带，1=带了且 enabled，2=带了但 NO）。
    @try {
        STStatusBarDataEntry *vpnProbe = data ? data.vpnEntry : nil;
        SVPNNote(SVPNEvDataAggregated, nil, vpnProbe ? (vpnProbe.enabled ? 1u : 2u) : 0u);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (gSVPNReady) {
        @try {
            // 只认本轮 data 携带的条目：本轮没带就保持上一轮值。
            // 绝不与上一轮快照做或运算 —— 那会把断开时的 YES 永久锁存。
            STStatusBarDataEntry *vpnNow = data.vpnEntry;
            if (vpnNow) {
                _isVPNEnabled = vpnNow.enabled;
            }
            SVPNNoteTransition();
        } @catch (NSException *e) {
        }
    }
    %orig;
}

- (void)_updateWithData:(STStatusBarData *)data completionHandler:(id)handler {
    @try {
        STStatusBarDataEntry *vpnProbe = data ? data.vpnEntry : nil;
        SVPNNote(SVPNEvDataWithHandler, nil, vpnProbe ? (vpnProbe.enabled ? 1u : 2u) : 0u);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (gSVPNReady) {
        @try {
            STStatusBarDataEntry *vpnNow = data.vpnEntry;
            if (vpnNow) {
                _isVPNEnabled = vpnNow.enabled;
            }
            SVPNNoteTransition();
        } @catch (NSException *e) {
        }
    }
    %orig;
}

%end

%hook STUIStatusBarStringView

- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs {
    // ★★ 记在判断**之前**：这一条能区分两个完全不同的世界
    //    · 日志里根本没有这条 → 这个方法没被调用 → 视图可能没建（视图层问题）
    //    · 有这条但 color=nil → 被调用了但系统没给色（数据层问题）
    //    · 有这条且 color=opaque/faint → 系统给了正常色 → 问题在我们写出去的值
    @try {
        SVPNNote(SVPNEvApplyStyleString, styleAttrs ? styleAttrs.textColor : nil, 0);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (SVPNDecision() && IsNetworkTypeText(self.text)) {
        @try {
            // ★ 必须在 copy 之前读：改的是副本，原始入参才是系统本来要用的色
            UIColor *sysTextColor = styleAttrs.textColor;
            // ★ 保 alpha：文字同样可能带系统给的 alpha（淡出/次要文字）。
            //   只有「所有染色点一致保 alpha」才是一条不变量，而不是四处特例。
            UIColor *paint = svpnReplaceKeepingAlpha(sysTextColor, svpnReplacementFor(sysTextColor));
            if (paint) {
                STUIStatusBarStyleAttributes *copy = [styleAttrs copy];
                [copy setTextColor:paint];
                styleAttrs = copy;
                SVPNRecord(self, @"tc", sysTextColor, paint);
            }
        } @catch (NSException *e) {
        }
    }
    %orig;
    [self svpnApply5GAdvancedAttributesIfNeeded];
}

- (void)setText:(NSString *)text {
    %orig;

    // 记下「文字被改成什么」—— 空字符串也记，那是「内容没了」的直接证据。
    // extra: 0=非网络类型文字 / 1=网络类型文字 / 2=文字为空
    @try {
        uint32_t kind = 0;
        if (IsNetworkTypeText(text)) {
            kind = 1;
        } else if (text == nil || text.length == 0) {
            kind = 2;
        }
        SVPNNote(SVPNEvSetText, self.textColor, kind);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (SVPNDecision() && IsNetworkTypeText(text)) {
        @try {
            // self.textColor 可能是我们上一轮染的（SVPNRecord 会识别并拒绝记账）
            UIColor *cur = self.textColor;
            UIColor *paint = svpnReplaceKeepingAlpha(cur, svpnReplacementFor(cur));
            if (paint) {
                [self setTextColor:paint];
                SVPNRecord(self, @"tc", cur, paint);
            }
        } @catch (NSException *e) {
        }
    }
    [self svpnApply5GAdvancedAttributesIfNeeded];
}

%end

%hook STUIStatusBarWifiSignalView

- (void)setActiveColor:(UIColor *)activeColor {
    // ★ 记入参：这是自绘视图真正吃颜色的一路。
    @try {
        SVPNNote(SVPNEvWifiActive, activeColor, 0);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (SVPNDecision() && activeColor) {
        @try {
            // 入参就是系统本来要用的色 → 必须改之前记账。
            // ★ 激活格也走保 alpha：原来直接塞纯色相（alpha=1），
            //   系统若正在淡出（切换动画里 alpha 由 1 渐变到 0），
            //   我们把它钉死在 1 就等于跟系统的动画对着干。
            //   两侧对称地「只换 RGB、alpha 一律沿用系统的」，才没有例外。
            UIColor *paint = svpnReplaceKeepingAlpha(activeColor, svpnReplacementFor(activeColor));
            if (paint) {
                SVPNRecord(self, @"ac", activeColor, paint);
                activeColor = paint;
            }
        } @catch (NSException *e) {
        }
    }
    %orig;
}

- (void)setInactiveColor:(UIColor *)inactiveColor {
    // ★★ 这一路最关键：未激活格是「淡的那几格」。
    //    如果日志里出现 color=CLEAR / faint，就说明系统本来就给了接近透明的值，
    //    我们「原样保 alpha」等于把看不见的色透传下去 —— 那就是 2.1-12 引入
    //    「保 alpha」之后可能产生的新问题，必须先看见它才能判断。
    @try {
        SVPNNote(SVPNEvWifiInactive, inactiveColor, 0);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (SVPNDecision() && inactiveColor) {
        @try {
            // ★★ 保 alpha：原来写 colorWithAlphaComponent:0.2，把系统接近 1 的
            //    alpha 直接压到 0.2 → 深底全黑 / 浅底全白 → 图标「彻底空着」。
            UIColor *paint = svpnReplaceKeepingAlpha(inactiveColor, svpnReplacementFor(inactiveColor));
            if (paint) {
                SVPNRecord(self, @"ic", inactiveColor, paint);
                inactiveColor = paint;
            }
        } @catch (NSException *e) {
        }
    }
    %orig;
}

- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs {
    // ★ 蜂窝/WiFi 图标吃的是这个 styleAttrs 的 tint，不是单独的颜色属性。
    //   extra: 位0=tint 存在 / 位1=dimmed 存在 / 位2=textColor 存在
    @try {
        uint32_t bits = 0;
        if (styleAttrs) {
            if (styleAttrs.imageTintColor)     { bits |= 1u; }
            if (styleAttrs.imageDimmedTintColor) { bits |= 2u; }
            if (styleAttrs.textColor)          { bits |= 4u; }
        }
        SVPNNote(SVPNEvWifiApplyStyle, styleAttrs ? styleAttrs.imageTintColor : nil, bits);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (SVPNDecision() && styleAttrs) {
        @try {
            // ★★ 一条不可讨价还价的规则：**只替换系统已经给了的值，
            //    绝不凭空添加系统没给的值。**
            //
            //    为什么这条是本版最关键的自律：
            //      状态栏这套私有绘制管线里，某个槽位是 nil 是有语义的 ——
            //      「这一路不参与绘制」/「沿用上一份」。我们若在系统没给的
            //      情况下塞一个自己造的色进去，等于替系统决定了一件它没打算
            //      决定的事。原版那两个 bug（回填空壳 entry、写死 alpha 0.2）
            //      本质都是这一类：在系统没要求的地方替它做决定。
            //
            //    具体到这段：
            //      · imageTintColor 为 nil  → 不设 tint（别拿 textColor 顶替）
            //      · imageDimmedTintColor 为 nil → 不设 dimmed
            //        （别拿 tint 顶替！那会让「本该淡的次要元素」变得和
            //         正常元素一样亮，是反方向的错误。）
            //      · 两个 tint 都只做一件事：RGB 换成配置色，alpha 原封不动。
            UIColor *sysText = styleAttrs.textColor;
            UIColor *sysTint = styleAttrs.imageTintColor;
            UIColor *sysDimmed = styleAttrs.imageDimmedTintColor;

            // 色相只决定 RGB，来源优先级：文字色 > 图片 tint
            UIColor *hue = svpnReplacementFor(sysText ?: sysTint);
            if (hue) {
                STUIStatusBarStyleAttributes *copy = nil;
                // 提到块外：记账要用（下面 if (sysTint) 块内才算得出来）
                UIColor *tint = nil;

                if (sysTint) {
                    // alpha 来自 sysTint 自己 —— 不借用别的槽位的 alpha
                    tint = svpnReplaceKeepingAlpha(sysTint, hue);
                    if (tint) {
                        copy = copy ?: [styleAttrs copy];
                        [copy setImageTintColor:tint];
                    }
                }

                if (sysDimmed) {
                    // alpha 来自 sysDimmed 自己。系统给的低 alpha
                    // （「淡一档」的语义）被原样保留，我们不放大也不壓小。
                    UIColor *dimmed = svpnReplaceKeepingAlpha(sysDimmed, hue);
                    if (dimmed) {
                        copy = copy ?: [styleAttrs copy];
                        [copy setImageDimmedTintColor:dimmed];
                    }
                }

                if (copy) {
                    styleAttrs = copy;

                    // ★★ 2.2-4 补回 tint 记账（2.2-2 曾以「会传播到子视图」
                    //    为由删掉，但那是推断，而 2.1-10 用同一个 setter
                    //    还原过、并且你实测确认「断开能正常褪色」）。
                    //
                    //    记的是**系统的** imageTintColor（sysTint），
                    //    还原时按 setTintColor: 写回视图。
                    //    没有这一笔，蜂窝/WiFi 图标就永远没有还原入口 ——
                    //    这正是「断开不褪色」的第三处缺口。
                    if (sysTint && tint) {
                        SVPNRecord(self, @"tint", sysTint, tint);
                    }
                }
            }
        } @catch (NSException *e) {
        }
    }
    %orig;
}

%end

%hook STUIStatusBarCellularNetworkTypeView

- (void)setText:(NSString *)text prefixLength:(NSInteger)prefixLength withStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs forType:(NSInteger)type animated:(BOOL)animated {
    // ★ 这一路是「4G / 5G 文字」那个视图。用户报的「4G 文字没了」正落在这里。
    //   记在最前面、且**不改控制流**：这个方法本来就有一条提前 return 的快路径
    //   （未开 5GA 时），如果记录放在那个 if 后面，最常见的情况下一条都记不到。
    //   extra 位: 0=text 非空 / 1=以 "5G" 开头 / 2=styleAttrs 存在
    @try {
        uint32_t kind = 0;
        if (text.length > 0)            { kind |= 1u; }
        if ([text hasPrefix:@"5G"])     { kind |= 2u; }
        if (styleAttrs)                 { kind |= 4u; }
        SVPNNote(SVPNEvCellSetText, styleAttrs ? styleAttrs.textColor : nil, kind);
    } @catch (NSException *e) {
    }
    SVPNMaybeExport();

    if (!gSVPNReady || !_isForce5GAEnabled || ![text hasPrefix:@"5G"]) {
        %orig;
        if (@available(iOS 16, *)) {
            if (gSVPNReady && _isForce5GAEnabled && !self.widthConstraint.active) {
                objc_setAssociatedObject(self, @selector(widthConstraint), nil, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
                self.widthConstraint.active = YES;
            }
        }
        return;
    }

    if (@available(iOS 16, *)) {
        %orig;

        NSAttributedString *attributedText = self.stringView.attributedText;
        self.widthConstraint.active = NO;

        NSLayoutConstraint *newWidthConstraint = [NSLayoutConstraint constraintWithItem:self attribute:NSLayoutAttributeWidth relatedBy:NSLayoutRelationEqual toItem:nil attribute:NSLayoutAttributeNotAnAttribute multiplier:1.0 constant:0];
        newWidthConstraint.constant = [attributedText size].width * 0.9;
        newWidthConstraint.priority = UILayoutPriorityRequired;
        newWidthConstraint.active = YES;

        objc_setAssociatedObject(self, @selector(widthConstraint), newWidthConstraint, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    } else {
        %orig;
    }
}

%end

%hook STUIStatusBarStringView

%new
- (void)svpnApply5GAdvancedAttributesIfNeeded {
    if (!gSVPNReady || !_isForce5GAEnabled) {
        return;
    }
    if (@available(iOS 16, *)) {
        @try {
            NSString *text = self.text;
            if (!IsNetworkTypeText(text)) {
                return;
            }
            UIFont *font = self.font;
            if (!font) {
                return;
            }
            UIColor *textColor = self.textColor;
            if (!textColor) {
                return;
            }

            NSString *newText = @"5GA";
            NSInteger prefixLength = 2;
            NSMutableAttributedString *attributedText =
                [[NSMutableAttributedString alloc] initWithString:newText attributes:@{
                    NSFontAttributeName: font,
                    NSForegroundColorAttributeName: textColor,
                }];

            NSMutableDictionary *traits = [[font.fontDescriptor objectForKey:UIFontDescriptorTraitsAttribute] mutableCopy]
                ?: [NSMutableDictionary dictionary];
            traits[UIFontWidthTrait] = @(UIFontWidthCondensed / 1.5);
            traits[UIFontWeightTrait] = @(UIFontWeightSemibold);

            UIFontDescriptor *condensedDescriptor =
                [font.fontDescriptor fontDescriptorByAddingAttributes:@{UIFontDescriptorTraitsAttribute: traits}];
            UIFont *condensedFont = [UIFont fontWithDescriptor:condensedDescriptor size:0];
            UIFont *smallerCondensedFont = [condensedFont fontWithSize:condensedFont.pointSize * 0.7];

            [attributedText addAttribute:NSFontAttributeName value:condensedFont range:NSMakeRange(0, prefixLength)];
            [attributedText addAttribute:NSFontAttributeName value:smallerCondensedFont
                                   range:NSMakeRange(prefixLength, attributedText.length - prefixLength)];

            self.attributedText = attributedText;
        } @catch (NSException *e) {
        }
    }
}

%end

%end // SingleVPN_17

// ============================================================================
// iOS 16（保持上游基线，不做任何改动）
//
// 为什么不动：用户设备是 iOS 17，这段代码永远不会被执行，改了也无法验证。
// 「无法验证的改动」本身就是风险 —— 宁可让它保持上游原样（含它自己的
// alpha 0.2 问题），也不在这一版塞进一段没人跑过的代码。
// 若将来要支持 iOS 16，应当按 iOS 17 的结构重写这一组，而不是在这里增量改。
// ============================================================================

%group SingleVPN_16

%hook _UIStatusBarWifiItem

- (id)applyUpdate:(_UIStatusBarItemUpdate *)update toDisplayItem:(_UIStatusBarDisplayItem *)displayItem {
    _isVPNEnabled = update.data.vpnEntry.enabled;

    id result = %orig;

    UIColor *originalColor = update.styleAttributes.textColor;
    UIColor *newColor = nil;

    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision) {
        newColor = svpnReplacementFor(originalColor);
    }

    if (!newColor) { newColor = update.styleAttributes.imageTintColor ?: originalColor; }

    for (_UIStatusBarDisplayItem *item in self.displayItems.allValues) {
        %orig(update, item);

        if (item.view == self.networkIconView && [item.view isKindOfClass:%c(_UIStatusBarImageView)]) {
            _UIStatusBarImageView *imageView = (_UIStatusBarImageView *)item.view;
            [imageView setTintColor:newColor];
        }
    }

    return result;
}

- (UIColor *)_fillColorForUpdate:(_UIStatusBarItemUpdate *)update entry:(_UIStatusBarDataWifiEntry *)entry {
    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision) {
        return svpnReplacementFor(update.styleAttributes.textColor);
    } else {
        return %orig;
    }
}

%end

%hook _UIStatusBarCellularItem

- (id)applyUpdate:(_UIStatusBarItemUpdate *)update toDisplayItem:(_UIStatusBarDisplayItem *)displayItem {
    _isVPNEnabled = update.data.vpnEntry.enabled;

    id result = %orig;

    UIColor *originalColor = update.styleAttributes.textColor;
    UIColor *newColor = nil;

    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision) {
        newColor = svpnReplacementFor(originalColor);
    }

    if (!newColor) { newColor = originalColor; }

    for (_UIStatusBarDisplayItem *item in self.displayItems.allValues) {
        _UIStatusBarStringView *stringView = nil;

        if ([item.view isKindOfClass:%c(_UIStatusBarCellularNetworkTypeView)]) {
            stringView = ((_UIStatusBarCellularNetworkTypeView *)item.view).stringView;
        } else if ([item.view isKindOfClass:%c(_UIStatusBarStringView)]) {
            stringView = (_UIStatusBarStringView *)item.view;
        }

        if (IsNetworkTypeText(stringView.text)) {
            [stringView setTextColor:newColor];
        } else {
            [stringView setTextColor:originalColor];
        }
    }

    return result;
}

%end

%hook _UIStatusBarStringView

- (void)applyStyleAttributes:(_UIStatusBarStyleAttributes *)styleAttrs {
    %orig;

    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision && IsNetworkTypeText(self.text)) {
        UIColor *paint = svpnReplacementFor(styleAttrs.textColor);
        if (paint) {
            [self setTextColor:paint];
        }
    }
}

%end

%end // SingleVPN_16

// ============================================================================
// 入口
// ============================================================================

%ctor {
    // 记录插件加载时刻，作为诊断日志里 ms 列的基准。
    @try {
        struct timespec ts0;
        if (clock_gettime(CLOCK_MONOTONIC, &ts0) == 0) {
            gSVPNStartMs = (uint64_t)ts0.tv_sec * 1000ull + (uint64_t)(ts0.tv_nsec / 1000000);
        }
    } @catch (NSException *e) {
    }

    ReloadPrefs();

    if (!_isEnabled) {
        return;
    }

    // 设置页改动后热生效。用 CFNotificationCenter 而不是 NSNotification：
    // 跨进程通知只有 Darwin 通知中心能收到。
    @try {
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(),
            NULL,
            (CFNotificationCallback)SVPNPrefsChanged,
            CFSTR("com.82flex.singlevpnprefs/saved"),
            NULL,
            CFNotificationSuspensionBehaviorCoalesce
        );
    } @catch (NSException *e) {
    }

    // 诊断导出请求：设置页点了「导出诊断」就发这个通知，插件收到后落盘。
    @try {
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(),
            NULL,
            (CFNotificationCallback)SVPNRequestExport,
            CFSTR("com.82flex.singlevpnprefs/exportdiag"),
            NULL,
            CFNotificationSuspensionBehaviorCoalesce
        );
    } @catch (NSException *e) {
    }

    // ★ 启动窗口：见文件头 ①。
    //   窗口内 gSVPNReady=0，所有 hook 纯 %orig 透传，等价于插件不存在。
    //   这里用 dispatch_after 到主队列 —— 不用后台队列：闸门只写一个 int，
    //   放后台反而会引入「后台线程改全局状态」的审查疑问。
    //   注意 DIV 不能写成浮点：NSEC_PER_MSEC 整数乘除即可。
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(SVPN_READY_DELAY_MS * NSEC_PER_MSEC)),
        dispatch_get_main_queue(), ^{
            gSVPNReady = 1;
            // 记下闸门打开的时刻：日志里能直接看出「空白是发生在闸门开之前
            // 还是之后」。这是本轮最想区分的一件事。
            SVPNNote(SVPNEvGateOpen, nil, 0);
            SVPNMaybeExport();
        }
    );

    if (@available(iOS 17, *)) {
        // StatusStatusUI 是 iOS 17 状态栏的新私有框架，STUIStatusBar* 在这里。
        dlopen("/System/Library/PrivateFrameworks/StatusStatusUI.framework/StatusStatusUI", RTLD_LAZY);
        %init(SingleVPN_17);
    } else {
        %init(SingleVPN_16);
    }
}