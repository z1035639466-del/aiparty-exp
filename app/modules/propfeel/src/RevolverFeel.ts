import { NativeModule, requireNativeModule } from 'expo';

declare class RevolverFeelModule extends NativeModule {
  isSupported(): boolean;
  hasTorch(): boolean;
  statusSync(): { stage?: string; session?: string; click?: number; bang?: number; spin?: number };
  start(): Promise<void>;
  stop(): Promise<void>;
  spin(): Promise<void>;   // 转膛:棘轮咔哒 + 震动
  click(): Promise<void>;  // 空膛:轻咔 + 轻震
  bang(): Promise<void>;   // 击发:枪响 + 手电爆闪 + 重震
}

export default requireNativeModule<RevolverFeelModule>('RevolverFeel');
