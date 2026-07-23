import { NativeModule, requireNativeModule } from 'expo';

/**
 * 摇骰子的手感 —— 纯震动 + 声音，不含 3D。
 * 盅是盖着的，玩家看不见骰子，所以手感全靠这个。
 */
declare class DiceFeelModule extends NativeModule<{
  onShakeTick: (e: { magnitude: number; energy: number }) => void;
  onSettled: () => void;
  onHapticError: (e: { message: string }) => void;
  onAudioStatus: (e: { session?: string; engine?: string; buffersLoaded?: number }) => void;
}> {
  isSupported(): boolean;
  /** 同步拉音频状态（不走事件） */
  audioStatusSync(): { stage?: string; session?: string; clicks?: number; settle?: boolean; reveal?: boolean };
  start(diceCount: number): Promise<void>;
  stop(): Promise<void>;
  /** 单次碰撞群，也可不靠陀螺仪、由按钮直接触发 */
  rattle(intensity: number, diceCount: number): Promise<void>;
  settle(): Promise<void>;
  reveal(): Promise<void>;
  /** 诊断：摇了多少次、播放器还在不在 */
  stats(): { rattleCount: number; players: number; engineAlive: boolean };
}

export default requireNativeModule<DiceFeelModule>('DiceFeel');
