/**
 * 撞壁反馈：震动 + 实录采样，一次碰撞一次触发。
 *
 * 震动映射（iOS Taptic 实测手感）：
 *   heavy  → ImpactFeedbackStyle.Heavy   骰子狠砸盅壁
 *   medium → ImpactFeedbackStyle.Rigid   脆硬的"嗒"——最像骰子的一档
 *   light  → ImpactFeedbackStyle.Light   轻碰
 * Android 走同一 API（expo-haptics 内部映射到 VibrationEffect），粒度粗一点但节奏同源。
 *
 * 声音：4 个真实骰盅碰撞采样轮播（round-robin 防打断），
 * 音量随能量、播速 ±8% 随机抖动 —— 消除"同一段音效循环"的塑料感。
 */
import * as Haptics from 'expo-haptics';
import { Audio } from 'expo-av';
import type { CollisionEvent } from './useShakeEngine';

// 采样清单见 README（实录：骰盅内 2-3 颗骰子单次撞壁，44.1kHz，<300ms，去尾静音）
const SAMPLE_MODULES = [
  require('./sfx/rattle_1.m4a'),
  require('./sfx/rattle_2.m4a'),
  require('./sfx/rattle_3.m4a'),
  require('./sfx/rattle_4.m4a'),
];

let pool: Audio.Sound[] = [];
let cursor = 0;

export async function loadRattle() {
  await Audio.setAudioModeAsync({
    playsInSilentModeIOS: true, // 派对场景静音键常开着，声音是体验核心，穿透之
  });
  pool = await Promise.all(
    SAMPLE_MODULES.map(async (m) => (await Audio.Sound.createAsync(m, { volume: 0 })).sound),
  );
}

export async function unloadRattle() {
  await Promise.all(pool.map((s) => s.unloadAsync()));
  pool = [];
}

const IMPACT = {
  heavy: Haptics.ImpactFeedbackStyle.Heavy,
  medium: Haptics.ImpactFeedbackStyle.Rigid,
  light: Haptics.ImpactFeedbackStyle.Light,
} as const;

/** 一次撞壁 = 一记震动 + 一声采样。fire-and-forget，绝不 await 阻塞帧。 */
export function onCollision({ strength, energy }: CollisionEvent) {
  Haptics.impactAsync(IMPACT[strength]).catch(() => {});
  const s = pool[cursor++ % pool.length];
  if (!s) return;
  s.setStatusAsync({
    shouldPlay: true,
    positionMillis: 0,
    volume: 0.35 + 0.65 * energy,
    rate: 0.92 + Math.random() * 0.16,
    shouldCorrectPitch: false, // 变速带变调，正是碰撞声的自然差异
  }).catch(() => {});
}

/** 落定收尾：盅底磕桌一声闷"咚" + 重震（调用方在 onSettle 里触发） */
export function onSlam() {
  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy).catch(() => {});
  Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
}
