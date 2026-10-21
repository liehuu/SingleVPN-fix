#import <HBLog.h>
#import <UIKit/UIKit.h>
#import <dlfcn.h>

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
#define SVPN_DISABLE_ENTRY_MERGE 0

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
// 输出：NSLog，前缀 [SVPN]。不写文件 —— RootHide 的 jbroot 在
//       AppGroup 随机哈希目录里，SpringBoard 无该容器访问权（CellularTint 踩过），
//       所以文件方案不可靠；NSLog 由系统统一收口，最省事也最不会失手。
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

/// 上一份被记录过的 data 指针。_updateWithAggregatedData: 每帧都走，
/// 同一次渲染可能连着调好几次；同一份数据的对象地址相同就跳过，
/// 避免重复拼字符串（热路径开销的主要来源）。
static const void *gLastLoggedData = NULL;

/// 判定「这份数据是否可疑」——只有可疑才落日志。
/// 正常状态下这几个 entry 应当是 enabled=YES 的；出现 nil 或 en=0 都值得记。
static BOOL SVPNDataLooksSuspicious(id data, BOOL *hasAnyNil, BOOL *hasAnyDisabled) {
    BOOL anyNil = NO, anyDisabled = NO;
    if (!data) {
        if (hasAnyNil) *hasAnyNil = YES;
        if (hasAnyDisabled) *hasAnyDisabled = NO;
        return YES;
    }
    NSArray *keys = @[@"cellularEntry", @"wifiEntry"];
    for (NSString *k in keys) {
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

/// 一次性打印一条 STStatusBarData 的全貌
static void SVPNLogData(NSString *tag, id data) {
    if (!data) {
        NSLog(@"[SVPN] %@ data=nil", tag);
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
    NSLog(@"%@", s);
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
    if (gDiagCount < gDiagMax && (const void *)data != gLastLoggedData) {
        BOOL anyNil = NO, anyDis = NO;
        BOOL bad = SVPNDataLooksSuspicious(data, &anyNil, &anyDis);
        if (bad) {
            gLastLoggedData = (const void *)data;
            gDiagCount++;
            gDiagSeq = gDiagCount;
            NSLog(@"[SVPN] #%lu AGG SUSPECT nil=%d disabled=%d vpnOn=%d",
                  (unsigned long)gDiagSeq, (int)anyNil, (int)anyDis, (int)_isVPNEnabled);
            SVPNLogData(@"AGG  in ", data);
            SVPNLogData(@"AGG  cur", currentData);
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
    if (gDiagCount < gDiagMax && (const void *)data != gLastLoggedData) {
        BOOL anyNil = NO, anyDis = NO;
        BOOL bad = SVPNDataLooksSuspicious(data, &anyNil, &anyDis);
        if (bad) {
            gLastLoggedData = (const void *)data;
            gDiagCount++;
            gDiagSeq = gDiagCount;
            NSLog(@"[SVPN] #%lu UPD SUSPECT nil=%d disabled=%d vpnOn=%d",
                  (unsigned long)gDiagSeq, (int)anyNil, (int)anyDis, (int)_isVPNEnabled);
            SVPNLogData(@"UPD  in ", data);
            SVPNLogData(@"UPD  cur", currentData);
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
        NSLog(@"[SVPN] #%lu WIFI setActiveColor in=%@ tint=%d out=%@ self=%@",
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
    if (!_isEnabled) {
        return;
    }

#if SVPN_DIAG
    // 启动快照：先确认「我们以为 hook 到的类」真的存在。
    // 若某个类是 Nil，那套 hook 就是空转 —— 所有基于它的推理全部失效。
    NSLog(@"[SVPN] ==== ctor begin ====");
    NSLog(@"[SVPN] prefs IsEnabled=%d Rev=%d Force5GA=%d light=%@ dark=%@",
          (int)_isEnabled, (int)_isEnabledReversed, (int)_isForce5GAEnabled,
          SVPNColorDesc(_lightReplacementColor), SVPNColorDesc(_darkReplacementColor));

    // 外观 + 系统色基准：svpn_isDarkColor 对「非 RGB 色」一律返回 NO（当亮色处理），
    // 所以在浅色模式下遇到动态色会选错色。这里把判定结果直接打出来。
    @try {
        UITraitCollection *tr = nil;
        @try { tr = [(id)[UIApplication sharedApplication] traitCollection]; }
        @catch (NSException *e) {}
        NSString *style = @"?";
        if (tr) {
            style = (tr.userInterfaceStyle == UIUserInterfaceStyleDark) ? @"dark" : @"light";
        }
        UIColor *labelC = nil;
        @try { labelC = [UIColor labelColor]; } @catch (NSException *e) {}
        BOOL dark = NO;
        @try { dark = [labelC svpn_isDarkColor]; } @catch (NSException *e) {}
        NSLog(@"[SVPN] appearance=%@ labelColor=%@ isDark=%d => pick=%@",
              style, SVPNColorDesc(labelC), (int)dark,
              SVPNColorDesc(svpnColorWithTextColor(labelC)));
    } @catch (NSException *e) {
        NSLog(@"[SVPN] appearance probe failed: %@", e.reason);
    }
    NSArray *cls = @[@"STUIStatusBar", @"STUIStatusBarCellularNetworkTypeView",
                     @"STUIStatusBarStringView", @"STUIStatusBarWifiSignalView",
                     @"STUIStatusBarCellularSignalView",
                     @"STUIStatusBarDualCellularSignalView"];
    for (NSString *n in cls) {
        Class c = NSClassFromString(n);
        NSLog(@"[SVPN] class %@ = %@", n, c ? @"FOUND" : @"NIL");
    }
    NSLog(@"[SVPN] ==== ctor end ====");
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