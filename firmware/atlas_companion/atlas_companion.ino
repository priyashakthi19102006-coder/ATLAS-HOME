#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <ESP32Servo.h>

// ============================================================
// ATLAS HOME — FINAL COMPANION FIRMWARE
// ============================================================

// ---------------- OLED #1 ----------------
// Hardware I2C
// SDA = GPIO21
// SCL = GPIO22
// Address = 0x3C

U8G2_SH1106_128X64_NONAME_F_HW_I2C leftEye(
  U8G2_R0,
  U8X8_PIN_NONE
);

// ---------------- OLED #2 ----------------
// Software I2C
// SCL = GPIO18
// SDA = GPIO19
// Address = 0x3C

U8G2_SH1106_128X64_NONAME_F_SW_I2C rightEye(
  U8G2_R0,
  18,
  19,
  U8X8_PIN_NONE
);

// ---------------- SERVO ----------------

Servo atlasHead;

const int SERVO_PIN = 13;

int currentHeadPosition = 90;

// ---------------- AUDIO ----------------

#define AUDIO_PIN 25

#define SERIAL_BAUD 921600

const uint32_t SAMPLE_RATE = 16000;

// Maximum audio buffer.
// Increase only if required.
const uint32_t MAX_AUDIO_SIZE = 60000;

uint8_t *audioBuffer = nullptr;


// ============================================================
// EYES
// ============================================================

void normalEye(
  U8G2 &eye,
  int pupilX,
  int pupilY
) {
  eye.clearBuffer();

  eye.setDrawColor(1);

  // Eye
  eye.drawRBox(
    25, 12,
    78, 40,
    18
  );

  // Pupil
  eye.setDrawColor(0);

  eye.drawDisc(
    pupilX,
    pupilY,
    14
  );

  // Highlight
  eye.setDrawColor(1);

  eye.drawDisc(
    pupilX - 5,
    pupilY - 5,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// HAPPY
// ============================================================

void happyEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawLine(28,38,40,29);
  eye.drawLine(40,29,52,25);
  eye.drawLine(52,25,64,24);
  eye.drawLine(64,24,76,25);
  eye.drawLine(76,25,88,29);
  eye.drawLine(88,29,100,38);

  eye.drawLine(32,39,44,33);
  eye.drawLine(44,33,56,30);
  eye.drawLine(56,30,64,29);
  eye.drawLine(64,29,72,30);
  eye.drawLine(72,30,84,33);
  eye.drawLine(84,33,96,39);

  eye.sendBuffer();
}


// ============================================================
// SAD
// ============================================================

void sadEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawRBox(
    25, 16,
    78, 38,
    17
  );

  eye.setDrawColor(0);

  eye.drawDisc(
    64,
    39,
    13
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    59,
    34,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// SURPRISED
// ============================================================

void surprisedEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawDisc(
    64,
    32,
    25
  );

  eye.setDrawColor(0);

  eye.drawDisc(
    64,
    32,
    11
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    60,
    28,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// CONFUSED
// ============================================================

void confusedEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawRBox(
    25, 15,
    78, 38,
    17
  );

  eye.setDrawColor(0);

  eye.drawDisc(
    56,
    35,
    12
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    52,
    31,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// THINKING
// ============================================================

void thinkingEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawRBox(
    25, 12,
    78, 40,
    18
  );

  eye.setDrawColor(0);

  eye.drawDisc(
    76,
    32,
    13
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    71,
    27,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// LISTENING
// ============================================================

void listeningEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawRBox(
    25, 12,
    78, 40,
    18
  );

  eye.setDrawColor(0);

  eye.drawDisc(
    64,
    32,
    16
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    59,
    27,
    5
  );

  eye.sendBuffer();
}


// ============================================================
// SLEEPING
// ============================================================

void sleepingEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawLine(
    30,34,
    95,34
  );

  eye.drawLine(
    35,37,
    90,37
  );

  eye.sendBuffer();
}


// ============================================================
// SPEAKING
// ============================================================

void speakingEye(U8G2 &eye) {

  eye.clearBuffer();

  eye.setDrawColor(1);

  eye.drawRBox(
    25, 12,
    78, 40,
    18
  );

  eye.setDrawColor(0);

  eye.drawRBox(
    52, 37,
    24, 9,
    4
  );

  eye.setDrawColor(1);

  eye.drawDisc(
    59,
    27,
    4
  );

  eye.sendBuffer();
}


// ============================================================
// BOTH EYES
// ============================================================

void bothNormal() {
  normalEye(leftEye,64,32);
  normalEye(rightEye,64,32);
}

void bothHappy() {
  happyEye(leftEye);
  happyEye(rightEye);
}

void bothSad() {
  sadEye(leftEye);
  sadEye(rightEye);
}

void bothSurprised() {
  surprisedEye(leftEye);
  surprisedEye(rightEye);
}

void bothConfused() {
  confusedEye(leftEye);
  confusedEye(rightEye);
}

void bothThinking() {
  thinkingEye(leftEye);
  thinkingEye(rightEye);
}

void bothListening() {
  listeningEye(leftEye);
  listeningEye(rightEye);
}

void bothSleeping() {
  sleepingEye(leftEye);
  sleepingEye(rightEye);
}

void bothSpeaking() {
  speakingEye(leftEye);
  speakingEye(rightEye);
}


// ============================================================
// SERVO
// ============================================================

void moveHead(int target) {
  target = constrain(target, 45, 135);
  atlasHead.write(target);
  currentHeadPosition = target;
}


// ============================================================
// REACTIONS
// ============================================================

void reactionNeutral() {
  bothNormal();
  moveHead(90);
  Serial.println("[SERVO] NEUTRAL");
}

void reactionHappy() {
  bothHappy();
  moveHead(75);
  delay(180);
  moveHead(105);
  delay(180);
  moveHead(90);
  Serial.println("[SERVO] HAPPY");
}

void reactionSad() {
  bothSad();
  moveHead(75);
  delay(300);
  moveHead(90);
  Serial.println("[SERVO] SAD");
}

void reactionSurprised() {
  bothSurprised();
  moveHead(65);
  delay(180);
  moveHead(115);
  delay(250);
  moveHead(90);
  Serial.println("[SERVO] SURPRISED");
}

void reactionConfused() {
  bothConfused();
  moveHead(75);
  delay(250);
  moveHead(105);
  delay(250);
  moveHead(90);
  Serial.println("[SERVO] CONFUSED");
}

void reactionThinking() {
  bothThinking();
  moveHead(70);
  delay(300);
  moveHead(110);
  delay(300);
  moveHead(90);
  Serial.println("[SERVO] THINKING");
}

void reactionListening() {
  bothListening();
  moveHead(90);
  Serial.println("[SERVO] LISTENING");
}

void reactionSpeaking() {
  bothSpeaking();
  moveHead(90);
  Serial.println("[SERVO] SPEAKING");
}

void servoTest() {
  Serial.println("[SERVO TEST START]");
  moveHead(60);
  Serial.println("[SERVO] 60");
  delay(350);
  moveHead(120);
  Serial.println("[SERVO] 120");
  delay(350);
  moveHead(90);
  Serial.println("[SERVO] 90");
  delay(200);
  Serial.println("[SERVO TEST COMPLETE]");
}

// ============================================================
// COMMAND HANDLER
// ============================================================

void handleCommand(String raw) {
  raw.trim();
  if (raw.length() == 0) return;

  String cmd = raw;
  cmd.toUpperCase();

  Serial.print("[COMMAND] ");
  Serial.println(raw);

  if (cmd == "SERVO_TEST" || cmd == "TEST") {
    servoTest();
  }
  else if (cmd == "SERVO_CENTER" || cmd == "CENTER") {
    bothNormal();
    moveHead(90);
    Serial.println("[SERVO] CENTER");
  }
  else if (cmd == "SERVO_LEFT" || cmd == "LEFT") {
    normalEye(leftEye, 48, 32);
    normalEye(rightEye, 48, 32);
    moveHead(60);
    Serial.println("[SERVO] LEFT");
  }
  else if (cmd == "SERVO_RIGHT" || cmd == "RIGHT") {
    normalEye(leftEye, 80, 32);
    normalEye(rightEye, 80, 32);
    moveHead(120);
    Serial.println("[SERVO] RIGHT");
  }
  else if (cmd == "EMOTION_HAPPY" || cmd == "STATE:HAPPY" || cmd == "HAPPY") {
    reactionHappy();
  }
  else if (cmd == "EMOTION_SAD" || cmd == "STATE:SAD" || cmd == "SAD") {
    reactionSad();
  }
  else if (cmd == "EMOTION_SURPRISED" || cmd == "STATE:SURPRISED" || cmd == "SURPRISED" || cmd == "SURPRISE") {
    reactionSurprised();
  }
  else if (cmd == "EMOTION_CONFUSED" || cmd == "STATE:CONFUSED" || cmd == "CONFUSED") {
    reactionConfused();
  }
  else if (cmd == "EMOTION_THINKING" || cmd == "STATE:THINKING" || cmd == "THINKING" || cmd == "THINK") {
    reactionThinking();
  }
  else if (cmd == "EMOTION_NEUTRAL" || cmd == "STATE:IDLE" || cmd == "STATE:NEUTRAL" || cmd == "NEUTRAL" || cmd == "IDLE") {
    reactionNeutral();
  }
  else if (cmd == "LISTEN" || cmd == "STATE:LISTENING" || cmd == "LISTENING") {
    reactionListening();
  }
  else if (cmd == "SPEAK" || cmd == "STATE:SPEAKING" || cmd == "SPEAKING") {
    reactionSpeaking();
  }
  else if (cmd == "STATUS" || cmd == "PING") {
    Serial.println("[STATUS] READY");
  }
  else if (cmd.indexOf("\"STATE\":\"HAPPY\"") >= 0) {
    reactionHappy();
  }
  else if (cmd.indexOf("\"STATE\":\"SAD\"") >= 0) {
    reactionSad();
  }
  else if (cmd.indexOf("\"STATE\":\"CONFUSED\"") >= 0) {
    reactionConfused();
  }
  else if (cmd.indexOf("\"STATE\":\"SURPRISED\"") >= 0) {
    reactionSurprised();
  }
  else if (cmd.indexOf("\"STATE\":\"THINKING\"") >= 0) {
    reactionThinking();
  }
  else if (cmd.indexOf("\"STATE\":\"IDLE\"") >= 0 || cmd.indexOf("\"STATE\":\"NEUTRAL\"") >= 0) {
    reactionNeutral();
  }
  else if (cmd.indexOf("\"STATE\":\"LISTENING\"") >= 0) {
    reactionListening();
  }
  else if (cmd.indexOf("\"STATE\":\"SPEAKING\"") >= 0) {
    reactionSpeaking();
  }
}

// ============================================================
// RECEIVE AUDIO
// ============================================================

bool readExact(
  uint8_t *buffer,
  uint32_t length
) {
  uint32_t received = 0;
  uint32_t start = millis();

  while (received < length) {
    if (Serial.available()) {
      int availableBytes = Serial.available();
      uint32_t remaining = length - received;
      uint32_t toRead = min((uint32_t)availableBytes, remaining);

      size_t n = Serial.readBytes((char *)(buffer + received), toRead);
      received += n;
      start = millis();
    }

    if (millis() - start > 10000) {
      Serial.println("[ERROR] AUDIO RECEIVE TIMEOUT");
      return false;
    }
  }

  return true;
}

// ============================================================
// PLAY AUDIO
// ============================================================

void playAudio(
  uint8_t *buffer,
  uint32_t length
) {
  Serial.println("[AUDIO] PLAYING 16K AUDIO");
  bothSpeaking();

  uint32_t nextSample = micros();
  const uint32_t interval = 1000000UL / SAMPLE_RATE;

  for (uint32_t i = 0; i < length; i++) {
    while ((int32_t)(micros() - nextSample) < 0) {
      yield();
    }
    dacWrite(AUDIO_PIN, buffer[i]);
    nextSample += interval;
  }

  dacWrite(AUDIO_PIN, 128);
  Serial.println("[AUDIO] PLAYBACK COMPLETE");
}

// ============================================================
// VOICE PACKET
// ============================================================

void receiveVoice() {
  Serial.println("[VOICE] HEADER RECEIVED");

  uint8_t lenBytes[4];
  if (!readExact(lenBytes, 4)) {
    return;
  }

  uint32_t length =
    ((uint32_t)lenBytes[0]) |
    ((uint32_t)lenBytes[1] << 8) |
    ((uint32_t)lenBytes[2] << 16) |
    ((uint32_t)lenBytes[3] << 24);

  Serial.print("[VOICE] BYTES: ");
  Serial.println(length);

  if (length == 0 || length > MAX_AUDIO_SIZE) {
    Serial.println("[ERROR] INVALID AUDIO SIZE");
    return;
  }

  if (audioBuffer != nullptr) {
    free(audioBuffer);
    audioBuffer = nullptr;
  }

  audioBuffer = (uint8_t *)malloc(length);
  if (audioBuffer == nullptr) {
    Serial.println("[ERROR] AUDIO MEMORY FAILED");
    return;
  }

  Serial.println("[VOICE] RECEIVING BUFFER...");
  if (!readExact(audioBuffer, length)) {
    free(audioBuffer);
    audioBuffer = nullptr;
    return;
  }

  Serial.println("[VOICE] FULL BUFFER RECEIVED");
  playAudio(audioBuffer, length);

  free(audioBuffer);
  audioBuffer = nullptr;

  bothNormal();
  moveHead(90);
  Serial.println("[VOICE] READY");
}

// ============================================================
// SERIAL PROCESSOR (Line-based; no character hijacking)
// ============================================================

String serialLine = "";

void processSerial() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      serialLine.trim();
      if (serialLine.length() > 0) {
        if (serialLine == "VOICE") {
          receiveVoice();
        } else {
          handleCommand(serialLine);
        }
        serialLine = "";
      }
    } else {
      serialLine += c;
      if (serialLine.length() > 64) {
        serialLine = "";
      }
    }
  }
}


// ============================================================
// SETUP
// ============================================================

void setup() {

  Serial.begin(
    SERIAL_BAUD
  );

  Serial.setTimeout(100);

  // Important for buffered serial
  Serial.setRxBufferSize(
    60000
  );

  delay(1000);

  dacWrite(
    AUDIO_PIN,
    128
  );

  // OLED #1
  Wire.begin(
    21,
    22
  );

  leftEye.setI2CAddress(
    0x3C * 2
  );

  leftEye.begin();

  // OLED #2
  rightEye.setI2CAddress(
    0x3C * 2
  );

  rightEye.begin();

  // Servo PWM setup (ensure all LEDC timers are allocated)
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  atlasHead.setPeriodHertz(50);
  atlasHead.attach(
    SERVO_PIN,
    500,
    2400
  );

  atlasHead.write(90);
  currentHeadPosition = 90;

  bothNormal();

  Serial.println();
  Serial.println(
    "================================"
  );
  Serial.println(
    " ATLAS HOME FINAL ROBOT"
  );
  Serial.println(
    " OLED + SERVO + AUDIO"
  );
  Serial.println(
    "================================"
  );

  Serial.println(
    "BAUD: 921600"
  );

  Serial.println(
    "AUDIO: GPIO25"
  );

  Serial.println(
    "SERVO: GPIO13"
  );

  Serial.println(
    "OLED1: 21/22"
  );

  Serial.println(
    "OLED2: 19/18"
  );

  Serial.println(
    "ATLAS READY"
  );
}


// ============================================================
// LOOP
// ============================================================

void loop() {

  processSerial();

  delay(1);
}