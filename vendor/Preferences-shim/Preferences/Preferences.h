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

// ★★ 设置 specifier 属性的唯一正确入口（本项目强制约定）
//
// 为什么不能直接写 [spec setProperty:x forKey:@"..."]：
//   PSSpecifier 的 -setProperty:forKey: 与 NSStream 的 -setProperty:forKey: 同名异签名
//   （前者返回 void 且 key 为 NSString*，后者返回 BOOL 且 key 为 NSStreamPropertyKey）。
//   · 若在 @interface 里声明它 → 接收者静态类型为 id 时 clang 硬报错：
//       error: multiple methods named 'setProperty:forKey:' found with mismatched
//              result, parameter type or attributes
//   · 若不声明 → NSStream.h 那份还在，会按 BOOL 签名编译通过，而真实实现返回 void，
//     arm64 上返回值寄存器语义不符，变成更难查的隐性错误。
//   ⇒ 唯一安全做法：走字符串选择子 + methodForSelector + 显式函数指针强转，
//     编译器完全不参与签名解析。
//
// 用法：SVPNSetSpecifierProperty(spec, @"cellClass", someClass);
static inline void SVPNSetSpecifierProperty(id spec, NSString *key, id value) {
    if (!spec || !key) return;
    SEL sel = NSSelectorFromString(@"setProperty:forKey:");
    if (![spec respondsToSelector:sel]) return;
    void (*fn)(id, SEL, id, NSString *) =
        (void (*)(id, SEL, id, NSString *))[spec methodForSelector:sel];
    if (fn) fn(spec, sel, value, key);
}

#pragma mark - PSListController

// ★ _specifiers 必须声明为**可见 ivar**（用 @public 或至少 protected 默认），
//   原因：SingleVPNRootListController 在**自己的 @implementation 里**直接读写
//   `_specifiers`（懒加载缓存 specifiers）。不声明 ivar 就只有 getter 方法，
//   编译器报 `use of undeclared identifier '_specifiers'`。
//
//   @property specifiers 会自动合成 _specifiers 吗？**不会**：
//   属性名是 specifiers，ivar 是 _specifiers，名字对得上会自动合成 ——
//   但只在「本类自己 @synthesize」时才生成 ivar。这里是**头文件里的声明**，
//   子类继承不到父类的 ivar 合成，所以必须显式 @public NSArray *_specifiers;。
//
//   ★ 注意这与「用 class_getInstanceVariable 拿系统那份真实 ivar 的偏移量」是
//   两回事：这里只是让**编译器**知道有这个成员、能算出偏移量用于编译期访问。
//   真正的好处是：设置 App 里 PSListController 的真实布局与这个声明一致
//   （_specifiers 是较靠前的 ivar，实际工程编译运行正常即可），
//   若发现运行时读写串味，才需要换成 object_getIvar 方案。
@interface PSListController : UIViewController {
@public
    NSArray *_specifiers;
}
@property(nonatomic, strong) NSArray *specifiers;
- (NSArray *)loadSpecifiersFromPlistName:(NSString *)name target:(id)target;
- (void)reloadSpecifiers;
- (void)reloadSpecifier:(PSSpecifier *)specifier;
- (void)reloadSpecifier:(PSSpecifier *)specifier animated:(BOOL)animated;
- (void)reloadSpecifierID:(NSString *)identifier;

// 以下三个是 SingleVPNRootListController.m 实际调用的：
- (void)setPreferenceValue:(id)value specifier:(PSSpecifier *)specifier;
- (PSSpecifier *)specifierAtIndexPath:(NSIndexPath *)indexPath;
- (UITableViewCell *)tableView:(UITableView *)tableView
         cellForRowAtIndexPath:(NSIndexPath *)indexPath;
@end

#pragma mark - PSTableCell

@interface PSTableCell : UITableViewCell
@property(nonatomic, strong) PSSpecifier *specifier;
- (instancetype)initWithStyle:(UITableViewCellStyle)style
              reuseIdentifier:(NSString *)reuseIdentifier
                    specifier:(PSSpecifier *)specifier;
@end

#endif /* SVPN_PREFERENCES_SHIM_H */
