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
//   核心修复：**只换 RGB，alpha 沿用系统原色的**。
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

static NSMapTable *gSVPNLedger = nil;   // 视图(weak, 指针同一性) → NSMutableDictionary
static int gSVPNLastDecision = -1;      // -1 未知 / 0 不染 / 1 染

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
            [gSVPNLedger removeObjectForKey:view];
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