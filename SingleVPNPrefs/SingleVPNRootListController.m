#import <Foundation/Foundation.h>
#import <Preferences/PSSpecifier.h>
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

- (void)support {
    NSURL *url = [NSURL URLWithString:@"https://havoc.app/search/82Flex"];
    if ([[UIApplication sharedApplication] canOpenURL:url]) {
        [[UIApplication sharedApplication] openURL:url options:@{} completionHandler:nil];
    }
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    if (indexPath.section == 2) {
        PSSpecifier *specifier = [self specifierAtIndexPath:indexPath];
        NSString *key = [specifier propertyForKey:@"cell"];
        if ([key isEqualToString:@"PSButtonCell"]) {
            UITableViewCell *cell = [super tableView:tableView cellForRowAtIndexPath:indexPath];
            NSNumber *isDestructiveValue = [specifier propertyForKey:@"isDestructive"];
            BOOL isDestructive = [isDestructiveValue boolValue];
            cell.textLabel.textColor = isDestructive ? [UIColor systemRedColor] : [UIColor systemBlueColor];
            cell.textLabel.highlightedTextColor = isDestructive ? [UIColor systemRedColor] : [UIColor systemBlueColor];
            return cell;
        }
    }
    return [super tableView:tableView cellForRowAtIndexPath:indexPath];
}

@end
