package com.tomeygeneral.elgrande;

import android.app.Activity;
import android.webkit.CookieManager;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

import static org.junit.Assert.*;

/**
 * On-device system smoke test against the actual Python server, SQLite,
 * dashboard and bundled brand image. Never uses fake server responses.
 */
@RunWith(AndroidJUnit4.class)
public class JaleSmokeTest {
    private static final String ROOT = "http://127.0.0.1:8765";

    private static HttpURLConnection connect(String path, String method) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(ROOT + path).openConnection();
        c.setConnectTimeout(2500);
        c.setReadTimeout(5000);
        c.setRequestMethod(method);
        c.setInstanceFollowRedirects(false);
        String cookie = CookieManager.getInstance().getCookie(ROOT);
        if (cookie != null) c.setRequestProperty("Cookie", cookie);
        return c;
    }

    private static String read(HttpURLConnection conn) throws Exception {
        try (InputStream in = conn.getInputStream();
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[8192];
            for (int n; (n = in.read(b)) >= 0; ) out.write(b, 0, n);
            return out.toString("UTF-8");
        } finally { conn.disconnect(); }
    }

    private static String awaitDashboard() throws Exception {
        long end = System.currentTimeMillis() + 90000L;
        Exception last = null;
        while (System.currentTimeMillis() < end) {
            try {
                HttpURLConnection conn = connect("/", "GET");
                if (conn.getResponseCode() == 200) {
                    String page = read(conn);
                    if (page.contains("<strong>JALE</strong>")) return page;
                } else conn.disconnect();
            } catch (Exception e) { last = e; }
            Thread.sleep(500);
        }
        throw new AssertionError("JALE did not serve dashboard in 90 seconds", last);
    }

    @Test public void launchesWithRealLogoCreatesAndPersistsJob() throws Exception {
        String customer = "Android smoke " + UUID.randomUUID();
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            String first = awaitDashboard();
            assertTrue("JALE name", first.contains("<strong>JALE</strong>"));
            assertTrue("Tomey parent branding", first.contains("/brand/tomey.png"));
            assertFalse("No subsidiary header", first.contains("All by Devoll"));

            HttpURLConnection logo = connect("/brand/tomey.png", "GET");
            assertEquals("Bundled logo must exist in Android app", 200, logo.getResponseCode());
            assertEquals("image/png", logo.getHeaderField("Content-Type"));
            try (InputStream in = logo.getInputStream()) {
                byte[] header = new byte[8];
                assertEquals(8, in.read(header));
                assertEquals((byte)0x89, header[0]);
                assertEquals((byte)'P', header[1]);
            } finally { logo.disconnect(); }

            HttpURLConnection create = connect("/ticket/new", "POST");
            create.setDoOutput(true);
            create.setRequestProperty("Origin", ROOT);
            create.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            String body = "customer=" + java.net.URLEncoder.encode(customer, "UTF-8") +
                    "&problem=Android+smoke+test&service=Service";
            try (OutputStream out = create.getOutputStream()) {
                out.write(body.getBytes(StandardCharsets.UTF_8));
            }
            assertEquals("Job creation should redirect", 303, create.getResponseCode());
            create.disconnect();

            assertTrue("Real job is persisted in dashboard", awaitDashboard().contains(customer));
        }
        try (ActivityScenario<MainActivity> reopened = ActivityScenario.launch(MainActivity.class)) {
            assertTrue("Job survives reopening app", awaitDashboard().contains(customer));
        }
    }
}
