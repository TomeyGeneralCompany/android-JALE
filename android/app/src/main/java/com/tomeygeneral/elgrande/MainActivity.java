package com.tomeygeneral.elgrande;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.WebResourceRequest;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.content.pm.PackageManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import com.chaquo.python.Python;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.SecureRandom;

public class MainActivity extends Activity {
    private static final String ROOT = "http://127.0.0.1:8765";
    private WebView web;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private static String processSession;
    private static volatile String startupFailure;

    public static String getStartupFailure() { return startupFailure; }
    private String session;
    private PermissionRequest pendingMicrophone;
    private static final int CALL_MICROPHONE = 150;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        startupFailure = null;
        QwenNative.initialize(getApplicationContext());
        CallSpeech.initialize(getApplicationContext());
        synchronized (MainActivity.class) {
            if (processSession == null) {
                byte[] random = new byte[32];
                new SecureRandom().nextBytes(random);
                StringBuilder key = new StringBuilder();
                for (byte b : random) key.append(String.format(java.util.Locale.ROOT, "%02x", b & 0xff));
                processSession = key.toString();
            }
            session = processSession;
        }

        FrameLayout frame = new FrameLayout(this);
        web = new WebView(this);
        frame.addView(web);
        ProgressBar loading = new ProgressBar(this);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(130, 130,
                android.view.Gravity.CENTER);
        frame.addView(loading, lp);
        setContentView(frame);

        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setDomStorageEnabled(false);
        web.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setCookie(ROOT, "eg_session=" + session +
                "; Path=/; HttpOnly; SameSite=Strict", ignored -> CookieManager.getInstance().flush());
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(PermissionRequest request) {
                String page = web.getUrl();
                if (!ROOT.equals(request.getOrigin().toString().replaceAll("/$", ""))
                        || page == null || !page.matches(java.util.regex.Pattern.quote(ROOT) + "/office/call-coach/[0-9]+")
                        || !java.util.Arrays.equals(request.getResources(),
                            new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE})) {
                    request.deny();
                    return;
                }
                if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED) {
                    request.grant(request.getResources());
                } else if (pendingMicrophone == null) {
                    pendingMicrophone = request;
                    requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, CALL_MICROPHONE);
                } else {
                    request.deny();
                }
            }

            @Override public void onPermissionRequestCanceled(PermissionRequest request) {
                if (pendingMicrophone == request) pendingMicrophone = null;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri uri = req.getUrl();
                if ("127.0.0.1".equals(uri.getHost()) && uri.getPort() == 8765
                        && "http".equals(uri.getScheme())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
                catch (Exception ex) { Toast.makeText(MainActivity.this, "No app available for link", Toast.LENGTH_LONG).show(); }
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            if (url.startsWith(ROOT + "/") && ("application/pdf".equals(mime) || url.endsWith(".pdf"))) {
                shareLocalFile(url, "application/pdf", "el-grande-", ".pdf", "Share customer invoice");
            } else if ((ROOT + "/office/routes.csv").equals(url)) {
                shareLocalFile(url, "text/csv", "jale-routes-", ".csv", "Share route schedule");
            } else {
                Toast.makeText(this, "This download is unavailable in JALE", Toast.LENGTH_LONG).show();
            }
        });
        new Thread(() -> {
            try {
                Python.getInstance().getModule("mobile_server")
                    .callAttr("start", getFilesDir().getAbsolutePath(), session);
                pollReady(0, loading);
            } catch (Exception error) {
                startupFailure = error.toString();
                android.util.Log.e("JALE_STARTUP", startupFailure, error);
                ui.post(() -> new AlertDialog.Builder(this)
                    .setTitle("El Grande could not start")
                    .setMessage(error.toString())
                    .setPositiveButton("Close", (d, w) -> finish()).show());
            }
        }, "python-start").start();
    }

    private void pollReady(int tries, ProgressBar loading) {
        String pyError = Python.getInstance().getModule("mobile_server").callAttr("startup_error").toString();
        if (!pyError.isEmpty()) {
            startupFailure = pyError;
            android.util.Log.e("JALE_STARTUP", pyError);
            ui.post(() -> new AlertDialog.Builder(this).setTitle("JALE could not start")
                .setMessage("The local job desk failed to start. See app logs for details.")
                .setPositiveButton("Close", (d, w) -> finish()).show());
            return;
        }
        if (tries >= 200) {
            startupFailure = "Local desk did not become ready within 20 seconds";
            android.util.Log.e("JALE_STARTUP", startupFailure);
            ui.post(() -> new AlertDialog.Builder(this).setTitle("Startup failed")
                .setMessage("Local job desk did not become ready. Check Android app logs.")
                .setPositiveButton("Close", (d, w) -> finish()).show());
            return;
        }
        try {
            HttpURLConnection conn = (HttpURLConnection) new URL(ROOT + "/ready").openConnection();
            conn.setConnectTimeout(1000);
            conn.setReadTimeout(1000);
            conn.setRequestProperty("Cookie", "eg_session=" + session);
            int code = conn.getResponseCode();
            conn.disconnect();
            if (code == 200) {
                ui.post(() -> { loading.setVisibility(View.GONE); web.loadUrl(ROOT + "/"); });
                return;
            }
        } catch (Exception ignored) { }
        try { Thread.sleep(100); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
        pollReady(tries + 1, loading);
    }

    private void shareLocalFile(String url, String mime, String prefix, String suffix, String title) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(10000);
                conn.setRequestProperty("Cookie", "eg_session=" + session);
                if (conn.getResponseCode() != 200) throw new IllegalStateException("Invoice request failed");
                File dir = new File(getCacheDir(), "invoices");
                if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("Cannot create invoice cache");
                File out = File.createTempFile(prefix, suffix, dir);
                try (InputStream in = conn.getInputStream(); FileOutputStream target = new FileOutputStream(out)) {
                    byte[] buffer = new byte[8192];
                    int n;
                    while ((n = in.read(buffer)) != -1) target.write(buffer, 0, n);
                } finally { conn.disconnect(); }
                Uri shareUri = FileProvider.getUriForFile(this, getPackageName() + ".files", out);
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType(mime);
                send.putExtra(Intent.EXTRA_STREAM, shareUri);
                send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                ui.post(() -> startActivity(Intent.createChooser(send, title)));
            } catch (Exception error) {
                ui.post(() -> Toast.makeText(this, "Export failed: " + error.getMessage(),
                        Toast.LENGTH_LONG).show());
            }
        }, "invoice-share").start();
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != CALL_MICROPHONE || pendingMicrophone == null) return;
        PermissionRequest request = pendingMicrophone;
        pendingMicrophone = null;
        if (results.length == 1 && results[0] == PackageManager.PERMISSION_GRANTED) {
            request.grant(request.getResources());
        } else {
            request.deny();
        }
    }

    @Override public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (pendingMicrophone != null) pendingMicrophone.deny();
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
