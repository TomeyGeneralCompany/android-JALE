#!/usr/bin/env bash
set -u
# Called by android-emulator-runner inside the running emulator session.
# Never let a failing Gradle instrumentation test skip Android diagnostics.
status=0
gradle --no-daemon -p android :app:connectedDebugAndroidTest -PciEmulator || status=$?
adb logcat -d -t 6000 > jale-android-logcat.txt || true
if [ -s jale-android-logcat.txt ]; then
  grep -E 'JALE_STARTUP|AndroidRuntime|python\.stderr|System\.err|Traceback|PyException|FATAL EXCEPTION' jale-android-logcat.txt | tail -100 || true
fi
exit "$status"
