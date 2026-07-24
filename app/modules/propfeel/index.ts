// 道具手感原生模块统一出口(降级安全版)。
// 三件原生(Core Haptics / 音频引擎 / 手电 / CoreMotion)只在 dev client 打进二进制;
// Expo Go 与模拟器里这些原生件一律缺席。此前直接 requireNativeModule 会在缺席时
// **抛异常**——import 一旦被拉起就整屏白屏,这正是降级铁律要堵死的坑。
//
// 这里用 requireOptionalNativeModule 安全解析:模块在=返回原生实例,模块不在=返回 null
// (绝不抛)。"有没有原生手感"的判断交给上层组件做无感降级(震动走 expo-haptics、
// 音效走 expo-av、视觉照常)。旧的 ./src/*.ts 保留为原生接口的类型/文档参照,运行时不再走它。
import { requireOptionalNativeModule, requireNativeModule } from 'expo';

function optional(name: string): any {
  // 首选官方可选解析(SDK 48+):缺席返回 null 不抛。
  try {
    if (typeof requireOptionalNativeModule === 'function') {
      return requireOptionalNativeModule(name) ?? null;
    }
  } catch (e) { /* 落到下面的兜底 */ }
  // 兜底:老入口用 try/catch 包住,缺席即降级返回 null。
  try {
    return requireNativeModule(name);
  } catch (e) {
    return null;
  }
}

export const DiceFeel: any = optional('DiceFeel');
export const RevolverFeel: any = optional('RevolverFeel');
export const BombFeel: any = optional('BombFeel');
