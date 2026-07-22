# 摇盅参考实现 · Expo

目标体验：**摇动手机 = 摇骰盅**——盅体跟手倾斜、骰子在里面真实地撞壁（震动+声音），停手落定「咚」一声，丝滑 60fps。

## 手感的物理模型（为什么它摸起来是真的）

真骰盅里，骰子**只在手腕换向的瞬间撞壁**——不是匀速咔啦响。所以：

- ❌ 错误做法：摇得快就连续震（均匀蜂鸣，一摸就假）
- ✅ 本实现：检测**加速度方向反转**（前后帧点积 < 0）且幅值过门限 ⇒ 一次撞壁 ⇒ 一记离散震动 + 一声采样。甩得越狠那一下越重（Heavy/Rigid/Light 三档）。

丝滑的架构保证：**画面全程 UI 线程**（Reanimated `useAnimatedSensor` 读重力直接驱动盅体倾斜，能量只写 sharedValue，零 React 重渲染）；JS 线程只处理离散碰撞事件去触发声/震（native 异步，不占帧）。

## 文件

| 文件 | 职责 |
|---|---|
| `useShakeEngine.ts` | 传感器 → 碰撞事件 + 平滑能量 + 落定检测（纯信号，无渲染） |
| `rattle.ts` | 碰撞 → expo-haptics 三档震动 + 4 采样轮播（音量随能量、±8% 变速去循环感） |
| `ShakeCup.tsx` | 盅体 sprite + UI 线程动画（跟手倾斜/能量微抖/落定磕桌）+ 结果先行揭示 |

## 接入

```bash
npx expo install expo-sensors expo-haptics expo-av react-native-reanimated
```

```tsx
<ShakeCup
  shaking={phase === 'shake'}
  result={rollFromServer}          // 结果先行：进场前已定好
  onSettled={(r) => revealToOwner(r)}  // 落定后才允许「按住偷看」
/>
```

## 待补资产（命名即约定）

- `assets/cup_closed.png` — 盅体透明底 sprite（可用 tools/render_props.py 的 cup 场景开 film_transparent 渲）
- `sfx/rattle_1..4.m4a` — 实录：盅内 2–3 颗骰子**单次**撞壁，44.1kHz，<300ms，掐头去尾
- `sfx/slam.m4a` — 盅底磕桌闷「咚」（onSlam 播，当前先用震动占位）

## 手感调参表（useShakeEngine TUNING）

| 参数 | 默认 | 拧它的效果 |
|---|---|---|
| `hitMinMag` | 3.0 | 调低→轻轻晃也响（灵）；调高→要甩才响（沉） |
| `hitHeavyMag` | 14 | 重击门槛，决定「狠甩」的爽点位置 |
| `hitCooldownMs` | 55 | 两击最小间隔；<50ms iOS 马达会吞拍 |
| `energySmooth` | 0.85 | 动画振幅的跟手糊/跳 |
| `settleEnergy` + `settleMs` | 0.06 / 600 | 停手多久算落定 |

## 已知边界

- iOS 模拟器无震动无传感器，真机调参。
- Android 震动粒度粗（无 Rigid 细分），节奏一致所以手感仍成立；低端机把 `updateIntervalMs` 放宽到 32 依旧流畅（动画在 UI 线程，与传感器频率解耦）。
- 音频若感到起播延迟，换 `expo-audio`（SDK 52+ 新库）或预热一次静音播放。
