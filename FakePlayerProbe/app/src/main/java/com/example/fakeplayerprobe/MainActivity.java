package com.example.fakeplayerprobe;

import android.app.Activity;
import android.os.Bundle;
import android.content.Intent;
import android.net.Uri;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.widget.TextView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {

    private static final String DEFAULT_SERVER = "10.0.2.2:16888";

    private TextView output;
    private TextView status;
    private EditText serverEdit;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        setContentView(R.layout.activity_main);

        output = findViewById(R.id.output);
        status = findViewById(R.id.status);
        serverEdit = findViewById(R.id.server);

        SharedPreferences prefs = getSharedPreferences("playbridge", MODE_PRIVATE);
        serverEdit.setText(prefs.getString("server", DEFAULT_SERVER));

        Button copy = findViewById(R.id.copy);

        copy.setOnClickListener(v -> {
            ClipboardManager cm =
                    (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);

            cm.setPrimaryClip(
                    ClipData.newPlainText(
                            "PlayBridge",
                            output.getText().toString()
                    )
            );

            Toast.makeText(this, "已复制", Toast.LENGTH_SHORT).show();
        });

        dumpIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);

        setIntent(intent);

        dumpIntent(intent);
    }

    private void dumpIntent(Intent intent) {

        StringBuilder sb = new StringBuilder();

        sb.append("========== URI ==========\n");
        Uri uri = intent.getData();
        sb.append(uri != null ? uri.toString() : "(null)");
        sb.append("\n\n");

        sb.append("========== MIME TYPE ==========\n");
        sb.append(intent.getType());
        sb.append("\n\n");

        sb.append("========== EXTRAS ==========\n");
        Bundle extras = intent.getExtras();
        if (extras == null || extras.isEmpty()) {
            sb.append("(none)\n");
        } else {
            for (String key : extras.keySet()) {
                Object value;
                try {
                    value = extras.get(key);
                } catch (Exception e) {
                    value = "(读取失败)";
                }
                sb.append(key).append(" = ").append(value).append("\n");
            }
        }

        output.setText(sb.toString());

        if (uri != null) {
            String server = serverEdit.getText().toString().trim();
            if (!server.isEmpty()) {
                getSharedPreferences("playbridge", MODE_PRIVATE)
                        .edit().putString("server", server).apply();
            }
            sendToWindows(server, uri.toString(), pickTitle(intent, uri));
        }
    }

    private String pickTitle(Intent intent, Uri uri) {
        Bundle extras = intent.getExtras();
        if (extras != null) {
            for (String key : new String[]{"title", "name", "displayName", "videoTitle"}) {
                Object v = extras.get(key);
                if (v instanceof CharSequence && ((CharSequence) v).length() > 0) {
                    return v.toString();
                }
            }
        }
        String name = uri.getLastPathSegment();
        if (name != null) {
            try {
                name = URLDecoder.decode(name, "UTF-8");
            } catch (Exception ignored) {
            }
            return name;
        }
        return "";
    }

    private void sendToWindows(String server, String url, String title) {
        final String srv = server.startsWith("http") ? server : "http://" + server;
        new Thread(() -> {
            String result;
            try {
                JSONObject json = new JSONObject();
                json.put("version", 1);
                json.put("action", "play");
                json.put("url", url);
                json.put("title", title);
                json.put("position", 0);

                // extras 原样透传（只透传字符串值），影视仓若用 extras 传 header/UA/Referer 才不会丢
                Bundle ex = getIntent().getExtras();
                if (ex != null && !ex.isEmpty()) {
                    JSONObject extras = new JSONObject();
                    for (String key : ex.keySet()) {
                        Object v = ex.get(key);
                        if (v instanceof CharSequence || v instanceof Number) {
                            extras.put(key, v.toString());
                        }
                    }
                    if (extras.length() > 0) {
                        json.put("extras", extras);
                    }
                }

                HttpURLConnection conn =
                        (HttpURLConnection) new URL(srv + "/play").openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(5000);
                conn.setDoOutput(true);

                byte[] body = json.toString().getBytes(StandardCharsets.UTF_8);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(body);
                }

                int code = conn.getResponseCode();
                conn.disconnect();
                result = code == 200
                        ? "✓ 已发送到 Windows（" + srv + "）"
                        : "✗ Windows 返回 HTTP " + code;
            } catch (Exception e) {
                result = "✗ 发送失败 → " + srv + "\n(" + e.getMessage() + ")";
            }

            final String r = result;
            runOnUiThread(() -> status.setText(r));
        }).start();
    }
}
