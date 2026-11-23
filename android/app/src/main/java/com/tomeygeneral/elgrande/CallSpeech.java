package com.tomeygeneral.elgrande;

import android.content.Context;
import android.os.Build;
import android.os.SystemClock;
import android.util.Base64;

import ai.moonshine.voice.AssetDownloader;
import ai.moonshine.voice.JNI;
import ai.moonshine.voice.ModelCache;
import ai.moonshine.voice.ModelSpec;
import ai.moonshine.voice.Transcriber;
import ai.moonshine.voice.TranscriptEvent;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** One remote-audio stream, exposed only through the private loopback desk. */
public final class CallSpeech {
    private static Context context;
    private static Transcriber engine;
    private static String session;
    private static long lastAudio;
    private static int stream = -1;
    private static int sequence;
    private static int revision;
    private static String latest = "";
    private static String completed = "";
    private static String lastFinal = "";

    private CallSpeech() { }

    public static void initialize(Context app) {
        context = app.getApplicationContext();
    }

    public static String bundle() throws IOException {
        try (InputStream input = context.getAssets().open("jale-sip.js");
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            for (int count; (count = input.read(buffer)) != -1;) {
                output.write(buffer, 0, count);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    public static synchronized void prepare() throws Exception {
        if (Build.VERSION.SDK_INT < 26) {
            throw new IllegalStateException("Call transcription requires Android 8.0 or newer.");
        }
        if (engine != null) return;
        int architecture = JNI.MOONSHINE_MODEL_ARCH_TINY_STREAMING;
        ModelSpec spec = ModelSpec.stt("en", architecture, false);
        File directory = ModelCache.directoryFor(context, spec, null);
        new AssetDownloader().ensureModelPresent(directory, spec, null);
        Transcriber candidate = new Transcriber();
        try {
            candidate.loadFromFiles(directory.getAbsolutePath(), architecture);
            candidate.setUpdateInterval(0.35);
            candidate.addListener(event -> {
                if (event instanceof TranscriptEvent.LineTextChanged) {
                    update(((TranscriptEvent.LineTextChanged) event).line.text, false);
                } else if (event instanceof TranscriptEvent.LineCompleted) {
                    update(((TranscriptEvent.LineCompleted) event).line.text, true);
                } else if (event instanceof TranscriptEvent.Error) {
                    throw new IllegalStateException("Transcription failed.");
                }
            });
            engine = candidate;
        } finally {
            if (engine != candidate) candidate.close();
        }
    }

    public static synchronized String start() {
        if (engine == null) throw new IllegalStateException("Prepare the speech model before coaching.");
        if (session != null && SystemClock.elapsedRealtime() - lastAudio < 15_000L) {
            throw new IllegalStateException("Another coaching session is active.");
        }
        releaseStream();
        stream = engine.createStream();
        if (stream < 0) throw new IllegalStateException("Could not start transcription.");
        try {
            engine.startStream(stream);
        } catch (RuntimeException error) {
            releaseStream();
            throw error;
        }
        session = UUID.randomUUID().toString();
        latest = completed = lastFinal = "";
        sequence = revision = 0;
        lastAudio = SystemClock.elapsedRealtime();
        return session;
    }

    private static void update(String text, boolean finalLine) {
        if (text == null || text.trim().isEmpty()) return;
        String line = text.trim();
        if (finalLine) {
            completed = (completed.isEmpty() ? "" : completed + " ") + line;
            if (completed.length() > 2400) completed = completed.substring(completed.length() - 2400);
            latest = "";
            lastFinal = line;
        } else if (line.equals(latest)) {
            return;
        } else {
            latest = line;
        }
        revision++;
    }

    private static String result() throws Exception {
        return new JSONObject().put("latest", latest).put("complete", completed)
                .put("lastFinal", lastFinal).put("revision", revision).toString();
    }

    public static synchronized String audio(String id, int index, String encoded) throws Exception {
        if (!id.equals(session)) throw new IllegalStateException("The coaching session has ended.");
        if (index == sequence - 1) return result();
        if (index != sequence) throw new IllegalStateException("Audio arrived out of order.");
        byte[] bytes = Base64.decode(encoded, Base64.NO_WRAP);
        if (bytes.length == 0 || bytes.length > 16000 || bytes.length % 2 != 0) {
            throw new IllegalArgumentException("Invalid audio frame.");
        }
        ByteBuffer pcm = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        float[] samples = new float[bytes.length / 2];
        for (int i = 0; i < samples.length; i++) samples[i] = pcm.getShort() / 32768.0f;
        engine.addAudioToStream(stream, samples, 16_000);
        sequence++;
        lastAudio = SystemClock.elapsedRealtime();
        return result();
    }

    public static synchronized String stop(String id) throws Exception {
        if (!id.equals(session)) return "{}";
        try {
            engine.stopStream(stream);
            return result();
        } finally {
            releaseStream();
            latest = completed = lastFinal = "";
        }
    }

    private static void releaseStream() {
        if (stream >= 0) engine.freeStream(stream);
        stream = -1;
        session = null;
    }
}
