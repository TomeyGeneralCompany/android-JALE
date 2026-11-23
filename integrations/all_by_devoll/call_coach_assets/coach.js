"use strict";
const byId = id => document.getElementById(id);
const dial = byId("dial");
const remote = byId("remote");
const keypad = byId("keypad");
const hangup = byId("hangup");
const prepare = byId("prepare");
const transcribe = byId("transcribe");
const stopCoach = byId("stop-coach");
const callStatus = byId("call-status");
const speechStatus = byId("speech-status");
let phone, context, capture, attempt, finishing;
let prepared = false;
let connected = false;
let coaching = false;
let transcript = "";

async function speech(action, data = {}) {
    const result = await fetch("/office/call-speech/" + action, {
        method: "POST", body: new URLSearchParams(data)
    });
    const body = await result.json();
    if (!result.ok) throw new Error(body.error || "Call coaching failed.");
    return body;
}

function controls() {
    dial.querySelector("button").disabled = Boolean(attempt);
    hangup.disabled = !attempt || Boolean(attempt.ending);
    keypad.querySelector("button").disabled = !connected;
    transcribe.disabled = !(prepared && connected) || coaching || Boolean(finishing);
    stopCoach.disabled = !coaching;
}

function update(result) {
    if (!result || result.revision === undefined) return;
    transcript = [result.complete, result.latest].filter(Boolean).join(" ");
    byId("transcript").textContent = transcript;
    if (result.suggestion) byId("suggestion").textContent = result.suggestion;
}

function finishCoaching() {
    if (!finishing) {
        finishing = releaseCoaching().finally(() => {
            finishing = null;
            controls();
        });
        controls();
    }
    return finishing;
}

async function releaseCoaching() {
    const recording = capture;
    capture = null;
    coaching = false;
    controls();
    if (!recording) return;
    recording.worklet.port.onmessage = null;
    recording.source.disconnect();
    recording.worklet.disconnect();
    recording.muted.disconnect();
    recording.queue.length = 0;
    try {
        await recording.inFlight;
        update(await speech("stop", {session: recording.session}));
    } catch {
        speechStatus.textContent = "Coaching ended. Review any transcript shown.";
    }
}

async function endCall(item, message) {
    if (attempt !== item || item.ending) return;
    item.ending = true;
    connected = false;
    controls();
    callStatus.textContent = message;
    const oldPhone = phone;
    const oldContext = context;
    phone = context = null;
    await finishCoaching();
    if (oldPhone) {
        try { await oldPhone.disconnect(); } catch { /* transport already closed */ }
    }
    remote.srcObject = null;
    if (oldContext && oldContext.state !== "closed") await oldContext.close().catch(() => {});
    attempt = null;
    controls();
}

function sipConfig() {
    const form = new FormData(dial);
    const server = new URL(form.get("server").trim());
    if (server.protocol !== "wss:" || server.username || server.password || server.hash) {
        throw new Error("Enter a secure SIP WebSocket address (wss://).");
    }
    const aor = form.get("aor").trim();
    const parsed = /^sip:[\w.!~*'+%-]+@([a-z0-9.-]+(?::[0-9]{1,5})?)$/i.exec(aor);
    if (!parsed) throw new Error("Enter a SIP account like sip:user@example.com.");
    const domain = form.get("domain").trim() || parsed[1];
    if (!/^[a-z0-9.-]+(?::[0-9]{1,5})?$/i.test(domain)) throw new Error("Enter a valid dial domain.");
    const destination = form.get("destination").trim();
    const number = destination.replace(/[\s().-]/g, "");
    const target = /^\+?\d{3,16}$/.test(number) ? "sip:" + number + "@" + domain : destination;
    if (!/^sip:[\w.!~*'+%-]+@[a-z0-9.-]+(?::[0-9]{1,5})?$/i.test(target)) {
        throw new Error("Enter a phone number or valid SIP address.");
    }
    return {server: server.href, aor, target, username: form.get("username").trim(),
        password: form.get("password"), register: form.has("register")};
}

dial.addEventListener("submit", async event => {
    event.preventDefault();
    if (attempt) return;
    let config;
    try {
        config = sipConfig();
        if (!window.JaleSip || !navigator.mediaDevices?.getUserMedia) {
            throw new Error("Update Android System WebView to use VoIP calling.");
        }
    } catch (error) {
        callStatus.textContent = error.message;
        return;
    }
    const item = {};
    transcript = "";
    byId("transcript").textContent = "";
    byId("suggestion").textContent = "Who handles snow, hauling, or cleanouts for your properties?";
    attempt = item;
    controls();
    callStatus.textContent = "Connecting to VoIP service…";
    try {
        context = new AudioContext();
        await context.resume();
        if (attempt !== item || item.ending) return;
        const client = new window.JaleSip(config.server, {
            aor: config.aor,
            sendDTMFUsingSessionDescriptionHandler: true,
            media: {constraints: {audio: true, video: false}, remote: {audio: remote}},
            userAgentOptions: {authorizationUsername: config.username || undefined,
                authorizationPassword: config.password, logLevel: "error", logBuiltinEnabled: false},
            delegate: {
                onCallAnswered() {
                    if (attempt !== item || item.ending) return;
                    connected = true;
                    callStatus.textContent = "Connected";
                    controls();
                },
                onCallHangup() { void endCall(item, "Call ended."); },
                onServerDisconnect() { void endCall(item, "VoIP connection closed."); }
            }
        });
        phone = client;
        await client.connect();
        if (attempt !== item || item.ending) return;
        if (config.register) await client.register();
        if (attempt !== item || item.ending) return;
        callStatus.textContent = "Dialing…";
        await client.call(config.target);
    } catch {
        await endCall(item, "Could not place the call. Check the provider settings and microphone permission.");
    }
});

hangup.addEventListener("click", async () => {
    const item = attempt;
    if (!item) return;
    hangup.disabled = true;
    try { if (phone) await phone.hangup(); } catch { /* call may still be connecting */ }
    await endCall(item, "Call ended.");
});

keypad.addEventListener("submit", async event => {
    event.preventDefault();
    if (!connected) return;
    const digit = keypad.elements.tone.value;
    if (!/^[0-9*#]$/.test(digit)) return;
    try {
        await phone.sendDTMF(digit);
        keypad.reset();
    } catch {
        callStatus.textContent = "The call service did not accept that keypad digit.";
    }
});

prepare.addEventListener("click", async () => {
    prepare.disabled = true;
    speechStatus.textContent = "Preparing the English model…";
    try {
        await speech("prepare");
        prepared = true;
        speechStatus.textContent = "English speech model ready.";
    } catch (error) {
        speechStatus.textContent = error.message;
        prepare.disabled = false;
    }
    controls();
});

async function pump(recording) {
    while (capture === recording && recording.queue.length) {
        const frame = recording.queue.shift();
        const encoded = btoa(String.fromCharCode(...new Uint8Array(frame)));
        const data = { session: recording.session, index: recording.index++, audio: encoded };
        try {
            update(await speech("audio", data));
        } catch {
            speechStatus.textContent = "Live transcription stopped. The call is still active.";
            setTimeout(() => { if (capture === recording) void finishCoaching(); }, 0);
            return;
        }
    }
}

transcribe.addEventListener("click", async () => {
    if (!prepared || !connected || coaching) return;
    const item = attempt;
    const audioContext = context;
    let session;
    coaching = true;
    controls();
    try {
        const inbound = phone.remoteMediaStream || remote.srcObject;
        if (!inbound?.getAudioTracks().length || !audioContext.audioWorklet) {
            throw new Error("Remote audio or streaming audio support is unavailable.");
        }
        await audioContext.audioWorklet.addModule("/office/call-assets/pcm.js");
        if (attempt !== item || !coaching) return;
        ({session} = await speech("start"));
        if (attempt !== item || !coaching) {
            await speech("stop", {session});
            return;
        }
        const source = audioContext.createMediaStreamSource(inbound);
        const worklet = new AudioWorkletNode(audioContext, "jale-pcm");
        const muted = audioContext.createGain();
        const recording = {session, source, worklet, muted, index: 0, queue: [], inFlight: null};
        capture = recording;
        worklet.port.onmessage = event => {
            if (capture !== recording) return;
            if (recording.queue.length === 20) {
                speechStatus.textContent = "This device fell behind. Coaching stopped; the call is still active.";
                void finishCoaching();
                return;
            }
            recording.queue.push(event.data);
            if (!recording.inFlight) {
                recording.inFlight = pump(recording).finally(() => { recording.inFlight = null; });
            }
        };
        source.connect(worklet);
        muted.gain.value = 0;
        worklet.connect(muted).connect(audioContext.destination);
        speechStatus.textContent = "Live coaching on";
    } catch (error) {
        if (session && !capture) await speech("stop", {session}).catch(() => {});
        await finishCoaching();
        speechStatus.textContent = error.message;
    }
});

stopCoach.addEventListener("click", async () => {
    await finishCoaching();
    speechStatus.textContent = "Coaching off";
});

byId("copy-notes").addEventListener("click", () => {
    const notes = byId("call-notes");
    notes.value = [notes.value, transcript.slice(-500)].filter(Boolean).join("\n").slice(0, 500);
    notes.focus();
});

window.addEventListener("pagehide", () => {
    if (capture) {
        void fetch("/office/call-speech/stop", {method: "POST",
            body: new URLSearchParams({session: capture.session}), keepalive: true}).catch(() => {});
    }
    if (phone) void phone.disconnect().catch(() => {});
});
