package org.local.reader;

// 用户确认的默认设置；设置页、后台服务和翻页服务必须共用同一份值。
final class ReaderDefaults {
  static final String READER_PROTOCOL_VERSION = "1.4";
  static final int READER_PORT = 8765;
  static final String EMULATOR_ADDRESS = "http://10.0.2.2:" + READER_PORT;
  static boolean isEmulatorHardware(String hardware) {
    return "ranchu".equals(hardware) || "goldfish".equals(hardware);
  }
  static boolean isEmulator() { return isEmulatorHardware(android.os.Build.HARDWARE); }
  static String defaultAddressForHardware(String hardware) {
    return isEmulatorHardware(hardware) ? EMULATOR_ADDRESS : ReaderBuildDefaults.PHONE_ADDRESS;
  }
  static String defaultAddress() {
    return defaultAddressForHardware(android.os.Build.HARDWARE);
  }
  static String resolveSavedAddress(String saved, String hardware) {
    if (saved == null || saved.isEmpty()
        || (!isEmulatorHardware(hardware) && EMULATOR_ADDRESS.equals(saved)))
      return defaultAddressForHardware(hardware);
    return saved;
  }
  static String savedAddress(android.content.SharedPreferences prefs) {
    return resolveSavedAddress(prefs.getString("address", ""), android.os.Build.HARDWARE);
  }
  static final String TOKEN = "reader-local";
  static final String MODEL_LABEL = "菲比_ZH · v4 · 菲比_ZH_e10_s290_l32";
  static final int SENTENCE_GAP_MS = 100;
  static final int SPEECH_SPEED_PERCENT = 90;
  static final boolean AUTO_FLIP = true;
  static final int OVERLAY_OPACITY_PERCENT = 95;
  static final boolean ASR_CHECK = true;
  static final boolean PARALLEL_VISION = true;
  static final boolean EAGER_FIRST_AUDIO = true;
  static final boolean EARLY_PREFETCH = true;
  static final boolean ADAPTIVE_CAPTURE = true;
  static final boolean HISTORY_REUSE = true;
  static final boolean HISTORY_HIDDEN = false;
  static final boolean NEXT_PAGE_LEFT = false;
  static final int SWIPE_Y_PERCENT = 55;
  static final int SWIPE_DURATION_MS = 400;

  private ReaderDefaults() {}
}
