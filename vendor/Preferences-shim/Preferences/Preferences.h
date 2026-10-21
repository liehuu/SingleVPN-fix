// Preferences.framework 的最小兼容头（私有框架，官方 SDK 里没有）。
//
// 为什么需要：本工程已改为「零 Theos」构建，Theos 自带的那套
// $THEOS/vendor/include/Preferences/*.h 不可用。这里只声明我们用到的成员，
// 真正的实现由设置 App 在运行时提供（链接用 -F .../Frameworks 的 Preferences.tbd，
// 符号走 -undefined dynamic_lookup）。
//
// ★ 只放「编译必需」的声明。宁少勿多：
//   多声明一个不存在的方法不会有运行时问题，但会掩盖真实的 API 不匹配。
//   已有教训：setProperty:forKey: 与 NSStream 同名异签名会让 clang 硬报错，
//   这类情况一律走 NSSelectorFromString + methodForSelector，不在此声明。
#ifndef SVPN_PREFERENCES_SHIM_H
#define SVPN_PREFERENCES_SHIM_H

#import <UIKit/UIKit.h>

@class PSSpecifier;

#pragma mark - PSSpecifier

// 注意：只声明 getter 与几个明确用到的 setter。
// setProperty:forKey: 故意不声明 —— 它与 NSStream.h 的同名方法签名冲突，
// 声明后 [id setProperty:...] 会触发 clang 的
// "multiple methods named 'setProperty:forKey:' found with mismatched ..."。
// 需要时用 methodForSelector 动态调用。
@interface PSSpecifier : NSObject
@property(nonatomic, weak) id target;
@property(nonatomic, assign) SEL getter;
@property(nonatomic, assign) SEL setter;
@property(nonatomic, strong) NSString *name;
@property(nonatomic, strong) NSDictionary *properties;
@property(nonatomic, weak) UITableViewCell *cell;

+ (instancetype)preferenceSpecifierNamed:(NSString *)name
                                  target:(id)target
                                    set:(SEL)setter
                                    get:(SEL)getter
                                 detail:(Class)detail;
+ (instancetype)groupSpecifierWithName:(NSString *)name;

- (id)propertyForKey:(NSString *)key;
// ★ 不声明 -setProperty:forKey:
//   它与 NSStream.h 的同名方法签名不一致（NSStream 返回 BOOL，Preferences 返回 void）。
//   一旦声明，写 [someId setProperty:x forKey:@"..."] 时 clang 会在所有已声明方法里
//   找该选择子，找到两个不一致的就报硬错误：
//     multiple methods named 'setProperty:forKey:' found with mismatched result...
//   而删掉声明也解决不了（NSStream 那份还在，会按 BOOL 编译通过，运行时返回值语义不符）。
//   正确做法：NSSelectorFromString + methodForSelector + 显式函数指针强转。
- (void)removePropertyForKey:(NSString *)key;
- (void)performSetterWithValue:(id)value;
- (id)performGetter;
@end

#pragma mark - PSListController

@interface PSListController : UIViewController
@property(nonatomic, strong) NSArray *specifiers;
- (NSArray *)loadSpecifiersFromPlistName:(NSString *)name target:(id)target;
- (void)reloadSpecifiers;
- (void)reloadSpecifier:(PSSpecifier *)specifier;
- (void)reloadSpecifier:(PSSpecifier *)specifier animated:(BOOL)animated;
- (void)reloadSpecifierID:(NSString *)identifier;
@end

#pragma mark - PSTableCell

@interface PSTableCell : UITableViewCell
@property(nonatomic, strong) PSSpecifier *specifier;
- (instancetype)initWithStyle:(UITableViewCellStyle)style
              reuseIdentifier:(NSString *)reuseIdentifier
                    specifier:(PSSpecifier *)specifier;
@end

#endif /* SVPN_PREFERENCES_SHIM_H */
