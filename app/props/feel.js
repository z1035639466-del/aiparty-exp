/**
 * 道具实感的降级中枢 —— 一处收口"原生优先、缺席回落"的判断,组件只调语义方法。
 *
 * 分层(降级铁律):
 *   原生在(dev client)→ propfeel 的 Core Haptics / 音频引擎 / 手电 出高保真手感;
 *   原生缺席(Expo Go / 模拟器)→ 触感走 expo-haptics、音效走 expo-av、视觉照常。
 * 任何一层报错都被吞掉:实感是增强层,不是命脉;道具不锁在单一感官后面,更不许 crash。
 *
 * ⚠️ 铁则:这里只管"怎么让人感觉到",绝不产出任何游戏结果。点数/胜负永远来自服务器。
 *
 * 【为什么骰盅不接原生 DiceFeel】
 *   DiceFeel.start() 会**自己接管 CoreMotion** 做摇动检测并发 onShakeTick,而 App 的
 *   DiceCup 状态机已用 expo-sensors 的 Accelerometer 做脉冲计数——两套动检并存会打架,
 *   且 rattle/reveal 不先 start 就是空 no-op。铁则「状态机一行不动」优先:骰盅的触感留在
 *   expo-haptics(每次脉冲那一下,全平台都在)、音效走 expo-av(下方 makePlayer),
 *   不去驱动会抢动检的原生会话。左轮的 bang 是干净的一次性击发(start 只备音频/手电、
 *   不碰动检),所以左轮正常接原生。
 */
import { Audio } from 'expo-av';
import * as Haptics from 'expo-haptics';
import { RevolverFeel } from '../modules/propfeel';

// —— 原生在不在(import 已在 propfeel/index 里守卫过,这里是 null 安全的布尔)——
export const hasRevolverFeel = !!RevolverFeel;

// Promise 方法的双保险:同步抛→try 兜住;异步拒→.catch 兜住。绝不冒泡。
function fire(fn) {
  try {
    const p = fn();
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch (e) { /* 原生这一下没响应就算了,降级层还在 */ }
}

// —— 左轮原生手感(存在才调;缺席时全是安全 no-op)——
export function revStart() { if (RevolverFeel && RevolverFeel.start) fire(() => RevolverFeel.start()); }
export function revStop() { if (RevolverFeel && RevolverFeel.stop) fire(() => RevolverFeel.stop()); }
// 击发:响一声(枪响+手电爆闪+重震)。返回 true=原生接管了,调用方就别再叠 expo-av。
export function revBang() {
  if (RevolverFeel && RevolverFeel.bang) { fire(() => RevolverFeel.bang()); return true; }
  return false;
}
export function revHasTorch() {
  try { return !!(RevolverFeel && RevolverFeel.hasTorch && RevolverFeel.hasTorch()); }
  catch (e) { return false; }
}

// —— expo-av 音效降级层 —— 预加载一次、replayAsync 复用;全平台/Expo Go 都在。
let audioModeReady = false;
async function ensureAudioMode() {
  if (audioModeReady) return;
  try {
    await Audio.setAudioModeAsync({ playsInSilentModeIOS: true, allowsRecordingIOS: false });
    audioModeReady = true;
  } catch (e) { /* 设不上就直接尝试播,失败再吞 */ }
}

/**
 * 造一个可复用的音效播放器。assetModule 必须是调用处字面 require(...) 的结果
 * (Metro 静态解析资源)。加载/播放任一步失败都静默——没声也照玩。
 */
export function makePlayer(assetModule) {
  let sound = null;
  let loading = false;
  let dead = false;
  const load = async () => {
    if (sound || loading || dead) return;
    loading = true;
    try {
      await ensureAudioMode();
      const { sound: sd } = await Audio.Sound.createAsync(assetModule, { volume: 1.0 });
      sound = sd;
    } catch (e) {
      sound = null; // 加载失败:后续 play 会再试一次,还是不行就一直是静默 no-op
    } finally {
      loading = false;
    }
  };
  load();
  return {
    play: async () => {
      try {
        if (dead) return;
        if (!sound) await load();
        if (sound) await sound.replayAsync();
      } catch (e) { /* 播不出就算了 */ }
    },
    unload: async () => {
      dead = true;
      try { if (sound) { await sound.unloadAsync(); sound = null; } } catch (e) {}
    },
  };
}

// —— 触感降级(始终可用:expo-haptics 在 Expo Go / 真机都在)——
export function tapLight() { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {}); }
export function tapHeavy() { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy).catch(() => {}); }
export function tapMedium() { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {}); }
