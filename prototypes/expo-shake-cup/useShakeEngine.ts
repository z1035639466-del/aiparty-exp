/**
 * 摇盅物理引擎（无渲染，纯信号）
 *
 * 核心模型：骰子只在「手腕换向」的瞬间撞盅壁。
 * 用重力剥离后的加速度向量做两件事：
 *   1. 碰撞检测 —— 加速度方向反转（前后帧点积 < 0）且幅值够大 ⇒ 一次撞壁事件
 *   2. 能量估计 —— 幅值的指数平滑，驱动动画振幅与音量
 *
 * 事件消费方（rattle.ts / ShakeCup.tsx）拿到的是离散碰撞 + 连续能量，
 * 震动、声音、画面三路共享同一信号源，节奏天然同步。
 */
import { useEffect, useRef } from 'react';
import { DeviceMotion } from 'expo-sensors';

export type CollisionStrength = 'light' | 'medium' | 'heavy';
export interface CollisionEvent {
  strength: CollisionStrength;
  energy: number; // 0..1 平滑能量，供音量/振幅用
}

export interface ShakeEngineOptions {
  enabled: boolean;
  onCollision: (e: CollisionEvent) => void;
  /** 每帧平滑能量（0..1），建议直接写进 Reanimated sharedValue */
  onEnergy?: (energy: number) => void;
  /** 能量低于阈值持续 settleMs 后触发一次（骰子落定） */
  onSettle?: () => void;
  settleMs?: number;
}

// —— 手感调参区（详见 README 调参表）——
const TUNING = {
  updateIntervalMs: 16,   // 传感器频率 ~60Hz
  energySmooth: 0.85,     // 能量平滑系数（越大越"糊"，越小越"跳"）
  energyFullScale: 22,    // 多大加速度算能量=1（m/s²）
  hitMinMag: 3.0,         // 低于此幅值的换向不算撞壁（排除手抖）
  hitHeavyMag: 14,        // 重击阈值
  hitMediumMag: 7,        // 中击阈值
  hitCooldownMs: 55,      // 两次撞壁最小间隔（iOS 震动马达节流）
  settleEnergy: 0.06,     // 低于此能量视为"静止中"
};

export function useShakeEngine(opts: ShakeEngineOptions) {
  const cb = useRef(opts);
  cb.current = opts;

  useEffect(() => {
    if (!opts.enabled) return;
    DeviceMotion.setUpdateInterval(TUNING.updateIntervalMs);

    let prev = { x: 0, y: 0, z: 0 };
    let energy = 0;
    let lastHit = 0;
    let quietSince: number | null = null;
    let settled = true; // 初始静止，第一次摇动后才可 settle

    const sub = DeviceMotion.addListener(({ acceleration }) => {
      // DeviceMotion.acceleration 已剥离重力（iOS userAcceleration；Android 推算）
      const a = acceleration ?? { x: 0, y: 0, z: 0 };
      const mag = Math.hypot(a.x ?? 0, a.y ?? 0, a.z ?? 0);
      const now = Date.now();

      // 能量：指数平滑
      energy =
        energy * TUNING.energySmooth +
        Math.min(mag / TUNING.energyFullScale, 1) * (1 - TUNING.energySmooth);
      cb.current.onEnergy?.(energy);

      // 碰撞：方向反转 + 幅值门限 + 冷却
      const dot = a.x * prev.x + a.y * prev.y + a.z * prev.z;
      if (dot < 0 && mag > TUNING.hitMinMag && now - lastHit > TUNING.hitCooldownMs) {
        lastHit = now;
        settled = false;
        const strength: CollisionStrength =
          mag > TUNING.hitHeavyMag ? 'heavy' : mag > TUNING.hitMediumMag ? 'medium' : 'light';
        cb.current.onCollision({ strength, energy });
      }
      prev = { x: a.x ?? 0, y: a.y ?? 0, z: a.z ?? 0 };

      // 落定：能量持续低位
      if (energy < TUNING.settleEnergy) {
        quietSince ??= now;
        if (!settled && now - quietSince > (cb.current.settleMs ?? 600)) {
          settled = true;
          cb.current.onSettle?.();
        }
      } else {
        quietSince = null;
      }
    });

    return () => sub.remove();
  }, [opts.enabled]);
}
