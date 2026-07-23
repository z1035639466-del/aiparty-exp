import AVFoundation
import CoreHaptics
import ExpoModulesCore

/**
 虚拟左轮的「手感」—— 照 DiceFeel 那套结构。

 左轮的核心时刻是【扣扳机】:黑屏一顿 → 空膛轻响,或击发那一下全屏闪白 + 手电爆闪 + 重震。
 新硬件 = 手电筒(AVCaptureDevice.torch),骰件没用过 —— 击发时真的闪一下光。

 沿用骰件踩过的坑:
  · 所有建立/播放回主线程(AVAudioPlayer 在后台线程起会不出声)
  · 状态走同步 Function 轮询,不走事件
  · AVAudioPlayer 池,不用 AVAudioEngine 节点图
 */
public class RevolverFeelModule: Module {
  private var engine: CHHapticEngine?

  // 音频:空膛「咔」/ 击发「BANG」/ 转膛棘轮声,各预建一组实例轮流放
  private var clickPlayers: [AVAudioPlayer] = []   // 空膛咔哒
  private var bangPlayers: [AVAudioPlayer] = []    // 枪响
  private var spinPlayers: [AVAudioPlayer] = []    // 转膛棘轮
  private var clickIdx = 0, bangIdx = 0, spinIdx = 0
  private var audioReady = false
  private var lastStatus: [String: Any] = ["stage": "not-started"]

  // 手电:击发爆闪
  private var torchDevice: AVCaptureDevice?

  public func definition() -> ModuleDefinition {
    Name("RevolverFeel")

    Function("isSupported") { () -> Bool in
      CHHapticEngine.capabilitiesForHardware().supportsHaptics
    }
    Function("hasTorch") { () -> Bool in
      AVCaptureDevice.default(for: .video)?.hasTorch ?? false
    }
    Function("statusSync") { () -> [String: Any] in self.lastStatus }

    AsyncFunction("start") {
      DispatchQueue.main.async {
        self.setupAudio()
        try? self.startEngine()
        self.torchDevice = AVCaptureDevice.default(for: .video)
      }
    }
    AsyncFunction("stop") {
      DispatchQueue.main.async {
        self.engine?.stop()
        self.setTorch(false)
      }
    }

    // 转膛:一串加速又减速的棘轮咔哒 + 震动
    AsyncFunction("spin") {
      DispatchQueue.main.async { self.playSpin() }
    }

    // 空膛:轻「咔」一声 + 轻震(扳机扣下但没子弹)
    AsyncFunction("click") {
      DispatchQueue.main.async {
        self.playPool(&self.clickPlayers, &self.clickIdx, gain: 0.9)
        self.fireHaptic(intensity: 0.5, sharpness: 0.8, count: 1)
      }
    }

    // 击发:枪响 + 手电爆闪 + 一记炸裂重震
    AsyncFunction("bang") {
      DispatchQueue.main.async {
        self.playPool(&self.bangPlayers, &self.bangIdx, gain: 1.0)
        self.flashTorch()
        self.fireBangHaptic()
      }
    }

    OnDestroy {
      self.engine?.stop()
      self.setTorch(false)
    }
  }

  // MARK: - 音频

  private func setupAudio() {
    lastStatus["stage"] = "entered"
    guard !audioReady else { return }
    var st: [String: Any] = ["stage": "done"]
    let session = AVAudioSession.sharedInstance()
    do {
      try session.setCategory(.playback, mode: .default, options: [])
      try session.setActive(true)
      st["session"] = "ok"
    } catch { st["session"] = "FAIL: \(error.localizedDescription)" }

    func load(_ name: String, _ n: Int) -> [AVAudioPlayer] {
      guard let url = Bundle.main.url(forResource: name, withExtension: "wav") else { return [] }
      return (0..<n).compactMap { _ in
        let p = try? AVAudioPlayer(contentsOf: url); p?.prepareToPlay(); return p
      }
    }
    clickPlayers = load("rev_click", 4)
    bangPlayers = load("rev_bang", 2)
    spinPlayers = load("rev_spin", 2)
    st["click"] = clickPlayers.count; st["bang"] = bangPlayers.count; st["spin"] = spinPlayers.count
    audioReady = true
    lastStatus = st
  }

  private func playPool(_ pool: inout [AVAudioPlayer], _ idx: inout Int, gain: Float) {
    guard audioReady, !pool.isEmpty else { return }
    let p = pool[idx % pool.count]; idx += 1
    p.volume = gain; p.currentTime = 0; p.play()
  }

  private func playSpin() {
    playPool(&spinPlayers, &spinIdx, gain: 0.9)
    // 转膛震动:一串由密到疏的轻击,模拟弹巢转动棘轮
    guard let engine else { return }
    var events: [CHHapticEvent] = []
    var t = 0.0
    var gap = 0.03
    while t < 1.1 {
      events.append(CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.6),
        .init(parameterID: .hapticSharpness, value: 0.9),
      ], relativeTime: t))
      t += gap; gap *= 1.12   // 越转越慢
    }
    play(engine, events)
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

  private func fireHaptic(intensity: Float, sharpness: Float, count: Int) {
    guard let engine else { return }
    let events = (0..<count).map { i in
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: intensity),
        .init(parameterID: .hapticSharpness, value: sharpness),
      ], relativeTime: Double(i) * 0.05)
    }
    play(engine, events)
  }

  /// 击发重震:一记满强度炸裂 + 短促余震尾巴
  private func fireBangHaptic() {
    guard let engine else { return }
    var events: [CHHapticEvent] = [
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.5),
      ], relativeTime: 0),
      CHHapticEvent(eventType: .hapticContinuous, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.7),
        .init(parameterID: .hapticSharpness, value: 0.3),
      ], relativeTime: 0.01, duration: 0.16),
    ]
    for (i, amp) in [0.5, 0.3, 0.18].enumerated() {
      events.append(CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: Float(amp)),
        .init(parameterID: .hapticSharpness, value: 0.4),
      ], relativeTime: 0.12 + Double(i) * 0.06))
    }
    play(engine, events)
  }

  private func play(_ engine: CHHapticEngine, _ events: [CHHapticEvent]) {
    do {
      let pattern = try CHHapticPattern(events: events, parameters: [])
      let player = try engine.makePlayer(with: pattern)
      try player.start(atTime: CHHapticTimeImmediate)
    } catch {
      try? engine.start()
    }
  }

  // MARK: - 手电筒(击发爆闪)

  private func setTorch(_ on: Bool) {
    guard let d = torchDevice, d.hasTorch else { return }
    do {
      try d.lockForConfiguration()
      if on { try d.setTorchModeOn(level: 1.0) } else { d.torchMode = .off }
      d.unlockForConfiguration()
    } catch {}
  }

  /// 枪口火光:两下快闪,【务必】停在关,再加兜底强制熄灭防常亮。
  private func flashTorch() {
    let ons: [Double] = [0.0, 0.09]      // 开的时刻
    let offs: [Double] = [0.05, 0.16]    // 关的时刻
    for t in ons {
      DispatchQueue.main.asyncAfter(deadline: .now() + t) { [weak self] in self?.setTorch(true) }
    }
    for t in offs {
      DispatchQueue.main.asyncAfter(deadline: .now() + t) { [weak self] in self?.setTorch(false) }
    }
    // 兜底:0.3s 后无条件关,任何竞态都不会留常亮
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.setTorch(false) }
  }
}
