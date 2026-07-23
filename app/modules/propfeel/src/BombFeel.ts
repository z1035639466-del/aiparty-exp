import { NativeModule, requireNativeModule } from 'expo';

/** 定时炸弹:隐藏的震动倒计时 + 爆炸(闪白/轰/重震/手电) */
declare class BombFeelModule extends NativeModule<{
  onExplode: () => void;
  onTick: (e: { remain: number; urgency: number }) => void;
  onPass: (e: { magnitude: number }) => void;
  onStatus: (e: any) => void;
}> {
  isSupported(): boolean;
  statusSync(): { stage?: string; fuse?: number };
  arm(minSec: number, maxSec: number): Promise<void>;
  passed(): Promise<void>;
  defuse(): Promise<void>;
}

export default requireNativeModule<BombFeelModule>('BombFeel');
