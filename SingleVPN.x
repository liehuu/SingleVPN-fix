// ============================================================================
// SingleVPN —— 状态栏 VPN 染色
//
// 2.2-1：重写。
// 上一版（2.1-x 系列）在「还原机制」上叠了四轮补丁，每次修完又冒出新症状。
// 重新写一遍，只保留经过实测确认的机制，其余全部删掉。
//
// 保留的四条（都有用户实测背书，缺一不可）：
//   ① 启动 5 秒闸门 —— respring 后主屏状态栏实例在那一刻建成，插件介入会把
//      它建成残缺状态，且 iOS 17 之后不会重建。整体挪出启动窗口即可（2.1-8）。
//   ② VPN 状态只认本轮 data —— 与上一轮快照做或运算会把断开时的 YES 永久
//      锁存，导致断开不褪色（2.1-9）。
//   ③ 断开后主动写回原色 —— 视图染色是「一次性下发」，断开不改变格数/强度，
//      系统不重发样式，上一轮的色就留在视图上（2.1-10）。
//   ④ 换色相时保住系统 alpha —— 这是「桌面不定期图标空白」的真正根因（2.1-12）。
//
// 删掉的三类：
//   · 诊断/取证脚手架（SVPN_DIAG 整块、日志双通道、心跳计数、地址去重）
//     它们只在排查期需要；留在交付版里是热路径负担与新的干扰源。
//   · entry 回填（svpnMergeMissingEntries）
//     回填源是「已经渲染出去的上一轮数据」，respring/切换网络时可能是
//     「存在但 enabled=NO」的空壳，塞回渲染管线 = 图标整块空白。
//   · 还原机制的过度设计（全局外观 flag、tint 槽位、强制重绘）
//     详见下面「还原」一节的注释。
//
// ★★ 关于「图标空白」的定性（这一版最重要的一条，别再走错方向）：
//   用户实测：桌面不定期「信号条 + WiFi 一起彻底空着，连轮廓都没有」，
//   深浅色背景都出现过，改一下网络或过一会儿自己恢复，**且原版也有**。
//   「原版也有」这一条排除了所有 2.1-x 新写的代码 —— 根因在上游染色里：
//
//     [svpnColorWithTextColor(x) colorWithAlphaComponent:0.2]
//
//   colorWithAlphaComponent: 是**把 alpha 替换成 0.2**，不是叠加。系统给
//   未激活信号格的 alpha 本来接近 1（实心灰格），被压到 0.2 后：
//     深色壁纸 → rgb(10,40,18)  ≈ 全黑
//     浅色壁纸 → rgb(214,244,222) ≈ 全白
//   两边都「看不见」。改网络/等一会儿恢复，是系统重新下发样式把 alpha 改回去。
//
//   所以本版的核心修复是：**只换 RGB，alpha 沿用原色的**。
//   这样「未激活格比激活格淡」这个系统语义保持不变，我们只负责换色相。
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
    _lightReplacementColor = l ?: [UIColor colorWithRed:0.196 green:0.780 blue:0.349 alpha:1];
    _darkReplacementColor = d ?: [UIColor colorWithRed:0.173 green:0.816 blue:0.341 alpha:1];
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
    @try {
        return [UIColor colorWithRed:r green:g blue:b alpha:sa];
    } @catch (NSException *e) {
        return hue;
    }
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
// 数据结构：**一视图一字典**，键是槽位名，同一字典里放三样：
//     @"tc" / @"ac" / @"ic"  → 系统原本的颜色
//     @"tc#" / @"ac#" / @"ic#" → 我们自己染上去的颜色（残留识别用）
//     @"@style"               → 记账时的界面外观
//   销账时一次 removeObjectForKey 就干净，不存在「两张表漏销一张」的问题。
//
// 四个必须遵守的点（每一条都对应一次踩坑）：
//   ① 残留识别：setText: 路径读到的 self.textColor 可能已被我们染过。
//      若读到的色等于「我上次染的这个值」，它就不是系统色，拒绝记账。
//      不判这一条，第二次调用会把绿色当系统色存下来，还原时还原成绿色。
//   ② 逐视图比对外观：系统色随深浅色变化。若「记账时的外观」与「还原时的
//      外观」不同，这批色已经过期，写回去就是错色 —— 只跳过该视图，不清表
//      （清表会连带丢掉外观没变的那些记录）。
//   ③ 写回前实色 + 幂等：只写能取到 RGBA 的实色；透明色不写（写上去就是
//      看不见）；当前值已相同就不写（我们是在系统更新流程之外改私有属性）。
//   ④ 处理完销账：账本只在一个「染色 → 断开」周期内有效。桌面视图长期存活，
//      账留着就会在下次断开时拿一本跨了多次样式更新的旧账去校准屏幕。

#define SVPN_RESTORE_MAX_VIEWS 32
#define SVPN_STYLE_KEY @"@style"

static NSMapTable *gSVPNLedger = nil;   // 视图(weak, 指针同一性) → NSMutableDictionary
static int gSVPNLastDecision = -1;      // -1 未知 / 0 不染 / 1 染

/// 当前界面外观：1 深色 / 0 浅色 / -1 拿不到。
/// forceRefresh=YES 时绕过缓存 —— 还原是一次性动作，必须拿新鲜值，
/// 否则「刚切外观就断开」这个窗口会判成「外观没变」而写出过期色。
/// 染色热路径传 NO：那里每帧都可能走，不能每次都同步问 UIApplication。
static int SVPNUserInterfaceStyle(BOOL forceRefresh) {
    static int cached = -2;
    static CFAbsoluteTime cachedAt = 0;
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    if (!forceRefresh && cached != -2 && (now - cachedAt) < 5.0) {
        return cached;
    }
    int v = -1;
    @try {
        UITraitCollection *tc = nil;
        @try { tc = [UITraitCollection currentTraitCollection]; } @catch (NSException *e) {}
        if (!tc) {
            @try {
                UIApplication *app = [UIApplication sharedApplication];
                if (app) { tc = [(id)app traitCollection]; }
            } @catch (NSException *e) {}
        }
        if (tc) {
            v = (tc.userInterfaceStyle == UIUserInterfaceStyleDark) ? 1 : 0;
        }
    } @catch (NSException *e) {
        v = -1;
    }
    if (v >= 0) {
        cached = v;
        cachedAt = now;
    }
    return v;
}

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
        int style = SVPNUserInterfaceStyle(NO);
        if (style >= 0) {
            [d setObject:@(style) forKey:SVPN_STYLE_KEY];
        }
    } @catch (NSException *e) {
        // 记账失败最多是这次还原不全，绝不影响宿主
    }
}

/// 写一个色回视图的某个槽位。任意一条检查不过就不写。
static void SVPNRestoreSlot(id view, NSString *setter, NSString *getter, UIColor *color) {
    if (!view || !setter || !color) {
        return;
    }
    @try {
        SEL sel = NSSelectorFromString(setter);
        if (!sel || ![view respondsToSelector:sel]) {
            return;
        }
        CGFloat r = 0, g = 0, b = 0, a = 0;
        if (![color getRed:&r green:&g blue:&b alpha:&a]) {
            return;   // 非实色：按当前 trait 重解析可能又变另一个色，不写
        }
        if (a <= 0.001) {
            return;   // 透明色写上去就是看不见
        }
        // 幂等：已经相同就不写
        if (getter) {
            SEL gsel = NSSelectorFromString(getter);
            if (gsel && [view respondsToSelector:gsel]) {
                @try {
                    IMP gimp = [view methodForSelector:gsel];
                    if (gimp) {
                        // __unsafe_unretained：ARC 不知道函数指针返回对象的所有权，
                        // 默认按 +1 处理会误 release 别人的对象 → 崩宿主。
                        __unsafe_unretained id cur = ((id (*)(id, SEL))gimp)(view, gsel);
                        if ([cur isKindOfClass:[UIColor class]] && [cur isEqual:color]) {
                            return;
                        }
                    }
                } @catch (NSException *e) {
                }
            }
        }
        IMP imp = [view methodForSelector:sel];
        if (imp) {
            ((void (*)(id, SEL, id))imp)(view, sel, color);
        }
    } @catch (NSException *e) {
    }
}

static void SVPNRestoreNow(void) {
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

    int styleNow = SVPNUserInterfaceStyle(YES);

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

    // 槽位 → setter / getter。只碰这三个：来源明确、有对应视图 setter。
    // （tint 那一路已删：记账来源是 textColor 但系统原 imageTintColor 未必等于它，
    //   且 setTintColor: 是 UIView 通用属性会传播到子视图。）
    NSArray *plan = @[
        @[@"tc", @"setTextColor:", @"textColor"],
        @[@"ac", @"setActiveColor:", @"activeColor"],
        @[@"ic", @"setInactiveColor:", @"inactiveColor"],
    ];

    for (id view in views) {
        NSMutableDictionary *d = SVPNLedgerDict(view, NO);
        if (!d) {
            continue;
        }
        // ② 逐视图外观比对：过期就跳过这个视图（不清表）
        BOOL usable = YES;
        @try {
            NSNumber *rec = [d objectForKey:SVPN_STYLE_KEY];
            if (styleNow >= 0 && rec && [rec intValue] != styleNow) {
                usable = NO;
            }
        } @catch (NSException *e) {
            usable = NO;
        }
        if (usable) {
            for (NSArray *pair in plan) {
                UIColor *c = nil;
                @try {
                    c = [d objectForKey:pair[0]];
                } @catch (NSException *e) {
                    c = nil;
                }
                if (c) {
                    SVPNRestoreSlot(view, pair[1], pair[2], c);
                }
            }
        }
        // ④ 销账。原色表必销（账本不跨周期）；
        //    「我染的色」只在真还原时销 —— 外观不符跳过时若把它销掉，
        //    下一轮残留识别就失效，会把绿色当系统色存下来。
        @try {
            [gSVPNLedger removeObjectForKey:view];
        } @catch (NSException *e) {
        }
    }
}

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
    if (SVPNDecision() && IsNetworkTypeText(self.text)) {
        @try {
            // ★ 必须在 copy 之前读：改的是副本，原始入参才是系统本来要用的色
            UIColor *sysTextColor = styleAttrs.textColor;
            UIColor *paint = svpnReplacementFor(sysTextColor);
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

    if (SVPNDecision() && IsNetworkTypeText(text)) {
        @try {
            // self.textColor 可能是我们上一轮染的（SVPNRecord 会识别并拒绝记账）
            UIColor *cur = self.textColor;
            UIColor *paint = svpnReplacementFor(cur);
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
    if (SVPNDecision() && activeColor) {
        @try {
            // 入参就是系统本来要用的色 → 必须改之前记账
            UIColor *paint = svpnReplacementFor(activeColor);
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
    if (SVPNDecision() && styleAttrs) {
        @try {
            UIColor *sysTint = styleAttrs.imageTintColor ?: styleAttrs.textColor;
            UIColor *sysDimmed = styleAttrs.imageDimmedTintColor;
            UIColor *hue = svpnReplacementFor(styleAttrs.textColor ?: sysTint);
            if (hue) {
                STUIStatusBarStyleAttributes *copy = [styleAttrs copy];
                [copy setImageTintColor:hue];
                // ★★ dimmed 也要保 alpha：原来压到 0.2 同样会把次要元素弄没。
                UIColor *dimmed = svpnReplaceKeepingAlpha(sysDimmed ?: sysTint, hue);
                if (dimmed) {
                    [copy setImageDimmedTintColor:dimmed];
                }
                styleAttrs = copy;
                // 这一路不记账：styleAttrs 是临时对象，改它只影响本次绘制。
                // 要还原得落到具体视图上，而视图侧没有与 imageTintColor 对应的
                // 安全 setter（setTintColor: 会传播到子视图）。
                // 这一路的色本来就会在下一次样式下发时被自然覆盖。
                (void)sysTint;
            }
        } @catch (NSException *e) {
        }
    }
    %orig;
}

%end

%hook STUIStatusBarCellularNetworkTypeView

- (void)setText:(NSString *)text prefixLength:(NSInteger)prefixLength withStyleAttributes:(STUIStatusBarStyleAttributes *)styleAttrs forType:(NSInteger)type animated:(BOOL)animated {
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
            (CFNotificationCallback)ReloadPrefs,
            CFSTR("com.82flex.singlevpnprefs/saved"),
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