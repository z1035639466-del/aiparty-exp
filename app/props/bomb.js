/**
 * 定时炸弹 · 击鼓传花（隐藏的震动倒计时）
 *
 * 产品事实（照 Yappa 宪法）:
 *   倒计时【对所有人隐藏】—— 屏幕上【不显示】还剩几秒。持机人只靠【震动】感觉:
 *   哒…哒…哒哒哒越来越急。别人看屏幕也看不出快炸了。这是「隐藏」这条短板的用法。
 *   到点爆炸才全场公开:全屏闪白 + 轰 + 重震 + 手电爆闪。
 *
 * 所以这一屏在倒计时阶段【故意什么进度都不显示】—— 那正是设计。
 * 屏幕只是个"烫手的东西"在手里传,手机就是炸弹本体。
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, Animated, Dimensions } from 'react-native';
import { Image } from 'expo-image';
import { BombFeel } from '../modules/propfeel';

const { width: SCREEN_W } = Dimensions.get('window');

const BOMB_IMG = require('../assets/props/bomb/bomb.webp');       // 未点燃
const BOMB_LIT = require('../assets/props/bomb/bomb_lit.webp');   // 点燃(引信火苗)
const CRACK_IMG = require('../assets/props/bomb/crack.webp');     // 屏幕碎裂 overlay
const AImage = Animated.createAnimatedComponent(Image);

// ⚠️ 题目不在客户端。这是【引擎占位】。
// 按端划分:回合题(风味层)由局长/引擎每局生成或从 atom 库筛选,再发下来;
// 客户端只【显示】收到的题,不拥有题库、不写死内容。
// 真接引擎时:改成从 arm() 的服务端响应 / props_dealt 里取 task 字段。
// 下面这句仅用于原型不空屏,明确标注是占位、不是真内容。
const ENGINE_TASK_PLACEHOLDER = '（等引擎发题）';

export default function Bomb() {
  const [phase, setPhase] = useState('idle');   // idle | armed | exploded
  const [supported, setSupported] = useState(null);
  const [task, setTask] = useState('');
  const flash = useRef(new Animated.Value(0)).current;   // 全屏闪白
  const crack = useRef(new Animated.Value(0)).current;   // 屏幕碎裂浮现
  const wobble = useRef(new Animated.Value(0)).current;   // 炸弹图标随震动抖

  // 降级铁律:BombFeel 原生件只在 dev client 存在(propfeel/index 已守卫成 null 安全)。
  // 缺席时所有原生访问都做特性检测,组件照常渲染,一个 crash 都不许。
  useEffect(() => {
    try { setSupported(BombFeel ? BombFeel.isSupported() : false); } catch (e) { setSupported(false); }
    if (!BombFeel) return;   // 原生缺席:不挂监听,视觉层仍在(炸弹留在仓里待命,未接引擎)
    const tick = BombFeel.addListener('onTick', () => {
      // 每次震动让炸弹图标抖一下(视觉等价物,给没震动的设备也有反馈)
      Animated.sequence([
        Animated.timing(wobble, { toValue: 1, duration: 40, useNativeDriver: true }),
        Animated.timing(wobble, { toValue: 0, duration: 90, useNativeDriver: true }),
      ]).start();
    });
    const boom = BombFeel.addListener('onExplode', () => {
      setPhase('exploded');
      // 基线:全屏闪白(手电的视觉等价物,全平台都有)
      flash.setValue(1);
      Animated.timing(flash, { toValue: 0, duration: 700, useNativeDriver: true }).start();
      // 白光退去时,屏幕碎裂浮现 —— 手机像被炸裂了
      crack.setValue(0);
      Animated.timing(crack, { toValue: 1, duration: 260, delay: 120, useNativeDriver: true }).start();
    });
    return () => { tick.remove(); boom.remove(); try { BombFeel.defuse(); } catch (e) {} };
  }, []);

  const arm = async () => {
    // 真接引擎时:task 从服务端响应取(局长出的题)。原型先显占位。
    setTask(ENGINE_TASK_PLACEHOLDER);
    setPhase('armed');
    try { if (BombFeel) await BombFeel.arm(8, 18); } catch (e) {}   // 引信走原生;缺席则纯视觉
  };

  const reset = async () => { try { if (BombFeel) await BombFeel.defuse(); } catch (e) {} crack.setValue(0); setPhase('idle'); };

  return (
    <View style={s.root}>
      <View style={s.hostCard}>
        <Text style={s.hostText}>
          {phase === 'idle' && '🎩 大家围一圈。点燃后，轮到谁谁做题、做完把手机递给下家。'}
          {phase === 'armed' && '🎩 该你了 —— 做完题赶紧递出去！'}
          {phase === 'exploded' && '💥 炸了！手机在谁手里，谁认罚。'}
        </Text>
      </View>

      <View style={s.stage}>
        {phase !== 'exploded' ? (
          <AImage
            source={phase === 'armed' ? BOMB_LIT : BOMB_IMG}
            contentFit="contain"
            style={[
              s.bomb,
              {
                transform: [
                  { translateX: wobble.interpolate({ inputRange: [0, 1], outputRange: [0, 7] }) },
                  { rotate: wobble.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '4deg'] }) },
                ],
              },
            ]}
          />
        ) : (
          <Text style={s.boom}>💥</Text>
        )}
        {/* 回合任务叠在底部:轮到你要做的事(局长设)。做完才能递下去——这才是「下家必须接」的原因:
            炸弹到手 = 轮到你,是义务不是选择。倒计时【故意不显示】,隐藏是设计。 */}
        {phase === 'armed' && (
          <View style={s.taskOverlay} pointerEvents="none">
            <Text style={s.taskLabel}>轮到你</Text>
            <Text style={s.task}>{task}</Text>
          </View>
        )}
      </View>

      <View style={s.btnRow}>
        {phase === 'idle' && (
          <Pressable style={[s.btn, s.arm]} onPress={arm}>
            <Text style={s.btnT}>点燃 🔥</Text>
          </Pressable>
        )}
        {phase === 'armed' && (
          <Text style={s.passHint}>
            👉 递手机是【真实动作】—— 直接把手机交给下家，不用点任何按钮
          </Text>
        )}
        {phase === 'exploded' && (
          <Pressable style={[s.btn, s.arm]} onPress={reset}>
            <Text style={s.btnT}>再来一局</Text>
          </Pressable>
        )}
      </View>

      <Text style={s.note}>
        炸弹就是手机本身，传递是真实动作，app 不感知（合宪）。回合任务由局长每局出题。
        倒计时对所有人隐藏——只有持机人靠震动感觉越来越急。
        Core Haptics {supported === null ? '检测中' : supported ? '可用' : '不可用（模拟器无震动）'}。
      </Text>

      {/* 屏幕碎裂 overlay:盖满全屏,pointerEvents none 让下面的按钮仍可点 */}
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { opacity: crack }]}>
        <Image source={CRACK_IMG} contentFit="cover" style={StyleSheet.absoluteFill} />
      </Animated.View>

      {/* 全屏闪白:爆炸瞬间,盖住一切(在裂纹之上,先白后退,露出裂纹) */}
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, s.flash, { opacity: flash }]} />
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#14141c', paddingHorizontal: 12, paddingTop: 54, paddingBottom: 14 },
  hostCard: { backgroundColor: '#1b1b28', borderRadius: 10, padding: 12, marginTop: 8 },
  hostText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  stage: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  bomb: { width: SCREEN_W * 1.02, height: SCREEN_W * 1.02 },
  boom: { fontSize: 190 },
  taskOverlay: { position: 'absolute', bottom: 8, alignItems: 'center', backgroundColor: '#000000aa', paddingHorizontal: 18, paddingVertical: 10, borderRadius: 12 },
  taskLabel: { color: '#a99bd8', fontSize: 12 },
  task: { color: '#fff', fontSize: 24, fontWeight: '800', marginTop: 2, textAlign: 'center' },
  passHint: { color: '#c9a86e', fontSize: 14, textAlign: 'center', padding: 14 },
  btnRow: { marginTop: 10 },
  btn: { padding: 16, borderRadius: 11, alignItems: 'center' },
  arm: { backgroundColor: '#5c2a2a' },
  btnT: { color: '#fff', fontSize: 16, fontWeight: '700' },
  note: { color: '#c9a86e', fontSize: 12, lineHeight: 19, marginTop: 16 },
  flash: { backgroundColor: '#ffffff' },
});
