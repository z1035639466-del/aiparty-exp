// 道具手感统一出口。三件原生(Core Haptics/音频/手电/CoreMotion),
// 从各自验证 fork 收拢入库(lab=骰盅 lab-bomb=炸弹 lab-revolver=左轮);
// 额头牌无原生件(expo-haptics 罐头震动,待统一到这套自研波形)。
export { default as DiceFeel } from './src/DiceFeel';
export { default as BombFeel } from './src/BombFeel';
export { default as RevolverFeel } from './src/RevolverFeel';
