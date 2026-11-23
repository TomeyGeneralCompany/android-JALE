package com.tomeygeneral.elgrande;

import android.content.Context;
import android.content.res.AssetManager;
import android.util.Log;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

/** The bundled Qwen model is used on the phone, never a network endpoint. */
public final class QwenNative {
    private static final String MODEL = "Qwen3-0.6B-Q4_K_M.gguf";
    private static volatile Context applicationContext;
    private static final Object INSTALL_LOCK = new Object();

    static {
        System.loadLibrary("jale_qwen");
    }

    private QwenNative() { }

    public static void initialize(Context context) {
        applicationContext = context.getApplicationContext();
    }

    private static native String nativeGenerate(String modelPath, String prompt, int maxTokens);

    public static String generate(String prompt) throws IOException {
        final Context context = applicationContext;
        if (context == null) throw new IOException("JALE mobile AI was not initialized");
        if (prompt == null || prompt.isEmpty()) throw new IOException("Empty AI request");
        final File model = ensureModel(context);
        Log.i("JALE_QWEN", "Generating from bundled local Qwen model");
        return nativeGenerate(model.getAbsolutePath(), prompt, 320);
    }

    public static boolean modelReady() {
        Context context = applicationContext;
        if (context == null) return false;
        File file = new File(new File(context.getFilesDir(), "models"), MODEL);
        return file.isFile() && file.length() > 350_000_000L;
    }

    private static File ensureModel(Context context) throws IOException {
        synchronized (INSTALL_LOCK) {
            File dir = new File(context.getFilesDir(), "models");
            if (!dir.isDirectory() && !dir.mkdirs()) {
                throw new IOException("Cannot prepare local Qwen model directory");
            }
            File result = new File(dir, MODEL);
            // The asset is included in the APK. It never comes from a URL.
            long assetSize;
            try (android.content.res.AssetFileDescriptor descriptor =
                         context.getAssets().openFd(MODEL)) {
                assetSize = descriptor.getLength();
            }
            if (assetSize < 350_000_000L) throw new IOException("Bundled Qwen model is incomplete");
            if (result.isFile() && result.length() == assetSize) return result;

            File staging = new File(dir, MODEL + ".partial");
            try (InputStream source = context.getAssets().open(MODEL, AssetManager.ACCESS_STREAMING);
                 FileOutputStream target = new FileOutputStream(staging)) {
                byte[] buffer = new byte[256 * 1024];
                int count;
                while ((count = source.read(buffer)) != -1) target.write(buffer, 0, count);
                target.getFD().sync();
            } catch (Exception error) {
                staging.delete();
                if (error instanceof IOException) throw (IOException) error;
                throw new IOException("Cannot extract bundled Qwen model", error);
            }
            if (staging.length() != assetSize) {
                staging.delete();
                throw new IOException("Bundled Qwen model extraction incomplete");
            }
            if (!staging.renameTo(result)) {
                staging.delete();
                throw new IOException("Cannot activate local Qwen model");
            }
            return result;
        }
    }
}
