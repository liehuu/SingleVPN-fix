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

@interface STStatusBarDataCellularEntry : NSObject
@end

@interface STStatusBarDataWifiEntry : NSObject
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

%group SingleVPN_17

%hook STUIStatusBar

- (void)_updateWithAggregatedData:(STStatusBarData *)data {
    BOOL changed = data.vpnEntry;
    STStatusBarData *currentData = [self currentData];
    if (changed && currentData.cellularEntry && !data.cellularEntry) {
        data = [data dataByReplacingEntry:[currentData.cellularEntry copy] forKey:@"cellularEntry"];
    }
    if (changed && currentData.secondaryCellularEntry && !data.secondaryCellularEntry) {
        data = [data dataByReplacingEntry:[currentData.secondaryCellularEntry copy] forKey:@"secondaryCellularEntry"];
    }
    if (changed && currentData.wifiEntry && !data.wifiEntry) {
        data = [data dataByReplacingEntry:[currentData.wifiEntry copy] forKey:@"wifiEntry"];
    }

    _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;
    %orig;
}

- (void)_updateWithData:(STStatusBarData *)data completionHandler:(id)a4 {
    BOOL changed = data.vpnEntry;
    STStatusBarData *currentData = [self currentData];
    if (changed && currentData.cellularEntry && !data.cellularEntry) {
        data = [data dataByReplacingEntry:[currentData.cellularEntry copy] forKey:@"cellularEntry"];
    }
    if (changed && currentData.secondaryCellularEntry && !data.secondaryCellularEntry) {
        data = [data dataByReplacingEntry:[currentData.secondaryCellularEntry copy] forKey:@"secondaryCellularEntry"];
    }
    if (changed && currentData.wifiEntry && !data.wifiEntry) {
        data = [data dataByReplacingEntry:[currentData.wifiEntry copy] forKey:@"wifiEntry"];
    }

    _isVPNEnabled = currentData.vpnEntry.enabled || data.vpnEntry.enabled;
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