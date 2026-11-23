#include <jni.h>
#include <android/log.h>
#include "llama.h"
#include <algorithm>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::mutex inference_mutex;
llama_model * cached_model = nullptr;
std::string cached_path;

void fail(JNIEnv * env, const std::string & message) {
    __android_log_print(ANDROID_LOG_ERROR, "JALE_QWEN", "%s", message.c_str());
    jclass klass = env->FindClass("java/lang/IllegalStateException");
    if (klass != nullptr) env->ThrowNew(klass, message.c_str());
}

void set_tokens(llama_batch_ext * batch, const llama_token * tokens, int n, int offset) {
    llama_batch_ext_clear(batch);
    for (int i = 0; i < n; ++i) {
        const int index = llama_batch_ext_add_token(batch, 0, tokens[i]);
        const llama_pos position = offset + i;
        llama_batch_ext_set_pos(batch, index, &position);
    }
    llama_batch_ext_set_output_logits(batch, n - 1, true);
}

// The estimate contract is one JSON object. Stop as soon as the object is
// complete, rather than spending CPU time on unnecessary trailing text.
bool complete_json_object(const std::string & output) {
    bool quoted = false;
    bool escaped = false;
    bool opened = false;
    int depth = 0;
    for (char ch : output) {
        if (escaped) { escaped = false; continue; }
        if (quoted && ch == '\\') { escaped = true; continue; }
        if (ch == '"') { quoted = !quoted; continue; }
        if (quoted) continue;
        if (ch == '{') { opened = true; ++depth; }
        if (ch == '}' && opened && --depth == 0) return true;
    }
    return false;
}

std::string generate(const std::string & model_path, const std::string & prompt, int max_new) {
    if (model_path.empty() || prompt.empty()) throw std::runtime_error("Model and prompt required");
    if (max_new < 1 || max_new > 1024) throw std::runtime_error("Invalid output token limit");
    // Serial inference prevents two jobs from allocating separate large model
    // tensors concurrently and exhausting a phone's RAM.
    std::lock_guard<std::mutex> lock(inference_mutex);
    static std::once_flag backend_once;
    std::call_once(backend_once, [] { llama_backend_init(); });
    if (cached_model == nullptr || cached_path != model_path) {
        if (cached_model != nullptr) {
            llama_model_free(cached_model);
            cached_model = nullptr;
            cached_path.clear();
        }
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        cached_model = llama_model_load_from_file(model_path.c_str(), mp);
        if (!cached_model) throw std::runtime_error("Could not load bundled Qwen GGUF");
        cached_path = model_path;
    }
    const llama_vocab * vocab = llama_model_get_vocab(cached_model);
    int count = -llama_tokenize(vocab, prompt.data(), prompt.size(), nullptr, 0, true, true);
    if (count <= 0 || count > 1900) throw std::runtime_error("Qwen input exceeds 1900 tokens");
    std::vector<llama_token> tokens(count);
    if (llama_tokenize(vocab, prompt.data(), prompt.size(), tokens.data(), count, true, true) < 0) {
        throw std::runtime_error("Qwen prompt tokenization failed");
    }

    auto cp = llama_context_default_params();
    cp.n_ctx = std::max(2048, count + max_new + 64);
    cp.n_batch = count;
    cp.n_threads = 4;
    cp.n_threads_batch = 4;
    cp.no_perf = true;
    llama_context * ctx = llama_init_from_model(cached_model, cp);
    if (!ctx) throw std::runtime_error("Qwen context allocation failed");
    llama_batch_ext * batch = nullptr;
    llama_sampler * sampler = nullptr;
    try {
        batch = llama_batch_ext_init(ctx);
        if (!batch) throw std::runtime_error("Qwen batch allocation failed");
        auto params = llama_sampler_chain_default_params();
        params.no_perf = true;
        sampler = llama_sampler_chain_init(params);
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
        set_tokens(batch, tokens.data(), count, 0);
        std::string output;
        int position = 0;
        int current_batch_size = count;
        for (int i = 0; i < max_new; ++i) {
            if (llama_process(ctx, LLAMA_PROCESS_TYPE_DECODE, batch) != 0) {
                throw std::runtime_error("Qwen decoding failed");
            }
            position += current_batch_size;
            llama_token next = llama_sampler_sample(sampler, ctx, -1);
            if (llama_vocab_is_eog(vocab, next)) break;
            char piece[4096];
            int size = llama_token_to_piece(vocab, next, piece, sizeof(piece), 0, true);
            if (size < 0 || size >= int(sizeof(piece))) throw std::runtime_error("Qwen token decoding failed");
            output.append(piece, size);
            if (complete_json_object(output)) break;
            if (output.size() > 16000) break;
            set_tokens(batch, &next, 1, position);
            current_batch_size = 1;
        }
        llama_sampler_free(sampler);
        llama_batch_ext_free(batch);
        llama_free(ctx);
        return output;
    } catch (...) {
        if (sampler) llama_sampler_free(sampler);
        if (batch) llama_batch_ext_free(batch);
        llama_free(ctx);
        throw;
    }
}
}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_tomeygeneral_elgrande_QwenNative_nativeGenerate(
    JNIEnv * env, jclass, jstring model_path, jstring prompt, jint max_tokens) {
    if (!model_path || !prompt) {
        fail(env, "Qwen requires a model file and prompt");
        return nullptr;
    }
    const char * model_chars = env->GetStringUTFChars(model_path, nullptr);
    if (!model_chars) return nullptr;
    const char * prompt_chars = env->GetStringUTFChars(prompt, nullptr);
    if (!prompt_chars) {
        env->ReleaseStringUTFChars(model_path, model_chars);
        return nullptr;
    }
    std::string model(model_chars);
    std::string input(prompt_chars);
    env->ReleaseStringUTFChars(model_path, model_chars);
    env->ReleaseStringUTFChars(prompt, prompt_chars);
    try {
        std::string result = generate(model, input, max_tokens);
        return env->NewStringUTF(result.c_str());
    } catch (const std::exception & error) {
        fail(env, error.what());
        return nullptr;
    }
}
