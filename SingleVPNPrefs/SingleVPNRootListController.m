#import <Foundation/Foundation.h>
#import <Preferences/PSSpecifier.h>
// UIKit 是显式链接的（SingleVPNPrefs_FRAMEWORKS += UIKit），
// 这里显式导入，避免依赖 Preferences 的传递包含。
#import <UIKit/UIKit.h>
#import <stdlib.h>
#import <sys/sysctl.h>

#import "SingleVPNRootListController.h"

void SingleVPNEnumerateProcessesUsingBlock(void (^enumerator)(pid_t pid, NSString *executablePath, BOOL *stop)) {
    static int kMaximumArgumentSize = 0;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
      size_t valSize = sizeof(kMaximumArgumentSize);
      if (sysctl((int[]){CTL_KERN, KERN_ARGMAX}, 2, &kMaximumArgumentSize, &valSize, NULL, 0) < 0) {
          perror("sysctl argument size");
          kMaximumArgumentSize = 4096;
      }
    });

    size_t procInfoLength = 0;
    if (sysctl((int[]){CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0}, 3, NULL, &procInfoLength, NULL, 0) < 0) {
        return;
    }

    static struct kinfo_proc *procInfo = NULL;
    procInfo = (struct kinfo_proc *)realloc(procInfo, procInfoLength + 1);
    if (!procInfo) {
        return;
    }

    bzero(procInfo, procInfoLength + 1);
    if (sysctl((int[]){CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0}, 3, procInfo, &procInfoLength, NULL, 0) < 0) {
        return;
    }

    static char *argBuffer = NULL;
    int procInfoCnt = (int)(procInfoLength / sizeof(struct kinfo_proc));
    for (int i = 0; i < procInfoCnt; i++) {

        pid_t pid = procInfo[i].kp_proc.p_pid;
        if (pid <= 1) {
            continue;
        }

        size_t argSize = kMaximumArgumentSize;
        if (sysctl((int[]){CTL_KERN, KERN_PROCARGS2, pid, 0}, 3, NULL, &argSize, NULL, 0) < 0) {
            continue;
        }

        argBuffer = (char *)realloc(argBuffer, argSize + 1);
        if (!argBuffer) {
            continue;
        }

        bzero(argBuffer, argSize + 1);
        if (sysctl((int[]){CTL_KERN, KERN_PROCARGS2, pid, 0}, 3, argBuffer, &argSize, NULL, 0) < 0) {
            continue;
        }

        BOOL stop = NO;
        @autoreleasepool {
            enumerator(pid, [NSString stringWithUTF8String:(argBuffer + sizeof(int))], &stop);
        }

        if (stop) {
            break;
        }
    }
}

void SingleVPNKillAll(NSString *processName, BOOL softly) {
    SingleVPNEnumerateProcessesUsingBlock(^(pid_t pid, NSString *executablePath, BOOL *stop) {
      if ([executablePath.lastPathComponent isEqualToString:processName]) {
          if (softly) {
              kill(pid, SIGTERM);
          } else {
              kill(pid, SIGKILL);
          }
      }
    });
}

void SingleVPNBatchKillAll(NSArray<NSString *> *processNames, BOOL softly) {
    SingleVPNEnumerateProcessesUsingBlock(^(pid_t pid, NSString *executablePath, BOOL *stop) {
      if ([processNames containsObject:executablePath.lastPathComponent]) {
          if (softly) {
              kill(pid, SIGTERM);
          } else {
              kill(pid, SIGKILL);
          }
      }
    });
}

@implementation SingleVPNRootListController

- (NSArray *)specifiers {
    if (!_specifiers) {
        NSArray *specs = [self loadSpecifiersFromPlistName:@"Root" target:self];
        NSMutableArray *mSpecs = [NSMutableArray arrayWithCapacity:specs.count];
        for (PSSpecifier *spec in specs) {
            NSString *cellClassName = [spec propertyForKey:@"cellClass"];
            if ([cellClassName isKindOfClass:[NSString class]]) {
                // ★ 不能写 [spec setProperty:... forKey:...]：
                //   与 NSStream 的 -setProperty:forKey: 同名异签名，clang 会硬报
                //   "multiple methods named ... found with mismatched result"。
                //   走 SVPNSetSpecifierProperty（Preferences.h 里的 static inline，
                //   用 NSSelectorFromString + methodForSelector 绕开编译期签名解析）。
                SVPNSetSpecifierProperty(spec, @"cellClass", NSClassFromString(cellClassName));
            }
            if (@available(iOS 17, *)) {
            } else {
                NSString *key = [spec propertyForKey:@"key"];
                if ([key isEqualToString:@"IsForce5GAEnabled"]) {
                    continue;
                }
            }
            [mSpecs addObject:spec];
        }
        _specifiers = mSpecs;
    }
    return _specifiers;
}

- (void)resetAppearance {
    for (PSSpecifier *specifier in self.specifiers) {
        NSString *key = [specifier propertyForKey:@"key"];
        if ([key hasPrefix:@"ForegroundColor"] || [key isEqualToString:@"IsForce5GAEnabled"]) {
            id defaultValue = [specifier propertyForKey:@"default"];
            [self setPreferenceValue:defaultValue specifier:specifier];
            [self reloadSpecifier:specifier animated:YES];
        }
    }
}

- (void)respring {
    SingleVPNBatchKillAll(@[ @"SpringBoard" ], YES);
}

/// 导出诊断日志（2.2-5）。
///
/// 做法：给 SpringBoard 里的插件发一个 Darwin 通知，它收到后把内存里的
/// 环形缓冲写到 /var/mobile/Library/Preferences/com.82flex.singlevpn-diag.txt。
/// 设置页这边**不直接读写**那个文件 —— 缓冲在 SpringBoard 进程里，
/// 跨进程只能靠通知触发，然后到 Files/终端里取文件。
///
/// 为什么不在设置页直接读文件：那样只能读到「上次导出」的内容；而缓冲在
/// 插件进程里、且是覆盖式的环形结构，必须由插件自己落盘才是当时那一刻的快照。
- (void)exportDiagnostics {
    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        CFSTR("com.82flex.singlevpnprefs/exportdiag"),
        NULL, NULL, true);

    // ★ 路径必须写成**一个连续字符串**，不要拆成 "目录/" "文件名" 两段拼接。
    //   拆开以后插件侧和设置页侧就没有「完全相同的一个字面量」可比了，
    //   闸门只能退化成判文件名在不在 —— 而两边目录写岔了正是最需要拦住的情况
    //   （用户会按提示去找，找不到，然后以为是插件没生效）。
    //   闸门 S12i 判两边这个字面量严格相等。
    NSString *diagPath = @"/var/mobile/Library/Preferences/com.82flex.singlevpn-diag.txt";

    // 给插件一点时间落盘（它是在下一次状态栏钩子被调用时写的），
    // 然后把路径告诉用户。这里不轮询、不等待 —— 只提示去哪儿找。
    UIAlertController *alert = [UIAlertController
        alertControllerWithTitle:@"Diagnostics Export Requested"
                         message:[NSString stringWithFormat:
                                     @"The tweak will write its in-memory trace on the next "
                                     @"status bar update.\n\nFile:\n%@\n\n"
                                     @"If you use Filza or SSH, grab that file after the status "
                                     @"bar refreshes once (e.g. toggle VPN).", diagPath]
                  preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"OK"
                                              style:UIAlertActionStyleDefault
                                            handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)support {
    NSURL *url = [NSURL URLWithString:@"https://havoc.app/search/82Flex"];
    if ([[UIApplication sharedApplication] canOpenURL:url]) {
        [[UIApplication sharedApplication] openURL:url options:@{} completionHandler:nil];
    }
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    // ★ 2.2-5 改掉原来的 `indexPath.section == 2` 硬编码。
    //   那个判据的含义本来是「只要是按钮就把字颜色调一下」，但写成了
    //   「第 2 段里的按钮」。这次在它前面插了 Diagnostics 一段，
    //   序号一挪，Respring 那个按钮就突然不染色了 —— 属于典型的
    //   「锚了个会漂移的指标」。改为直接问「这个 specifier 是不是按钮」，
    //   与段落布局解耦。
    PSSpecifier *specifier = [self specifierAtIndexPath:indexPath];
    if (!specifier) {
        return [super tableView:tableView cellForRowAtIndexPath:indexPath];
    }
    NSString *cellKind = [specifier propertyForKey:@"cell"];
    if ([cellKind isKindOfClass:[NSString class]] && [cellKind isEqualToString:@"PSButtonCell"]) {
        UITableViewCell *cell = [super tableView:tableView cellForRowAtIndexPath:indexPath];
        NSNumber *isDestructiveValue = [specifier propertyForKey:@"isDestructive"];
        BOOL isDestructive = [isDestructiveValue boolValue];
        cell.textLabel.textColor = isDestructive ? [UIColor systemRedColor] : [UIColor systemBlueColor];
        cell.textLabel.highlightedTextColor = isDestructive ? [UIColor systemRedColor] : [UIColor systemBlueColor];
        return cell;
    }
    return [super tableView:tableView cellForRowAtIndexPath:indexPath];
}

@end
