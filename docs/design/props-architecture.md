# 道具实现架构决策 · 结果先行 + 分层表演

## 总原则
道具动画只是表演；结果先由逻辑层公平随机定好并落账，动画向结果收敛。
（rigged presentation / server-authoritative——骰子、转盘类产品标准做法，
也是「账本之外无事实」在架构上的直接表达。）

## 四层
1. UI 壳层：黑黄外壳/极光时刻/AI 局卡 —— 跨端 UI 框架
2. 道具表演层：PropController（状态机）+ PropView（渲染档可替换：序列帧/实时3D/混合）
3. 逻辑判定层：FairRandom + Ledger，先出结果再通知表演层
4. 感官层：实录采样 + 震动表，状态机事件驱动

## 渲染档位分配
- 骰子：实时 3D 物理（公平感来源；MVP 可用 20 组预渲滚动随机挑）
- 骰盅：混合——盅体序列帧 + 盅内结果合成；主角是陀螺仪跟手与碰撞声
- 左轮：序列帧/图集——变速动画 + 咔哒节拍收敛到定好的膛位

## 栈
- iOS 先行：SwiftUI + RealityKit + CoreHaptics/AVAudioEngine（零第三方引擎）
- 双端：Flutter 壳 + Unity as a Library 仅道具层；MVP 可全序列帧零引擎

## 演进
MVP 全序列帧+实录声震+结果先行 → 验证道具时刻价值 → V2 仅骰子上真物理 →
其余停在序列帧，预算归生成质量与音效震动。

## 栈修订 · Expo 结论（2026-07-22）
Expo 全链路可行，无需换栈：
- 序列帧/Lottie、expo-sensors（摇盅）、expo-haptics（离散脉冲覆盖全部震动规格）、
  expo-av（实录音效）、expo-screen-capture（偷看防截屏）——MVP 全绿。
- 实时 3D 骰子：expo-gl + react-three-fiber + cannon-es 可做（≈原生 85% 质感）；
  更优后手 = Cycles 预渲 20 组收敛到不同点数的滚动序列帧，按结果先行挑段播放——
  保真度最高且零引擎依赖。
- CoreHaptics 高级曲线仅在需要 AHAP 时经 EAS prebuild 加原生模块，非墙。
