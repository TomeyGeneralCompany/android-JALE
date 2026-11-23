/* Produce little-endian 16 kHz PCM in 200 ms frames from the remote track. */
class JalePcm extends AudioWorkletProcessor {
    constructor() {
        super();
        this.phase = 0;
        this.sum = 0;
        this.count = 0;
        this.buffer = new ArrayBuffer(6400);
        this.view = new DataView(this.buffer);
        this.offset = 0;
    }

    process(inputs) {
        const input = inputs[0]?.[0];
        if (!input) return true;
        for (const sample of input) {
            this.sum += sample;
            this.count++;
            this.phase += 16000;
            if (this.phase < sampleRate) continue;
            this.phase -= sampleRate;
            const mono = Math.max(-1, Math.min(1, this.sum / this.count));
            this.view.setInt16(this.offset, Math.round(mono * (mono < 0 ? 32768 : 32767)), true);
            this.offset += 2;
            this.sum = 0;
            this.count = 0;
            if (this.offset === this.buffer.byteLength) {
                this.port.postMessage(this.buffer, [this.buffer]);
                this.buffer = new ArrayBuffer(6400);
                this.view = new DataView(this.buffer);
                this.offset = 0;
            }
        }
        return true;
    }
}
registerProcessor("jale-pcm", JalePcm);
