/**
 * 摇盅组件 —— 丝滑的关键：画面全程跑在 UI 线程（Reanimated），JS 线程只管声/震。
 *
 * 三路信号，一个源头（useShakeEngine）：
 *   画面  useAnimatedSensor(GRAVITY) 直接驱动盅体倾斜（UI 线程，0 桥接延迟）
 *         + energy sharedValue 驱动抖动振幅
 *   震动  碰撞事件 → expo-haptics（native 异步，不碰帧）
 *   声音  碰撞事件 → 采样池（native 异步，不碰帧）
 *
 * 结果先行：摇之前 roll 已由逻辑层定好（props.result），
 * 落定只是揭示，不掷骰 —— 断线卡顿都不影响公正。
 */
import React, { useCallback, useEffect } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import Animated, {
  Easing,
  interpolate,
  SensorType,
  useAnimatedSensor,
  useAnimatedStyle,
  useSharedValue,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useShakeEngine } from './useShakeEngine';
import { loadRattle, onCollision, onSlam, unloadRattle } from './rattle';

interface Props {
  /** 结果先行：进场前逻辑层已定好的点数 */
  result: number[];
  /** 落定并播完收尾动画后回调（此时才允许「偷看」） */
  onSettled: (result: number[]) => void;
  shaking: boolean; // 由局流程控制是否处于摇盅阶段
}

export function ShakeCup({ result, onSettled, shaking }: Props) {
  const energy = useSharedValue(0);
  const slam = useSharedValue(0);
  // GRAVITY 传感器直接在 UI 线程可读 —— 盅体跟手倾斜零延迟
  const gravity = useAnimatedSensor(SensorType.GRAVITY, { interval: 16 });

  useEffect(() => {
    loadRattle();
    return () => void unloadRattle();
  }, []);

  const handleSettle = useCallback(() => {
    onSlam(); // 「咚」+ 重震
    slam.value = withSequence(
      withTiming(1, { duration: 90, easing: Easing.out(Easing.quad) }),
      withSpring(0, { damping: 14, stiffness: 260 }),
    );
    onSettled(result);
  }, [result, onSettled]);

  useShakeEngine({
    enabled: shaking,
    onCollision, // 声 + 震，JS 线程，fire-and-forget
    onEnergy: (e) => {
      energy.value = e; // 只写 sharedValue，不触发 React 渲染
    },
    onSettle: handleSettle,
  });

  const cupStyle = useAnimatedStyle(() => {
    const g = gravity.sensor.value;
    // 手机姿态 → 盅体倾斜（±10°），spring 不必——传感器本身连续
    const tiltX = interpolate(g.y, [-9.8, 9.8], [10, -10]);
    const tiltY = interpolate(g.x, [-9.8, 9.8], [-10, 10]);
    // 能量 → 高频微抖（骰子在里面闹）。每帧随机相位，振幅 ∝ energy
    const jx = (Math.random() - 0.5) * 16 * energy.value;
    const jy = (Math.random() - 0.5) * 12 * energy.value;
    const jr = (Math.random() - 0.5) * 4 * energy.value;
    // 落定：向下一磕再弹回
    const drop = slam.value * 10;
    return {
      transform: [
        { perspective: 700 },
        { rotateX: `${tiltX}deg` },
        { rotateY: `${tiltY}deg` },
        { translateX: jx },
        { translateY: jy + drop },
        { rotate: `${jr}deg` },
        { scale: 1 - slam.value * 0.02 },
      ],
    };
  });

  const shadowStyle = useAnimatedStyle(() => ({
    opacity: 0.45 + energy.value * 0.2,
    transform: [{ scaleX: 1 + energy.value * 0.15 + slam.value * 0.1 }],
  }));

  return (
    <View style={styles.stage}>
      {/* 盅体：Cycles 渲的透明底 sprite（见 README 资产清单） */}
      <Animated.View style={cupStyle}>
        <Image source={require('./assets/cup_closed.png')} style={styles.cup} />
      </Animated.View>
      <Animated.View style={[styles.shadow, shadowStyle]} />
    </View>
  );
}

const styles = StyleSheet.create({
  stage: { alignItems: 'center', justifyContent: 'center' },
  cup: { width: 240, height: 276, resizeMode: 'contain' },
  shadow: {
    marginTop: -14,
    width: 200,
    height: 26,
    borderRadius: 100,
    backgroundColor: '#000',
  },
});
