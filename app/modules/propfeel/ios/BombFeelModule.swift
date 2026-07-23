import AVFoundation
import CoreHaptics
import ExpoModulesCore
import QuartzCore

/**
 定时炸弹（击鼓传花）—— 隐藏的震动倒计时。

 产品要点（照 Yappa 宪法与道具库规格）:
 · 倒计时【对所有人隐藏】—— 只有持机人用【震动】感觉得到,哒…哒…哒哒哒越来越快,
   别人不知道还剩几秒。这正是规格里「倒计时对所有人隐藏——道具内置行为」。
 · 到点爆炸 = 全场公开:全屏闪白 + 手电爆闪 + 一记重震 + 轰。
 · 补的短板:隐藏·随机。总时长本该服务端 rngSeed 定,这里原型先客户端随机。

 基线/增强分层（宪法）:
 · 爆炸的【闪白 + 轰声】是基线,全平台保底,没震动也能玩(炸弹照炸,全场都看到)。
 · 倒计时的【私密震动】是增强层 —— 它本就是隐藏的,没震动就是少了给持机人的紧张感,
   不影响炸弹本身可玩。
 · 手电爆闪也是增强(有手电才点),闪白是它的视觉等价物(fx 必须有视觉等价物)。

 结构照 DiceFeel 那套已验证的坑:
 · 一切 setup / 发事件回主线程(AsyncFunction 默认后台线程)。
 · haptic 引擎共享同一个 AVAudioSession(否则 setActive 打断 -4805)。
 · 状态走同步 Function 轮询,不赌事件。
 */
public class BombFeelModule: Module {
  private var engine: CHHapticEngine?

  private var audio = AVAudioPlayer?.none
  private var audioReady = false

  // 倒计时
  private var tickTimer: DispatchSourceTimer?
  private var armedAt: CFTimeInterval = 0
  private var fuse: Double = 0          // 总时长(秒)
  private var running = false
  private var lastStatus: [String: Any] = ["stage": "idle"]

  public func definition() -> ModuleDefinition {
    Name("BombFeel")

    Events("onExplode", "onTick", "onStatus")

    Function("isSupported") { () -> Bool in
      CHHapticEngine.capabilitiesForHardware().supportsHaptics
    }
    Function("statusSync") { () -> [String: Any] in self.lastStatus }

    // 点燃:随机时长,开始隐藏的加速震动倒计时
    AsyncFunction("arm") { (minSec: Double, maxSec: Double) in
      DispatchQueue.main.async { self.arm(minSec: minSec, maxSec: maxSec) }
    }
    AsyncFunction("defuse") {
      DispatchQueue.main.async { self.stop() }
    }

    OnDestroy { self.stop(); self.engine?.stop() }
  }

  // MARK: - 点燃 / 倒计时

  private func arm(minSec: Double, maxSec: Double) {
    stop()
    setupAudio()
    try? startEngine()
    // 随机引信时长。原型用客户端随机;真实产品这个数来自服务端 rngSeed。
    let lo = max(3.0, min(minSec, maxSec)), hi = max(minSec, maxSec)
    fuse = lo + (hi - lo) * Double.random(in: 0...1)
    armedAt = CACurrentMediaTime()
    running = true
    lastStatus = ["stage": "armed", "fuse": fuse]
    scheduleNextTick()
  }

  /// 加速震动:剩余时间越少,间隔越短、震得越急。间隔在 [0.08, 0.9]s 之间。
  private func scheduleNextTick() {
    guard running else { return }
    let elapsed = CACurrentMediaTime() - armedAt
    let remain = fuse - elapsed
    if remain <= 0 { explode(); return }

    // 进度 0→1,间隔从 0.9 收到 0.08(指数,末段特别急)
    let prog = min(1.0, elapsed / fuse)
    let interval = 0.9 * pow(0.09, prog)     // 0.9 → 0.081
    playTick(urgency: prog)
    sendEvent("onTick", ["remain": remain, "urgency": prog])

    let t = DispatchSource.makeTimerSource(queue: .main)
    t.schedule(deadline: .now() + interval)
    t.setEventHandler { [weak self] in self?.scheduleNextTick() }
    t.resume()
    tickTimer = t
  }

  private func explode() {
    running = false
    tickTimer?.cancel(); tickTimer = nil
    lastStatus = ["stage": "exploded"]
    // 三感官砸一个点:闪白/轰/重震/手电第一闪都对齐爆炸起振点。
    // onExplode 先发 —— JS 闪白经桥有延迟,先发才追得上下面几乎瞬时的原生效果。
    sendEvent("onExplode", [:])
    playBoom()            // 轰(基线)
    fireExplodeHaptic()   // 重震(增强)
    flashTorch(times: 3)  // 手电爆闪(增强),第一闪同步点亮
  }

  private func stop() {
    running = false
    tickTimer?.cancel(); tickTimer = nil
    torchOff()
    lastStatus = ["stage": "idle"]
  }

  // MARK: - Haptics

  private func startEngine() throws {
    guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else { return }
    if engine == nil {
      let e = try CHHapticEngine(audioSession: AVAudioSession.sharedInstance())
      e.resetHandler = { [weak self] in try? self?.engine?.start() }
      e.isAutoShutdownEnabled = false
      engine = e
    }
    try engine?.start()
  }

  private func playPattern(_ events: [CHHapticEvent]) {
    guard let engine else { return }
    do {
      let p = try CHHapticPattern(events: events, parameters: [])
      try engine.makePlayer(with: p).start(atTime: CHHapticTimeImmediate)
    } catch {
      try? engine.start()
    }
  }

  /// 一记倒计时"哒"。urgency 0→1:越接近爆炸越强越锐。
  /// 加重手法(单事件强度上限 1.0,只能靠叠+加长):满强瞬态 + 叠一段极短连续,
  /// 三记瞬态贴着砸,手上是一记"顿"而不是"点"。
  private func playTick(urgency: Double) {
    let u = Float(min(max(urgency, 0), 1))
    playPattern([
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.6 + u * 0.4),
      ], relativeTime: 0),
      // 垫一小段满强连续,把"点"撑成"顿"
      CHHapticEvent(eventType: .hapticContinuous, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.4),
      ], relativeTime: 0, duration: 0.05),
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.35),
      ], relativeTime: 0.02),
    ])
  }

  /// 爆炸重震:满强瞬态 + 更长更粗的持续轰,再补一串连击,炸得更狠更久。
  private func fireExplodeHaptic() {
    var events: [CHHapticEvent] = [
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 1.0),
      ], relativeTime: 0),
      // 持续轰拉到 0.9s,更粗(锐度更低=更闷更重)
      CHHapticEvent(eventType: .hapticContinuous, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.1),
      ], relativeTime: 0, duration: 0.9),
    ]
    // 前 250ms 补一串连击(6 记),炸开翻滚的"炸裂感"
    for t in [0.02, 0.05, 0.09, 0.14, 0.19, 0.25] {
      events.append(CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: Float.random(in: 0.35...0.85)),
      ], relativeTime: t))
    }
    playPattern(events)
  }

  // MARK: - 手电爆闪(增强;闪白是它的视觉等价物,在 JS 侧)

  private func flashTorch(times: Int) {
    guard let dev = AVCaptureDevice.default(for: .video), dev.hasTorch else { return }
    // 第一闪【同步】点亮 —— 跟轰声/重震同帧砸下去,不进后台队列等半帧。
    try? dev.lockForConfiguration(); try? dev.setTorchModeOn(level: 1.0); dev.unlockForConfiguration()
    // 剩下的开关脉冲丢后台跑,不阻塞主线程。
    DispatchQueue.global(qos: .userInitiated).async {
      usleep(70_000)
      try? dev.lockForConfiguration(); dev.torchMode = .off; dev.unlockForConfiguration()
      for _ in 1..<times {
        usleep(55_000)
        try? dev.lockForConfiguration(); try? dev.setTorchModeOn(level: 1.0); dev.unlockForConfiguration()
        usleep(70_000)
        try? dev.lockForConfiguration(); dev.torchMode = .off; dev.unlockForConfiguration()
      }
    }
  }
  private func torchOff() {
    guard let dev = AVCaptureDevice.default(for: .video), dev.hasTorch else { return }
    try? dev.lockForConfiguration(); dev.torchMode = .off; dev.unlockForConfiguration()
  }

  // MARK: - 音频(基线)

  private func setupAudio() {
    if audioReady { return }
    try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [])
    try? AVAudioSession.sharedInstance().setActive(true)
    if let url = Bundle.main.url(forResource: "boom", withExtension: "wav") {
      audio = try? AVAudioPlayer(contentsOf: url)
      audio?.prepareToPlay()
    }
    audioReady = true
  }
  private func playBoom() { audio?.currentTime = 0; audio?.play() }
}
