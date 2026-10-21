// ★ 不要 import <HBLog.h> / <substrate.h> 等 Theos 私有头：
//   它们由 Theos 的 lib 目录提供，本工程已改为「零 Theos」构建
//   （xcrun clang 直编 + vendored logos.pl），编不到。
//   原文件只有一行 #import <HBLog.h>，全文没有任何 HBLog() 调用，直接删除。
//   需要 hook 运行时符号时，用 -undefined dynamic_lookup 让 SpringBoard 在运行时解析。
#import <UIKit/UIKit.h>
#import <dlfcn.h>
#import <time.h>          // clock_gettime / CLOCK_MONOTONIC（心跳节流用）
#import <CoreFoundation/CoreFoundation.h>   // CFPreferences*（第二日志通道）

#import "Common.h"
#import "UIColor+.h"

#define IsNetworkTypeText(text) ( \
    [text isEqualToString:@"G"] || [text isEqualToString:@"3G"] || \
    [text isEqualToString:@"4G"] || [text containsString:@"5G"] || \
    [text isEqualToString:@"LTE"])

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
- (STStatusBarData *)dataByReplacingEntry:(id)arg1 forKey:(NSString *)arg2;
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

static BOOL _isEnabled = NO;
static BOOL _isVPNEnabled = NO;
static BOOL _isEnabledReversed = NO;
static BOOL _isForce5GAEnabled = NO;

static UIColor *_darkReplacementColor = nil;
static UIColor *_lightReplacementColor = nil;

static UIColor *svpnColorWithHexString(NSString *hexString) {
    if (!hexString) {
        return nil;
    }
    return [UIColor svpn_colorWithExternalRepresentation:hexString];
}

static UIColor *svpnColorWithTextColor(UIColor *textColor) {
    return [textColor svpn_isDarkColor] ? _lightReplacementColor : _darkReplacementColor;
}

static void ReloadPrefs() {
    static NSUserDefaults *prefs = nil;
    if (!prefs) {
        prefs = [[NSUserDefaults alloc] initWithSuiteName:@"com.82flex.singlevpnprefs"];
    }

    NSDictionary *settings = [prefs dictionaryRepresentation];
    _isEnabled = settings[@"IsEnabled"] ? [settings[@"IsEnabled"] boolValue] : YES;
    _isEnabledReversed = settings[@"IsEnabledReversed"] ? [settings[@"IsEnabledReversed"] boolValue] : NO;
    _isForce5GAEnabled = settings[@"IsForce5GAEnabled"] ? [settings[@"IsForce5GAEnabled"] boolValue] : NO;

    _lightReplacementColor = svpnColorWithHexString(settings[@"ForegroundColorLight"]) ?: [UIColor colorWithRed:0.19607843137254902 green:0.7803921568627451 blue:0.34901960784313724 alpha:1];
    _darkReplacementColor = svpnColorWithHexString(settings[@"ForegroundColorDark"]) ?: [UIColor colorWithRed:0.17254901960784313 green:0.8156862745098039 blue:0.3411764705882353 alpha:1];
}

%group SingleVPN_16

%hook _UIStatusBarWifiItem

- (id)applyUpdate:(_UIStatusBarItemUpdate *)update toDisplayItem:(_UIStatusBarDisplayItem *)displayItem {
    _isVPNEnabled = update.data.vpnEntry.enabled;

    id result = %orig;

    UIColor *originalColor = update.styleAttributes.textColor;
    UIColor *newColor = nil;

    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision) {
        newColor = svpnColorWithTextColor(originalColor);
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
        return svpnColorWithTextColor(update.styleAttributes.textColor);
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
        newColor = svpnColorWithTextColor(originalColor);
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
        [self setTextColor:svpnColorWithTextColor(styleAttrs.textColor)];
    }
}

%end

%end // SingleVPN_16

// ===== svpn 修复补丁 =========================================================
// 原版在 iOS 17 分支用「上一轮快照回填系统本轮没下发的 entry」来保住图标，
// 有两个确定性缺陷：
//   ① 用 `BOOL changed = data.vpnEntry` 当开关，判的是「vpnEntry 对象是否存在」，
//      而不是「VPN 是否已连接」。iOS 17 的 STStatusBarData 里 _vpnEntry 是固定字段，
//      该判断几乎恒真 —— 等于不管有没有连 VPN，每一帧都在替系统改写状态栏数据。
//   ② 回填源 [self currentData] 是「已经渲染出去的上一轮数据」，可能是残缺的：
//      respring 后蜂窝/WiFi 尚未重新上报时，系统会下发「存在但 enabled=NO」的空壳。
//      原版只判 `!data.cellularEntry`（完全缺失），不判 enabled，
//      于是把 disabled 空壳顶回去 —— 状态栏拿到一个「存在但不该显示」的 entry，
//      主屏的信号条和 WiFi 图标就此消失；而 App 内状态栏重新上报后自行恢复。
// 修复：只在 VPN 确实连接时才介入，且只回填「本身 enabled」的旧 entry。
// 把下面的宏改成 1 可完全关闭回填，用于 A/B 验证。
//
// ★★ v2.1-6：默认改为 1（关闭回填）。
// 用户实测结论（决定性证据）：
//   ① 图标消失只在 respring 之后出现，不是运行中逐渐变坏
//      → 属一次性初始化阶段的问题，不是"用久了状态漂移"
//   ② 完全卸载插件后，反复 respring 都不再消失
//      → 确认是插件引起的
//   ③ 消失形态是"整块空白，跟普通空白一样"
//      → 不是颜色问题（背景同色至少能看出轮廓），是 entry 被判定为不该显示
//
// 结合①：respring 瞬间 currentData 是上一轮的陈旧快照，enabled 可能仍为 YES，
// 但 string / signalStrength 等字段是残缺的。加 enabled 守卫拦不住这种
// "enabled=YES 但内容残缺"的壳 —— 塞回系统后它照样画不出来，表现就是整块空白。
// 因此正确做法不是"回填得更小心"，而是**根本不要回填**：
// 系统本来就会在稍后重新上报完整数据，我们插手只会把残缺数据顶进渲染管线。
//
// 染色功能与此无关，保持开启。
#define SVPN_DISABLE_ENTRY_MERGE 1

static STStatusBarData *svpnMergeMissingEntries(STUIStatusBar *bar, STStatusBarData *data) {
    if (!data) {
        return data;
    }

    STStatusBarData *cur = nil;
    @try {
        cur = [bar currentData];
    } @catch (NSException *e) {
        return data;
    }
    if (!cur) {
        return data;
    }

    if (!data.cellularEntry && cur.cellularEntry.enabled) {
        data = [data dataByReplacingEntry:[cur.cellularEntry copy] forKey:@"cellularEntry"];
    }
    if (!data.secondaryCellularEntry && cur.secondaryCellularEntry.enabled) {
        data = [data dataByReplacingEntry:[cur.secondaryCellularEntry copy] forKey:@"secondaryCellularEntry"];
    }
    if (!data.wifiEntry && cur.wifiEntry.enabled) {
        data = [data dataByReplacingEntry:[cur.wifiEntry copy] forKey:@"wifiEntry"];
    }
    return data;
}
// ============================================================================

// ===== svpn 诊断（v2.1-5 专用，交付排查完即删）=============================
// 目的：不再靠读源码猜。把「每次下发到状态栏的数据到底长什么样」记下来，
//       以及「我们到底改没改它」，一次性搞清楚图标消失时是谁的问题。
//
// 输出：双通道，前缀 [SVPN]
//   ① NSLog —— 系统统一日志服务，可用 log stream/show 抓
//   ② 文件 —— <jbroot>/var/mobile/Library/Logs/SingleVPN/svpn.log，
//      用 Filza 直接打开。RootHide 的 jbroot 在 AppGroup 随机哈希目录，
//      无法硬编码，所以用 dladdr 反推自身路径（CellularTint 验证过可行）。
//   两条都写，任一条能取到就够。
//
// 用法：安装后 respring 几次，复现后把日志导出来看。
// 判定要点见每条记录里的 isNil / isEn 字段。
#define SVPN_DIAG 1

#if SVPN_DIAG
static NSUInteger gDiagCount = 0;
static NSUInteger gDiagMax = 120;          // 异常样本上限

/// 当前记录序号（0 表示这次没记录），供 out 段配平使用
static NSUInteger gDiagSeq = 0;

/// 颜色染色采样计数（单独计数，避免被数据异常记录挤满）
static NSUInteger gColorDiagCount = 0;

/// styleAttrs 染色采样计数（蜂窝图标走这里，与 WiFi 的 activeColor 分开计）
static NSUInteger gStyleDiagCount = 0;

/// 上一份被记录过的 data 指针。_updateWithAggregatedData: 每帧都走，
/// 同一次渲染可能连着调好几次；同一份数据的对象地址相同就跳过，
/// 避免重复拼字符串（热路径开销的主要来源）。
static const void *gLastLoggedData = NULL;
static const void *gLastLoggedData2 = NULL;   // _updateWithData: 专用，避免两个 hook 互相干扰去重

/// ★ 心跳统计：解决「图标消失了但日志一片空白」的判定盲区。
/// 如果只在数据可疑时记录，那"数据层一切正常但视图没画出来"这种情况
/// 会留下零条 AGG 日志，拿到日志也无法区分是"没更新"还是"更新了但正常"。
///
/// ★ 但心跳本身绝不能变成新的热路径负担：
///   - 计数用普通自增（无锁、无分配），几乎零开销
///   - 只有「满 N 次」且「总条数未超限」且「距上次心跳超过 2 秒」才真正打日志
///   - 总上限 gHeartbeatMax 条，防止状态栏动画期间刷爆文件
/// 三条限制缺一不可 —— 直接写文件是主线程 IO，量大就是卡顿源。
///
/// ★ 顺序要求：这些变量必须定义在 SVPNHeartbeatDue 之前，
///   否则函数体内引用到的是未声明的标识符（clang 直接报错）。
static NSUInteger gAggCallCount = 0;
static NSUInteger gUpdCallCount = 0;
static NSUInteger gHeartbeatEvery = 50;
static NSUInteger gHeartbeatSent = 0;
static NSUInteger gHeartbeatMax = 12;
static double gLastHeartbeatAt = 0;

/// 心跳是否该真的打一条。三条闸门一起卡，任何一个不满足都直接返回 NO，
/// 让热路径保持「读几个整数就结束」的开销水平。
///
/// 刻意用 clock_gettime 而不是 CACurrentMediaTime：后者要引 QuartzCore 头文件，
/// 而本工程 Makefile 没有显式链该 framework（SpringBoard 进程内虽然一定有，
/// 但编译期缺声明会报隐式声明，属于自找麻烦）。
static BOOL SVPNHeartbeatDue(NSUInteger n) {
    if (n % gHeartbeatEvery != 0) return NO;
    if (gHeartbeatSent >= gHeartbeatMax) return NO;
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return NO;
    double now = (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
    if (now - gLastHeartbeatAt < 2.0) return NO;
    gLastHeartbeatAt = now;
    gHeartbeatSent++;
    return YES;
}

/// 判定「这份数据是否可疑」——只有可疑才落日志。
/// 正常状态下这几个 entry 应当是 enabled=YES 的；出现 nil 或 en=0 都值得记。
///
/// ★ 关于副卡：单卡设备上 secondaryCellularEntry 本来就没有，
///   nil 是正常的，不能算可疑。所以这里对副卡用「存在但 disabled」作为判据，
///   而不是「为 nil」——这样单卡不会刷屏，双卡真降级时又能抓到。
static BOOL SVPNDataLooksSuspicious(id data, BOOL *hasAnyNil, BOOL *hasAnyDisabled) {
    BOOL anyNil = NO, anyDisabled = NO;
    if (!data) {
        if (hasAnyNil) *hasAnyNil = YES;
        if (hasAnyDisabled) *hasAnyDisabled = NO;
        return YES;
    }
    // 主卡 / WiFi：缺失和停用都算可疑
    NSArray *mustKeys = @[@"cellularEntry", @"wifiEntry"];
    for (NSString *k in mustKeys) {
        id e = nil;
        @try { e = [data valueForKey:k]; } @catch (NSException *ex) {}
        if (!e) {
            anyNil = YES;
            continue;
        }
        BOOL en = NO;
        @try {
            if ([e respondsToSelector:@selector(isEnabled)]) en = [e isEnabled];
        } @catch (NSException *ex) {}
        if (!en) anyDisabled = YES;
    }
    // 副卡：只有「存在但停用」才可疑
    @try {
        id sec = [data valueForKey:@"secondaryCellularEntry"];
        if (sec) {
            BOOL en = NO;
            @try {
                if ([sec respondsToSelector:@selector(isEnabled)]) en = [sec isEnabled];
            } @catch (NSException *ex) {}
            if (!en) anyDisabled = YES;
        }
    } @catch (NSException *ex) {}

    if (hasAnyNil) *hasAnyNil = anyNil;
    if (hasAnyDisabled) *hasAnyDisabled = anyDisabled;
    return (anyNil || anyDisabled);
}

/// 把一个 entry 描述成「存在与否 + enabled + 类名」
/// 关键：entry 可能为 nil（未下发）、也可能存在但 enabled=NO（空壳）
static NSString *SVPNEntryDesc(NSString *name, id entry) {
    if (!entry) {
        return [NSString stringWithFormat:@"%@=nil", name];
    }
    BOOL en = NO;
    @try {
        if ([entry respondsToSelector:@selector(isEnabled)]) {
            en = [entry isEnabled];
        }
    } @catch (NSException *e) {
    }
    return [NSString stringWithFormat:@"%@=obj(en=%d,%@)",
                                      name, en, NSStringFromClass([entry class])];
}

/// 把颜色转成可比较的文本（不打印对象地址，否则每次日志都不一样，没法比对）
static NSString *SVPNColorDesc(UIColor *c) {
    if (!c) {
        return @"nil";
    }
    CGFloat r = 0, g = 0, b = 0, a = 0;
    BOOL rgb = NO;
    @try {
        rgb = [c getRed:&r green:&g blue:&b alpha:&a];
    } @catch (NSException *e) {
    }
    if (!rgb) {
        // 关键特征：getRed: 失败的颜色（动态色 / 灰度色 / 图案色）。
        // svpn_isDarkColor 遇到这种会一律当亮色，是本插件最容易失手的地方。
        return [NSString stringWithFormat:@"nonRGB(%@)", NSStringFromClass([c class])];
    }
    return [NSString stringWithFormat:@"rgba(%d,%d,%d,%d)",
                    (int)lround(r * 255), (int)lround(g * 255),
                    (int)lround(b * 255), (int)lround(a * 255)];
}

// ---- 落盘通道（NSLog 抓不到时的兜底）------------------------------------
// 为什么两路都做：NSLog 走系统的统一日志服务，需要从电脑或终端去捞，
// 多一道手续；而 dylib 自己就躺在 <jbroot>/Library/MobileSubstrate/DynamicLibraries/，
// 用 dladdr 反推 jbroot 后直接写文件，用户用 Filza 就能打开看。
// 这条路 CellularTint 验证过可用（见该项目 main.m 的 CTResolveJbroot）。
//
// 安全约束（注入 SpringBoard，任何异常都不能外泄）：
//   * 只写固定路径、固定文件名，不做任何拼接扫描
//   * 无上限行数控制由调用方 gDiagCount 决定（最多 120 条）
//   * 全程 @try/@catch，失败静默降级为「只有 NSLog」
//   * 用 appendFile 而不是自己 fopen/写/关，交给 Foundation 管
static NSString *SVPNLogFilePath(void) {
    // 注意：这里不能用 dispatch_once —— 若首次反推 jbroot 失败，
    // once 已被消耗，之后再也不会重试，整个诊断通道就永久哑掉。
    // 改用一个普通标记：成功后才锁定。
    static NSString *cached = nil;
    static NSString *failed = nil;   // 只要失败过就不再重试，避免每次打日志都去 IO
    if (cached || failed) {
        return cached;
    }
    @try {
        NSMutableArray *dirs = [NSMutableArray array];

        // ★ 候选顺序按「在 SpringBoard 里越可能可写」排，而不是按「越标准」排。
        //
        // 实测教训：上一版只放 /var/mobile/Library/Logs/... 和 jbroot 下两条，
        // 用户机上**连目录都建不出来**（整个 SingleVPN 文件夹不存在）。
        // 说明 RootHide 环境下 SpringBoard 对 /var/mobile/Library 没有创建权限。
        //
        // 关键洞察：tweak 的配置 plist 一直写在
        //   /var/mobile/Library/Preferences/
        // 并且一直是成功的（插件能读到开关）。这条路径**已被证明可写**，
        // 所以优先用它。日志文件名带自己的前缀，不与配置冲突。
        NSString *prefsDir = @"/var/mobile/Library/Preferences";
        [dirs addObject:prefsDir];

        // 其次：jbroot 内 Library/Logs（RootHide 下这个根通常可写）
        Dl_info info;
        if (dladdr((const void *)SVPNLogFilePath, &info) && info.dli_fname) {
            NSString *selfPath = [NSString stringWithUTF8String:info.dli_fname];
            NSString *p2 = selfPath;
            for (int i = 0; i < 4 && p2.length > 1; i++) {
                p2 = [p2 stringByDeletingLastPathComponent];
            }
            if (p2.length > 1) {
                [dirs addObject:[p2 stringByAppendingPathComponent:@"var/mobile/Library/Preferences"]];
                [dirs addObject:[p2 stringByAppendingPathComponent:@"Library/Logs"]];
            }
        }

        // 兜底：常见 RootHide 根与标准日志目录
        [dirs addObject:@"/var/mobile/Library/Logs"];
        [dirs addObject:@"/var/jb/var/mobile/Library/Logs"];
        [dirs addObject:@"/tmp"];

        NSFileManager *fm = [NSFileManager defaultManager];
        for (NSString *dir in dirs) {
            if (![fm fileExistsAtPath:dir]) {
                [fm createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:NULL];
            }
            if (![fm fileExistsAtPath:dir]) {
                continue;   // 建不出来，换下一个
            }
            // 用真实文件名探测，而不是 .probe：万一 .probe 被沙盒特判，
            // 至少能确认「这个目录能写我自己的文件」。
            NSString *target = [dir stringByAppendingPathComponent:@"SingleVPN-svpn.log"];
            NSData *d = [@"1" dataUsingEncoding:NSUTF8StringEncoding];
            if ([d writeToFile:target atomically:NO]) {
                // 探测成功，把探测内容清掉，之后正常追加
                [fm removeItemAtPath:target error:NULL];
                cached = target;
                break;
            }
        }
        if (!cached) {
            failed = @"1";
        }
    } @catch (NSException *e) {
        failed = @"1";
        cached = nil;
    }
    return cached;
}

/// ★ 第二通道：写进 NSUserDefaults（走 cfprefsd → plist）。
/// 为什么需要：文件通道可能因沙盒被整条拒绝（已真实发生过一次，用户机上
/// 连目录都建不出来）。而 tweak 的配置 plist 一直是能写的，
/// 说明 cfprefsd 这条链路在 SpringBoard 里是可用的。
/// 代价是每次写都要序列化，所以只保留最近 N 行，且按计数节流。
static NSMutableArray *gDefaultsLines = nil;
static NSUInteger gDefaultsWrites = 0;
#define SVPN_DEFAULTS_MAX_LINES 40
#define SVPN_DEFAULTS_MAX_WRITES 60   // 总写入次数上限，防止长期运行磨损 plist

static void SVPNPushToDefaults(NSString *line) {
    if (!line.length) return;
    if (gDefaultsWrites >= SVPN_DEFAULTS_MAX_WRITES) return;
    @try {
        if (!gDefaultsLines) {
            gDefaultsLines = [NSMutableArray array];
        }
        [gDefaultsLines addObject:line];
        while ((NSUInteger)[gDefaultsLines count] > SVPN_DEFAULTS_MAX_LINES) {
            [gDefaultsLines removeObjectAtIndex:0];
        }
        // 每 5 行刷一次，避免每行都触发一次 cfprefsd 往返
        gDefaultsWrites++;
        if (gDefaultsWrites % 5 != 0 && gDefaultsWrites < SVPN_DEFAULTS_MAX_WRITES) {
            return;
        }
        NSArray *snap = [gDefaultsLines copy];
        CFStringRef key = CFSTR("DiagLog");
        CFStringRef app = CFSTR("com.82flex.singlevpn");
        CFPreferencesSetAppValue(key, (__bridge CFPropertyListRef)snap, app);
        CFPreferencesAppSynchronize(app);
    } @catch (NSException *e) {
        // 静默
    }
}

/// 追加一行到文件（失败不报错，不影响宿主）
static void SVPNAppendToFile(NSString *line) {
    @try {
        NSString *path = SVPNLogFilePath();
        if (!path || !line.length) {
            // 文件通道不可用时，至少让内容进 plist
            SVPNPushToDefaults(line);
            return;
        }
        NSString *text = [line stringByAppendingString:@"\n"];
        NSData *data = [text dataUsingEncoding:NSUTF8StringEncoding];
        if (!data) {
            return;
        }
        NSFileManager *fm = [NSFileManager defaultManager];
        if (![fm fileExistsAtPath:path]) {
            [data writeToFile:path atomically:NO];
            return;
        }
        NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:path];
        if (!fh) {
            SVPNPushToDefaults(line);
            return;
        }
        @try {
            [fh seekToEndOfFile];
            [fh writeData:data];
        } @finally {
            [fh closeFile];
        }
    } @catch (NSException *e) {
        SVPNPushToDefaults(line);
    }
}

/// 统一输出入口：NSLog（便于用 log 命令抓）+ 文件（便于 Filza 直接看）
// 刻意「不声明任何局部变量」——本宏有调用点位于 @catch 块内，
// @catch 对跨初始化的局部声明有额外限制，少一个变量少一分编译失败风险。
#define SVPNLog(fmt, ...) do { \
    NSLog(fmt, ##__VA_ARGS__); \
    SVPNAppendToFile([NSString stringWithFormat:fmt, ##__VA_ARGS__]); \
} while (0)

/// 一次性打印一条 STStatusBarData 的全貌
static void SVPNLogData(NSString *tag, id data) {
    if (!data) {
        SVPNLog(@"[SVPN] %@ data=nil", tag);
        return;
    }


    NSMutableString *s = [NSMutableString stringWithFormat:@"[SVPN] %@ {", tag];
    @try {
        // 这几个是我们关心的；vpn / cellular / wifi 直接读属性
        id vpn = nil, cel = nil, sec = nil, wifi = nil;
        @try { vpn = [data valueForKey:@"vpnEntry"]; } @catch (NSException *e) {}
        @try { cel = [data valueForKey:@"cellularEntry"]; } @catch (NSException *e) {}
        @try { sec = [data valueForKey:@"secondaryCellularEntry"]; } @catch (NSException *e) {}
        @try { wifi = [data valueForKey:@"wifiEntry"]; } @catch (NSException *e) {}

        [s appendString:SVPNEntryDesc(@"vpn", vpn)];
        [s appendString:@" "];
        [s appendString:SVPNEntryDesc(@"cell", cel)];
        [s appendString:@" "];
        [s appendString:SVPNEntryDesc(@"sec", sec)];
        [s appendString:@" "];
        [s appendString:SVPNEntryDesc(@"wifi", wifi)];

        // 蜂窝的文字内容（4G/5G/LTE），能反映「有新数据还是空壳」
        if (cel) {
            NSString *str = nil, *cross = nil;
            @try { str = [cel valueForKey:@"string"]; } @catch (NSException *e) {}
            @try { cross = [cel valueForKey:@"crossfadeString"]; } @catch (NSException *e) {}
            [s appendFormat:@" cellStr=%@ cross=%@", str ?: @"nil", cross ?: @"nil"];
        }
    } @catch (NSException *e) {
        [s appendFormat:@" <exception %@>", e.reason];
    }
    [s appendString:@"}"];
    SVPNLog(@"%@", s);
}

#endif
// ============================================================================

%group SingleVPN_17

%hook STUIStatusBar

- (void)_updateWithAggregatedData:(STStatusBarData *)data {
    STStatusBarData *currentData = [self currentData];
    _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;

#if SVPN_DIAG
    // ★ 只在数据可疑时记录：这个方法是每帧走的，绝不能无条件拼字符串。
    //   可疑 = cellularEntry / wifiEntry 出现 nil 或 enabled=NO。
    //   同一份 data（同一地址）只记一次，避免重复拼串拖慢 SpringBoard。
    gDiagSeq = 0;
    // 心跳：独立于 gDiagCount 上限，保证「数据一直正常」时也能证明 hook 在跑
    gAggCallCount++;
    if (SVPNHeartbeatDue(gAggCallCount)) {
        BOOL hn = NO, hd = NO;
        SVPNDataLooksSuspicious(data, &hn, &hd);
        SVPNLog(@"[SVPN] HB agg n=%lu nil=%d dis=%d vpnOn=%d cur=%@ %@",
              (unsigned long)gAggCallCount, (int)hn, (int)hd, (int)_isVPNEnabled,
              SVPNEntryDesc(@"cell", currentData.cellularEntry),
              SVPNEntryDesc(@"wifi", currentData.wifiEntry));
    }
    if (gDiagCount < gDiagMax) {
        const void *addr = (__bridge const void *)data;
        // 只有「和上一条完全同一份对象」才跳过，避免每帧重复拼串。
        // 注意不能只在可疑时才更新 gLastLoggedData —— 那样同一地址
        // 在「先正常、后可疑」两次回调里会被判成新数据而重复记录，
        // 且在地址被复用时可能漏记。这里无条件记账，只做相邻去重。
        if (addr != gLastLoggedData) {
            gLastLoggedData = addr;
            BOOL anyNil = NO, anyDis = NO;
            BOOL bad = SVPNDataLooksSuspicious(data, &anyNil, &anyDis);
            if (bad) {
                gDiagCount++;
                gDiagSeq = gDiagCount;
                SVPNLog(@"[SVPN] #%lu AGG SUSPECT nil=%d disabled=%d vpnOn=%d",
                      (unsigned long)gDiagSeq, (int)anyNil, (int)anyDis, (int)_isVPNEnabled);
                SVPNLogData(@"AGG  in ", data);
                SVPNLogData(@"AGG  cur", currentData);
            }
        }
    }
#endif

    if (!SVPN_DISABLE_ENTRY_MERGE && _isVPNEnabled) {
        data = svpnMergeMissingEntries(self, data);
    }

#if SVPN_DIAG
    if (gDiagSeq) {
        SVPNLogData(@"AGG  out", data);
    }
#endif

    %orig;
}

- (void)_updateWithData:(STStatusBarData *)data completionHandler:(id)a4 {
    STStatusBarData *currentData = [self currentData];
    _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;

#if SVPN_DIAG
    gDiagSeq = 0;
    gUpdCallCount++;
    if (SVPNHeartbeatDue(gUpdCallCount)) {
        BOOL hn = NO, hd = NO;
        SVPNDataLooksSuspicious(data, &hn, &hd);
        SVPNLog(@"[SVPN] HB upd n=%lu nil=%d dis=%d vpnOn=%d cur=%@ %@",
              (unsigned long)gUpdCallCount, (int)hn, (int)hd, (int)_isVPNEnabled,
              SVPNEntryDesc(@"cell", currentData.cellularEntry),
              SVPNEntryDesc(@"wifi", currentData.wifiEntry));
    }
    if (gDiagCount < gDiagMax) {
        const void *addr = (__bridge const void *)data;
        // 用独立变量做去重：两个 hook 共用一份会互相覆盖，导致该记的被跳过
        if (addr != gLastLoggedData2) {
            gLastLoggedData2 = addr;
            BOOL anyNil = NO, anyDis = NO;
            BOOL bad = SVPNDataLooksSuspicious(data, &anyNil, &anyDis);
            if (bad) {
                gDiagCount++;
                gDiagSeq = gDiagCount;
                SVPNLog(@"[SVPN] #%lu UPD SUSPECT nil=%d disabled=%d vpnOn=%d",
                      (unsigned long)gDiagSeq, (int)anyNil, (int)anyDis, (int)_isVPNEnabled);
                SVPNLogData(@"UPD  in ", data);
                SVPNLogData(@"UPD  cur", currentData);
            }
        }
    }
#endif

    if (!SVPN_DISABLE_ENTRY_MERGE && _isVPNEnabled) {
        data = svpnMergeMissingEntries(self, data);
    }

#if SVPN_DIAG
    if (gDiagSeq) {
        SVPNLogData(@"UPD  out", data);
    }
#endif

    %orig;
}

%end

%hook STUIStatusBarCellularNetworkTypeView

- (void)setText:(NSString *)text prefixLength:(NSInteger)prefixLength withStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs forType:(NSInteger)type animated:(BOOL)animated {
    if (![text hasPrefix:@"5G"] || !_isForce5GAEnabled) {
        %orig;
        if (@available(iOS 16, *)) {
            if (_isForce5GAEnabled && !self.widthConstraint.active) {
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
    if (@available(iOS 16, *)) {
        if (!_isForce5GAEnabled) {
            return;
        }

        NSString *text = self.text;
        if (![text hasPrefix:@"5G"]) {
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
        NSMutableAttributedString *attributedText = [[NSMutableAttributedString alloc] initWithString:newText attributes:@{
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: textColor,
        }];

        NSMutableDictionary *traits = [[font.fontDescriptor objectForKey:UIFontDescriptorTraitsAttribute] mutableCopy] ?: [NSMutableDictionary dictionary];
        traits[UIFontWidthTrait] = @(UIFontWidthCondensed / 1.5);
        traits[UIFontWeightTrait] = @(UIFontWeightSemibold);

        UIFontDescriptor *condensedDescriptor = [font.fontDescriptor fontDescriptorByAddingAttributes:@{UIFontDescriptorTraitsAttribute: traits}];
        UIFont *condensedFont = [UIFont fontWithDescriptor:condensedDescriptor size:0];
        UIFont *smallerCondensedFont = [condensedFont fontWithSize:condensedFont.pointSize * 0.7];

        [attributedText addAttribute:NSFontAttributeName value:condensedFont range:NSMakeRange(0, prefixLength)];
        [attributedText addAttribute:NSFontAttributeName value:smallerCondensedFont range:NSMakeRange(prefixLength, attributedText.length - prefixLength)];

        self.attributedText = attributedText;
    }
}

- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs {
    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision && IsNetworkTypeText(self.text)) {
        styleAttrs = [styleAttrs copy];
        [styleAttrs setTextColor:svpnColorWithTextColor(styleAttrs.textColor)];
    }

    %orig;
    [self svpnApply5GAdvancedAttributesIfNeeded];
}

- (void)setText:(NSString *)text {
    %orig;

    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision && IsNetworkTypeText(text)) {
        [self setTextColor:svpnColorWithTextColor(self.textColor)];
    }

    [self svpnApply5GAdvancedAttributesIfNeeded];
}

%end

%hook STUIStatusBarWifiSignalView

- (void)setActiveColor:(UIColor *)activeColor {
    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;

#if SVPN_DIAG
    // 只在「颜色为空」或「前 40 次染色」时记录 —— 后者用来抓
    // respring 后第一批渲染的输入/输出颜色（暗色误判就发生在这里）。
    // 正常染色不该额外产生任何日志，也不重复计算颜色。
    if ((!activeColor || gColorDiagCount < 40) && gDiagCount < gDiagMax) {
        gDiagCount++;
        gColorDiagCount++;
        SVPNLog(@"[SVPN] #%lu WIFI setActiveColor in=%@ tint=%d out=%@ self=%@",
              (unsigned long)gDiagCount, SVPNColorDesc(activeColor), (int)decision,
              SVPNColorDesc(decision ? svpnColorWithTextColor(activeColor) : activeColor),
              NSStringFromClass([self class]));
    }
#endif

    if (decision) {
        activeColor = svpnColorWithTextColor(activeColor);
    }

    %orig;
}

- (void)setInactiveColor:(UIColor *)inactiveColor {
    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;
    if (decision) {
        inactiveColor = [svpnColorWithTextColor(inactiveColor) colorWithAlphaComponent:0.2];
    }

    %orig;
}

- (void)applyStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs {
    BOOL decision = _isEnabledReversed ? !_isVPNEnabled : _isVPNEnabled;

#if SVPN_DIAG
    // 这是蜂窝图标真正吃染色的入口（改的是 styleAttrs 的 imageTintColor，
    // 不是 view 的 activeColor）。前 40 次 + 输入为空时记录。
    if ((!styleAttrs || gStyleDiagCount < 40) && gDiagCount < gDiagMax) {
        gDiagCount++;
        gStyleDiagCount++;
        UIColor *inColor = nil;
        @try { inColor = styleAttrs.textColor; } @catch (NSException *e) {}
        SVPNLog(@"[SVPN] #%lu STYLE in=%@ tint=%d out=%@ self=%@",
              (unsigned long)gDiagCount, SVPNColorDesc(inColor), (int)decision,
              SVPNColorDesc(decision ? svpnColorWithTextColor(inColor) : inColor),
              NSStringFromClass([self class]));
    }
#endif

    if (decision) {
        styleAttrs = [styleAttrs copy];
        UIColor *newColor = svpnColorWithTextColor(styleAttrs.textColor);
        [styleAttrs setImageTintColor:newColor];
        [styleAttrs setImageDimmedTintColor:[newColor colorWithAlphaComponent:0.2]];
    }

    %orig;
}

%end

%end // SingleVPN_17

%ctor {
    ReloadPrefs();

#if SVPN_DIAG
    // ★ 必须在 IsEnabled 判断之前打：否则「插件被关掉」时一条日志都没有，
    //   会被误判成「诊断版没装上」，白跑一轮。
    SVPNLog(@"[SVPN] ==== ctor enter ==== (SVPN_DIAG=1 version=2.1-6)");
    SVPNLog(@"[SVPN] prefs raw IsEnabled=%d", (int)_isEnabled);
    // 告诉用户日志落在哪：抓不到系统日志时可直接用 Filza 打开这个文件
    NSString *svpn_lp = SVPNLogFilePath();
    SVPNLog(@"[SVPN] logfile=%@", svpn_lp ? svpn_lp : @"(写入失败，只能看系统日志)");
#endif

    if (!_isEnabled) {
#if SVPN_DIAG
        SVPNLog(@"[SVPN] ==== ctor exit: plugin disabled in prefs, no hooks ====");
#endif
        return;
    }

#if SVPN_DIAG
    // 启动快照：先确认「我们以为 hook 到的类」真的存在。
    // 若某个类是 Nil，那套 hook 就是空转 —— 所有基于它的推理全部失效。
    SVPNLog(@"[SVPN] ==== ctor begin ====");
    SVPNLog(@"[SVPN] prefs IsEnabled=%d Rev=%d Force5GA=%d light=%@ dark=%@",
          (int)_isEnabled, (int)_isEnabledReversed, (int)_isForce5GAEnabled,
          SVPNColorDesc(_lightReplacementColor), SVPNColorDesc(_darkReplacementColor));

    // 外观 + 系统色基准：svpn_isDarkColor 对「非 RGB 色」一律返回 NO（当亮色处理），
    // 所以在浅色模式下遇到动态色会选错色。这里把判定结果直接打出来。
    @try {
        UITraitCollection *tr = nil;
        @try {
            UIApplication *app = [UIApplication sharedApplication];
            if (app) {
                tr = [(id)app traitCollection];
            }
        } @catch (NSException *e) {}
        if (!tr) {
            // %ctor 阶段 sharedApplication 可能还没建好，用当前 trait 兜底
            @try { tr = [UITraitCollection currentTraitCollection]; } @catch (NSException *e) {}
        }
        NSString *style = @"?";
        if (tr) {
            style = (tr.userInterfaceStyle == UIUserInterfaceStyleDark) ? @"dark"
                   : (tr.userInterfaceStyle == UIUserInterfaceStyleLight ? @"light" : @"unspecified");
        }
        UIColor *labelC = nil;
        @try { labelC = [UIColor labelColor]; } @catch (NSException *e) {}
        BOOL dark = NO;
        @try { dark = [labelC svpn_isDarkColor]; } @catch (NSException *e) {}
        SVPNLog(@"[SVPN] appearance=%@ labelColor=%@ isDark=%d => pick=%@",
              style, SVPNColorDesc(labelC), (int)dark,
              SVPNColorDesc(svpnColorWithTextColor(labelC)));
    } @catch (NSException *e) {
        SVPNLog(@"[SVPN] appearance probe failed: %@", e.reason);
    }
    NSArray *cls = @[@"STUIStatusBar", @"STUIStatusBarCellularNetworkTypeView",
                     @"STUIStatusBarStringView", @"STUIStatusBarWifiSignalView",
                     @"STUIStatusBarCellularSignalView",
                     @"STUIStatusBarDualCellularSignalView"];
    for (NSString *n in cls) {
        Class c = NSClassFromString(n);
        SVPNLog(@"[SVPN] class %@ = %@", n, c ? @"FOUND" : @"NIL");
    }

    // ★ 私有 API 存在性探测：这几处在染色路径上被直接调用，
    //   如果类不支持某选择子，运行时就是 unrecognized selector（可能崩 SpringBoard）。
    //   本段只做查询，不调用，无副作用。
    @try {
        Class sc = NSClassFromString(@"STUIStatusBarStyleAttributes");
        if (sc) {
            NSArray *sels = @[@"copy", @"copyWithZone:",
                              @"setImageTintColor:", @"setImageDimmedTintColor:",
                              @"textColor", @"setTextColor:"];
            NSMutableArray *have = [NSMutableArray array];
            NSMutableArray *miss = [NSMutableArray array];
            for (NSString *s in sels) {
                BOOL inst = [sc instancesRespondToSelector:NSSelectorFromString(s)];
                [(inst ? have : miss) addObject:s];
            }
            SVPNLog(@"[SVPN] StyleAttributes SUPPORTED=%@ MISSING=%@", have, miss);
        } else {
            SVPNLog(@"[SVPN] StyleAttributes = NIL");
        }
    } @catch (NSException *e) {
        SVPNLog(@"[SVPN] selector probe failed: %@", e.reason);
    }
    SVPNLog(@"[SVPN] ==== ctor end ====");
#endif

    CFNotificationCenterAddObserver(
        CFNotificationCenterGetDarwinNotifyCenter(), 
        NULL, 
        (CFNotificationCallback)ReloadPrefs, 
        CFSTR("com.82flex.singlevpnprefs/saved"), 
        NULL, 
        CFNotificationSuspensionBehaviorCoalesce
    );

    if (@available(iOS 17, *)) {
        dlopen("/System/Library/PrivateFrameworks/StatusStatusUI.framework/StatusStatusUI", RTLD_LAZY);
        %init(SingleVPN_17);
    } else {
        %init(SingleVPN_16);
    }
}